"""
Vocab Shooter - WebSocket 游戏服务
"""
import asyncio
import json
import random
import uuid
from datetime import datetime
from typing import Dict, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect
import socketio

# ============ Socket.IO Server ============
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*'
)

# ============ 房间状态 ============
class Room:
    def __init__(self, room_id: str, host_ws: WebSocket):
        self.room_id = room_id
        self.host = host_ws
        self.players: Dict[str, WebSocket] = {}  # player_id -> websocket
        self.player_names: Dict[str, str] = {}
        self.vocab_list: list = []
        self.distractor_ratio: float = 0.3
        self.fall_speed: int = 60  # px per second
        self.game_duration: int = 180  # 3 minutes
        self.is_playing: bool = False
        self.scores: Dict[str, int] = {}  # player_id -> score
        self.hits: Dict[str, int] = {}
        self.misses: Dict[str, int] = {}
        self.active_words: list = []  # 当前屏幕上的词汇

rooms: Dict[str, Room] = {}

def generate_room_code():
    """生成4位房间码"""
    return str(random.randint(1000, 9999))

# ============ Socket.IO 事件 ============

@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    # 清理离开的客户
    for room_id, room in list(rooms.items()):
        if room.host == sid or sid in room.players:
            if room.host == sid:
                # 主持人离开，解散房间
                await sio.emit('host_left', room=room_id)
                del rooms[room_id]
            else:
                # 玩家离开
                for pid, ws in list(room.players.items()):
                    if ws == sid:
                        del room.players[pid]
                        del room.scores[pid]
                        del room.player_names[pid]
                        await sio.emit('player_left', {'player_id': pid, 'room': room_id}, room=room_id)
                        break
    print(f"Client disconnected: {sid}")

@sio.event
async def create_room(sid, data):
    """创建房间（教师操作）"""
    room_id = generate_room_code()
    while room_id in rooms:
        room_id = generate_room_code()

    vocab_text = data.get('vocab', '')
    vocab_list = [v.strip() for v in vocab_text.split('\n') if v.strip()]
    
    if len(vocab_list) < 3:
        return {'success': False, 'error': '至少需要3个词汇'}
    
    distractor_ratio = data.get('distractor_ratio', 0.3)
    fall_speed = data.get('fall_speed', 60)
    game_duration = data.get('game_duration', 180)
    
    room = Room(room_id, sid)
    room.vocab_list = vocab_list
    room.distractor_ratio = distractor_ratio
    room.fall_speed = fall_speed
    room.game_duration = game_duration
    room.scores[sid] = 0
    room.hits[sid] = 0
    room.misses[sid] = 0
    
    rooms[room_id] = room
    
    await sio.save_session(sid, {'room': room_id, 'role': 'host'})
    
    return {'success': True, 'room_id': room_id}

@sio.event
async def join_room(sid, data):
    """加入房间（学生操作）"""
    room_id = str(data.get('room_id', ''))
    player_name = data.get('player_name', '学生')
    
    if room_id not in rooms:
        return {'success': False, 'error': '房间不存在'}
    
    room = rooms[room_id]
    player_id = str(uuid.uuid4())
    
    room.players[player_id] = sid
    room.player_names[player_id] = player_name
    room.scores[player_id] = 0
    room.hits[player_id] = 0
    room.misses[player_id] = 0
    
    await sio.save_session(sid, {'room': room_id, 'role': 'player', 'player_id': player_id})
    
    # 通知教师有新玩家加入
    await sio.emit('player_joined', {
        'player_id': player_id,
        'player_name': player_name,
        'players': room.player_names
    }, room=room_id, skip_sid=sid)
    
    return {
        'success': True,
        'player_id': player_id,
        'vocab_count': len(room.vocab_list),
        'fall_speed': room.fall_speed,
        'game_duration': room.game_duration
    }

@sio.event
async def start_game(sid, data):
    """开始游戏"""
    session = await sio.get_session(sid)
    room_id = session.get('room')
    
    if not room_id or room_id not in rooms:
        return {'success': False, 'error': '房间不存在'}
    
    room = rooms[room_id]
    
    # 生成干扰词（简单用现有词汇打乱重复）
    all_words = room.vocab_list.copy()
    distractor_count = int(len(room.vocab_list) * room.distractor_ratio)
    distractors = random.sample(room.vocab_list, min(distractor_count, len(room.vocab_list)))
    all_words.extend(distractors)
    random.shuffle(all_words)
    
    room.is_playing = True
    
    # 广播给所有玩家开始游戏
    await sio.emit('game_start', {
        'words': all_words,
        'fall_speed': room.fall_speed,
        'duration': room.game_duration
    }, room=room_id)
    
    # 启动游戏循环（服务端驱动词汇生成）
    asyncio.create_task(game_loop(room_id))
    
    return {'success': True}

async def game_loop(room_id: str):
    """游戏主循环 - 服务端驱动词汇生成"""
    room = rooms.get(room_id)
    if not room:
        return
    
    all_words = room.vocab_list.copy()
    distractor_count = int(len(room.vocab_list) * room.distractor_ratio)
    distractors = random.sample(room.vocab_list, min(distractor_count, len(room.vocab_list)))
    all_words.extend(distractors)
    
    interval = max(1.5, 3.0 - (room.fall_speed / 100))  # 速度越快，间隔越短
    elapsed = 0
    
    while room.is_playing and elapsed < room.game_duration:
        await asyncio.sleep(interval)
        if not room.is_playing:
            break
        
        # 随机选一个词下发
        word = random.choice(all_words)
        word_obj = {
            'id': str(uuid.uuid4()),
            'text': word,
            'is_target': word in room.vocab_list,
            'y': 0
        }
        room.active_words.append(word_obj)
        
        await sio.emit('word_spawn', word_obj, room=room_id)
        
        elapsed += interval
    
    # 游戏结束
    if room.is_playing:
        room.is_playing = False
        await sio.emit('game_end', {
            'scores': {pid: room.scores.get(pid, 0) for pid in room.player_names},
            'hits': {pid: room.hits.get(pid, 0) for pid in room.player_names},
            'misses': {pid: room.misses.get(pid, 0) for pid in room.player_names}
        }, room=room_id)

@sio.event
async def hit_word(sid, data):
    """玩家击中词汇"""
    session = await sio.get_session(sid)
    room_id = session.get('room')
    player_id = session.get('player_id')
    
    if not room_id or room_id not in rooms:
        return
    
    room = rooms[room_id]
    word_id = data.get('word_id')
    word_text = data.get('word_text')
    
    # 检查是否命中
    is_target = word_text in room.vocab_list
    
    if is_target:
        room.scores[player_id] = room.scores.get(player_id, 0) + 1
        room.hits[player_id] = room.hits.get(player_id, 0) + 1
        score_change = 1
    else:
        room.scores[player_id] = max(0, room.scores.get(player_id, 0) - 1)
        room.misses[player_id] = room.misses.get(player_id, 0) + 1
        score_change = -1
    
    # 广播给所有人（包含教师）
    await sio.emit('score_update', {
        'player_id': player_id,
        'player_name': room.player_names.get(player_id, '学生'),
        'score': room.scores[player_id],
        'change': score_change,
        'is_target': is_target,
        'word_id': word_id
    }, room=room_id)

@sio.event
async def pause_game(sid, data):
    """暂停游戏"""
    session = await sio.get_session(sid)
    room_id = session.get('room')
    
    if room_id and room_id in rooms:
        rooms[room_id].is_playing = False
        await sio.emit('game_paused', room=room_id)

@sio.event
async def resume_game(sid, data):
    """继续游戏"""
    session = await sio.get_session(sid)
    room_id = session.get('room')
    
    if room_id and room_id in rooms:
        rooms[room_id].is_playing = True
        asyncio.create_task(game_loop(room_id))
        await sio.emit('game_resumed', room=room_id)

@sio.event
async def end_game(sid, data):
    """结束游戏"""
    session = await sio.get_session(sid)
    room_id = session.get('room')
    
    if room_id and room_id in rooms:
        rooms[room_id].is_playing = False
        await sio.emit('game_ended', {
            'scores': {pid: rooms[room_id].scores.get(pid, 0) for pid in rooms[room_id].player_names},
            'hits': {pid: rooms[room_id].hits.get(pid, 0) for pid in rooms[room_id].player_names},
            'misses': {pid: rooms[room_id].misses.get(pid, 0) for pid in rooms[room_id].player_names}
        }, room=room_id)

@sio.event
async def add_words(sid, data):
    """动态添加词汇"""
    session = await sio.get_session(sid)
    room_id = session.get('room')
    
    if not room_id or room_id not in rooms:
        return
    
    new_words = data.get('words', '')
    new_vocab = [w.strip() for w in new_words.split('\n') if w.strip()]
    
    if new_vocab:
        rooms[room_id].vocab_list.extend(new_vocab)
        await sio.emit('words_added', {'words': new_vocab}, room=room_id)

@sio.event
async def get_room_status(sid, data):
    """获取房间状态"""
    room_id = data.get('room_id')
    
    if room_id not in rooms:
        return {'success': False, 'error': '房间不存在'}
    
    room = rooms[room_id]
    return {
        'success': True,
        'is_playing': room.is_playing,
        'players': room.player_names,
        'scores': room.scores,
        'vocab_count': len(room.vocab_list)
    }
