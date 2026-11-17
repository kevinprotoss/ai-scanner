import os
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = str(_PROJECT_ROOT / "data")

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


_DATA_DIR_FINAL = _env("DATA_ROOT", _DEFAULT_DATA_DIR) or _DEFAULT_DATA_DIR
_ATTACHMENTS_ROOT_FINAL = _env(
    "ATTACHMENTS_ROOT", os.path.join(_DATA_DIR_FINAL, "attachments")
) or os.path.join(_DATA_DIR_FINAL, "attachments")


@dataclass(frozen=True)
class Settings:
    host: str = _env("APP_HOST", "0.0.0.0")
    port: int = int(_env("APP_PORT", "8000"))

    data_dir: str = _DATA_DIR_FINAL
    attachments_root: str = _ATTACHMENTS_ROOT_FINAL
    max_images_per_invoice: int = int(_env("APP_MAX_IMAGES_PER_INVOICE", "20"))

    anthropic_api_key: str = _env("ANTHROPIC_API_KEY", "") or ""
    anthropic_model: str = _env("ANTHROPIC_MODEL", "claude-haiku-5-5") or "claude-haiku-5-5"
    anthropic_max_tokens: int = int(_env("ANTHROPIC_MAX_TOKENS", "16384"))


settings = Settings()