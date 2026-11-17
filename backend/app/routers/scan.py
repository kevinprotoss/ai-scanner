from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..services import scan_service

router = APIRouter()


class CreateScanRequest(BaseModel):
    target_type: str = Field(..., pattern="^(receipt|expense)$")
    image_paths: list[str] = Field(..., min_length=1)


@router.post("/scans", status_code=201)
async def create(body: CreateScanRequest) -> dict:
    try:
        return scan_service.create_scan(body.target_type, body.image_paths)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/scans/{scan_id}/process", status_code=202)
async def process(scan_id: str, background_tasks: BackgroundTasks) -> dict:
    doc = scan_service.read_scan(scan_id)
    if doc is None:
        raise HTTPException(404, "scan not found")
    if doc["status"] in ("processing", "ready"):
        return {"status": doc["status"]}
    background_tasks.add_task(scan_service.run_pipeline, scan_id)
    return {"status": "accepted"}


@router.get("/scans")
def list_scans(status: str | None = None) -> list[dict]:
    return scan_service.list_scans(status)


@router.get("/scans/{scan_id}")
def get_scan(scan_id: str) -> dict:
    doc = scan_service.read_scan(scan_id)
    if doc is None:
        raise HTTPException(404, "scan not found")
    return doc


@router.get("/scans/{scan_id}/result")
def get_result(scan_id: str) -> dict:
    if not scan_service.scan_exists(scan_id):
        raise HTTPException(404, "scan not found")
    doc = scan_service.read_result(scan_id)
    if doc is None:
        raise HTTPException(404, "result not ready")
    return doc


@router.get("/scans/{scan_id}/ocr")
def get_ocr(scan_id: str) -> dict:
    if not scan_service.scan_exists(scan_id):
        raise HTTPException(404, "scan not found")
    doc = scan_service.read_combined_ocr(scan_id)
    if doc is None:
        raise HTTPException(404, "OCR not ready")
    return doc


@router.delete("/scans/{scan_id}")
def delete(scan_id: str) -> dict:
    if not scan_service.delete_scan(scan_id):
        raise HTTPException(404, "scan not found")
    return {"status": "deleted", "scan_id": scan_id}