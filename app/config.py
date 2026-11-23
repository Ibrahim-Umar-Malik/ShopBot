"""Settings, all read from the environment (.env locally, host env vars in production). No secrets in code."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL = os.getenv("SHOPBOT_MODEL", "claude-haiku-5-5")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
DATA_DIR = Path(os.getenv("SHOPBOT_DATA_DIR", ROOT / "var"))
SEED_DIR = ROOT / "data"

APP_DB = DATA_DIR / "shop.db"            # sellers, products, (later) orders
CHECKPOINT_DB = DATA_DIR / "checkpoints.db"  # LangGraph short-term memory

MAX_MESSAGE_CHARS = 1000
