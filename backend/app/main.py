import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ocr.paddle_runner import is_loaded, warmup

from .config import settings
from .routers import scan as scan_router

logger = logging.getLogger("ocr-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_root = Path(settings.data_dir)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "scans").mkdir(parents=True, exist_ok=True)

    async def warmup_task() -> None:
        try:
            await asyncio.to_thread(warmup)
            logger.info("Paddle OCR engine warmed up")
        except Exception as e:
            logger.error("Paddle warmup failed: %s", e)

    task = asyncio.create_task(warmup_task())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="OCR Service",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.include_router(scan_router.router)


@app.get("/health")
def health() -> dict:
    loaded = is_loaded()
    return {
        "status": "ok" if loaded else "starting",
        "model_loaded": loaded,
    }