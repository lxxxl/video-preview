# Video Preview

基于磁力链接的视频预览缩略图生成服务。通过 P2P 选择性下载视频文件的关键片段，生成多个时间点的预览截图。

## 工作原理

1. 解析磁力链接，通过 DHT 获取种子元数据
2. 识别种子中最大的视频文件
3. 计算每个采样点（默认 5%、10%、...、95%）对应的 Piece 索引
4. 使用 libtorrent 仅下载目标 Piece（文件头部 + 尾部 + 采样点附近）
5. 通过 `read_piece()` 提取原始字节，拼接为可播放的视频片段
6. FFmpeg 从每个片段中截取缩略图

## 技术栈

- **Web 框架**: Flask + Gunicorn
- **P2P 引擎**: python-libtorrent >= 2.0
- **媒体处理**: FFmpeg 5.x+
- **任务队列**: Redis + RQ
- **存储**: SQLite（元数据）+ 本地磁盘（缩略图）

## 快速开始

### Docker 部署（推荐）

```bash
docker-compose up -d
```

启动后包含三个服务：
- **web** — Flask API，端口 5000
- **worker** — RQ 后台任务处理
- **redis** — 任务队列

### 手动部署

```bash
# 系统依赖
apt install python3-libtorrent ffmpeg redis-server

# Python 依赖
pip install -r requirements.txt

# 启动 Redis
redis-server &

# 启动 Worker
python worker.py &

# 启动 API 服务
python app.py
```

## API

### 提交任务

```bash
curl -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{"magnet": "magnet:?xt=urn:btih:..."}'
```

可选参数：
- `sample_points` — 采样百分比列表，默认 `[5, 10, 15, ..., 95]`，范围 1-99，最多 19 个
- `timeout` — 超时秒数，默认 600，范围 60-600

响应：
```json
{
  "task_id": "a1b2c3d4",
  "status": "queued",
  "message": "Task created"
}
```

### 查询任务状态

```bash
curl http://localhost:5000/api/task/{task_id}
```

响应包含下载进度、已生成的缩略图列表等信息。状态流转：

```
queued → resolving_metadata → downloading → generating → completed
                                  ↓                         ↓
                               timeout                   failed
```

### 获取缩略图列表

```bash
curl http://localhost:5000/api/task/{task_id}/snapshots
```

### 下载缩略图

```
GET /snapshots/{task_id}/snap_05.jpg
```

### 取消任务

```bash
curl -X DELETE http://localhost:5000/api/task/{task_id}
```

## 配置

通过环境变量覆盖默认配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接地址 |
| `DATABASE_PATH` | `data/tasks.db` | SQLite 数据库路径 |
| `SNAPSHOT_DIR` | `data/snapshots` | 缩略图存储目录 |
| `TEMP_DIR` | `data/tmp` | 临时文件目录 |
| `DOWNLOAD_DIR` | `data/downloads` | libtorrent 下载目录 |

更多配置项见 `config.py`。

## 项目结构

```
├── app.py                      # Flask 入口
├── config.py                   # 配置管理
├── worker.py                   # RQ Worker 入口
├── core/
│   ├── session_manager.py      # libtorrent 全局 session（单例）
│   ├── torrent_parser.py       # 种子解析、视频识别、Piece 映射
│   ├── smart_downloader.py     # Piece 优先级设置与下载监控
│   ├── segment_extractor.py    # Piece 字节提取与片段拼接
│   └── snapshot_generator.py   # FFmpeg 缩略图生成（含回退策略）
├── api/
│   ├── routes.py               # API 路由
│   ├── schemas.py              # 请求校验
│   └── errors.py               # 错误处理
├── storage/
│   ├── task_store.py           # SQLite 任务 CRUD
│   └── cleanup.py              # 资源清理
├── tests/                      # 单元测试（65 个）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 测试

```bash
pytest tests/ -v
```

## 资源限制

| 项目 | 限制 |
|------|------|
| 单任务磁盘占用 | 500MB |
| 全局磁盘上限 | 10GB |
| 单任务超时 | 10 分钟 |
| 最大并发任务 | 3 |
| 队列最大长度 | 20 |
| 缩略图保留时间 | 24 小时 |
| FFmpeg 执行超时 | 30 秒 |
| 每 IP 每分钟请求数 | 5 |

## License

MIT
