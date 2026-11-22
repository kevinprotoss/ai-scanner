import asyncio
import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from ..config import settings
from . import llm_service
from .ocr_service import run_ocr

_id_lock = threading.Lock()

_SCAN_RE = re.compile(r"^SCN-(\d{8})-(\d{3,})$")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def scans_root() -> Path:
    return Path(settings.data_dir) / "scans"


def scan_dir(scan_id: str) -> Path:
    m = _SCAN_RE.match(scan_id)
    if not m:
        raise ValueError("invalid scan_id")
    year = m.group(1)[:4]
    return scans_root() / year / scan_id


def _next_scan_id() -> str:
    with _id_lock:
        day = datetime.now().strftime("%Y%m%d")
        year = day[:4]
        base = scans_root() / year
        base.mkdir(parents=True, exist_ok=True)
        pattern = re.compile(rf"^SCN-{day}-(\d+)$")
        max_n = 0
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            m = pattern.match(entry.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"SCN-{day}-{max_n + 1:03d}"


def _scan_json_path(scan_id: str) -> Path:
    return scan_dir(scan_id) / "scan.json"


def _write_scan_json(scan_id: str, doc: dict) -> None:
    doc["updated_at"] = _now_iso()
    _atomic_write_text(
        _scan_json_path(scan_id),
        json.dumps(doc, ensure_ascii=False, indent=2),
    )


def read_scan(scan_id: str) -> dict | None:
    p = _scan_json_path(scan_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def scan_exists(scan_id: str) -> bool:
    try:
        return scan_dir(scan_id).is_dir()
    except ValueError:
        return False


def create_scan(target_type: str, image_paths: list[str]) -> dict:
    if not image_paths:
        raise ValueError("image_paths is empty")
    if len(image_paths) > settings.max_images_per_invoice:
        raise ValueError(
            f"too many images: {len(image_paths)} > {settings.max_images_per_invoice}"
        )

    scan_id = _next_scan_id()
    d = scan_dir(scan_id)
    (d / "ocr").mkdir(parents=True, exist_ok=True)
    (d / "result").mkdir(parents=True, exist_ok=True)

    doc = {
        "scan_id": scan_id,
        "target_type": target_type,
        "status": "pending",
        "image_paths": image_paths,
        "error_message": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    _write_scan_json(scan_id, doc)
    return doc


def update_scan_status(scan_id: str, status: str, error: str | None = None) -> dict:
    doc = read_scan(scan_id)
    if doc is None:
        raise ValueError("scan not found")
    doc["status"] = status
    doc["error_message"] = error
    _write_scan_json(scan_id, doc)
    return doc


def _resolve_attachment_path(rel_path: str) -> Path:
    if not rel_path:
        raise ValueError("empty path")
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise ValueError(f"absolute path not allowed: {rel_path}")
    if ".." in Path(rel_path).parts:
        raise ValueError(f"path traversal not allowed: {rel_path}")
    root = Path(settings.attachments_root).resolve()
    full = (root / rel_path).resolve()
    if not full.is_relative_to(root):
        raise ValueError(f"path escapes attachments root: {rel_path}")
    if not full.is_file():
        raise FileNotFoundError(rel_path)
    return full


def _page_ocr_path(scan_id: str, page: int) -> Path:
    return scan_dir(scan_id) / "ocr" / f"page_{page:03d}_ocr.json"


def _combined_ocr_path(scan_id: str) -> Path:
    return scan_dir(scan_id) / "ocr" / "combined_ocr.json"


def ocr_ready(scan_id: str) -> bool:
    try:
        return _combined_ocr_path(scan_id).is_file()
    except ValueError:
        return False


def _build_combined(scan_id: str, pages: list[dict]) -> dict:
    page_docs = []
    for img in pages:
        p = _page_ocr_path(scan_id, img["page"])
        page_docs.append(json.loads(p.read_text("utf-8")))
    return {
        "schema_version": "1.0",
        "total_pages": len(page_docs),
        "pages": page_docs,
    }


async def process_ocr(scan_id: str) -> dict:
    """Run OCR on each image path. Idempotent: already-OCR'd pages are skipped."""
    doc = read_scan(scan_id)
    if doc is None:
        raise ValueError("scan not found")

    d = scan_dir(scan_id)
    (d / "ocr").mkdir(parents=True, exist_ok=True)
    image_paths = doc["image_paths"]

    pages = [{"page": idx, "image": rel} for idx, rel in enumerate(image_paths, start=1)]

    for img in pages:
        page_path = _page_ocr_path(scan_id, img["page"])
        if page_path.is_file():
            continue
        full_path = _resolve_attachment_path(img["image"])
        data = await run_ocr(full_path)
        page_doc = {
            "page": img["page"],
            "image": img["image"],
            "items": data["items"],
        }
        _atomic_write_text(
            page_path, json.dumps(page_doc, ensure_ascii=False, indent=2)
        )

    combined = _build_combined(scan_id, pages)
    _atomic_write_text(
        _combined_ocr_path(scan_id),
        json.dumps(combined, ensure_ascii=False, indent=2),
    )
    return combined


def result_path(scan_id: str) -> Path:
    return scan_dir(scan_id) / "result" / "invoice.json"


def raw_llm_path(scan_id: str) -> Path:
    return scan_dir(scan_id) / "result" / "claude_raw.txt"


def result_ready(scan_id: str) -> bool:
    try:
        return result_path(scan_id).is_file()
    except ValueError:
        return False


def read_result(scan_id: str) -> dict | None:
    p = result_path(scan_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def read_combined_ocr(scan_id: str) -> dict | None:
    if not ocr_ready(scan_id):
        return None
    try:
        return json.loads(_combined_ocr_path(scan_id).read_text("utf-8"))
    except Exception:
        return None


def read_raw_llm(scan_id: str) -> str | None:
    p = raw_llm_path(scan_id)
    if not p.is_file():
        return None
    try:
        return p.read_text("utf-8")
    except Exception:
        return None


async def process_analyze(scan_id: str) -> dict:
    """Run Claude. Idempotent: returns existing result if present."""
    existing = read_result(scan_id)
    if existing is not None:
        return existing

    if not ocr_ready(scan_id):
        raise ValueError("OCR not ready")

    combined = json.loads(_combined_ocr_path(scan_id).read_text("utf-8"))

    try:
        payload, raw_text, meta = await asyncio.to_thread(llm_service.analyze, combined)
    except llm_service.LlmError as e:
        if e.raw_text:
            try:
                _atomic_write_text(raw_llm_path(scan_id), e.raw_text)
            except Exception:
                pass
        raise

    d = scan_dir(scan_id) / "result"
    d.mkdir(parents=True, exist_ok=True)

    # raw first: even if the JSON write fails, the raw response survives
    _atomic_write_text(raw_llm_path(scan_id), raw_text)

    result_doc = {
        "schema_version": "1.0",
        "scan_id": scan_id,
        **payload.model_dump(),
        "validation": {"status": "PENDING", "issues": []},
        "meta": meta,
    }
    _atomic_write_text(
        result_path(scan_id),
        json.dumps(result_doc, ensure_ascii=False, indent=2),
    )
    return result_doc


def list_scans(status: str | None = None) -> list[dict]:
    root = scans_root()
    if not root.is_dir():
        return []
    out = []
    for year_dir in sorted(root.iterdir(), reverse=True):
        if not year_dir.is_dir():
            continue
        for scan_subdir in sorted(year_dir.iterdir(), reverse=True):
            if not scan_subdir.is_dir():
                continue
            scan_id = scan_subdir.name
            if not _SCAN_RE.match(scan_id):
                continue
            doc = read_scan(scan_id)
            if doc is None:
                continue
            if status and doc.get("status") != status:
                continue
            out.append(doc)
    return out


def delete_scan(scan_id: str) -> bool:
    if not scan_exists(scan_id):
        return False
    shutil.rmtree(scan_dir(scan_id))
    return True


async def run_pipeline(scan_id: str) -> None:
    """Full pipeline: OCR then analyze. Updates status along the way."""
    try:
        update_scan_status(scan_id, "processing")
        await process_ocr(scan_id)
        await process_analyze(scan_id)
        update_scan_status(scan_id, "ready")
    except Exception as e:
        update_scan_status(scan_id, "failed", f"{type(e).__name__}: {e}")