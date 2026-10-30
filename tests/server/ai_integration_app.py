"""통합 검증용 app 서버 실행기: 실제 app.main을 그대로 띄우되 GitHub 접근만 로컬 fixture로 바꾼다(운영 코드 변경 없음).

    python tests/server/ai_integration_app.py <port>
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

import ai_integration_patches as patches  # noqa: E402
from app.main import app  # noqa: E402

patches.apply_app()

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
