"""Windows 相关的小工具：DPI、窗口、截屏排除、全局热键、提示音。"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        dwmapi = None
else:  # pragma: no cover - 仅用于源码检查
    user32 = kernel32 = dwmapi = None


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def set_dpi_awareness() -> None:
    """必须在创建 Tk 窗口之前调用，保证截屏坐标与屏幕坐标一致。"""
    if not IS_WINDOWS:
        return
    try:
        # PER_MONITOR_AWARE_V2
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            log.debug("无法设置 DPI 感知", exc_info=True)


def get_dpi_for_window(hwnd: int) -> int:
    if not IS_WINDOWS:
        return 96
    try:
        dpi = user32.GetDpiForWindow(wintypes.HWND(hwnd))
        return int(dpi) if dpi else 96
    except Exception:
        try:
            return int(user32.GetDpiForSystem())
        except Exception:
            return 96


def set_dark_titlebar(hwnd: int) -> None:
    """Windows 10/11 深色标题栏；失败时保持系统默认。"""
    if not IS_WINDOWS or not hwnd or dwmapi is None:
        return
    try:
        enabled = wintypes.BOOL(True)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            hr = dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), attribute,
                ctypes.byref(enabled), ctypes.sizeof(enabled),
            )
            if hr == 0:
                return
    except Exception:
        log.debug("设置深色标题栏失败", exc_info=True)


def get_window_title(hwnd: int) -> str:
    if not IS_WINDOWS or not hwnd:
        return ""
    try:
        length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def get_window_pid(hwnd: int) -> int:
    if not IS_WINDOWS or not hwnd:
        return 0
    pid = wintypes.DWORD()
    try:
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    except Exception:
        return 0
    return int(pid.value)


def is_own_window(hwnd: int) -> bool:
    return bool(hwnd) and get_window_pid(hwnd) == os.getpid()


def is_window_usable(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        if not user32.IsWindow(wintypes.HWND(hwnd)):
            return False
        if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            return False
        if user32.IsIconic(wintypes.HWND(hwnd)):
            return False
        # 排除被 DWM 隐藏的 UWP 幽灵窗口
        if dwmapi is not None:
            cloaked = wintypes.DWORD()
            hr = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            if hr == 0 and cloaked.value:
                return False
        return True
    except Exception:
        return False


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """返回窗口的物理像素 (left, top, right, bottom)，尽量不含阴影。"""
    if not IS_WINDOWS or not hwnd:
        return None
    rect = RECT()
    try:
        if dwmapi is not None:
            hr = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)
            )
            if hr == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    try:
        if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


def get_foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return int(user32.GetForegroundWindow())
    except Exception:
        return 0


def get_monitor_for_point(x: int, y: int) -> tuple[int, int, int, int]:
    if not IS_WINDOWS:
        return 0, 0, 1920, 1080
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    try:
        hmon = user32.MonitorFromPoint(POINT(x, y), 2)  # NEAREST
        if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return get_virtual_screen()


def get_cursor_monitor() -> tuple[int, int, int, int]:
    if not IS_WINDOWS:
        return 0, 0, 1920, 1080
    pt = POINT()
    try:
        user32.GetCursorPos(ctypes.byref(pt))
        return get_monitor_for_point(pt.x, pt.y)
    except Exception:
        return get_virtual_screen()


def get_virtual_screen() -> tuple[int, int, int, int]:
    if not IS_WINDOWS:
        return 0, 0, 1920, 1080
    try:
        left = user32.GetSystemMetrics(76)
        top = user32.GetSystemMetrics(77)
        width = user32.GetSystemMetrics(78)
        height = user32.GetSystemMetrics(79)
        if width > 0 and height > 0:
            return left, top, left + width, top + height
    except Exception:
        pass
    return 0, 0, 1920, 1080


def _enum_candidate_windows() -> list[int]:
    if not IS_WINDOWS:
        return []
    handles: list[int] = []
    own_pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        try:
            h = int(hwnd)
            if not h or get_window_pid(h) == own_pid:
                return True
            if not is_window_usable(h):
                return True
            if user32.GetWindowTextLengthW(wintypes.HWND(h)) <= 0:
                return True
            ex_style = user32.GetWindowLongW(wintypes.HWND(h), -20)
            if ex_style & 0x00000080:  # WS_EX_TOOLWINDOW
                return True
            handles.append(h)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        log.debug("枚举窗口失败", exc_info=True)
    return handles


def find_candidate_window(preferred: int = 0) -> int:
    """找到最可能的视频窗口，跳过本程序自己的窗口、桌面和任务栏。"""
    if not IS_WINDOWS:
        return 0
    vx1, vy1, vx2, vy2 = get_virtual_screen()
    screen_area = max(1, (vx2 - vx1) * (vy2 - vy1))

    # 优先跟随用户此刻正在操作的窗口。
    foreground = get_foreground_window()
    if (foreground and not is_own_window(foreground)
            and is_window_usable(foreground) and get_window_title(foreground)):
        rect = get_window_rect(foreground)
        if rect:
            left, top, right, bottom = rect
            area = max(0, right - left) * max(0, bottom - top)
            if area >= screen_area * 0.04:
                return foreground

    if preferred and not is_own_window(preferred) and is_window_usable(preferred):
        return preferred
    for hwnd in _enum_candidate_windows():
        rect = get_window_rect(hwnd)
        if not rect:
            continue
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width < 320 or height < 200:
            continue
        if width * height < screen_area * 0.06:
            continue
        # 跳过完全在屏幕外的窗口
        if right <= vx1 or left >= vx2 or bottom <= vy1 or top >= vy2:
            continue
        return hwnd
    return 0


def set_capture_exclusion(hwnd: int, enable: bool = True) -> bool:
    """让本程序窗口不被自己截进画面（Win10 2004+）。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        affinity = 0x11 if enable else 0  # WDA_EXCLUDEFROMCAPTURE
        if user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), affinity):
            return True
        if enable:
            return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), 0x01))
    except Exception:
        log.debug("设置截屏排除失败", exc_info=True)
    return False


def beep(frequency: int = 880, duration_ms: int = 110, kind: str = "ok") -> None:
    if not IS_WINDOWS:
        return
    try:
        import winsound

        if kind == "error":
            winsound.Beep(320, 180)
            winsound.Beep(240, 220)
        elif kind == "start":
            winsound.Beep(660, 80)
            winsound.Beep(990, 110)
        elif kind == "stop":
            winsound.Beep(660, 80)
            winsound.Beep(440, 120)
        else:
            winsound.Beep(max(120, min(3000, frequency)), max(30, duration_ms))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 全局热键
# ---------------------------------------------------------------------------

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

_VK = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A, "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33,
    "4": 0x34, "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    parts = [p.strip().upper() for p in text.replace("＋", "+").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    key = None
    for part in parts:
        if part in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif part == "ALT":
            mods |= MOD_ALT
        elif part == "SHIFT":
            mods |= MOD_SHIFT
        elif part in _VK:
            key = _VK[part]
        else:
            return None
    if key is None:
        return None
    return mods, key


class HotkeyManager:
    """在独立线程注册系统级热键，回调会从该线程触发。"""

    def __init__(
        self,
        bindings: dict[str, str | list[str]],
        callback: Callable[[str], None],
    ):
        self.bindings = bindings
        self.callback = callback
        self.errors: list[str] = []
        self.registered: dict[str, str] = {}
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._id_to_action: dict[int, str] = {}
        self._running = False

    def start(self) -> None:
        if not IS_WINDOWS or self._thread is not None:
            self._ready.set()
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def _run(self) -> None:
        try:
            self._thread_id = int(kernel32.GetCurrentThreadId())
            idx = 0
            for action, spec in self.bindings.items():
                candidates = spec if isinstance(spec, (list, tuple)) else [spec]
                success = False
                last_err = ""
                for text in candidates:
                    parsed = parse_hotkey(str(text))
                    if not parsed:
                        last_err = f"{text} 格式不正确"
                        continue
                    mods, vk = parsed
                    idx += 1
                    ok = user32.RegisterHotKey(None, idx, mods | MOD_NOREPEAT, vk)
                    if ok:
                        self._id_to_action[idx] = action
                        self.registered[action] = str(text)
                        success = True
                        break
                    err = ctypes.get_last_error()
                    last_err = (f"{text} 已被其他软件占用" if err == 1409
                                else f"{text} 注册失败（错误 {err}）")
                if not success:
                    self.errors.append(last_err or f"{action} 注册失败")
            self._ready.set()
            msg = wintypes.MSG()
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                if msg.message == 0x0312:  # WM_HOTKEY
                    action = self._id_to_action.get(int(msg.wParam))
                    if action:
                        try:
                            self.callback(action)
                        except Exception:
                            log.exception("热键回调失败")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log.exception("热键线程异常")
            self._ready.set()
        finally:
            for idx in self._id_to_action:
                try:
                    user32.UnregisterHotKey(None, idx)
                except Exception:
                    pass

    def stop(self) -> None:
        self._running = False
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None
