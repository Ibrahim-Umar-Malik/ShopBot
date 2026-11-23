import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir for every test run, set before the app is imported.
os.environ["SHOPBOT_DATA_DIR"] = tempfile.mkdtemp(prefix="shopbot-test-")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
