"""Serves the generated page, and rebuilds it on a schedule so it tracks the live directory.

The page is a render of the same pipeline the command line runs; nothing here decides anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
REBUILD_SECONDS = 900


def rebuild() -> None:
    while True:
        try:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "build_site.py")], cwd=ROOT, timeout=120)
        except Exception as err:  # a failed rebuild leaves the last good page in place
            print(f"rebuild failed, keeping the previous page: {err}", flush=True)
        time.sleep(REBUILD_SECONDS)


def main() -> None:
    SITE.mkdir(exist_ok=True)
    threading.Thread(target=rebuild, daemon=True).start()
    port = int(os.environ.get("PORT", "4400"))
    handler = partial(SimpleHTTPRequestHandler, directory=str(SITE))
    print(f"serving {SITE} on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


if __name__ == "__main__":
    main()
