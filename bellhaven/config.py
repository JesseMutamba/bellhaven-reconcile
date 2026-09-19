from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class Config:
    mode: str = "demo"
    db_path: Path = ROOT / "data/bellhaven.sqlite3"
    port: int = 8765
    parent_id: str = "bellhaven-parent"
    crm_url: str = ""
    crm_token: str = ""
    website_url: str = ""
    path_pattern: str = r"/(locations|communities|facilities)(/|\?|$)"
    max_pages: int = 100
    expected_min: int = 1
    allow_absence: bool = False

    @classmethod
    def from_env(cls):
        load_env()
        mode = os.getenv("APP_MODE", "demo")
        if mode not in {"demo", "live"}:
            raise ValueError("APP_MODE must be demo or live")
        path = Path(os.getenv("DB_PATH", "data/bellhaven.sqlite3"))
        config = cls(mode=mode, db_path=path if path.is_absolute() else ROOT / path,
                     port=int(os.getenv("PORT", "8765")),
                     parent_id=os.getenv("CRM_PARENT_ID") or ("bellhaven-parent" if mode == "demo" else ""),
                     crm_url=os.getenv("CRM_BASE_URL", "").rstrip("/"),
                     crm_token=os.getenv("CRM_API_TOKEN", ""),
                     website_url=os.getenv("WEBSITE_URL", ""),
                     path_pattern=os.getenv("SCRAPE_PATH_PATTERN", cls.path_pattern),
                     max_pages=int(os.getenv("SCRAPE_MAX_PAGES", "100")),
                     expected_min=int(os.getenv("SCRAPE_EXPECTED_MIN", "1")),
                     allow_absence=os.getenv("SCRAPE_ALLOW_ABSENCE_REVIEW") == "true")
        if mode == "live":
            if not all([config.parent_id, config.crm_url, config.crm_token, config.website_url]):
                raise ValueError("Live mode requires the website URL, CRM URL, token, and parent ID")
            if not config.crm_url.startswith("https://") or not config.website_url.startswith("https://"):
                raise ValueError("Live website and CRM URLs must use HTTPS")
        return config
