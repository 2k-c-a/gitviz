"""Source-only P07 entry; production dispatcher, no injected execution policy."""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / '.validation/venv-workspace/Lib/site-packages'))
from app.workspace.__main__ import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
