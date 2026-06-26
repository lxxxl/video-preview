---
name: p2p-snapshot-dev-plan
description: P2P 视频片段预览服务开发计划（改进版）
category: development
---

# P2P 视频片段预览服务开发计划

## 1. 项目概述

构建一个基于 Python 的轻量级 Web 服务，通过磁力链接（Magnet URI）自动识别种子中最大的视频文件，选择性下载其每 5% 位置的数据块，并生成对应的预览缩略图。

**核心技术挑战**：libtorrent 的 piece 级选择性下载产生的是磁盘上的稀疏文件，FFmpeg 无法直接对其进行有效的 seek 和解码。本方案通过 `read_piece()` API 提取原始字节、拼接为可播放的独立视频片段来解决此问题。

## 2. 技术栈

| 层级 | 选型 | 说明 |
|------|------|------|
| 运行环境 | Ubuntu 22.04+ | 需安装 `libtorrent-dev`, `ffmpeg` |
| Web 框架 | Flask + Gunicorn | Gunicorn 提供多 worker 进程模型 |
| P2P 核心 | python-libtorrent >= 2.0 | 解析种子、控制 Piece 优先级、`read_piece()` |
| 媒体处理 | FFmpeg 5.x+ | 从拼接的视频片段中截取缩略图 |
| 任务队列 | Redis + RQ (Redis Queue) | 后台异步任务、并发控制、任务持久化 |
| 存储 | 本地磁盘 + SQLite | SQLite 存储任务元数据，磁盘存储缩略图 |

## 3. 架构设计

```
                          ┌──────────────┐
    用户请求 ──────────▶  │  Flask API   │
                          └──────┬───────┘
                                 │ 提交任务
                          ┌──────▼───────┐
                          │  Redis Queue │  ← 任务持久化 + 并发上限
                          └──────┬───────┘
                                 │
                          ┌──────▼───────┐
                          │   Worker     │  ← 可水平扩展
                          └──────┬───────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
     ┌────────────────┐ ┌───────────────┐ ┌─────────────────┐
     │ Torrent Parser │ │ Smart         │ │ Snapshot         │
     │                │ │ Downloader    │ │ Generator        │
     │ 解析元数据     │ │ Piece级下载   │ │ 片段拼接+截图    │
     └────────────────┘ └───────────────┘ └─────────────────┘
```

## 4. 核心功能模块

### 4.1 种子解析器 (Torrent Parser)

- [ ] 解析 Magnet URI，通过 DHT 获取元数据（设定超时：60s）。
- [ ] 解析 `.torrent` 文件的元数据（作为可选输入方式）。
- [ ] 遍历文件列表，根据扩展名筛选视频文件（`.mp4`, `.mkv`, `.avi`, `.wmv`, `.mov`, `.flv`, `.ts`）。
- [ ] 识别并锁定体积最大的视频文件作为目标。
- [ ] 计算目标文件在种子中的全局 Piece 范围（起始 Piece 索引、结束 Piece 索引）。

### 4.2 智能下载控制器 (Smart Downloader)

#### 4.2.1 Piece 映射策略

- [ ] 获取种子的 `piece_length`（典型值 256KB~4MB）和总 Piece 数。
- [ ] 计算目标文件的字节范围 `[file_offset, file_offset + file_size)`。
- [ ] 将文件字节范围映射到全局 Piece 索引范围 `[first_piece, last_piece]`。
- [ ] 对每个采样点（5%, 10%, ..., 95%），计算其在文件内的字节偏移量，再映射为全局 Piece 索引。
- [ ] 注意 Piece 边界对齐：一个 Piece 可能跨越多个文件，需正确处理偏移计算。

#### 4.2.2 关键区域优先下载

- [ ] **文件头部区域**：无论视频格式如何，始终优先下载文件头部的前 N 个 Piece（覆盖至少 2MB），确保容器格式的头部信息可解析。
- [ ] **文件尾部区域**（针对 MP4）：检测 moov atom 位置。MP4 的 moov atom 可能在文件尾部（非 fast-start 编码），此时需额外下载文件最后 2~10MB 对应的 Piece。
- [ ] **采样点区域**：每个 5% 采样点对应的 Piece，加上前后各 2 个相邻 Piece（确保 FFmpeg 有足够上下文解码关键帧）。

#### 4.2.3 优先级设置

- [ ] 所有非目标 Piece 优先级设为 0（不下载）。
- [ ] 文件头部/尾部 Piece 优先级设为 7（最高，第一批下载）。
- [ ] 采样点 Piece 及相邻 Piece 优先级设为 6（第二批下载）。
- [ ] 使用 `handle.set_piece_deadline()` 进一步控制下载顺序。

#### 4.2.4 下载监控

- [ ] 轮询 `handle.have_piece()` 检查每个目标 Piece 的完成状态。
- [ ] 当某个采样点的所有相关 Piece 就绪后，立即触发该采样点的截图任务（无需等待所有采样点完成）。
- [ ] 设置全局超时（默认 10 分钟），超时后中止下载并返回已完成的截图。
- [ ] 记录下载速度和 peer 数量，用于前端展示。

### 4.3 视频片段提取器 (Segment Extractor) — 新增模块

解决核心技术难点：FFmpeg 无法读取磁盘上的稀疏文件。

- [ ] 当某个采样点的 Piece 下载完成后，使用 `handle.read_piece()` 异步读取原始字节。
- [ ] 通过 `read_piece_alert` 回调收集 Piece 数据。
- [ ] 将连续 Piece 的字节拼接，写入临时文件（如 `/tmp/task_xxx/segment_05.bin`）。
- [ ] 在拼接时正确裁剪首尾 Piece 中不属于目标文件的字节。
- [ ] 每个片段的大小约为 `piece_length * 5`（目标 Piece + 前后各 2 个），典型值 1~20MB。

### 4.4 缩略图生成器 (Snapshot Generator)

- [ ] 接收提取出的视频片段文件路径和目标时间戳。
- [ ] 对于片段内的相对时间偏移，计算 FFmpeg `-ss` 参数值。
- [ ] 调用 FFmpeg 截取单帧：
  ```bash
  ffmpeg -ss <offset> -i <segment_file> -vframes 1 -q:v 2 <output.jpg>
  ```
- [ ] 如果直接 seek 失败，回退策略：
  1. 尝试 `-ss` 放在 `-i` 之后（输入级 seek → 输出级 seek）。
  2. 尝试增大 `-analyzeduration` 和 `-probesize`。
  3. 尝试从片段起始位置逐帧解码到目标位置。
  4. 如果全部失败，取片段中第一个可解码帧作为缩略图，并标记为"近似"。
- [ ] 输出缩略图统一为 JPEG 格式，宽度 320px，保持原始宽高比。
- [ ] 截图完成后立即删除临时视频片段文件。

### 4.5 Web API 接口 (Flask Server)

#### 接口定义

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/task` | 提交磁力链接，返回 `task_id` |
| `GET` | `/api/task/<task_id>` | 查询任务状态、进度、已生成缩略图列表 |
| `GET` | `/api/task/<task_id>/snapshots` | 获取所有缩略图的 URL 列表 |
| `GET` | `/snapshots/<task_id>/<filename>` | 下载单张缩略图 |
| `DELETE` | `/api/task/<task_id>` | 取消任务并清理资源 |

#### POST /api/task 请求/响应

```json
// 请求
{
  "magnet": "magnet:?xt=urn:btih:...",
  "sample_points": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95],
  "timeout": 600
}

// 响应
{
  "task_id": "a1b2c3d4",
  "status": "queued",
  "message": "Task created"
}
```

#### GET /api/task/<task_id> 响应

```json
{
  "task_id": "a1b2c3d4",
  "status": "downloading",       // queued | downloading | generating | completed | failed | timeout
  "progress": {
    "metadata_resolved": true,
    "video_file": "Movie.2024.mp4",
    "video_size_bytes": 4294967296,
    "pieces_needed": 95,
    "pieces_downloaded": 42,
    "download_speed_bps": 1048576,
    "peers_connected": 12
  },
  "snapshots": [
    {"percent": 5,  "url": "/snapshots/a1b2c3d4/snap_05.jpg", "status": "ready"},
    {"percent": 10, "url": "/snapshots/a1b2c3d4/snap_10.jpg", "status": "ready"},
    {"percent": 15, "url": null, "status": "pending"}
  ],
  "created_at": "2026-06-26T10:00:00Z",
  "error": null
}
```

## 5. 任务生命周期与状态机

```
  提交请求
     │
     ▼
  [queued] ──超出并发上限──▶ 排队等待
     │
     ▼ Worker 取出
  [resolving_metadata] ──超时/失败──▶ [failed]
     │
     ▼ 元数据解析成功
  [downloading] ──全局超时──▶ [timeout]（返回已有截图）
     │
     ▼ 目标 Piece 下载完成
  [generating] ──截图失败──▶ [failed]
     │
     ▼ 所有截图生成完毕
  [completed]
     │
     ▼ TTL 过期
  [cleaned]（缩略图被清理）
```

## 6. 并发控制与资源管理

### 6.1 任务队列

- [ ] 使用 Redis Queue (RQ) 管理任务，最大并发 worker 数 = 3（可配置）。
- [ ] 超出并发上限的任务排队等待，队列最大长度 = 20。
- [ ] 队列满时拒绝新任务，返回 HTTP 503。

### 6.2 资源限额

| 资源 | 限制 | 说明 |
|------|------|------|
| 单任务磁盘占用 | 500MB | 超出则中止下载 |
| 临时片段文件 | 用后即删 | 截图完成后立即清理 |
| 缩略图保留时间 | 24 小时 | 过期后由清理任务删除 |
| 全局磁盘上限 | 10GB | 所有任务共享 |
| 单任务超时 | 10 分钟 | 超时返回已有结果 |
| libtorrent session | 全局复用 | 避免每个任务创建独立 session |

### 6.3 清理机制

- [ ] 任务完成/失败后立即清理：临时视频片段、libtorrent 下载数据。
- [ ] 定时清理任务（每小时运行）：删除超过 24 小时的缩略图、清理孤立临时文件。
- [ ] 磁盘水位告警：当全局占用超过 80% 上限时，按 LRU 清理最旧任务的缩略图。

## 7. 安全设计

### 7.1 输入校验

- [ ] 校验 Magnet URI 格式（必须包含 `xt=urn:btih:` 且 info_hash 为 40 位十六进制或 32 位 Base32）。
- [ ] 限制 `sample_points` 参数范围（1~99）和数量（最多 19 个）。
- [ ] 限制 `timeout` 参数范围（60~600 秒）。

### 7.2 速率限制

- [ ] 每个 IP 每分钟最多 5 次 `POST /api/task` 请求。
- [ ] 全局每分钟最多 30 次任务创建请求。
- [ ] 使用 Flask-Limiter + Redis 后端实现。

### 7.3 内容安全

- [ ] 仅生成缩略图，不提供原始视频文件的下载或流媒体播放。
- [ ] 下载完成后立即删除原始视频数据，只保留缩略图。
- [ ] 日志中不记录完整磁力链接，仅记录 info_hash 的前 8 位。

### 7.4 进程隔离

- [ ] FFmpeg 以受限用户运行，设置 CPU 和内存的 cgroup 限制。
- [ ] FFmpeg 执行超时 30 秒，超时则 kill。

## 8. 日志与监控

### 8.1 结构化日志

- [ ] 使用 `structlog` 输出 JSON 格式日志。
- [ ] 关键事件记录：
  - 任务创建、状态变更
  - 元数据解析结果（文件列表、目标文件、piece 信息）
  - Piece 下载完成事件
  - FFmpeg 执行结果（成功/失败/耗时）
  - 资源清理事件

### 8.2 指标采集

- [ ] 暴露 `/metrics` 端点（Prometheus 格式），采集：
  - 活跃任务数、排队任务数
  - 任务成功率、平均耗时
  - 当前磁盘占用
  - libtorrent peer 连接数、下载速度

## 9. 开发步骤

### 第一阶段：POC 验证（关键路径）— 预计 3 天

**目标**：验证"Piece 级选择性下载 → 片段提取 → FFmpeg 截图"端到端可行性。

1. [ ] 搭建开发环境，安装 `python3-libtorrent`, `ffmpeg`, `redis`。
2. [ ] 编写 `poc_download.py`：
   - 用一个真实磁力链接启动 libtorrent session。
   - 解析元数据，找到最大视频文件。
   - 仅下载文件 50% 位置对应的 Piece（及前后各 2 个）。
   - 使用 `read_piece()` 提取字节并写入临时文件。
3. [ ] 编写 `poc_snapshot.py`：
   - 用 FFmpeg 对提取出的片段截图。
   - 测试不同视频格式（MP4/MKV/AVI）的兼容性。
   - 记录各格式的成功率和失败原因。
4. [ ] **决策点**：根据 POC 结果决定：
   - 如果 `read_piece()` 拼接方案可行 → 继续第二阶段。
   - 如果不可行 → 评估替代方案（如基于 FUSE 的虚拟文件系统、或通过 libtorrent 的 streaming 模式）。

### 第二阶段：核心逻辑实现 — 预计 5 天

1. [ ] 实现 `torrent_parser.py`：
   - Magnet URI 解析与元数据获取（带超时）。
   - 视频文件识别与 Piece 范围计算。
2. [ ] 实现 `smart_downloader.py`：
   - Piece 优先级设置（头部 > 尾部 > 采样点）。
   - 下载状态监控循环。
   - 全局 libtorrent session 管理（单例模式）。
3. [ ] 实现 `segment_extractor.py`：
   - `read_piece()` 调用与 alert 回调处理。
   - Piece 字节拼接与首尾裁剪。
   - 临时文件管理。
4. [ ] 实现 `snapshot_generator.py`：
   - FFmpeg 调用封装（含超时、重试、回退策略）。
   - 缩略图输出标准化。
5. [ ] 实现 MP4 moov atom 检测逻辑：
   - 解析头部判断 moov 位置。
   - 若 moov 在尾部，触发尾部 Piece 优先下载。

### 第三阶段：API 封装与集成 — 预计 4 天

1. [ ] 搭建 Flask 应用骨架。
2. [ ] 集成 Redis Queue：任务提交、状态查询、并发控制。
3. [ ] 实现 SQLite 任务元数据存储。
4. [ ] 实现所有 API 端点（含输入校验、错误处理）。
5. [ ] 集成 Flask-Limiter 速率限制。
6. [ ] 实现任务状态推送（可选：SSE 或 WebSocket）。

### 第四阶段：健壮性与运维 — 预计 3 天

1. [ ] 实现资源清理机制（定时任务 + 磁盘水位监控）。
2. [ ] 实现结构化日志（structlog）。
3. [ ] 实现 Prometheus metrics 端点。
4. [ ] 编写 `Dockerfile` 和 `docker-compose.yml`（含 Redis）。
5. [ ] 编写 `requirements.txt` / `pyproject.toml`。
6. [ ] 编写启动脚本和配置文件模板。
7. [ ] 实现优雅关停（graceful shutdown）：收到 SIGTERM 时等待当前任务完成。

### 第五阶段：测试 — 贯穿各阶段

1. [ ] 单元测试：
   - Piece 索引计算逻辑。
   - Magnet URI 校验。
   - 文件筛选算法。
   - 字节裁剪逻辑。
2. [ ] 集成测试：
   - 使用本地 tracker + seeder 搭建测试环境。
   - 端到端测试：提交磁力链接 → 生成缩略图。
   - 不同视频格式的兼容性测试。
3. [ ] 压力测试：
   - 并发 10 个任务的资源占用。
   - 磁盘清理机制在高负载下的表现。

## 10. 潜在风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| MP4 moov atom 在文件尾部，无法 seek | 截图全部失败 | 解析头部检测 moov 位置，必要时优先下载尾部 2~10MB |
| MKV 等格式缺少 seekable 索引 | 部分采样点截图失败 | 回退到逐帧解码；接受部分采样点失败 |
| Peer 数量不足或拒绝非连续请求 | 下载极慢或超时 | 设置全局超时；下载采样点前后相邻 Piece 满足协议校验 |
| `read_piece()` 返回数据不完整 | 拼接的片段无法解码 | 校验返回字节长度是否与 piece_length 一致；失败时重试 |
| 恶意用户提交大量请求 | 资源耗尽 | IP 速率限制 + 任务队列上限 + 磁盘配额 |
| 磁力链接无法解析元数据 | 任务无法启动 | 60 秒超时后返回失败，附带错误信息 |
| FFmpeg 处理畸形视频挂起 | Worker 阻塞 | FFmpeg 进程 30 秒超时强杀 |
| 服务重启导致任务丢失 | 用户体验差 | Redis 持久化任务状态；重启后可恢复排队任务 |

## 11. 项目结构

```
video_preview/
├── app.py                      # Flask 入口
├── config.py                   # 配置管理（环境变量 + 默认值）
├── worker.py                   # RQ Worker 启动入口
│
├── core/
│   ├── torrent_parser.py       # 种子解析与视频文件识别
│   ├── smart_downloader.py     # Piece 级选择性下载
│   ├── segment_extractor.py    # Piece 字节提取与片段拼接
│   ├── snapshot_generator.py   # FFmpeg 缩略图生成
│   └── session_manager.py      # libtorrent 全局 session 管理
│
├── api/
│   ├── routes.py               # API 路由定义
│   ├── schemas.py              # 请求/响应数据校验
│   └── errors.py               # 统一错误处理
│
├── storage/
│   ├── task_store.py           # SQLite 任务元数据 CRUD
│   └── cleanup.py              # 资源清理定时任务
│
├── tests/
│   ├── test_torrent_parser.py
│   ├── test_piece_mapping.py
│   ├── test_segment_extractor.py
│   ├── test_snapshot_generator.py
│   ├── test_api.py
│   └── conftest.py             # 测试 fixtures
│
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

## 12. 配置项

```python
# config.py 关键配置项
LIBTORRENT_SETTINGS = {
    "listen_port_range": (6881, 6891),
    "download_rate_limit": 5 * 1024 * 1024,    # 5MB/s per task
    "connections_limit": 100,
    "metadata_timeout": 60,                     # 元数据获取超时(秒)
}

TASK_SETTINGS = {
    "max_concurrent_tasks": 3,
    "max_queue_size": 20,
    "task_timeout": 600,                        # 单任务超时(秒)
    "max_disk_per_task_mb": 500,
    "global_disk_limit_gb": 10,
}

SNAPSHOT_SETTINGS = {
    "output_width": 320,                        # 缩略图宽度(px)
    "output_format": "jpeg",
    "jpeg_quality": 85,
    "ffmpeg_timeout": 30,                       # FFmpeg 超时(秒)
    "adjacent_pieces": 2,                       # 采样点前后额外下载的 Piece 数
    "head_bytes": 2 * 1024 * 1024,              # 头部优先下载量(2MB)
    "tail_bytes": 10 * 1024 * 1024,             # 尾部优先下载量(10MB, 仅 MP4)
}

CLEANUP_SETTINGS = {
    "snapshot_ttl_hours": 24,
    "cleanup_interval_minutes": 60,
    "disk_high_watermark_percent": 80,
}

RATE_LIMIT = {
    "per_ip_per_minute": 5,
    "global_per_minute": 30,
}
```

## 13. 交付物

- `app.py`: Flask 主程序入口
- `worker.py`: RQ Worker 入口
- `core/`: 核心业务逻辑（4 个模块 + session 管理）
- `api/`: API 层（路由、校验、错误处理）
- `storage/`: 持久化与清理
- `tests/`: 单元测试与集成测试
- `docker-compose.yml`: 一键启动（Flask + Redis + Worker）
- `requirements.txt`: Python 依赖清单
- `README.md`: 部署与使用说明
