from __future__ import annotations

import ctypes
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .paths import CACHE_ROOT, ensure_layout
from .server import HOST, PORT, run_server


APP_URL = f"http://{HOST}:{PORT}/"
APP_WINDOW_TITLE = "即时 AI · 全球财经情报"
MOBILE_PREVIEW_URL = f"{APP_URL}?mobile-preview=1"
MOBILE_PREVIEW_WINDOW_TITLE = "即时 AI · 手机预览"
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


def client_window_bounds() -> tuple[int, int, int, int]:
    """Return a centered desktop-client size inside the Windows work area."""

    if sys.platform != "win32":
        return 1180, 760, 80, 60

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    work_area = RECT()
    if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
        return 1180, 760, 80, 60
    available_width = max(800, work_area.right - work_area.left)
    available_height = max(600, work_area.bottom - work_area.top)
    width = min(1240, max(980, available_width - 180), available_width - 40)
    height = min(820, max(620, available_height - 100), available_height - 40)
    left = work_area.left + max(0, (available_width - width) // 2)
    top = work_area.top + max(0, (available_height - height) // 2)
    return width, height, left, top


def mobile_preview_window_bounds() -> tuple[int, int, int, int]:
    """Return a phone-like preview size that remains visible on the desktop."""

    if sys.platform != "win32":
        return 430, 900, 80, 50

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    work_area = RECT()
    if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
        return 430, 900, 80, 50
    available_width = max(500, work_area.right - work_area.left)
    available_height = max(680, work_area.bottom - work_area.top)
    width = min(460, max(400, available_width // 4))
    height = min(900, available_height - 60)
    left = work_area.left + 60
    top = work_area.top + 30
    return width, height, left, top


def _find_app_window(window_title: str = APP_WINDOW_TITLE) -> int | None:
    if sys.platform != "win32":
        return None
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    handles: list[tuple[int, int]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(handle: int, _: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(handle):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(handle, title, length + 1)
        if title.value == window_title:
            rect = RECT()
            if ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect)):
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                handles.append((area, int(handle)))
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return max(handles)[1] if handles else None


def _fit_app_window(
    bounds: tuple[int, int, int, int],
    window_title: str = APP_WINDOW_TITLE,
) -> bool:
    if sys.platform != "win32":
        return False
    width, height, left, top = bounds
    handle = _find_app_window(window_title)
    if handle is None:
        return False
    ctypes.windll.user32.ShowWindow(handle, 9)  # SW_RESTORE
    return bool(
        ctypes.windll.user32.SetWindowPos(
            handle,
            0,
            left,
            top,
            width,
            height,
            0x0004 | 0x0010,  # SWP_NOZORDER | SWP_NOACTIVATE
        )
    )


def open_window() -> None:
    edge = next((path for path in EDGE_CANDIDATES if path.is_file()), None)
    if edge:
        profile = CACHE_ROOT / "desktop-shell"
        profile.mkdir(parents=True, exist_ok=True)
        bounds = client_window_bounds()
        width, height, left, top = bounds
        subprocess.Popen(
            [
                str(edge),
                f"--app={APP_URL}",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                f"--window-position={left},{top}",
                "--no-first-run",
                "--disable-features=msEdgeSidebarV2",
            ],
            close_fds=True,
        )
        for _ in range(40):
            _fit_app_window(bounds)
            time.sleep(0.1)
    else:
        import webbrowser

        webbrowser.open(APP_URL)


def open_mobile_preview_window() -> None:
    edge = next((path for path in EDGE_CANDIDATES if path.is_file()), None)
    if edge:
        profile = CACHE_ROOT / "mobile-preview-shell"
        profile.mkdir(parents=True, exist_ok=True)
        bounds = mobile_preview_window_bounds()
        width, height, left, top = bounds
        subprocess.Popen(
            [
                str(edge),
                f"--app={MOBILE_PREVIEW_URL}",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                f"--window-position={left},{top}",
                "--no-first-run",
                "--disable-features=msEdgeSidebarV2",
            ],
            close_fds=True,
        )
        for _ in range(40):
            _fit_app_window(bounds, MOBILE_PREVIEW_WINDOW_TITLE)
            time.sleep(0.1)
    else:
        import webbrowser

        webbrowser.open(MOBILE_PREVIEW_URL)


def _run_with_window(opener: Callable[[], None]) -> None:
    ensure_layout()
    if server_is_running():
        opener()
        return

    import threading

    thread = threading.Thread(target=run_server, name="instant-ai-server", daemon=True)
    thread.start()
    for _ in range(50):
        if server_is_running():
            opener()
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("即时 AI 本地服务启动失败")
    thread.join()


def main() -> None:
    _run_with_window(open_window)


def mobile_preview_main() -> None:
    _run_with_window(open_mobile_preview_window)


if __name__ == "__main__":
    main()
