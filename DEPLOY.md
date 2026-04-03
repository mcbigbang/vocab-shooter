# Vocab Shooter - 部署指南

## 🚀 Railway 部署（推荐英国用户）

### 步骤

1. **Fork 这个仓库** 到你的 GitHub

2. **注册 Railway**
   - 访问 [railway.app](https://railway.app)
   - 用 GitHub 登录

3. **创建新项目**
   - 点击 "New Project" → "Deploy from GitHub repo"
   - 选择你 Fork 的仓库

4. **部署完成**
   - Railway 会自动检测 Python 项目
   - 构建完成后会给你一个 URL，例如：`https://vocab-shooter.up.railway.app`

5. **分享给学生**
   - 教师端：`https://你的域名/host`
   - 学生端：`https://你的域名/player?room=房间码`

---

## 📁 部署文件说明

| 文件 | 说明 |
|------|------|
| `app_shooter.py` | 后端服务器（Socket.IO + FastAPI）|
| `static/host.html` | 教师端页面 |
| `static/player.html` | 学生端页面 |
| `requirements.txt` | Python 依赖 |

---

## ⚠️ 注意事项

- Railway 免费版每月 $5 额度，足够学生日常使用
- 30天无活动会休眠，下次访问会自动唤醒
- 学生端链接格式：`https://你的域名/player?room=1234`

---

## 🔧 本地运行

```bash
cd vocab-shooter
pip install -r requirements.txt
python app_shooter.py
```

访问 `http://localhost:8001/host` 打开教师端
