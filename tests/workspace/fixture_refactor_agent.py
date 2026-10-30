"""Source-only P06 fixture. Injection is absent from the production entry/spec."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '.validation/venv-workspace/Lib/site-packages'))
from app.workspace import dispatcher  # noqa: E402
from app.workspace.__main__ import main  # noqa: E402
from app.workspace.apply import Application  # noqa: E402
from app.workspace.review import Reviews  # noqa: E402
from app.workspace.review_transport import Reply  # noqa: E402
from app.workspace.snapshots import Snapshots  # noqa: E402
from tests.workspace.test_apply import refactor_output  # noqa: E402
from tests.workspace.test_review import output  # noqa: E402


def transport(profile: dict[str, Any], system: str, user: str, cancelled: threading.Event) -> Reply:
    result = refactor_output(user) if profile['schemaVersion'] == 'workspace-refactor-v1' else output(json.loads(user)['files'][0])
    return Reply(json.dumps(result).encode())


class FixtureReviews(Reviews):
    def __init__(self, snapshots: Snapshots):
        super().__init__(snapshots, transport)


class FixtureApplication(Application):
    def apply(self, project: str, payload: dict[str, Any], key: str,
              checkpoint: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
        interrupt = self.proposals.body(project, payload['proposalId'])['purpose'].startswith('interrupt fixture')
        last_phase = 'preflight'
        def point(phase: str) -> None:
            nonlocal last_phase
            last_phase = phase
            if interrupt and phase == 'original_moved_before_record':
                raise RuntimeError('fixture interruption')
            checkpoint(phase)
        try:
            return super().apply(project, payload, key, point)
        except Exception as error:
            reason = error.__context__
            # Test-only classification, never source/payload/capability contents.
            sys.stderr.write('fixture-apply-failure:' + last_phase + ':' + type(reason).__name__ + ':' + str(getattr(reason, 'code', 'CLASSIFICATION_ONLY')) + '\n')
            database = Path(self.db.execute('PRAGMA database_list').fetchone()[2])
            (database.parent / 'p06-fixture-apply-error.json').write_text(json.dumps({'phase': last_phase,
                'type': type(reason).__name__, 'code': str(getattr(reason, 'code', 'CLASSIFICATION_ONLY'))}), encoding='utf-8')
            raise


if __name__ == '__main__':
    dispatcher.Reviews = FixtureReviews
    dispatcher.Application = FixtureApplication
    raise SystemExit(main())
