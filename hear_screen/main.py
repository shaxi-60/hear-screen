"""程序入口、自检和开发辅助命令。"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import traceback
from pathlib import Path

from . import APP_NAME, APP_VERSION
from .config import Config, LOG_DIR, app_data_dir, resource_path, setup_logging
from . import winapi

log = logging.getLogger(__name__)


def _write_json(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_selftest(output: str, speak_test: bool = False) -> int:
    winapi.set_dpi_awareness()
    setup_logging()
    result: dict = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "python": sys.version,
        "frozen": bool(getattr(sys, "frozen", False)),
        "data_dir": str(app_data_dir()),
        "checks": {},
    }
    failed = False
    try:
        import mss
        import PIL
        import requests

        result["checks"]["imports"] = {
            "ok": True,
            "pillow": PIL.__version__,
            "requests": requests.__version__,
            "mss": getattr(mss, "__version__", "installed"),
        }
    except Exception as exc:
        failed = True
        result["checks"]["imports"] = {"ok": False, "error": str(exc)}

    try:
        from .capture import ScreenCapturer

        capturer = ScreenCapturer(1280)
        shot = capturer.capture("screen")
        sig = capturer.signature(shot.image)
        result["checks"]["capture"] = {
            "ok": True,
            "size": list(shot.image.size),
            "bbox": list(shot.bbox),
            "mean": round(sig.mean, 2),
            "stddev": round(sig.stddev, 2),
        }
    except Exception as exc:
        failed = True
        log.exception("自检：截屏失败")
        result["checks"]["capture"] = {"ok": False, "error": str(exc)}

    try:
        from .speech import SpeechEngine

        engine = SpeechEngine()
        available = engine.start(timeout=5)
        voices = engine.voices()
        result["checks"]["speech"] = {
            "ok": bool(available and voices),
            "voices": [v.get("name") for v in voices],
            "error": engine.error,
        }
        if not available or not voices:
            failed = True
        if available and speak_test:
            engine.speak("听见画面自检。语音正常。", interrupt=True)
            engine.wait_until_idle(6)
        engine.shutdown()
    except Exception as exc:
        failed = True
        log.exception("自检：语音失败")
        result["checks"]["speech"] = {"ok": False, "error": str(exc)}

    try:
        cfg = Config()
        cfg.clamp()
        test_path = app_data_dir() / "selftest-config.json"
        old_path = cfg.path
        cfg.path = test_path
        cfg.save()
        # 仅验证配置文件可写，不覆盖用户配置
        payload = json.loads(test_path.read_text(encoding="utf-8"))
        test_path.unlink(missing_ok=True)
        cfg.path = old_path
        result["checks"]["config"] = {
            "ok": "api_key_enc" in payload,
            "path": str(old_path),
            "has_key": bool(cfg.api_key),
        }
    except Exception as exc:
        failed = True
        log.exception("自检：配置失败")
        result["checks"]["config"] = {"ok": False, "error": str(exc)}

    result["ok"] = not failed
    result["log_dir"] = str(LOG_DIR)
    _write_json(output, result)
    return 0 if not failed else 1


def run_snapshot(output: str) -> int:
    winapi.set_dpi_awareness()
    from .capture import ScreenCapturer

    capturer = ScreenCapturer(1920)
    size = capturer.save_snapshot(output)
    print(f"saved {output} {size[0]}x{size[1]}")
    return 0


_SINGLE_INSTANCE_MUTEX = None


def _acquire_single_instance() -> bool:
    """避免重复打开导致热键被占用、多个程序同时朗读。"""
    global _SINGLE_INSTANCE_MUTEX
    if sys.platform != "win32":
        return True
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR
        ]
        _SINGLE_INSTANCE_MUTEX = kernel32.CreateMutexW(
            None, False, "Local\\HearScreen.SingleInstance.1"
        )
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.FindWindowW.restype = wintypes.HWND
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            for title in ("听见画面 · 迷你悬浮条", "听见画面 · AI 视频解说"):
                hwnd = user32.FindWindowW(None, title)
                if hwnd:
                    user32.ShowWindow(hwnd, 9)
                    user32.SetForegroundWindow(hwnd)
                    break
            user32.MessageBoxW(
                None,
                "听见画面已经在运行。\n\n"
                "如果看不到窗口，请按 Ctrl+Alt+M 显示迷你悬浮条，"
                "或从任务栏打开。",
                "听见画面", 0x40,
            )
            return False
        return True
    except Exception:
        log.exception("单实例检查失败，继续启动")
        return True


def run_gui(mock_ai: bool = False) -> int:
    winapi.set_dpi_awareness()
    if not _acquire_single_instance():
        return 0
    logfile = setup_logging()
    log.info("启动 %s %s，日志：%s", APP_NAME, APP_VERSION, logfile)

    import tkinter as tk
    from tkinter import messagebox

    from .speech import SpeechEngine
    from .ui import HearScreenApp

    config = Config()
    config.clamp()
    if mock_ai:
        os.environ["HEARSCREEN_MOCK_AI"] = "1"
        config.api_key = "mock-key"
        config.set("base_url", "https://mock.local/v1")
        config.set("model", "mock-vision")
        log.warning("启用 MOCK AI 模式，不会访问真实接口")

    speech = SpeechEngine(
        voice_id=str(config.get("voice_id", "")),
        rate=int(config.get("tts_rate", 0)),
        volume=int(config.get("tts_volume", 100)),
    )
    speech.start(timeout=5.0)

    root = tk.Tk()
    try:
        icon = resource_path("assets/icon.ico")
        if icon.exists():
            root.iconbitmap(default=str(icon))
    except Exception:
        log.debug("设置图标失败", exc_info=True)

    app = HearScreenApp(root, config, speech)

    def report_callback_exception(exc, value, tb) -> None:
        log.error("Tk 回调异常：%s", value, exc_info=(exc, value, tb))
        try:
            messagebox.showerror(
                "听见画面", "界面遇到一个错误，但软件还在运行。\n"
                f"{value}\n\n详细信息已写入日志。", parent=root
            )
        except Exception:
            pass

    root.report_callback_exception = report_callback_exception
    try:
        root.mainloop()
    finally:
        try:
            if app.hotkeys:
                app.hotkeys.stop()
        except Exception:
            pass
        log.info("程序退出")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="HearScreen", add_help=False)
    parser.add_argument("--selftest", metavar="JSON")
    parser.add_argument("--snapshot", metavar="JPG")
    parser.add_argument("--mock-ai", action="store_true")
    parser.add_argument("--speak-test", action="store_true")
    parser.add_argument("--version", action="store_true")
    args, _unknown = parser.parse_known_args(argv)

    if args.version:
        return 0
    if args.selftest:
        return run_selftest(args.selftest, speak_test=args.speak_test)
    if args.snapshot:
        return run_snapshot(args.snapshot)
    try:
        return run_gui(mock_ai=args.mock_ai)
    except Exception:
        log.exception("启动失败")
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "听见画面无法启动",
                "启动时遇到错误：\n" + traceback.format_exc(limit=2)
                + f"\n\n日志：{LOG_DIR / 'app.log'}",
                parent=root,
            )
            root.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
