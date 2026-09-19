"""Convenience launcher:  python run.py

`app/main.py` uses package-relative imports, so running it directly
(`python app/main.py`) fails with "attempted relative import with no known
parent package". This starts uvicorn correctly from any working directory.

Equivalent to:
    uvicorn app.main:app --reload --port 8000     (run from backend/)
"""

import os
import sys
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent

if __name__ == "__main__":
    # Make sure `app` is importable no matter where this is launched from.
    sys.path.insert(0, str(BACKEND_DIR))
    os.chdir(BACKEND_DIR)

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload="--reload" in sys.argv or "-r" in sys.argv,
    )
