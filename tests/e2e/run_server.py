import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
data_dir = Path(tempfile.gettempdir()) / "index-inbox-playwright-data"
shutil.rmtree(data_dir, ignore_errors=True)
os.environ.update({
    "AUTH_PROVIDER": "local",
    "AUTH_COOKIE_SECURE": "false",
    "AUTH_ALLOWED_ORIGINS": "http://127.0.0.1:5055",
    "LOCAL_SETUP_TOKEN": "playwright-setup-token",
    "DATA_DIR": str(data_dir),
    "WEBHOOK_SECRET": "playwright-webhook-secret",
})

from app import app

app.run(host="127.0.0.1", port=5055, threaded=True)
