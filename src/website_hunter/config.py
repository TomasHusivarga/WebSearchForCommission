import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


APP_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = APP_ROOT.parent
DEFAULTS_PATH = APP_ROOT / "config" / "defaults.json"
load_dotenv(APP_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    app_root: Path
    data_dir: Path
    screenshot_dir: Path
    database_path: Path
    defaults: dict[str, Any]
    openai_api_key: str | None
    openai_model: str

    @classmethod
    def load(cls) -> "Settings":
        defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
        data_dir = Path(os.getenv("HUNTER_DATA_DIR", str(APP_ROOT / "data"))).resolve()
        return cls(
            app_root=APP_ROOT,
            data_dir=data_dir,
            screenshot_dir=data_dir / "screenshots",
            database_path=data_dir / "hunter.sqlite3",
            defaults=defaults,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("HUNTER_OPENAI_MODEL", defaults["openai_model"]),
        )


settings = Settings.load()
