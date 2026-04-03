"""
Vocab Shooter - 独立运行的Web服务器
重新设计版本 - 目标词汇和干扰词汇分离
"""
import asyncio
import json
import random
import uuid
from datetime import datetime
from typing import Dict, Set, Optional
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import socketio
import os

# ============ Socket.IO Server ============
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*'
)

# ============ FastAPI App ============
app = FastAPI(title="Vocab Shooter", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载Socket.IO
socket_app = socketio.ASGIApp(sio, app)

# 挂载静态文件目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/js", StaticFiles(directory=os.path.join(BASE_DIR, "static", "js")), name="js")

# ============ 房间状态 ============
class Room:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: Dict[str, str] = {}  # player_id -> player_name
        self.player_sids: Dict[str, str] = {}  # player_id -> socket_id
        self.sid_to_player: Dict[str, str] = {}  # socket_id -> player_id
        self.target_words: list = []  # 目标词汇（学生需要点击的）
        self.distractor_words: list = []  # 干扰词汇（陷阱词汇）
        self.fall_speed: int = 60
        self.game_duration: int = 180
        self.is_playing: bool = False
        self.scores: Dict[str, int] = {}
        self.hits: Dict[str, int] = {}
        self.misses: Dict[str, int] = {}

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
    print(f"Client disconnected: {sid}")
    # 清理离开的客户
    for room_id, room in list(rooms.items()):
        if sid in room.sid_to_player:
            player_id = room.sid_to_player[sid]
            if player_id in room.players:
                del room.players[player_id]
            if player_id in room.player_sids:
                del room.player_sids[player_id]
            del room.sid_to_player[sid]
            if player_id in room.scores:
                del room.scores[player_id]
            if player_id in room.hits:
                del room.hits[player_id]
            if player_id in room.misses:
                del room.misses[player_id]
            # 通知房间里的其他人
            await sio.emit('player_left', {'player_id': player_id, 'players': room.players}, room=room_id)
            break

@sio.event
async def create_room(sid, data):
    """Socket.IO 创建房间"""
    room_id = generate_room_code()
    while room_id in rooms:
        room_id = generate_room_code()

    # 解析目标词汇
    target_text = data.get('target_words', '')
    target_list = [v.strip() for v in target_text.split('\n') if v.strip()]
    
    # 解析干扰词汇
    distractor_text = data.get('distractor_words', '')
    distractor_list = [v.strip() for v in distractor_text.split('\n') if v.strip()]
    
    if len(target_list) < 1:
        return {'success': False, 'error': '至少需要1个目标词汇'}
    
    if len(distractor_list) < 1:
        return {'success': False, 'error': '至少需要1个干扰词汇'}
    
    room = Room(room_id)
    room.target_words = target_list
    room.distractor_words = distractor_list
    room.fall_speed = data.get('fall_speed', 60)
    room.game_duration = data.get('game_duration', 180)
    
    rooms[room_id] = room
    
    # 教师自动加入房间
    player_id = str(uuid.uuid4())
    room.players[player_id] = '教师'
    room.player_sids[player_id] = sid
    room.sid_to_player[sid] = player_id
    room.scores[player_id] = 0
    room.hits[player_id] = 0
    room.misses[player_id] = 0
    
    await sio.save_session(sid, {'room': room_id, 'player_id': player_id})
    await sio.enter_room(sid, room_id)
    
    return {'success': True, 'room_id': room_id, 'player_id': player_id}

@sio.event
async def join_room(sid, data):
    """Socket.IO 加入房间"""
    room_id = str(data.get('room_id', ''))
    player_name = data.get('player_name', '学生')
    
    if room_id not in rooms:
        return {'success': False, 'error': '房间不存在'}
    
    room = rooms[room_id]
    
    player_id = str(uuid.uuid4())
    room.players[player_id] = player_name
    room.player_sids[player_id] = sid
    room.sid_to_player[sid] = player_id
    room.scores[player_id] = 0
    room.hits[player_id] = 0
    room.misses[player_id] = 0
    
    await sio.save_session(sid, {'room': room_id, 'player_id': player_id})
    await sio.enter_room(sid, room_id)
    
    # 通知房间里的所有人（包括新加入者）
    await sio.emit('player_joined', {
        'player_id': player_id,
        'player_name': player_name,
        'players': room.players
    }, room=room_id)
    
    return {
        'success': True,
        'player_id': player_id,
        'target_count': len(room.target_words),
        'distractor_count': len(room.distractor_words),
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
    room.is_playing = True
    
    # 构建词汇队列：(text, isTarget) 元组标记
    # 目标词标记为 True，干扰词标记为 False
    all_words = [(w, True) for w in room.target_words]  # 目标词
    all_words.extend([(w, False) for w in room.distractor_words])  # 干扰词
    random.shuffle(all_words)
    
    await sio.emit('game_start', {
        'target_words': room.target_words,
        'distractor_words': room.distractor_words,
        'fall_speed': room.fall_speed,
        'duration': room.game_duration
    }, room=room_id)
    
    asyncio.create_task(game_loop(room_id))
    
    return {'success': True}

async def game_loop(room_id: str):
    """游戏主循环"""
    room = rooms.get(room_id)
    if not room:
        return
    
    # 构建词汇队列
    all_words = [(w, True) for w in room.target_words]
    all_words.extend([(w, False) for w in room.distractor_words])
    random.shuffle(all_words)
    
    # 复制队列用于循环
    word_queue = all_words * 10  # 扩大循环次数
    word_index = 0
    
    # 下落间隔：根据速度计算
    interval = max(1.0, 2.5 - (room.fall_speed / 100))
    elapsed = 0
    start_time = asyncio.get_event_loop().time()
    
    while room.is_playing and elapsed < room.game_duration:
        await asyncio.sleep(interval)
        if not room.is_playing:
            break
        
        # 如果词汇用完，重新构建队列
        if word_index >= len(word_queue):
            all_words = [(w, True) for w in room.target_words]
            all_words.extend([(w, False) for w in room.distractor_words])
            random.shuffle(all_words)
            word_queue = all_words * 10
            word_index = 0
        
        word_text, is_target = word_queue[word_index]
        word_index += 1
        
        word_obj = {
            'id': str(uuid.uuid4()),
            'text': word_text,
            'y': 0,
            'isTarget': is_target
        }
        
        await sio.emit('word_spawn', word_obj, room=room_id)
        elapsed = asyncio.get_event_loop().time() - start_time
    
    if room.is_playing:
        room.is_playing = False
        final_scores = {pid: room.scores.get(pid, 0) for pid in room.players}
        final_hits = {pid: room.hits.get(pid, 0) for pid in room.players}
        final_misses = {pid: room.misses.get(pid, 0) for pid in room.players}
        await sio.emit('game_end', {
            'scores': final_scores,
            'hits': final_hits,
            'misses': final_misses
        }, room=room_id)

@sio.event
async def hit_word(sid, data):
    """玩家击中词汇"""
    session = await sio.get_session(sid)
    room_id = session.get('room')
    player_id = session.get('player_id')
    
    if not room_id or room_id not in rooms or not player_id:
        return
    
    room = rooms[room_id]
    word_text = data.get('word_text')
    
    # 判断是否为目标词
    is_target = word_text in room.target_words
    
    if is_target:
        room.scores[player_id] = room.scores.get(player_id, 0) + 1
        room.hits[player_id] = room.hits.get(player_id, 0) + 1
        score_change = 1
    else:
        room.scores[player_id] = max(0, room.scores.get(player_id, 0) - 1)
        room.misses[player_id] = room.misses.get(player_id, 0) + 1
        score_change = -1
    
    await sio.emit('score_update', {
        'player_id': player_id,
        'player_name': room.players.get(player_id, '学生'),
        'score': room.scores[player_id],
        'change': score_change,
        'is_target': is_target,
        'word_id': data.get('word_id')
    }, room=room_id)

@sio.event
async def pause_game(sid, data):
    session = await sio.get_session(sid)
    room_id = session.get('room')
    if room_id and room_id in rooms:
        rooms[room_id].is_playing = False
        await sio.emit('game_paused', {}, room=room_id)

@sio.event
async def resume_game(sid, data):
    session = await sio.get_session(sid)
    room_id = session.get('room')
    if room_id and room_id in rooms:
        rooms[room_id].is_playing = True
        await sio.emit('game_resumed', {}, room=room_id)
        asyncio.create_task(game_loop(room_id))

@sio.event
async def end_game(sid, data):
    session = await sio.get_session(sid)
    room_id = session.get('room')
    if room_id and room_id in rooms:
        room = rooms[room_id]
        room.is_playing = False
        final_scores = {pid: room.scores.get(pid, 0) for pid in room.players}
        final_hits = {pid: room.hits.get(pid, 0) for pid in room.players}
        final_misses = {pid: room.misses.get(pid, 0) for pid in room.players}
        await sio.emit('game_ended', {
            'scores': final_scores,
            'hits': final_hits,
            'misses': final_misses
        }, room=room_id)

@sio.event
async def add_words(sid, data):
    session = await sio.get_session(sid)
    room_id = session.get('room')
    if room_id and room_id in rooms:
        new_words = data.get('words', '')
        new_vocab = [w.strip() for w in new_words.split('\n') if w.strip()]
        if new_vocab:
            rooms[room_id].target_words.extend(new_vocab)
            await sio.emit('words_added', {'words': new_vocab}, room=room_id)

# ============ REST API ============

@app.get("/")
async def root():
    return FileResponse("static/host.html")

@app.get("/host")
async def host_page():
    return FileResponse("static/host.html")

@app.get("/player.html")
async def player_page():
    return FileResponse("static/player.html")

@app.get("/player")
async def player_page():
    return FileResponse("static/player.html")

@app.post("/api/shooter/create")
async def api_create_room(request: Request):
    data = await request.json()
    
    target_text = data.get('target_words', '')
    target_list = [v.strip() for v in target_text.split('\n') if v.strip()]
    
    distractor_text = data.get('distractor_words', '')
    distractor_list = [v.strip() for v in distractor_text.split('\n') if v.strip()]
    
    if len(target_list) < 1:
        return JSONResponse({'success': False, 'error': '至少需要1个目标词汇'})
    
    if len(distractor_list) < 1:
        return JSONResponse({'success': False, 'error': '至少需要1个干扰词汇'})
    
    room_id = generate_room_code()
    while room_id in rooms:
        room_id = generate_room_code()
    
    room = Room(room_id)
    room.target_words = target_list
    room.distractor_words = distractor_list
    room.fall_speed = data.get('fall_speed', 60)
    room.game_duration = data.get('game_duration', 180)
    
    rooms[room_id] = room
    
    return JSONResponse({'success': True, 'room_id': room_id})

@app.post("/api/shooter/join")
async def api_join_room(request: Request):
    data = await request.json()
    room_id = str(data.get('room_id', ''))
    player_name = data.get('player_name', '学生')
    
    if room_id not in rooms:
        return JSONResponse({'success': False, 'error': '房间不存在'})
    
    room = rooms[room_id]
    player_id = str(uuid.uuid4())
    
    room.players[player_id] = player_name
    room.scores[player_id] = 0
    room.hits[player_id] = 0
    room.misses[player_id] = 0
    
    # 通过 Socket.IO 通知房间里的所有人
    await sio.emit('player_joined', {
        'player_id': player_id,
        'player_name': player_name,
        'players': room.players
    }, room=room_id)
    
    return JSONResponse({
        'success': True,
        'player_id': player_id,
        'target_count': len(room.target_words),
        'distractor_count': len(room.distractor_words),
        'fall_speed': room.fall_speed,
        'game_duration': room.game_duration
    })

@app.get("/api/shooter/status")
async def api_room_status(room_id: str):
    if room_id not in rooms:
        return JSONResponse({'success': False, 'error': '房间不存在'})
    
    room = rooms[room_id]
    return JSONResponse({
        'success': True,
        'is_playing': room.is_playing,
        'players': room.players,
        'target_count': len(room.target_words),
        'distractor_count': len(room.distractor_words)
    })

@app.get("/api/rooms/count")
async def rooms_count():
    return {"status": "ok", "rooms": len(rooms)}

@app.get("/health")
async def health():
    return {"status": "ok", "rooms": len(rooms)}

# ============ 启动 ============
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8001)
