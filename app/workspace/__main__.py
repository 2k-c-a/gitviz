"""Dedicated stdio entry point: never loads .env or app.main."""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from typing import Any, cast

from .contracts import ProtocolError, encode, read_frame
from .dispatcher import Dispatcher
from .windows import ProcessLifetime, secure_path


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    args = parser.parse_args()
    timer = threading.Timer(15, lambda: os._exit(1))
    timer.daemon = True
    timer.start()
    dispatcher: Dispatcher | None = None
    lifetime: ProcessLifetime | None = None
    try:
        lifetime = ProcessLifetime(args.parent_pid)
        dispatcher = Dispatcher(Path(args.data_dir), secure_path)
        while not dispatcher.stopping:
            message = read_frame(sys.stdin.buffer)
            if message is None:
                break
            if isinstance(message, dict) and cast(dict[str, Any], message).get("type") == "bootstrap":
                dispatcher.bootstrap(message)
                if dispatcher.state == "ready":
                    timer.cancel()
                continue
            response = dispatcher.dispatch(message)
            sys.stdout.buffer.write(encode(response))
            sys.stdout.buffer.flush()
        return 0
    except (ProtocolError, OSError, ValueError):
        sys.stderr.write("workspace_agent_unavailable\n")
        return 1
    finally:
        timer.cancel()
        if dispatcher:
            dispatcher.close()
        # OS exit closes the job handle after buffers/SQLite are closed.
        # Do not close a kill-on-close job while flushing the acknowledgement.
        _ = lifetime


if __name__ == "__main__":
    raise SystemExit(main())
