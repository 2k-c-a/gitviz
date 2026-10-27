"""Test-only source agent with an injected provider; absent from production entry/spec."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '.validation/venv-workspace/Lib/site-packages'))

from app.workspace import dispatcher  # noqa: E402
from app.workspace.__main__ import main  # noqa: E402
from app.workspace.review import Reviews  # noqa: E402
from app.workspace.review_transport import Reply  # noqa: E402
from app.workspace.snapshots import Snapshots  # noqa: E402
from tests.workspace.test_review import output  # noqa: E402


def fixture_transport(profile: dict[str, Any], system: str, user: str, cancelled: threading.Event) -> Reply:
    time.sleep(.15)
    return Reply(json.dumps(output(json.loads(user)['files'][0])).encode(), {'inputTokens': 100, 'outputTokens': 50})


class FixtureReviews(Reviews):
    def __init__(self, snapshots: Snapshots):
        super().__init__(snapshots, fixture_transport)


if __name__ == '__main__':
    dispatcher.Reviews = FixtureReviews
    raise SystemExit(main())
