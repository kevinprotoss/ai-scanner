FROM python:3.11.0

# 基础环境变量
# - PYTHONPATH=/app：让 ocr_service.py 里 sys.path 的逻辑和容器路径对齐
# - DATA_ROOT / ATTACHMENTS_ROOT：默认值，运行时可用 -e 覆盖
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DATA_ROOT=/data \
    ATTACHMENTS_ROOT=/data/attachments

# 系统依赖
# - libgl1 / libglib2.0-0 / libgomp1：Paddle 运行时
# - libsm6 / libxext6 / libxrender1：OpenCV 内部依赖（PaddleOCR 用到）
# - curl：HEALTHCHECK 用
RUN sed -i 's|deb.debian.org|archive.debian.org|g; s|security.debian.org|archive.debian.org|g' /etc/apt/sources.list \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \dock
    libglib2.0-0 \
    libgomp1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用层缓存：代码改了但 requirements 没变时，这层不重建
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 复制代码
# 保持 ocr/ 和 backend/ 同级，ocr_service.py 里 parents[3] 才能找到 /app
COPY backend/ /app/backend/
COPY ocr/ /app/ocr/

WORKDIR /app/backend

EXPOSE 8000

# 健康检查
# - start-period=120s：给 Paddle 首次下载和加载模型留足时间
# - /health 端点由改造后的 main.py 提供，未实现前 healthcheck 会一直失败，属正常
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=6 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]