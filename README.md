# OCR Service

发票扫描识别微服务：接收图片路径，运行 PaddleOCR 和 Claude，返回结构化的发票 JSON。

这个服务是 [accounting-pwa](../accounting_pwa) 的配套组件，不对外暴露，只由 Node 主服务在 compose network 内部调用。

## 职责范围

**做什么**
- 对图片运行 PaddleOCR，提取文本和坐标
- 调用 Claude 将 OCR 结果解析成结构化发票 JSON
- 管理扫描任务的状态和结果文件

**不做什么**
- 不接收文件上传（图片由 Node 存到共享目录，Python 只读路径）
- 不维护业务数据（进货单、支出单等）
- 不做认证（compose network 内部隔离）
- 不托管前端

## 架构

```
┌─────────────────┐        HTTP        ┌─────────────────┐
│  Node 主服务    │ ─────────────────► │  OCR 微服务      │
│  (accounting)   │                    │  (this project) │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         │ 写入                                  │ 读写
         ▼                                      ▼
┌─────────────────────────────────────────────────────┐
│              共享数据目录 (宿主机挂载)                 │
│  data/attachments/   ← Node 写，Python 读             │
│  data/scans/         ← Python 读写                    │
└─────────────────────────────────────────────────────┘
```

- 图片由 Node 存入 `data/attachments/`，Python 以相对路径读取
- Python 将 OCR 和 LLM 结果存入 `data/scans/{SCN-xxx}/`
- Node 轮询 Python 的扫描状态，拿到 `ready` 的结果后消费并创建业务记录

## 接口

所有端点不带 `/api` 前缀，直接由 Node 调用。

### `GET /health`

```json
{ "status": "ok", "model_loaded": true }
```

- `starting`：Paddle 模型正在加载
- `ok`：就绪，可以接受扫描请求

### `POST /scans`

创建扫描任务。

```json
// Request
{
  "target_type": "receipt",
  "image_paths": ["attachments/202511/a.jpg", "attachments/202511/b.jpg"]
}
```

- `target_type`：`receipt`（进货单）或 `expense`（支出单）
- `image_paths`：相对 `ATTACHMENTS_ROOT` 的路径数组
- 最多 `APP_MAX_IMAGES_PER_INVOICE` 页

```json
// Response 201
{
  "scan_id": "SCN-20251115-001",
  "target_type": "receipt",
  "status": "pending",
  "image_paths": [...],
  "error_message": null,
  "created_at": "2025-11-15T14:30:22",
  "updated_at": "2025-11-15T14:30:22"
}
```

### `POST /scans/{scan_id}/process`

触发 OCR 和 LLM 处理。后台异步执行。

```json
// Response 202
{ "status": "accepted" }
```

幂等：
- 已 OCR 的页面跳过
- 已有 LLM 结果直接返回
- 重复调用不会重复消耗 Claude token

### `GET /scans?status={status}`

列出扫描任务。`status` 可选：`pending` / `processing` / `ready` / `failed`。

```json
[
  { "scan_id": "SCN-20251115-001", "status": "ready", ... },
  { "scan_id": "SCN-20251115-002", "status": "processing", ... }
]
```

### `GET /scans/{scan_id}`

查询单个扫描任务的状态。

### `GET /scans/{scan_id}/result`

获取 LLM 解析后的发票 JSON。

```json
{
  "schema_version": "1.0",
  "scan_id": "SCN-20251115-001",
  "document_type": "invoice",
  "supplier": {
    "name": "Example GmbH",
    "address": { "street": "...", "postal_code": "...", "city": "...", "country": "DE" },
    "vat_id": "DE123456789",
    "tax_number": null
  },
  "invoice": {
    "invoice_number": "INV-2025-042",
    "invoice_date": "2025-11-10",
    "currency": "EUR"
  },
  "amounts": {
    "net_amount": "100.00",
    "tax_amount": "19.00",
    "gross_amount": "119.00",
    "tax_breakdown": [{ "tax_rate": "19%", "net_amount": "100.00", "tax_amount": "19.00" }]
  },
  "line_items": [
    {
      "description": "Product A",
      "quantity": "2",
      "unit_price": "50.00",
      "net_amount": "100.00",
      "tax_rate": "19%",
      "source_refs": ["p1_t005"]
    }
  ],
  "field_relationships": [...],
  "uncertain_items": [],
  "validation": { "status": "PENDING", "issues": [] },
  "meta": {
    "model": "claude-haiku-5-5",
    "input_tokens": 1234,
    "output_tokens": 567,
    "processing_time_seconds": 2.3
  }
}
```

如果结果还没就绪，返回 404。

### `GET /scans/{scan_id}/ocr`

获取合并后的 OCR 原始数据（多页）。可用于调试。

### `DELETE /scans/{scan_id}`

删除扫描任务及其所有文件。Node 消费完结果后调用。

```json
{ "status": "deleted", "scan_id": "SCN-20251115-001" }
```

## 扫描状态

```
pending → processing → ready
                    ↘ failed
```

| 状态 | 说明 |
|---|---|
| `pending` | 刚创建，尚未处理 |
| `processing` | OCR 或 LLM 处理中 |
| `ready` | 结果可读取，Node 可消费 |
| `failed` | 处理失败，`error_message` 含原因 |

失败后可调 `POST /scans/{id}/process` 重试。已完成的步骤会跳过（幂等）。

## 环境变量

参考 `.env.example`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `8000` | 监听端口 |
| `DATA_ROOT` | `/data` | 数据根目录 |
| `ATTACHMENTS_ROOT` | `/data/attachments` | 附件只读目录 |
| `APP_MAX_IMAGES_PER_INVOICE` | `20` | 单次扫描最多页数 |
| `ANTHROPIC_API_KEY` | — | 必填 |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Claude 模型名 |
| `ANTHROPIC_MAX_TOKENS` | `16384` | 单次输出上限 |
| `OCR_LANG` | `german` | PaddleOCR 语言 |
| `OCR_USE_GPU` | `0` | `1` 启用 GPU |

## 开发环境

Node 在宿主机运行，Python 服务在容器内运行，共享宿主机数据目录。

### 1. 准备 `.env`

在项目根目录创建 `.env`：

```bash
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxx
ANTHROPIC_MODEL=claude-haiku-5-5
OCR_LANG=german
```

`.env` 已在 `.gitignore` 中，不会被提交。

### 2. 构建镜像

```powershell
docker build -t ocr-service:dev .
```

首次构建约 5–10 分钟，主要是 `paddlepaddle` 和 `paddleocr` 的依赖下载。镜像最终大小约 2–2.5 GB。

### 3. 启动容器

```powershell
$DataDir = "C:\path\to\accounting_pwa\server\data"

docker run -d --name ocr-service `
  -p 8000:8000 `
  -v "${PWD}:/app" `
  -v "${DataDir}:/data" `
  -v "ocr-paddle-cache:/root/.paddleocr" `
  --env-file .env `
  ocr-service:dev
```

- `-v "${PWD}:/app"`：挂载源码，改代码后 `docker restart ocr-service` 即生效，不用重建镜像
- `-v "${DataDir}:/data"`：共享主项目的数据目录
- `-v "ocr-paddle-cache:/root/.paddleocr"`：Paddle 模型缓存，容器删除重建不重新下载模型

### 4. 等待就绪

```powershell
docker logs -f ocr-service
```

首次启动下载模型 + 加载约 1–3 分钟。就绪后：

```powershell
curl http://localhost:8000/health
# → {"status":"ok","model_loaded":true}
```

### 5. 修改代码后的重启

```powershell
docker restart ocr-service
```

源码挂载，重启即加载新代码，10 秒内恢复。

### 修改依赖或 Dockerfile 后

```powershell
docker rm -f ocr-service
docker build -t ocr-service:dev .
# 重新 docker run
```

## 生产部署

`docker-compose.yml` 示例：

```yaml
services:
  app:
    build: ./server
    volumes:
      - ./data:/app/data
    environment:
      DATA_ROOT: /app/data
      OCR_URL: http://ocr:8000
    depends_on:
      ocr:
        condition: service_healthy
    ports:
      - "3000:3000"

  ocr:
    image: ocr-service:prod
    volumes:
      - ./data:/data
    environment:
      DATA_ROOT: /data
      ATTACHMENTS_ROOT: /data/attachments
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      ANTHROPIC_MODEL: ${ANTHROPIC_MODEL}
      OCR_LANG: german
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      retries: 6
      start_period: 120s
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2g
    # 不映射端口到宿主机，只在 compose network 内可达
```

`ocr` 服务不暴露端口，只在 compose 内部通过 `http://ocr:8000` 访问。

## 数据目录结构

```
data/
├── attachments/                # Node 写，Python 只读
│   └── 202511/
│       ├── 1731500000000_abc_IMG_1234.jpg
│       └── ...
└── scans/                      # Python 读写
    └── 2025/
        └── SCN-20251115-001/
            ├── scan.json           # { scan_id, status, image_paths, ... }
            ├── ocr/
            │   ├── page_001_ocr.json
            │   ├── page_002_ocr.json
            │   └── combined_ocr.json
            └── result/
                ├── invoice.json    # LLM 解析结果
                └── claude_raw.txt  # Claude 原始输出
```

扫描任务消费完成（Node 创建了业务记录）后，Node 调 `DELETE /scans/{id}` 清理 `scans/` 目录。`attachments/` 里的图片保留，作为业务记录的附件。

## 项目结构

```
invoice-scanner/
├── Dockerfile
├── .dockerignore
├── .gitignore
├── .env.example
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── run.py
│   └── app/
│       ├── config.py              # 环境变量解析
│       ├── main.py                # FastAPI 应用、lifespan、/health
│       ├── models/
│       │   └── invoice.py         # ClaudeInvoicePayload
│       ├── routers/
│       │   └── scan.py            # /scans 端点
│       └── services/
│           ├── ocr_service.py     # 异步包装 ocr_image
│           ├── llm_service.py     # Claude 调用和校验
│           ├── prompt.py          # 系统提示词
│           └── scan_service.py    # 扫描任务管理、OCR、LLM、状态
└── ocr/
    └── paddle_runner.py           # PaddleOCR 单例封装
```

`ocr/` 与 `backend/` 在容器内 `/app/` 下同级，`ocr_service.py` 里的 `sys.path` 逻辑依赖这个层级。

## 关键设计

**幂等**
- `process_ocr` 跳过已 OCR 的页面
- `process_analyze` 有结果直接返回
- Node 重试不会重复消耗 Claude token

**原子写入**
- 所有 JSON 和文本文件用临时文件 + rename 写入
- 中断不会留下半写文件

**无认证**
- 只在 compose network 内部可达，外部访问不到
- 生产部署时 `ocr` 容器不映射端口

**只读附件**
- `ATTACHMENTS_ROOT` 下的文件 Python 只读不写
- 扫描结果写入独立的 `scans/` 目录，与附件隔离

**路径安全**
- Node 传相对路径
- Python 用 `DATA_ROOT` 拼绝对路径，校验不逃逸出 `ATTACHMENTS_ROOT`

## 与 Node 服务的集成

完整的调用流程：

```
1. Node 收到用户上传的账单图片
2. Node 存图到 data/attachments/202511/xxx.jpg
3. Node 调 POST /scans { target_type, image_paths }
4. Node 调 POST /scans/{id}/process
5. Node 轮询 GET /scans?status=ready
6. 拿到 ready 的 scan，调 GET /scans/{id}/result
7. Node 将发票 JSON 映射成进货单/支出单，调自己的 POST /api/receipts 或 /api/expenses
8. Node 在 scan_links 表记录 scan_id 与业务记录的映射
9. Node 调 DELETE /scans/{id} 清理扫描文件
```

失败处理：
- `status = failed` 时，Node 在列表页显示「识别失败」卡片
- 用户点「重试」，Node 调 `POST /scans/{id}/process` 重新触发
- 用户可以放弃，Node 调 `DELETE /scans/{id}`

## 许可

MIT License, 详见 [LICENSE](./LICENSE)