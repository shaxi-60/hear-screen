"""实时解说调度：截屏 → 变化检测 → 视觉模型 → 语音播报。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from .ai import AIClient, AIError
from .capture import CaptureError, FrameSignature, ScreenCapturer, format_bbox
from .config import Config
from .speech import SpeechEngine

log = logging.getLogger(__name__)


class Narrator:
    def __init__(
        self,
        config: Config,
        speech: SpeechEngine,
        *,
        on_state: Callable[[str, str, str], None],
        on_caption: Callable[[str, dict[str, Any]], None],
        on_error: Callable[[str, bool], None],
    ) -> None:
        self.config = config
        self.speech = speech
        self.on_state = on_state
        self.on_caption = on_caption
        self.on_error = on_error

        self._q: queue.Queue[tuple] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._quit = False
        self._active = False
        self._paused = False
        self._one_shot = False
        self._generation = 0
        self._next_at = 0.0
        self._cfg: dict[str, Any] = config.snapshot()
        self._capturer = ScreenCapturer(int(self._cfg.get("max_image_side", 1440)))
        self._client: AIClient | None = None
        self._client_key: tuple | None = None

        self._previous = ""
        self._index = 0
        self._last_sig: FrameSignature | None = None
        self._last_bbox: tuple[int, int, int, int] | None = None
        self._last_source = ""
        self._black_warned = False
        self._last_error_spoken = 0.0
        self._error_count = 0
        self._state = "idle"

    # ------------------------------------------------------------------ 属性

    @property
    def live_active(self) -> bool:
        return self._active

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def state(self) -> str:
        return self._state

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> bool:
        self.refresh_config()
        if not self._cfg.get("api_key"):
            self._emit_state("error", "还没有填写 API Key",
                             "请打开“设置 → AI 服务”，填入豆包或兼容服务的 Key。")
            self.on_error("还没有填写 API Key。请先在设置中填写。", False)
            return False
        self._ensure_worker()
        # 立即进入“正在运行”，避免用户连续按两次快捷键时重复启动。
        self._active = True
        self._paused = False
        self._generation += 1
        self._q.put(("start",))
        return True

    def stop(self, announce: bool = True) -> None:
        self._active = False
        self._paused = False
        self.speech.stop()
        self._generation += 1
        self._q.put(("stopped",))
        if announce:
            self._emit_state("idle", "待机", "按 Ctrl+Alt+S 开始实时解说，或按 Ctrl+Alt+D 描述当前画面。")

    def toggle(self) -> None:
        if self._active:
            self.stop()
        else:
            self.start()

    def pause(self) -> None:
        if not self._active:
            return
        self._paused = not self._paused
        self.speech.stop()
        self._generation += 1
        if self._paused:
            self._emit_state("paused", "已暂停",
                             "实时观察已暂停。按 Ctrl+Alt+P 继续，按 Ctrl+Alt+X 只停止朗读。")
        else:
            self._next_at = time.monotonic() + 0.3
            self._emit_state("listening", "正在听画面",
                             self._listening_detail())

    def describe_once(self) -> bool:
        self.refresh_config()
        if not self._cfg.get("api_key"):
            self._emit_state("error", "还没有填写 API Key",
                             "请打开“设置 → AI 服务”，填入豆包或兼容服务的 Key。")
            self.on_error("还没有填写 API Key。请先在设置中填写。", False)
            return False
        self._ensure_worker()
        self._q.put(("once",))
        return True

    def stop_speech(self) -> None:
        self.speech.stop()
        if self._active and self._paused:
            self._emit_state("paused", "已暂停",
                             "实时观察已暂停。按 Ctrl+Alt+P 继续，按 Ctrl+Alt+D 可描述当前画面。")
        elif self._active:
            self._emit_state("listening", "正在听画面", self._listening_detail())
        else:
            self._emit_state("idle", "待机", "朗读已停止。按 Ctrl+Alt+D 可以重新描述当前画面。")

    def shutdown(self) -> None:
        self._quit = True
        self._active = False
        self.speech.stop()
        self._q.put(("quit",))
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def refresh_config(self) -> None:
        self._cfg = self.config.snapshot()
        self._capturer.max_side = max(600, int(self._cfg.get("max_image_side", 1440)))

    def update_config(self) -> None:
        self.refresh_config()
        self._client_key = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._q.put(("config",))
        if self._active and not self._paused:
            self._emit_state("listening", "正在听画面", self._listening_detail())

    # ------------------------------------------------------------------ 内部工具

    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._quit = False
        self._thread = threading.Thread(target=self._run, name="narrator", daemon=True)
        self._thread.start()

    def _emit_state(self, state: str, title: str, detail: str) -> None:
        self._state = state
        try:
            self.on_state(state, title, detail)
        except Exception:
            log.debug("状态回调失败", exc_info=True)

    def _emit_caption(self, text: str, meta: dict[str, Any]) -> None:
        try:
            self.on_caption(text, meta)
        except Exception:
            log.debug("字幕回调失败", exc_info=True)

    def _report(self, message: str, fatal: bool, speak: bool = False) -> None:
        log.warning("解说错误：%s", message)
        try:
            self.on_error(message, fatal)
        except Exception:
            log.debug("错误回调失败", exc_info=True)
        now = time.monotonic()
        if speak and now - self._last_error_spoken > 35:
            self._last_error_spoken = now
            short = message.split("\n", 1)[0]
            self.speech.speak("提示。" + short, interrupt=True)

    def _listening_detail(self) -> str:
        mode = {
            "window": "跟随当前窗口",
            "screen": "全屏（鼠标所在屏幕）",
            "region": "自定义区域",
        }.get(self._cfg.get("capture_mode"), "跟随当前窗口")
        interval = self._cfg.get("interval", 4.5)
        if self._cfg.get("change_only", True):
            return f"{mode}，每 {interval:g} 秒观察一次，只在画面变化时请求 AI。"
        return f"{mode}，每 {interval:g} 秒观察并解说一次。"

    def _get_client(self) -> AIClient:
        key = (
            self._cfg.get("base_url"), self._cfg.get("model"),
            self._cfg.get("api_key"), self._cfg.get("timeout"),
            self._cfg.get("fast_mode"), self._cfg.get("read_dialogue"),
            self._cfg.get("provider"),
        )
        if self._client is None or self._client_key != key:
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = AIClient(self._cfg)
            self._client_key = key
        return self._client

    def _grab(self):
        return self._capturer.capture(
            str(self._cfg.get("capture_mode", "window")),
            self._cfg.get("region"),
        )

    def _speak_description(self, text: str, *, interrupt: bool) -> None:
        if self.speech.available:
            self.speech.speak(text, interrupt=interrupt)
        else:
            log.warning("语音不可用，仅显示文字")

    def _handle_black(self, *, manual: bool) -> None:
        if manual or not self._black_warned:
            self._black_warned = True
            self._emit_state(
                "listening", "画面几乎是黑的",
                "可能视频还没开始播放，或画面受版权保护。请尝试用“框选区域”只框住播放器。"
            )
            self._speak_description(
                "画面现在是黑的。可能视频还没开始播放，或者画面受版权保护，无法读取。",
                interrupt=True,
            )

    # ------------------------------------------------------------------ 执行一次

    def _observe(self, *, manual: bool) -> None:
        started = time.monotonic()
        generation = self._generation
        detailed = manual or self._cfg.get("style") == "detailed"
        try:
            self._emit_state("thinking", "正在观察画面",
                             "正在截取画面并请求 AI 解说，请稍候。")
            result = self._grab()
            signature = self._capturer.signature(result.image)
            source = result.source
            bbox = result.bbox

            if signature.is_black:
                self._emit_caption(
                    "（当前画面几乎是黑的，无法读取。）",
                    {"source": source, "skipped": "black"},
                )
                self._handle_black(manual=manual)
                return

            scene_changed = (
                source != self._last_source
                or bbox != self._last_bbox
                or self._capturer.changed(self._last_sig, signature)
            )
            if manual:
                scene_changed = True
            if (not manual and self._cfg.get("change_only", True)
                    and not scene_changed):
                self._last_sig = signature
                self._last_bbox = bbox
                self._last_source = source
                self._emit_state("listening", "正在听画面",
                                 "画面没有明显变化，这次跳过 AI 请求以节省费用。")
                return

            image_bytes = self._capturer.to_jpeg_bytes(result.image)
            client = self._get_client()
            # 窗口标题可能包含隐私信息，只把“窗口/全屏”这种范围提示发给 AI。
            source_hint = source.split("：", 1)[0]
            text = client.describe(
                image_bytes,
                previous="" if manual else self._previous,
                index=self._index + 1,
                detailed=detailed,
                source=source_hint,
            )
            if generation != self._generation:
                log.info("画面已停止或暂停，丢弃迟到的解说")
                return

            self._last_sig = signature
            self._last_bbox = bbox
            self._last_source = source
            self._index += 1
            self._black_warned = False
            self._error_count = 0

            if text == "无变化":
                self._emit_state("listening", "正在听画面",
                                 "这一帧和上次基本相同，没有新的内容。")
                return

            self._previous = text
            elapsed = time.monotonic() - started
            meta = {
                "source": source,
                "bbox": bbox,
                "index": self._index,
                "latency": elapsed,
                "detailed": detailed,
                "mode": "manual" if manual else "live",
            }
            self._emit_caption(text, meta)
            if self._active and self._paused:
                state, title = "paused", "已暂停"
            elif self._active:
                state, title = "listening", "正在听画面"
            else:
                state, title = "idle", "解说完成"
            self._emit_state(
                state,
                title,
                f"第 {self._index} 次解说，用时 {elapsed:.1f} 秒。"
                + ("按 Ctrl+Alt+X 可停止朗读。" if self._active else ""),
            )
            self._speak_description(
                text, interrupt=bool(self._cfg.get("interrupt", True)) or manual
            )
            log.info("解说完成：%.1fs，%d 字，来源=%s", elapsed, len(text),
                     self._cfg.get("capture_mode"))
        except CaptureError as exc:
            self._error_count += 1
            if self._active:
                self._emit_state("error", "截屏失败", str(exc))
            self._report(str(exc), False, speak=True)
            if self._error_count >= 5 and self._active:
                self._active = False
                self._paused = False
                self.speech.stop()
                self._emit_state(
                    "error", "已停止实时解说",
                    "连续多次无法截屏。请检查窗口是否最小化，或改用“框选区域”。",
                )
        except AIError as exc:
            self._error_count += 1
            fatal = exc.status in (401, 403)
            msg = exc.user_message
            self._emit_state("error", "AI 请求失败", msg)
            self._report(msg, fatal, speak=True)
            if fatal:
                log.warning("鉴权失败，自动停止实时解说")
                self._active = False
                self._paused = False
                self.speech.stop()
                self._emit_state("error", "已停止：需要检查设置", msg)
        except Exception as exc:
            self._error_count += 1
            log.exception("解说过程异常")
            msg = "解说时遇到意外错误，请重试。详细信息已写入日志。"
            self._emit_state("error", "出现错误", msg)
            self._report(msg, False, speak=False)
        finally:
            if manual:
                self._next_at = time.monotonic() + float(self._cfg.get("interval", 4.5))
            else:
                interval = float(self._cfg.get("interval", 4.5))
                now = time.monotonic()
                # 按截屏开始时间排下一次；请求再慢也不会无限堆积。
                self._next_at = max(started + interval, now + 1.0)

    # ------------------------------------------------------------------ 主循环

    def _run(self) -> None:
        log.info("解说线程启动")
        while not self._quit:
            try:
                cmd = self._q.get(timeout=0.05)
            except queue.Empty:
                cmd = None
            if cmd:
                kind = cmd[0]
                if kind == "quit":
                    break
                if kind == "start":
                    self._active = True
                    self._paused = False
                    self._generation += 1
                    self._previous = ""
                    self._index = 0
                    self._last_sig = None
                    self._last_bbox = None
                    self._last_source = ""
                    self._black_warned = False
                    self._error_count = 0
                    self._next_at = time.monotonic()
                    self.speech.stop()
                    self._emit_state("listening", "正在听画面", self._listening_detail())
                elif kind == "stopped":
                    pass
                elif kind == "once":
                    if self._client:
                        pass
                    self._observe(manual=True)
                elif kind == "config":
                    pass

            if (self._active and not self._paused
                    and time.monotonic() >= self._next_at):
                self._observe(manual=False)

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        log.info("解说线程结束")
