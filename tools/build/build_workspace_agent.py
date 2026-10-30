"""Build the isolated onedir agent. Explicit invocation required; no installation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    # -I prevents project/site customizations from entering the build tool process.
    return subprocess.call([sys.executable, "-I", "-m", "PyInstaller", "--noconfirm",
                            "--distpath", str(root / "build-output/agent/dist"),
                            "--workpath", str(root / "build-output/agent/work"),
                            str(root / "packaging/agent/workspace_agent.spec")], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
