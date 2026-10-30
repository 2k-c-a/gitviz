# PyInstaller entry uses an absolute import shim generated only in the build area.
from pathlib import Path
root = Path(SPECPATH).parents[1]
shim = root / "build-output/agent/work/entry.py"
shim.parent.mkdir(parents=True, exist_ok=True)
shim.write_text("from app.workspace.__main__ import main\nraise SystemExit(main())\n", encoding="utf-8")
a = Analysis([str(shim)], pathex=[str(root)],
             datas=[(str(root / "contracts/workspace/v1"), "contracts/workspace/v1"),
                    *[(str(path), "app/services/ai/prompts") for path in (root / "app/services/ai/prompts").glob("*.txt")]],
             hiddenimports=["app.services.ai.content_policy", "app.services.ai.input_builder", "app.services.ai.difftext",
                            "app.services.ai.validators", "app.services.ai.prompts", "app.services.ai.providers.registry"],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=["app.main", "app.database", "app.core", "app.api", "app.models",
                       "app.collectors.git.clone_manager", "app.collectors.git.ai_diff_collector", "app.services.repository_access", "app.services.ai.access",
                       "app.services.ai.providers.json_provider", "app.services.ai.providers.anthropic_provider",
                       "celery", "sqlalchemy", "fastapi", "dotenv", "git", "requests", "httpx", "httpx2"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="gitviz-workspace-agent", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="agent")
