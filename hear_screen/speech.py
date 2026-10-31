"""Windows 内置语音合成（SAPI5），离线可用、无需额外语音服务。"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

# SAPI 常量
SVSF_ASYNC = 1
SVSF_PURGE = 2
SVSF_IS_XML = 8


def clean_for_speech(text: str, limit: int = 600) -> str:
    if not text:
        return ""
    value = str(text).replace("\r", "\n")
    value = re.sub(r"```.*?```", " ", value, flags=re.S)
    value = re.sub(r"[`*#_>|~]+", "", value)
    value = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", value)
    value = re.sub(r"^\s*(解说|描述|画面解说|AI)\s*[:：]\s*", "", value)
    value = value.replace("\n", "。")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"。{2,}", "。", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    value = value.strip(" 。；;，,")
    if len(value) > limit:
        cut = value[:limit]
        positions = [cut.rfind(ch) for ch in "。！？；!?;"]
        end = max(positions)
        value = cut[: end + 1] if end > limit * 0.55 else cut.rstrip() + "。"
    return value.strip()


class SpeechEngine:
    """独立线程持有 COM 对象，所有公开方法线程安全。"""

    def __init__(
        self,
        voice_id: str = "",
        rate: int = 0,
        volume: int = 100,
        on_error: Callable[[str], None] | None = None,
        on_speaking: Callable[[bool], None] | None = None,
    ) -> None:
        self._q: queue.Queue[tuple] = queue.Queue()
        self._voice_id = voice_id or ""
        self._rate = max(-10, min(10, int(rate)))
        self._volume = max(0, min(100, int(volume)))
        self._on_error = on_error
        self._on_speaking = on_speaking
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._available = False
        self._error = ""
        self._voices: list[dict[str, str]] = []
        self._speaking = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 公共 API

    @property
    def available(self) -> bool:
        return self._available

    @property
    def error(self) -> str:
        return self._error

    def set_callbacks(
        self,
        on_error: Callable[[str], None] | None = None,
        on_speaking: Callable[[bool], None] | None = None,
    ) -> None:
        self._on_error = on_error
        self._on_speaking = on_speaking

    def voices(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(v) for v in self._voices]

    def start(self, timeout: float = 4.0) -> bool:
        if self._thread is not None:
            return self._available
        self._thread = threading.Thread(target=self._run, name="speech", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        return self._available

    def configure(self, *, voice_id: str | None = None,
                  rate: int | None = None, volume: int | None = None) -> None:
        if voice_id is not None:
            self._voice_id = voice_id
        if rate is not None:
            self._rate = max(-10, min(10, int(rate)))
        if volume is not None:
            self._volume = max(0, min(100, int(volume)))
        self._q.put(("config", self._voice_id, self._rate, self._volume))

    def speak(self, text: str, interrupt: bool = True) -> bool:
        cleaned = clean_for_speech(text)
        if not cleaned:
            return False
        if not self._available:
            return False
        self._q.put(("say", cleaned, bool(interrupt)))
        return True

    def stop(self) -> None:
        self._q.put(("stop",))

    def pause(self) -> None:
        self._q.put(("pause",))

    def resume(self) -> None:
        self._q.put(("resume",))

    def shutdown(self) -> None:
        self._q.put(("quit",))
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------ 线程实现

    def _set_speaking(self, value: bool) -> None:
        if self._speaking == value:
            return
        self._speaking = value
        if self._on_speaking:
            try:
                self._on_speaking(value)
            except Exception:
                pass

    def _run(self) -> None:
        voice = None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._load_voices(voice)
            self._apply_config(voice)
            self._available = True
            self._ready.set()
        except Exception as exc:
            log.exception("初始化 Windows 语音失败")
            self._error = (
                "Windows 语音组件初始化失败。请确认系统已安装中文语音"
                "（设置 → 时间和语言 → 语音），或重启软件。"
            )
            self._ready.set()
            if self._on_error:
                try:
                    self._on_error(self._error)
                except Exception:
                    pass
            return

        speaking = False
        paused = False
        purge = SVSF_ASYNC | SVSF_PURGE
        try:
            while True:
                try:
                    cmd = self._q.get(timeout=0.025)
                except queue.Empty:
                    cmd = None

                if cmd:
                    kind = cmd[0]
                    if kind == "quit":
                        break
                    if kind == "say":
                        _, text, interrupt = cmd
                        try:
                            if interrupt:
                                try:
                                    voice.Speak("", purge)
                                except Exception:
                                    pass
                            if paused:
                                voice.Resume()
                                paused = False
                            voice.Speak(text, SVSF_ASYNC)
                            speaking = True
                            self._set_speaking(True)
                        except Exception:
                            log.exception("朗读失败")
                            speaking = False
                            paused = False
                            self._set_speaking(False)
                    elif kind == "stop":
                        try:
                            voice.Speak("", purge)
                        except Exception:
                            pass
                        speaking = False
                        paused = False
                        self._set_speaking(False)
                    elif kind == "pause":
                        try:
                            if speaking:
                                voice.Pause()
                                paused = True
                        except Exception:
                            log.debug("暂停语音失败", exc_info=True)
                    elif kind == "resume":
                        try:
                            if paused:
                                voice.Resume()
                                paused = False
                        except Exception:
                            log.debug("恢复语音失败", exc_info=True)
                    elif kind == "config":
                        _, vid, rate, volume = cmd
                        self._voice_id = vid
                        self._rate = rate
                        self._volume = volume
                        try:
                            self._apply_config(voice)
                        except Exception:
                            log.debug("应用语音设置失败", exc_info=True)

                if speaking:
                    try:
                        done = bool(voice.WaitUntilDone(20))
                    except Exception:
                        done = True
                    if done:
                        speaking = False
                        paused = False
                        self._set_speaking(False)
        except Exception:
            log.exception("语音线程异常退出")
        finally:
            try:
                if voice is not None:
                    voice.Speak("", purge)
            except Exception:
                pass
            self._set_speaking(False)
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _load_voices(self, voice: Any) -> None:
        results: list[dict[str, str]] = []
        try:
            tokens = voice.GetVoices()
            for i in range(tokens.Count):
                token = tokens.Item(i)
                try:
                    desc = token.GetDescription()
                    vid = token.Id
                    lang = ""
                    try:
                        attrs = token.GetAttribute("Language")
                        lang = str(attrs)
                    except Exception:
                        pass
                    results.append({"id": str(vid), "name": str(desc), "lang": lang})
                except Exception:
                    continue
        except Exception:
            log.exception("枚举语音失败")
        with self._lock:
            self._voices = results
        if not results:
            self._error = "系统里没有找到可用的语音。请在 Windows 设置中安装中文语音包。"
            raise RuntimeError(self._error)

    def _apply_config(self, voice: Any) -> None:
        voice.Rate = self._rate
        voice.Volume = self._volume
        target = self._voice_id
        tokens = voice.GetVoices()
        if not tokens.Count:
            return
        chosen = None
        if target:
            for i in range(tokens.Count):
                token = tokens.Item(i)
                if str(token.Id) == target:
                    chosen = token
                    break
        if chosen is None:
            # 优先中文声音
            for i in range(tokens.Count):
                token = tokens.Item(i)
                desc = str(token.GetDescription()).lower()
                tid = str(token.Id).lower()
                if ("chinese" in desc or "中文" in desc or "huihui" in desc
                        or "zh-cn" in tid or "zh_cn" in tid):
                    chosen = token
                    break
        if chosen is None:
            chosen = tokens.Item(0)
        voice.Voice = chosen
        self._voice_id = str(chosen.Id)

    def wait_until_idle(self, timeout: float = 8.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self._speaking:
                return True
            time.sleep(0.05)
        return not self._speaking
