"""配置读写、API Key 加密与默认值。"""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import APP_NAME, APP_VERSION

log = logging.getLogger(__name__)

APP_DIR_NAME = "HearScreen"

PROVIDER_DOUBAO = "doubao"
PROVIDER_CUSTOM = "custom"

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    PROVIDER_DOUBAO: {
        "label": "豆包 · 火山方舟（推荐）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-vision-250815",
        "help_url": "https://console.volcengine.com/ark",
    },
    PROVIDER_CUSTOM: {
        "label": "自定义 OpenAI 兼容接口",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "help_url": "https://platform.openai.com/api-keys",
    },
}

DEFAULT_HOTKEYS = {
    "toggle": "Ctrl+Alt+S",
    "once": "Ctrl+Alt+D",
    "pause": "Ctrl+Alt+P",
    "stop_speech": "Ctrl+Alt+X",
    "mini": "Ctrl+Alt+M",
}

DEFAULTS: dict[str, Any] = {
    "version": APP_VERSION,
    "provider": PROVIDER_DOUBAO,
    "base_url": PROVIDER_PRESETS[PROVIDER_DOUBAO]["base_url"],
    "model": PROVIDER_PRESETS[PROVIDER_DOUBAO]["model"],
    "capture_mode": "window",  # window | screen | region
    "region": [0, 0, 0, 0],  # 物理像素，虚拟桌面坐标
    "interval": 4.5,
    "style": "concise",  # concise | detailed
    "change_only": True,
    "interrupt": True,
    "read_dialogue": False,
    "hide_window": True,
    "auto_start": False,
    "always_on_top": False,
    "voice_id": "",
    "tts_rate": 0,
    "tts_volume": 100,
    "fast_mode": True,
    "timeout": 35,
    "max_image_side": 1440,
    "first_run": True,
    "mini_x": None,
    "mini_y": None,
}


def _can_write_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _portable_dir() -> Path | None:
    """打包后的程序优先把配置放在程序旁边，整个文件夹可以拷走。"""
    if not getattr(sys, "frozen", False):
        return None
    try:
        base = Path(sys.executable).resolve().parent
    except Exception:
        return None
    return base / "data"


def app_data_dir() -> Path:
    """配置与日志目录。可用环境变量覆盖，方便便携使用和测试。"""
    override = os.environ.get("HEARSCREEN_DATA_DIR")
    if override:
        return Path(override).expanduser()
    portable = _portable_dir()
    if portable is not None and _can_write_directory(portable):
        return portable
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def local_data_dir() -> Path:
    """兼容旧版本：日志现在与配置放在同一目录。"""
    return app_data_dir()


CONFIG_PATH = app_data_dir() / "config.json"
LOG_DIR = app_data_dir() / "logs"


def resource_path(relative: str) -> Path:
    """兼容源码运行与 PyInstaller onefile。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parent.parent / relative


# ---------------------------------------------------------------------------
# Windows DPAPI：用当前 Windows 用户的凭据加密 API Key
# ---------------------------------------------------------------------------

def _dpapi(data: bytes, protect: bool) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(blob_in), APP_NAME, None, None, None, flags,
                ctypes.byref(blob_out),
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, flags,
                ctypes.byref(blob_out),
            )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        log.exception("DPAPI 操作失败")
        return None


def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    raw = secret.encode("utf-8")
    encrypted = _dpapi(raw, True)
    if encrypted is None:
        # 不把明文 Key 写入磁盘；本次运行仍可使用内存中的 Key。
        log.warning("DPAPI 不可用，API Key 不会保存到磁盘")
        return ""
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(token: str) -> str:
    if not token:
        return ""
    try:
        if token.startswith("dpapi:"):
            data = base64.b64decode(token[6:])
            raw = _dpapi(data, False)
            return raw.decode("utf-8") if raw else ""
        if token.startswith("plain:"):
            return base64.b64decode(token[6:]).decode("utf-8")
    except Exception:
        log.exception("读取 API Key 失败")
    return ""


class Config:
    """线程安全的轻量配置对象。"""

    def __init__(self) -> None:
        self.path = CONFIG_PATH
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.api_key: str = ""
        self.load()

    def load(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                secret = raw.pop("api_key_enc", "")
                raw.pop("api_key", None)
                for key, value in raw.items():
                    if key in DEFAULTS:
                        self.data[key] = value
                self.data["version"] = APP_VERSION
                self.api_key = unprotect_secret(secret) if secret else ""
        except Exception:
            log.exception("配置文件读取失败，使用默认配置")

    def save(self) -> None:
        self.data["version"] = APP_VERSION
        payload = dict(self.data)
        payload["api_key_enc"] = protect_secret(self.api_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            if key in DEFAULTS:
                self.data[key] = value

    def snapshot(self) -> dict[str, Any]:
        data = dict(self.data)
        data["api_key"] = self.api_key
        return data

    def clamp(self) -> None:
        try:
            self.data["interval"] = min(12.0, max(2.0, float(self.data.get("interval", 4.5))))
        except Exception:
            self.data["interval"] = 4.5
        try:
            self.data["tts_rate"] = min(10, max(-10, int(self.data.get("tts_rate", 0))))
        except Exception:
            self.data["tts_rate"] = 0
        try:
            self.data["tts_volume"] = min(100, max(0, int(self.data.get("tts_volume", 100))))
        except Exception:
            self.data["tts_volume"] = 100
        if self.data.get("style") not in ("concise", "detailed"):
            self.data["style"] = "concise"
        if self.data.get("capture_mode") not in ("window", "screen", "region"):
            self.data["capture_mode"] = "window"


def setup_logging() -> Path:
    from logging.handlers import RotatingFileHandler

    candidates = [LOG_DIR]
    try:
        candidates.append(app_data_dir() / "logs")
    except Exception:
        pass
    try:
        candidates.append(Path(tempfile.gettempdir()) / APP_DIR_NAME / "logs")
    except Exception:
        pass
    # 去重，避免重复尝试同一个目录
    seen: set[str] = set()
    unique = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    last_error: Exception | None = None
    for directory in unique:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            logfile = directory / "app.log"
            file_handler = RotatingFileHandler(
                logfile, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        except Exception as exc:
            last_error = exc
            continue
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        globals()["LOG_DIR"] = directory
        return logfile

    root.addHandler(logging.NullHandler())
    if last_error:
        root.warning("日志文件不可写：%s", last_error)
    return Path("")
