from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .paths import CACHE_ROOT, ensure_layout
from .server import HOST, PORT, run_server


APP_URL = f"http://{HOST}:{PORT}/"
EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def server_is_running() -> bool:
    try:
        with urllib.request.urlopen(f"{APP_URL}api/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def open_window() -> None:
    edge = next((path for path in EDGE_CANDIDATES if path.is_file()), None)
    if edge:
        profile = CACHE_ROOT / "desktop-shell"
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [
                str(edge),
                f"--app={APP_URL}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--disable-features=msEdgeSidebarV2",
                "--start-maximized",
            ],
            close_fds=True,
        )
    else:
        import webbrowser

        webbrowser.open(APP_URL)


def main() -> None:
    ensure_layout()
    if server_is_running():
        open_window()
        return

    import threading

    thread = threading.Thread(target=run_server, name="instant-ai-server", daemon=True)
    thread.start()
    for _ in range(50):
        if server_is_running():
            open_window()
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("即时 AI 本地服务启动失败")
    thread.join()


if __name__ == "__main__":
    main()
