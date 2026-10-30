"""Test-only kill-point runner against an already prepared temporary schema5 DB."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '.validation/venv-workspace/Lib/site-packages'))
from app.workspace.apply import Application  # noqa: E402
from app.workspace.crypto import PayloadCipher  # noqa: E402
from app.workspace.projects import Projects  # noqa: E402
from app.workspace.proposals import Proposals  # noqa: E402
from app.workspace.review import Reviews  # noqa: E402
from app.workspace.snapshots import Snapshots  # noqa: E402
from app.workspace.store import LocalStore  # noqa: E402
from app.workspace.windows import ProcessLifetime  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ['data', 'project', 'proposal', 'phase', 'signal', 'operation-key']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    lifetime = ProcessLifetime(os.getppid())
    store = LocalStore(Path(args.data), lambda _: None)
    store.open()
    assert store.connection is not None
    reviews = Reviews(Snapshots(Projects(store.connection), PayloadCipher(b'P' * 32, 1)))
    proposals = Proposals(reviews)
    application = Application(proposals)
    current = proposals.get(args.project, args.proposal)
    approved = proposals.approve(args.project, {k: current[k] for k in ('proposalId', 'patchHash', 'preconditionsHash')} |
        {'expectedVersion': current['version'], 'capability': 'e' * 64}, args.operation_key)
    def checkpoint(phase: str) -> None:
        if phase == args.phase:
            Path(args.signal).write_text('ready', encoding='ascii')
            while True:
                time.sleep(.05)
    application.apply(args.project, {'proposalId': args.proposal, 'approvalId': approved['approvalId'], 'capability': 'e' * 64}, args.operation_key, checkpoint)
    _ = lifetime
    raise RuntimeError('fixture checkpoint not reached')


if __name__ == '__main__':
    main()
