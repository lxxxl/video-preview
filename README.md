# Video Preview

基于磁力链接的视频预览缩略图生成服务。通过 P2P 选择性下载视频文件的关键片段，生成多个时间点的 720P 预览截图。相同磁力链接自动命中缓存，即时返回。

## 工作原理

1. 解析磁力链接，通过 DHT + 公共 Tracker 获取种子元数据
2. 识别种子中最大的视频文件
3. 下载文件头部，解析 MP4 moov atom 获取关键帧索引
4. 计算每个采样点（默认 5%~95%）在文件中的实际关键帧位置
5. 使用 libtorrent 仅下载关键帧对应的 Piece（通常只需下载 <1% 的文件数据）
6. 构建稀疏 MP4 文件，FFmpeg 精确 seek 截取 720P 缩略图
7. 截图按 info_hash 归档，相同磁力链接再次请求直接返回缓存

## 技术栈

| 组件 | 选型 |
|------|------|
| Web 框架 | Flask + Gunicorn |
| P2P 引擎 | python-libtorrent >= 2.0 |
| 媒体处理 | FFmpeg 5.x+ |
| 任务队列 | Redis + RQ |
| 存储 | SQLite（任务元数据）+ 本地磁盘（截图归档） |

## 快速开始

### Docker 部署（推荐）

```bash
docker-compose up -d
```

启动三个服务：web（端口 5000）、worker、redis。

### 手动部署

```bash
apt install python3-libtorrent ffmpeg redis-server
pip install -r requirements.txt
redis-server &
python worker.py &
python app.py
```

---

## API 文档

Base URL: `http://localhost:5000`

### 1. 提交任务

创建一个视频预览截图任务。如果该磁力链接已有缓存截图，立即返回结果。

```
POST /api/task
Content-Type: application/json
```

**请求参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `magnet` | string | 是 | — | 磁力链接，必须包含 `xt=urn:btih:` + 40 位 hex 或 32 位 Base32 的 info_hash |
| `sample_points` | int[] | 否 | `[5,10,15,...,95]` | 采样百分比列表，每个值 1-99，最多 19 个 |
| `timeout` | int | 否 | `600` | 任务超时秒数，范围 60-600 |

**请求示例：**

```bash
# 使用默认参数（19 个采样点）
curl -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{"magnet": "magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c"}'

# 自定义采样点和超时
curl -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{
    "magnet": "magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
    "sample_points": [10, 30, 50, 70, 90],
    "timeout": 300
  }'
```

**响应 — 新任务入队（201）：**

```json
{
  "task_id": "a1b2c3d4",
  "status": "queued",
  "message": "Task created"
}
```

**响应 — 缓存命中（200）：**

相同磁力链接之前已生成过截图，直接返回。

```json
{
  "task_id": "e5f6a7b8",
  "status": "completed",
  "message": "Cache hit"
}
```

**错误响应：**

| 状态码 | 场景 | 示例 |
|--------|------|------|
| 400 | 参数校验失败 | `{"error": "Invalid magnet URI format (must contain xt=urn:btih: with valid info_hash)"}` |
| 400 | 采样点超限 | `{"error": "'sample_points' max length is 19"}` |
| 400 | 超时范围错误 | `{"error": "'timeout' must be an integer between 60 and 600"}` |
| 503 | 任务队列已满 | `{"error": "Task queue is full, try again later"}` |

---

### 2. 查询任务状态

获取任务的完整状态，包括下载进度和截图列表。

```
GET /api/task/{task_id}
```

**响应（200）：**

```json
{
  "task_id": "a1b2c3d4",
  "info_hash": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
  "status": "downloading",
  "magnet": "magnet:?xt=urn:btih:...",
  "sample_points": [10, 30, 50, 70, 90],
  "timeout": 300,
  "metadata_resolved": true,
  "video_file": "Big Buck Bunny/Big Buck Bunny.mp4",
  "video_size_bytes": 276134947,
  "pieces_needed": 25,
  "pieces_downloaded": 18,
  "download_speed_bps": 2457600,
  "peers_connected": 37,
  "error": null,
  "created_at": "2026-06-26T10:00:00+00:00",
  "updated_at": "2026-06-26T10:01:30+00:00",
  "snapshots": [
    {"percent": 10, "filename": "snap_10.jpg", "status": "ready", "url": "/snapshots/dd8255ec.../snap_10.jpg"},
    {"percent": 30, "filename": "snap_30.jpg", "status": "ready", "url": "/snapshots/dd8255ec.../snap_30.jpg"},
    {"percent": 50, "filename": null, "status": "pending", "url": null},
    {"percent": 70, "filename": null, "status": "pending", "url": null},
    {"percent": 90, "filename": null, "status": "pending", "url": null}
  ]
}
```

**任务状态流转：**

```
queued → resolving_metadata → downloading → generating → completed
              ↓                    ↓
            failed              timeout（返回已生成的截图）
```

| 状态 | 说明 |
|------|------|
| `queued` | 任务已创建，等待 Worker 处理 |
| `resolving_metadata` | 正在通过 DHT 获取种子元数据 |
| `downloading` | 正在下载目标 Piece |
| `generating` | 正在用 FFmpeg 生成截图 |
| `completed` | 所有截图已生成 |
| `timeout` | 下载超时，返回已完成的截图 |
| `failed` | 任务失败（元数据超时、无视频文件等） |
| `cancelled` | 用户取消 |

**错误响应：**

| 状态码 | 场景 |
|--------|------|
| 404 | task_id 不存在 |

---

### 3. 获取截图列表

列出任务已生成的所有截图文件及其 URL。

```
GET /api/task/{task_id}/snapshots
```

**响应（200）：**

```json
{
  "task_id": "a1b2c3d4",
  "snapshots": [
    {"filename": "snap_10.jpg", "url": "/snapshots/dd8255ec.../snap_10.jpg"},
    {"filename": "snap_30.jpg", "url": "/snapshots/dd8255ec.../snap_30.jpg"},
    {"filename": "snap_50.jpg", "url": "/snapshots/dd8255ec.../snap_50.jpg"},
    {"filename": "snap_70.jpg", "url": "/snapshots/dd8255ec.../snap_70.jpg"},
    {"filename": "snap_90.jpg", "url": "/snapshots/dd8255ec.../snap_90.jpg"}
  ]
}
```

---

### 4. 下载截图

直接获取截图的 JPEG 文件。截图按磁力链接的 info_hash 归档存储。

```
GET /snapshots/{info_hash}/{filename}
```

**参数：**

| 参数 | 说明 | 示例 |
|------|------|------|
| `info_hash` | 磁力链接的 info_hash | `dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c` |
| `filename` | 截图文件名（必须以 `.jpg` 结尾） | `snap_50.jpg` |

**响应：** JPEG 图片文件（720P，宽度 1280px）

**示例：**

```bash
# 下载 50% 位置的截图
curl -o preview.jpg http://localhost:5000/snapshots/dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c/snap_50.jpg

# 在浏览器中直接访问
open http://localhost:5000/snapshots/dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c/snap_50.jpg
```

**错误响应：**

| 状态码 | 场景 |
|--------|------|
| 400 | 文件名不以 `.jpg` 结尾 |
| 404 | info_hash 目录不存在或文件不存在 |

---

### 5. 取消任务

取消一个进行中的任务。

```
DELETE /api/task/{task_id}
```

**响应（200）：**

```json
{
  "task_id": "a1b2c3d4",
  "status": "cancelled"
}
```

---

## 典型使用流程

### 场景一：首次请求

```bash
# 1. 提交任务
RESP=$(curl -s -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{"magnet": "magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c"}')
TASK_ID=$(echo $RESP | jq -r .task_id)
echo "Task: $TASK_ID, Status: $(echo $RESP | jq -r .status)"

# 2. 轮询状态直到完成
while true; do
  STATUS=$(curl -s http://localhost:5000/api/task/$TASK_ID | jq -r .status)
  echo "Status: $STATUS"
  [ "$STATUS" = "completed" ] || [ "$STATUS" = "timeout" ] || [ "$STATUS" = "failed" ] && break
  sleep 5
done

# 3. 获取截图列表
curl -s http://localhost:5000/api/task/$TASK_ID/snapshots | jq .

# 4. 下载截图
INFO_HASH=$(curl -s http://localhost:5000/api/task/$TASK_ID | jq -r .info_hash)
curl -o snap_50.jpg http://localhost:5000/snapshots/$INFO_HASH/snap_50.jpg
```

### 场景二：缓存命中

```bash
# 再次提交相同的磁力链接
curl -s -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{"magnet": "magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c"}'

# 响应：status=completed, message="Cache hit"（无需等待）
```

---

## 截图规格

| 属性 | 值 |
|------|-----|
| 分辨率 | 1280px 宽（720P），高度按原始比例自动计算 |
| 格式 | JPEG |
| 质量 | 85/100 |
| 命名规则 | `snap_{percent:02d}.jpg`（如 `snap_05.jpg`、`snap_50.jpg`） |
| 存储路径 | `data/snapshots/{info_hash}/` |
| 保留时间 | 24 小时（可配置） |

## 配置

通过环境变量覆盖默认配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接地址 |
| `DATABASE_PATH` | `data/tasks.db` | SQLite 数据库路径 |
| `SNAPSHOT_DIR` | `data/snapshots` | 截图归档目录 |
| `TEMP_DIR` | `data/tmp` | 临时文件目录 |
| `DOWNLOAD_DIR` | `data/downloads` | libtorrent 下载目录 |

更多配置项见 `config.py`。

## 项目结构

```
├── app.py                      # Flask 入口
├── config.py                   # 配置管理
├── worker.py                   # RQ Worker 入口
├── core/
│   ├── session_manager.py      # libtorrent 全局 session（单例 + DHT 引导）
│   ├── torrent_parser.py       # 种子解析、视频识别、公共 Tracker
│   ├── smart_downloader.py     # Piece 优先级设置与下载监控
│   ├── segment_extractor.py    # Piece 字节提取与片段拼接
│   ├── snapshot_generator.py   # FFmpeg 截图（多策略回退）
│   └── mp4_utils.py            # MP4 moov 解析、关键帧索引
├── api/
│   ├── routes.py               # API 路由（含缓存命中逻辑）
│   ├── schemas.py              # 请求校验
│   └── errors.py               # 统一错误处理
├── storage/
│   ├── task_store.py           # SQLite 任务 CRUD + 缓存查询
│   └── cleanup.py              # 资源清理（按 info_hash 引用计数）
├── tests/                      # 81 个测试（含 API benchmark）
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
| 截图分辨率 | 1280px (720P) |
| 单任务磁盘占用 | 500MB |
| 全局磁盘上限 | 10GB |
| 单任务超时 | 10 分钟 |
| 最大并发任务 | 3 |
| 队列最大长度 | 20 |
| 截图保留时间 | 24 小时 |
| FFmpeg 执行超时 | 30 秒 |
| 每 IP 每分钟请求数 | 5 |
| 全局每分钟请求数 | 30 |

## License

MIT
