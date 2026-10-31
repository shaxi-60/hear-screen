"""Tkinter 无障碍界面：主窗口、区域框选、迷你悬浮条和设置页。"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import APP_NAME, APP_TAGLINE, APP_VERSION
from .ai import AIClient, AIError
from .capture import ScreenCapturer, format_bbox
from .config import DEFAULT_HOTKEYS, Config, PROVIDER_DOUBAO, PROVIDER_PRESETS
from .narrator import Narrator
from .speech import SpeechEngine
from . import winapi

log = logging.getLogger(__name__)

BG = "#0B1016"
SURFACE = "#141C25"
SURFACE_2 = "#1B2633"
FIELD = "#0D151E"
BORDER = "#2B3A4A"
TEXT = "#F4F7FA"
MUTED = "#A7B5C4"
DIM = "#748394"
ACCENT = "#FFD24A"
ACCENT_HOVER = "#FFE27A"
ACCENT_TEXT = "#171307"
BLUE = "#4F8DFF"
BLUE_HOVER = "#6EA2FF"
GREEN = "#45D48A"
RED = "#FF6B6B"
RED_HOVER = "#FF8C8C"
ORANGE = "#FFA94D"

STATE_STYLE = {
    "idle": (DIM, "待机"),
    "listening": (GREEN, "正在听画面"),
    "paused": (ACCENT, "已暂停"),
    "thinking": (BLUE, "正在理解画面"),
    "speaking": (ACCENT, "正在解说"),
    "error": (RED, "需要注意"),
}

CAPTURE_LABELS = {
    "window": "跟随当前窗口（推荐）",
    "screen": "全屏（鼠标所在屏幕）",
    "region": "自定义区域",
}
CAPTURE_KEYS = {v: k for k, v in CAPTURE_LABELS.items()}

PROVIDER_LABELS = {
    PROVIDER_DOUBAO: "豆包 · 火山方舟（推荐）",
    "custom": "自定义 OpenAI 兼容接口",
}
PROVIDER_KEYS = {v: k for k, v in PROVIDER_LABELS.items()}

HELP_TEXT = """听见画面 1.0
把屏幕上的视频和画面，变成视障用户可以听懂的实时解说。

【最快上手】
1. 打开“设置 → AI 服务”。
2. 填入火山方舟的 API Key。模型 ID 一般不用改；如果控制台显示的是 ep- 开头的推理接入点 ID，也可以直接填。
3. 点“测试连接”，听到或看到“连接成功”即可保存。
4. 打开抖音、B 站或其他视频，按 Ctrl+Alt+S 开始实时解说。
5. 想只听当前这一帧，按 Ctrl+Alt+D。

【解说范围】
· 跟随当前窗口：自动跟住前台视频窗口，推荐。
· 全屏（鼠标所在屏幕）：把鼠标放到目标显示器上，再开始。
· 自定义区域：只框住播放器，能减少无关信息，也更省流量。

【全局快捷键】
Ctrl+Alt+S  开始 / 停止实时解说
Ctrl+Alt+D  描述当前画面（可反复按）
Ctrl+Alt+P  暂停 / 继续实时观察
Ctrl+Alt+X  立即停止朗读
Ctrl+Alt+M  显示 / 收起迷你悬浮条（被占用时自动改用 Ctrl+Alt+B）
F1          打开本帮助
Ctrl+逗号   打开设置

【使用建议】
· 戴耳机，避免解说声被麦克风或扬声器回声干扰。
· 实时模式优先播报最新画面，上一句可能被新解说打断，这是正常的。
· 视频原声已经能听到时，默认不复述台词，只补充画面、动作、表情和关键文字。
· 如果视频没有声音，请在“设置 → 解说”勾选“同时朗读字幕/台词”。

【隐私】
· 截图只发送到你填写的 AI 接口，不会保存到硬盘。
· API Key 使用 Windows 当前用户的 DPAPI 加密，保存在本机配置文件中。
· 自定义区域只截取你框选的范围。请不要把密码、聊天等敏感内容框进去。

【常见问题】
· 401 / 403：API Key 无效、过期或模型未开通。
· 404：模型 ID 或接口地址不对，去火山方舟控制台复制准确的 ID。
· 429：请求太频繁或余额不足，调大观察间隔或充值。
· 黑屏：部分版权视频禁止截屏，或播放器用了独占全屏；可改用窗口播放或框选区域。
· 没有声音：在 Windows 设置 → 时间和语言 → 语音 中安装中文语音包。
· 快捷键无效：可能被其他软件占用；界面按钮仍然可用。

日志文件：程序目录\\data\\logs\\app.log（便携版）
"""


class ToggleButton(tk.Button):
    """高对比度复选按钮：整行可点，屏幕阅读器能读到勾选状态。"""

    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.BooleanVar,
        *,
        command: Callable[[], Any] | None = None,
        font: tuple | None = None,
        padx: int = 10,
        pady: int = 4,
    ) -> None:
        self.label = label
        self.variable = variable
        self._command = command
        super().__init__(
            parent,
            text="",
            command=self._toggle,
            anchor="w",
            bg=SURFACE,
            fg=TEXT,
            activebackground=SURFACE_2,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=2,
            highlightbackground=BG,
            highlightcolor=ACCENT,
            takefocus=True,
            cursor="hand2",
            font=font or ("Microsoft YaHei UI", 9),
            padx=padx,
            pady=pady,
        )
        self.bind("<FocusIn>", lambda _e: self.configure(highlightbackground=ACCENT))
        self.bind("<FocusOut>", lambda _e: self.configure(highlightbackground=BG))
        self.variable.trace_add("write", lambda *_a: self.refresh())
        self.refresh()

    def _toggle(self) -> None:
        self.variable.set(not bool(self.variable.get()))
        if self._command:
            self._command()

    def refresh(self) -> None:
        selected = bool(self.variable.get())
        if selected:
            self.configure(
                text="✓  " + self.label,
                bg="#2A2410",
                fg=ACCENT,
                activebackground="#3A3115",
                activeforeground=ACCENT,
            )
        else:
            self.configure(
                text="○  " + self.label,
                bg=SURFACE,
                fg=TEXT,
                activebackground=SURFACE_2,
                activeforeground=TEXT,
            )


class ChoiceButton(tk.Button):
    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.Variable,
        value: Any,
        refresh_group: Callable[[], None],
        *,
        padx: int = 12,
        pady: int = 4,
    ) -> None:
        self.label = label
        self.variable = variable
        self.value = value
        self._refresh_group = refresh_group
        super().__init__(
            parent,
            text="",
            command=self._choose,
            bg=SURFACE,
            fg=TEXT,
            activebackground=SURFACE_2,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=2,
            highlightbackground=BG,
            highlightcolor=ACCENT,
            takefocus=True,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
            padx=padx,
            pady=pady,
        )
        self.bind("<FocusIn>", lambda _e: self.configure(highlightbackground=ACCENT))
        self.bind("<FocusOut>", lambda _e: self.configure(highlightbackground=BG))

    def _choose(self) -> None:
        self.variable.set(self.value)
        self._refresh_group()

    def refresh(self) -> None:
        selected = str(self.variable.get()) == str(self.value)
        if selected:
            self.configure(
                text="●  " + self.label, bg=ACCENT, fg=ACCENT_TEXT,
                activebackground=ACCENT_HOVER, activeforeground=ACCENT_TEXT,
            )
        else:
            self.configure(
                text="○  " + self.label, bg=SURFACE, fg=TEXT,
                activebackground=SURFACE_2, activeforeground=TEXT,
            )


class PresetRow(tk.Frame):
    """一组大按钮，代替难操作的滑块。"""

    def __init__(
        self,
        parent: tk.Misc,
        options: list[tuple[str, float]],
        initial: float,
        *,
        suffix: str = "",
        on_change: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=BG)
        self.options = options
        self.on_change = on_change
        self.var = tk.StringVar(value=str(initial))
        self._buttons: list[ChoiceButton] = []
        for label, value in options:
            btn = ChoiceButton(
                self, label, self.var, str(value), self.refresh,
                padx=10, pady=3,
            )
            btn.pack(side="left", padx=(0, 6))
            self._buttons.append(btn)
        self.set(initial, notify=False)

    def set(self, value: float, notify: bool = True) -> None:
        closest = min(self.options, key=lambda item: abs(float(item[1]) - float(value)))
        self.var.set(str(closest[1]))
        self.refresh()
        if notify and self.on_change:
            self.on_change(float(closest[1]))

    def get(self) -> float:
        try:
            return float(self.var.get())
        except Exception:
            return float(self.options[0][1])

    def refresh(self) -> None:
        for btn in self._buttons:
            btn.refresh()


def _frame_hwnd(win: tk.Misc) -> int:
    """Tk 的 winfo_id 是客户区子窗口；DWM 和截屏排除需要顶层框架。"""
    try:
        return int(win.wm_frame(), 16)  # type: ignore[attr-defined]
    except Exception:
        try:
            return int(win.winfo_id())
        except Exception:
            return 0


def _make_button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], Any],
    *,
    kind: str = "dark",
    font: tuple | None = None,
    height: int = 1,
    padx: int = 16,
    pady: int = 9,
    anchor: str = "center",
    focus_bg: str | None = None,
) -> tk.Button:
    colors = {
        "accent": (ACCENT, ACCENT_TEXT, ACCENT_HOVER),
        "blue": (BLUE, "#FFFFFF", BLUE_HOVER),
        "dark": (SURFACE_2, TEXT, "#263646"),
        "danger": (RED, "#1A0808", RED_HOVER),
        "ghost": (BG, MUTED, SURFACE_2),
    }
    bg, fg, active = colors.get(kind, colors["dark"])
    parent_bg = focus_bg
    if parent_bg is None:
        try:
            parent_bg = str(parent.cget("bg"))
        except Exception:
            parent_bg = BG
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=fg,
        disabledforeground=DIM,
        font=font or ("Microsoft YaHei UI", 10, "bold"),
        relief="flat",
        bd=0,
        highlightthickness=2,
        highlightbackground=parent_bg,
        highlightcolor=ACCENT,
        takefocus=True,
        cursor="hand2",
        padx=padx,
        pady=pady,
        height=height,
        anchor=anchor,
    )
    button.bind(
        "<FocusIn>",
        lambda _e, b=button: b.configure(highlightbackground=ACCENT),
    )
    button.bind(
        "<FocusOut>",
        lambda _e, b=button, c=parent_bg: b.configure(highlightbackground=c),
    )
    return button


def _entry(parent: tk.Misc, textvariable: tk.Variable, *, show: str = "") -> tk.Entry:
    entry = tk.Entry(
        parent,
        textvariable=textvariable,
        show=show,
        bg=FIELD,
        fg=TEXT,
        insertbackground=TEXT,
        disabledbackground=FIELD,
        disabledforeground=DIM,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        font=("Microsoft YaHei UI", 10),
    )
    return entry


def _scrollbar(parent: tk.Misc, command: Callable[..., Any]) -> tk.Scrollbar:
    return tk.Scrollbar(
        parent,
        orient="vertical",
        command=command,
        bg=SURFACE_2,
        troughcolor=FIELD,
        activebackground=ACCENT,
        highlightthickness=0,
        bd=0,
        relief="flat",
        width=14,
    )


def _label(parent: tk.Misc, text: str, *, size: int = 10, bold: bool = False,
           fg: str = TEXT, bg: str | None = None, **kwargs) -> tk.Label:
    if bg is None:
        try:
            bg = str(parent.cget("bg"))
        except Exception:
            bg = BG
    return tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=fg,
        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
        anchor="w",
        justify="left",
        **kwargs,
    )


class MiniBar:
    def __init__(self, app: "HearScreenApp") -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.title("听见画面 · 迷你悬浮条")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.97)
        self.win.configure(bg=BORDER)
        self.scale = app.scale
        self._drag = None

        frame = tk.Frame(self.win, bg=SURFACE, padx=app.px(14), pady=app.px(9))
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        top = tk.Frame(frame, bg=SURFACE)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=app.px(16), height=app.px(16), bg=SURFACE,
                             highlightthickness=0)
        self.dot.pack(side="left", padx=(0, app.px(7)))
        self._dot = self.dot.create_oval(
            app.px(3), app.px(3), app.px(13), app.px(13), fill=DIM, outline=""
        )
        self.status = _label(top, "待机", size=11, bold=True, bg=SURFACE)
        self.status.pack(side="left")
        self.detail = _label(top, "Ctrl+Alt+S 开始", size=9, fg=MUTED, bg=SURFACE)
        self.detail.pack(side="left", padx=(app.px(10), 0))
        tk.Label(top, text="听见画面", bg=SURFACE, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="right")

        bottom = tk.Frame(frame, bg=SURFACE)
        bottom.pack(fill="x", pady=(app.px(7), 0))
        self.pause_btn = _make_button(
            bottom, "暂停", app.pause_live, kind="dark",
            font=("Microsoft YaHei UI", 9, "bold"), padx=app.px(12), pady=app.px(3),
        )
        self.pause_btn.pack(side="left")
        self.stop_btn = _make_button(
            bottom, "停止", app.stop_live, kind="dark",
            font=("Microsoft YaHei UI", 9, "bold"), padx=app.px(12), pady=app.px(3),
        )
        self.stop_btn.pack(side="left", padx=(app.px(6), 0))
        self.main_btn = _make_button(
            bottom, "主界面", app.show_main, kind="accent",
            font=("Microsoft YaHei UI", 9, "bold"), padx=app.px(12), pady=app.px(3),
        )
        self.main_btn.pack(side="right")

        for widget in (frame, top, self.status, self.detail):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)

        width = app.px(560)
        height = app.px(102)
        screen_w = app.root.winfo_screenwidth()
        screen_h = app.root.winfo_screenheight()
        x = app.config.get("mini_x")
        y = app.config.get("mini_y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            x = screen_w - width - app.px(34)
            y = screen_h - height - app.px(74)
        self.win.geometry(f"{width}x{height}+{int(x)}+{int(y)}")
        self.win.update_idletasks()
        winapi.set_capture_exclusion(_frame_hwnd(self.win), True)

    # ------------------------------------------------------------------ 拖动

    def _start_drag(self, event) -> None:
        self._drag = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _on_drag(self, event) -> None:
        if not self._drag:
            return
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self.win.geometry(f"+{x}+{y}")

    def _end_drag(self, _event) -> None:
        if self.win.winfo_ismapped():
            self.app.config.set("mini_x", self.win.winfo_x())
            self.app.config.set("mini_y", self.win.winfo_y())
        self._drag = None

    # ------------------------------------------------------------------ 显示

    def show(self) -> None:
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.win.lift()

    def hide(self) -> None:
        self.win.withdraw()

    def visible(self) -> bool:
        return bool(self.win.winfo_ismapped())

    def update(self, state: str, title: str, detail: str) -> None:
        color = STATE_STYLE.get(state, (DIM, ""))[0]
        try:
            self.dot.itemconfigure(self._dot, fill=color)
            self.status.configure(text=title)
            text = detail.split("。", 1)[0]
            if len(text) > 30:
                text = text[:29] + "…"
            self.detail.configure(text=text)
            if self.app.narrator.live_active:
                self.pause_btn.configure(text="继续" if self.app.narrator.paused else "暂停")
                self.stop_btn.configure(state="normal")
            else:
                self.pause_btn.configure(text="暂停", state="disabled")
                self.stop_btn.configure(state="disabled")
        except Exception:
            log.debug("更新悬浮条失败", exc_info=True)


class HearScreenApp:
    def __init__(self, root: tk.Tk, config: Config, speech: SpeechEngine) -> None:
        self.root = root
        self.config = config
        self.speech = speech
        self.events: queue.Queue[tuple] = queue.Queue()
        self.scale = max(0.82, winapi.get_dpi_for_window(root.winfo_id()) / 96.0)
        self._closing = False
        self.settings_window: SettingsWindow | None = None
        self.mini: MiniBar | None = None
        self.current_caption = ""
        self.caption_meta: dict[str, Any] = {}
        self.history: list[str] = []
        self.hotkeys: winapi.HotkeyManager | None = None
        self._last_status = ("idle", "待机", "")

        self._setup_root()
        self._setup_style()
        self._build_main()
        self._start_after_id: str | None = None
        self.narrator = Narrator(
            config,
            speech,
            on_state=lambda s, t, d: self.events.put(("state", s, t, d)),
            on_caption=lambda text, meta: self.events.put(("caption", text, meta)),
            on_error=lambda msg, fatal: self.events.put(("error", msg, fatal)),
        )
        speech.set_callbacks(
            on_error=lambda msg: self.events.put(("speech_error", msg)),
            on_speaking=lambda speaking: self.events.put(("speech", speaking)),
        )
        if not speech.available and speech.error:
            self.events.put(("speech_error", speech.error))
        self._start_hotkeys()
        self._update_ai_pill()
        self.root.after(60, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<F1>", lambda _e: self.open_help())
        self.root.bind("<Control-comma>", lambda _e: self.open_settings())
        self.root.bind("<Escape>", self._on_escape)

        if config.get("first_run", True):
            config.set("first_run", False)
            try:
                config.save()
            except Exception:
                log.exception("保存首次运行标记失败")
            self.root.after(700, self._speak_welcome)
        if config.get("auto_start", False):
            self.root.after(1200, self.start_live)

    # ------------------------------------------------------------------ 基础

    def px(self, value: int | float) -> int:
        return int(round(float(value) * self.scale))

    def _setup_root(self) -> None:
        self.root.title(f"{APP_NAME} · AI 视频解说")
        self.root.configure(bg=BG)
        dpi = 96
        try:
            dpi = winapi.get_dpi_for_window(self.root.winfo_id())
        except Exception:
            pass
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # 小屏幕（例如 1366×768 开了 125% 缩放）时整体缩小，避免底部按钮被裁掉。
        fit = min(
            (screen_w - 48) / 940.0,
            (screen_h - 96) / 700.0,
        )
        self.scale = max(0.82, min(max(0.82, dpi / 96.0), fit))
        try:
            self.root.tk.call("tk", "scaling", 96.0 * self.scale / 72.0)
        except Exception:
            pass
        width = self.px(940)
        height = self.px(700)
        width = min(width, max(self.px(720), screen_w - self.px(32)))
        height = min(height, max(self.px(560), screen_h - self.px(72)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - self.px(12))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(self.px(760), self.px(560))
        self.root.update_idletasks()
        try:
            frame = _frame_hwnd(self.root)
            winapi.set_capture_exclusion(frame, True)
            winapi.set_dark_titlebar(frame)
        except Exception:
            pass
        if self.config.get("always_on_top"):
            self.root.attributes("-topmost", True)

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        family = "Microsoft YaHei UI"
        try:
            if family not in tkfont.families(self.root):
                family = "Microsoft YaHei"
        except Exception:
            family = "Segoe UI"
        self.font_family = family
        style.configure(".", background=BG, foreground=TEXT, font=(family, 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=(family, 10))
        style.configure(
            "TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0)
        )
        style.configure(
            "TNotebook.Tab",
            background=SURFACE,
            foreground=MUTED,
            padding=(self.px(18), self.px(9)),
            font=(family, 10, "bold"),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", SURFACE_2)],
            foreground=[("selected", ACCENT)],
        )
        style.configure(
            "TCombobox",
            fieldbackground=FIELD,
            background=SURFACE_2,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=self.px(6),
            font=(family, 10),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD), ("disabled", FIELD)],
            foreground=[("readonly", TEXT), ("disabled", DIM)],
            selectbackground=[("readonly", FIELD)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT)],
        )
        style.configure(
            "TCheckbutton",
            background=BG,
            foreground=TEXT,
            font=(family, 10),
            focuscolor=ACCENT,
            indicatorcolor=FIELD,
        )
        style.map(
            "TCheckbutton",
            background=[("active", BG)],
            foreground=[("active", TEXT)],
            indicatorcolor=[("selected", ACCENT)],
        )
        style.configure(
            "TRadiobutton",
            background=BG,
            foreground=TEXT,
            font=(family, 10),
            focuscolor=ACCENT,
            indicatorcolor=FIELD,
        )
        style.map(
            "TRadiobutton",
            background=[("active", BG)],
            foreground=[("active", TEXT)],
            indicatorcolor=[("selected", ACCENT)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=SURFACE_2,
            troughcolor=FIELD,
            bordercolor=FIELD,
            arrowcolor=MUTED,
        )
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", BLUE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.font", (family, 10))

    # ------------------------------------------------------------------ 主界面

    def _build_main(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=self.px(24), pady=self.px(16))
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        logo = tk.Canvas(
            header, width=self.px(54), height=self.px(54), bg=BG,
            highlightthickness=0,
        )
        logo.pack(side="left")
        self._draw_logo(logo)
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", padx=(self.px(13), 0))
        _label(title_box, APP_NAME, size=21, bold=True).pack(anchor="w")
        _label(title_box, APP_TAGLINE, size=9, fg=MUTED).pack(anchor="w",
                                                              pady=(self.px(2), 0))
        right = tk.Frame(header, bg=BG)
        right.pack(side="right")
        self.ai_pill = tk.Label(
            right,
            text="豆包：未配置",
            bg=SURFACE,
            fg=MUTED,
            font=(self.font_family, 9),
            padx=self.px(10),
            pady=self.px(5),
        )
        self.ai_pill.pack(anchor="e")
        _label(right, f"v{APP_VERSION}", size=8, fg=DIM).pack(anchor="e",
                                                              pady=(self.px(4), 0))

        status_card = tk.Frame(
            outer, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1,
            padx=self.px(18), pady=self.px(13),
        )
        status_card.pack(fill="x", pady=(self.px(13), 0))
        self.status_dot = tk.Canvas(
            status_card, width=self.px(22), height=self.px(50), bg=SURFACE,
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, self.px(10)))
        self.dot_item = self.status_dot.create_oval(
            self.px(5), self.px(20), self.px(17), self.px(32), fill=DIM, outline=""
        )
        status_text = tk.Frame(status_card, bg=SURFACE)
        status_text.pack(side="left", fill="both", expand=True)
        self.state_title = _label(status_text, "待机", size=16, bold=True, bg=SURFACE)
        self.state_title.pack(anchor="w")
        self.state_detail = _label(
            status_text,
            "按 Ctrl+Alt+S 开始实时解说，或按 Ctrl+Alt+D 描述当前画面。",
            size=9,
            fg=MUTED,
            bg=SURFACE,
            wraplength=self.px(600),
        )
        self.state_detail.pack(anchor="w", pady=(self.px(2), 0))
        status_card.bind(
            "<Configure>",
            lambda e: self.state_detail.configure(
                wraplength=max(self.px(260), e.width - self.px(90))
            ),
        )
        self.status_shortcut = _label(
            status_card, "Ctrl+Alt+S", size=9, fg=DIM, bg=SURFACE
        )
        self.status_shortcut.pack(side="right", anchor="n")

        caption_card = tk.Frame(
            outer, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1,
        )
        caption_card.pack(fill="both", expand=True, pady=(self.px(11), 0))
        cap_head = tk.Frame(caption_card, bg=SURFACE)
        cap_head.pack(fill="x", padx=self.px(14), pady=(self.px(9), 0))
        _label(cap_head, "最近一次解说", size=9, bold=True, fg=MUTED,
               bg=SURFACE).pack(side="left")
        self.replay_btn = _make_button(
            cap_head, "重听", self.replay_caption, kind="ghost",
            font=(self.font_family, 8, "bold"), padx=self.px(8), pady=self.px(2),
            focus_bg=SURFACE,
        )
        self.replay_btn.pack(side="right")
        self.caption_meta_label = _label(
            cap_head, "", size=8, fg=DIM, bg=SURFACE
        )
        self.caption_meta_label.pack(side="right", padx=(0, self.px(8)))

        text_box = tk.Frame(caption_card, bg=FIELD)
        text_box.pack(fill="both", expand=True, padx=self.px(10),
                      pady=(self.px(6), self.px(8)))
        self.caption_text = tk.Text(
            text_box,
            height=3,
            wrap="word",
            bg=FIELD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(self.font_family, 11),
            padx=self.px(10),
            pady=self.px(7),
            spacing1=self.px(2),
            spacing3=self.px(2),
            state="disabled",
            cursor="arrow",
        )
        self.caption_text.pack(side="left", fill="both", expand=True)
        self.caption_text.bind(
            "<MouseWheel>",
            lambda e: self.caption_text.yview_scroll(int(-e.delta / 120), "units"),
        )
        self._set_caption(
            "解说文字会显示在这里，同时通过扬声器朗读。按 Ctrl+Alt+D 可以先试一次。"
        )

        self.primary_btn = _make_button(
            outer,
            "开始实时解说     Ctrl+Alt+S",
            self.toggle_live,
            kind="accent",
            font=(self.font_family, 13, "bold"),
            height=1,
            pady=self.px(12),
        )
        self.primary_btn.pack(fill="x", pady=(self.px(13), 0))

        secondary = tk.Frame(outer, bg=BG)
        secondary.pack(fill="x", pady=(self.px(8), 0))
        self.once_btn = _make_button(
            secondary, "描述当前画面   Ctrl+Alt+D", self.describe_once,
            kind="blue", font=(self.font_family, 10, "bold"),
            pady=self.px(9),
        )
        self.once_btn.pack(side="left", fill="x", expand=True)
        self.pause_btn = _make_button(
            secondary, "暂停   Ctrl+Alt+P", self.pause_live,
            kind="dark", font=(self.font_family, 10, "bold"),
            pady=self.px(9),
        )
        self.pause_btn.pack(side="left", fill="x", expand=True,
                            padx=(self.px(8), 0))
        self.stop_btn = _make_button(
            secondary, "停止朗读   Ctrl+Alt+X", self.stop_speaking,
            kind="dark", font=(self.font_family, 10, "bold"),
            pady=self.px(9),
        )
        self.stop_btn.pack(side="left", fill="x", expand=True,
                           padx=(self.px(8), 0))

        capture_row = tk.Frame(outer, bg=BG)
        capture_row.pack(fill="x", pady=(self.px(12), 0))
        _label(capture_row, "解说范围", size=9, fg=MUTED).pack(side="left")
        self.capture_var = tk.StringVar(
            value=CAPTURE_LABELS.get(self.config.get("capture_mode"), CAPTURE_LABELS["window"])
        )
        self.capture_combo = ttk.Combobox(
            capture_row,
            textvariable=self.capture_var,
            values=list(CAPTURE_LABELS.values()),
            state="readonly",
            width=24,
            takefocus=True,
        )
        self.capture_combo.pack(side="left", padx=(self.px(8), 0))
        self.capture_combo.bind("<<ComboboxSelected>>", self._on_capture_changed)
        self.region_btn = _make_button(
            capture_row, "框选区域", self.select_region, kind="dark",
            font=(self.font_family, 9, "bold"), padx=self.px(10), pady=self.px(4),
        )
        self.region_btn.pack(side="left", padx=(self.px(7), 0))
        self.region_hint = _label(capture_row, self._region_hint(), size=8, fg=DIM)
        self.region_hint.pack(side="right")

        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", pady=(self.px(10), 0))
        _make_button(
            footer, "设置", self.open_settings, kind="dark",
            font=(self.font_family, 9, "bold"), padx=self.px(12), pady=self.px(4),
        ).pack(side="left")
        _make_button(
            footer, "迷你悬浮条", self.toggle_mini, kind="dark",
            font=(self.font_family, 9, "bold"), padx=self.px(12), pady=self.px(4),
        ).pack(side="left", padx=(self.px(6), 0))
        _make_button(
            footer, "退出", self.on_close, kind="ghost",
            font=(self.font_family, 9), padx=self.px(10), pady=self.px(4),
        ).pack(side="right")
        _make_button(
            footer, "使用说明  F1", self.open_help, kind="ghost",
            font=(self.font_family, 9), padx=self.px(10), pady=self.px(4),
        ).pack(side="right", padx=(0, self.px(4)))

        self.hotkey_note = _label(
            outer,
            "全局快捷键：Ctrl+Alt+S 开始/停止 · Ctrl+Alt+D 描述 · "
            "Ctrl+Alt+P 暂停 · Ctrl+Alt+X 停止朗读 · Ctrl+Alt+M 悬浮条",
            size=7,
            fg=DIM,
        )
        self.hotkey_note.pack(fill="x", pady=(self.px(7), 0))

    def _draw_logo(self, canvas: tk.Canvas) -> None:
        s = self.px
        canvas.create_oval(s(1), s(1), s(53), s(53), fill=SURFACE_2, outline=BORDER)
        points = [
            s(10), s(27), s(19), s(18), s(27), s(16), s(35), s(18), s(44), s(27),
            s(35), s(36), s(27), s(38), s(19), s(36),
        ]
        canvas.create_polygon(
            points, smooth=True, outline=ACCENT, fill="#33290C", width=2
        )
        canvas.create_oval(s(23), s(22), s(31), s(32), fill=ACCENT, outline="")
        for i, (x0, extent) in enumerate(((47, 50), (50, 70))):
            canvas.create_arc(
                s(x0 - 10), s(18 - i * 3), s(x0 + 2), s(36 + i * 3),
                start=300, extent=extent, style="arc", outline=ACCENT, width=2,
            )

    # ------------------------------------------------------------------ 状态

    def _set_status(self, state: str, title: str, detail: str) -> None:
        self._last_status = (state, title, detail)
        color = STATE_STYLE.get(state, (DIM, ""))[0]
        try:
            self.status_dot.itemconfigure(self.dot_item, fill=color)
            self.state_title.configure(text=title)
            self.state_detail.configure(text=detail)
            if state == "error":
                self.state_title.configure(fg=RED)
            else:
                self.state_title.configure(fg=TEXT)
            self.status_shortcut.configure(
                text="Ctrl+Alt+P" if state == "paused" else "Ctrl+Alt+S"
            )
        except Exception:
            log.debug("更新状态失败", exc_info=True)
        self._sync_live_buttons()
        if self.mini:
            self.mini.update(state, title, detail)

    def _sync_live_buttons(self) -> None:
        active = bool(getattr(self, "narrator", None) and self.narrator.live_active)
        paused = bool(getattr(self, "narrator", None) and self.narrator.paused)
        try:
            if active:
                self.primary_btn.configure(
                    text="停止实时解说     Ctrl+Alt+S",
                    bg="#3A1D22",
                    fg="#FFB4B4",
                    activebackground="#4A252B",
                    activeforeground="#FFD0D0",
                )
            else:
                self.primary_btn.configure(
                    text="开始实时解说     Ctrl+Alt+S",
                    bg=ACCENT,
                    fg=ACCENT_TEXT,
                    activebackground=ACCENT_HOVER,
                    activeforeground=ACCENT_TEXT,
                )
            if active:
                self.pause_btn.configure(
                    text=("继续   Ctrl+Alt+P" if paused else "暂停   Ctrl+Alt+P"),
                    state="normal",
                )
            else:
                self.pause_btn.configure(text="暂停   Ctrl+Alt+P", state="normal")
        except Exception:
            log.debug("同步按钮失败", exc_info=True)

    def _set_caption(self, text: str) -> None:
        self.current_caption = text
        try:
            self.caption_text.configure(state="normal")
            self.caption_text.delete("1.0", "end")
            if text:
                self.caption_text.insert("1.0", text)
            self.caption_text.configure(state="disabled")
            self.caption_text.yview_moveto(0)
        except Exception:
            log.debug("更新字幕失败", exc_info=True)

    def _update_caption_meta(self, meta: dict[str, Any]) -> None:
        parts: list[str] = []
        source = str(meta.get("source") or "")
        if source:
            parts.append(source)
        if meta.get("index"):
            parts.append(f"第 {meta['index']} 次")
        if meta.get("latency"):
            parts.append(f"{float(meta['latency']):.1f} 秒")
        if meta.get("skipped") == "black":
            parts.append("画面过暗")
        self.caption_meta_label.configure(text=" · ".join(parts))

    def _speak_welcome(self) -> None:
        if not self.speech.available:
            return
        if self.config.api_key:
            text = ("听见画面已启动。按 Control Alt S 开始实时解说，"
                    "按 F1 可以收听使用说明。")
        else:
            text = ("欢迎使用听见画面。还没有填写 API Key。"
                    "请按 F1 查看设置步骤，或在设置中填写。")
        self.speech.speak(text, interrupt=True)

    # ------------------------------------------------------------------ 操作

    def toggle_live(self) -> None:
        if self.narrator.live_active:
            self.stop_live()
        else:
            self.start_live()

    def start_live(self) -> None:
        if self._start_after_id:
            return
        self.config.set("capture_mode", CAPTURE_KEYS.get(
            self.capture_var.get(), self.config.get("capture_mode")))
        try:
            self.config.save()
        except Exception:
            log.exception("保存配置失败")
        if not self.config.api_key:
            self._set_status(
                "error", "还没有填写 API Key",
                "请先在“设置 → AI 服务”中填写豆包或兼容服务的 API Key。",
            )
            self.speech.speak("还没有填写 A P I Key。请先打开设置填写。", interrupt=True)
            self.open_settings(initial_tab=1)
            return
        self.narrator.refresh_config()
        if self.config.get("hide_window", True):
            self.enter_mini()
            self._start_after_id = self.root.after(420, self._start_live_after_hide)
        else:
            self._start_live_after_hide()

    def _start_live_after_hide(self) -> None:
        self._start_after_id = None
        if self.narrator.start():
            winapi.beep(kind="start")
            self._sync_live_buttons()

    def stop_live(self) -> None:
        if self._start_after_id:
            try:
                self.root.after_cancel(self._start_after_id)
            except Exception:
                pass
            self._start_after_id = None
        was_active = self.narrator.live_active
        self.narrator.stop()
        if was_active:
            winapi.beep(kind="stop")
        self._sync_live_buttons()

    def pause_live(self) -> None:
        if not self.narrator.live_active:
            self._set_status(
                "idle", "还没有开始解说",
                "按 Ctrl+Alt+S 开始实时解说，然后再暂停。",
            )
            return
        self.narrator.pause()
        self._sync_live_buttons()

    def describe_once(self) -> None:
        if self.narrator.describe_once():
            winapi.beep(frequency=760, duration_ms=70)

    def stop_speaking(self) -> None:
        self.narrator.stop_speech()

    def replay_caption(self) -> None:
        if self.current_caption and not self.current_caption.startswith("（"):
            self.speech.speak(self.current_caption, interrupt=True)
        else:
            self._set_status("idle", "还没有可重听的解说",
                             "先按 Ctrl+Alt+D 描述一次当前画面。")

    # ------------------------------------------------------------------ 事件轮询

    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "state":
                    self._set_status(event[1], event[2], event[3])
                elif kind == "caption":
                    text, meta = event[1], event[2]
                    self._set_caption(text)
                    self._update_caption_meta(meta)
                    if not meta.get("skipped"):
                        self.history.append(text)
                        self.history = self.history[-20:]
                elif kind == "error":
                    msg, _fatal = event[1], event[2]
                    log.warning("界面收到错误：%s", msg)
                elif kind == "speech_error":
                    self._set_status("error", "语音不可用", event[1])
                elif kind == "hotkey_error":
                    self.hotkey_note.configure(
                        text="部分快捷键未能注册：" + "；".join(event[1])
                        + "。界面按钮仍然可用。",
                        fg=RED,
                    )
                elif kind == "speech":
                    if event[1]:
                        self.status_shortcut.configure(text="正在播报")
                    else:
                        self.status_shortcut.configure(text="Ctrl+Alt+S")
                elif kind == "hotkey":
                    self._handle_hotkey(event[1])
        except queue.Empty:
            pass
        self.root.after(60, self._poll_events)

    # ------------------------------------------------------------------ 热键

    def _start_hotkeys(self) -> None:
        bindings = {
            "toggle": [self.config.get("hotkey_toggle", DEFAULT_HOTKEYS["toggle"]),
                       "Ctrl+Alt+F9"],
            "once": [self.config.get("hotkey_once", DEFAULT_HOTKEYS["once"]),
                     "Ctrl+Alt+F10"],
            "pause": [self.config.get("hotkey_pause", DEFAULT_HOTKEYS["pause"])],
            "stop_speech": [self.config.get(
                "hotkey_stop_speech", DEFAULT_HOTKEYS["stop_speech"])],
            "mini": [self.config.get("hotkey_mini", DEFAULT_HOTKEYS["mini"]),
                     "Ctrl+Alt+B", "Ctrl+Alt+N"],
        }
        self.hotkeys = winapi.HotkeyManager(bindings, self._on_hotkey)
        self.hotkeys.start()
        self._refresh_hotkey_note()
        if self.hotkeys.errors:
            self.events.put(("hotkey_error", self.hotkeys.errors))

    def _refresh_hotkey_note(self) -> None:
        if not self.hotkeys:
            return
        reg = self.hotkeys.registered
        parts = []
        for action, label in (
            ("toggle", "开始/停止"), ("once", "描述"), ("pause", "暂停"),
            ("stop_speech", "停止朗读"), ("mini", "悬浮条"),
        ):
            if action in reg:
                parts.append(f"{reg[action]} {label}")
        if parts:
            self.hotkey_note.configure(
                text="全局快捷键：" + " · ".join(parts),
                fg=DIM,
            )

    def _on_hotkey(self, action: str) -> None:
        self.events.put(("hotkey", action))

    def _handle_hotkey(self, action: str) -> None:
        if self.settings_window and self.settings_window.alive:
            if action == "toggle":
                self.settings_window.close(save=False)
        if action == "toggle":
            self.toggle_live()
        elif action == "once":
            self.describe_once()
        elif action == "pause":
            self.pause_live()
        elif action == "stop_speech":
            self.stop_speaking()
        elif action == "mini":
            self.toggle_mini()

    # ------------------------------------------------------------------ 迷你条

    def enter_mini(self) -> None:
        if not self.mini:
            self.mini = MiniBar(self)
        state, title, detail = self._last_status
        self.mini.update(state, title, detail)
        self.mini.show()
        self.root.withdraw()

    def show_main(self) -> None:
        if self.mini:
            self.mini.hide()
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def toggle_mini(self) -> None:
        if self.mini and self.mini.visible():
            self.show_main()
        else:
            self.enter_mini()

    # ------------------------------------------------------------------ 框选

    def _on_capture_changed(self, _event=None) -> None:
        mode = CAPTURE_KEYS.get(self.capture_var.get(), "window")
        self.config.set("capture_mode", mode)
        self.config.save()
        self.narrator.refresh_config()
        self.region_hint.configure(text=self._region_hint())
        if mode == "region" and not self._has_region():
            self.root.after(100, self.select_region)

    def _has_region(self) -> bool:
        region = self.config.get("region") or []
        return (isinstance(region, list) and len(region) == 4
                and int(region[2]) - int(region[0]) > 10
                and int(region[3]) - int(region[1]) > 10)

    def _region_hint(self) -> str:
        if self.config.get("capture_mode") == "region" and self._has_region():
            return format_bbox(self.config.get("region"))
        return "只框住播放器可减少无关信息"

    def select_region(self) -> None:
        if getattr(self, "_selecting", False):
            return
        self._selecting = True
        self.speech.speak("请拖动鼠标框选视频画面，按 Escape 取消。", interrupt=True)
        self.root.withdraw()
        if self.mini:
            self.mini.hide()
        self.root.after(260, self._show_region_overlay)

    def _show_region_overlay(self) -> None:
        vx1, vy1, vx2, vy2 = winapi.get_virtual_screen()
        width, height = vx2 - vx1, vy2 - vy1
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.42)
        overlay.configure(bg="#000000")
        geometry = f"{width}x{height}+{vx1}+{vy1}"
        overlay.geometry(geometry)
        canvas = tk.Canvas(overlay, bg="#000000", highlightthickness=0,
                           cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        s = self.px
        canvas.create_rectangle(
            max(s(20), width // 2 - s(330)), s(26),
            min(width - s(20), width // 2 + s(330)), s(26) + s(70),
            fill="#101820", outline=ACCENT, width=2,
        )
        canvas.create_text(
            width // 2, s(45), text="拖动鼠标框选要解说的区域",
            fill="#FFFFFF", font=(self.font_family, 15, "bold"),
        )
        canvas.create_text(
            width // 2, s(75), text="建议只框住视频播放器 · 按 Esc 取消",
            fill="#E8EDF2", font=(self.font_family, 10),
        )
        overlay.update_idletasks()
        try:
            overlay.grab_set()
            overlay.focus_force()
        except Exception:
            pass

        state = {"start": None, "rect": None}
        done = {"value": False}

        def close(cancelled: bool = True) -> None:
            if done["value"]:
                return
            done["value"] = True
            self._selecting = False
            try:
                overlay.grab_release()
            except Exception:
                pass
            overlay.destroy()
            self.show_main()
            if cancelled:
                self._set_status(
                    "idle", "没有更改解说范围",
                    "按 Ctrl+Alt+S 开始，或重新点击“框选区域”。",
                )

        def on_press(event) -> None:
            state["start"] = (event.x_root, event.y_root)
            if state["rect"]:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                event.x_root - vx1, event.y_root - vy1,
                event.x_root - vx1, event.y_root - vy1,
                outline=ACCENT, width=3,
            )

        def on_drag(event) -> None:
            if not state["start"] or not state["rect"]:
                return
            x0, y0 = state["start"]
            canvas.coords(
                state["rect"],
                x0 - vx1, y0 - vy1,
                event.x_root - vx1, event.y_root - vy1,
            )

        def on_release(event) -> None:
            if not state["start"]:
                close(True)
                return
            x0, y0 = state["start"]
            x1, y1 = event.x_root, event.y_root
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            left = max(vx1, min(vx2 - 16, left))
            top = max(vy1, min(vy2 - 16, top))
            right = max(vx1 + 16, min(vx2, right))
            bottom = max(vy1 + 16, min(vy2, bottom))
            if right - left < 40 or bottom - top < 30:
                close(True)
                return
            region = [int(left), int(top), int(right), int(bottom)]
            self.config.set("region", region)
            self.config.set("capture_mode", "region")
            self.config.save()
            self.capture_var.set(CAPTURE_LABELS["region"])
            self.region_hint.configure(text=format_bbox(region))
            self.narrator.refresh_config()
            self._selecting = False
            try:
                overlay.grab_release()
            except Exception:
                pass
            overlay.destroy()
            self.show_main()
            self._set_status(
                "idle", "已设置解说区域",
                f"{format_bbox(region)}。按 Ctrl+Alt+S 开始实时解说。",
            )
            self.speech.speak("区域已设置。按 Control Alt S 开始解说。", interrupt=True)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", lambda _e: close(True))
        overlay.protocol("WM_DELETE_WINDOW", lambda: close(True))
        overlay.after(100, lambda: overlay.focus_force())

    # ------------------------------------------------------------------ 设置/帮助

    def open_settings(self, initial_tab: int = 0) -> None:
        if self.settings_window and self.settings_window.alive:
            self.settings_window.lift()
            self.settings_window.notebook.select(
                max(0, min(3, int(initial_tab)))
            )
            return
        self.settings_window = SettingsWindow(
            self.root, self.config, self.speech, self.narrator,
            on_saved=self._on_settings_saved,
            initial_tab=initial_tab,
            scale=self.scale,
        )

    def open_help(self) -> None:
        self.open_settings(initial_tab=3)

    def _on_settings_saved(self) -> None:
        self._update_ai_pill()
        self._sync_live_buttons()
        self.region_hint.configure(text=self._region_hint())
        self.capture_var.set(CAPTURE_LABELS.get(
            self.config.get("capture_mode"), CAPTURE_LABELS["window"]))
        if self.config.get("always_on_top"):
            self.root.attributes("-topmost", True)
        else:
            self.root.attributes("-topmost", False)
        if not self.narrator.live_active:
            self._set_status(
                "idle", "设置已保存",
                f"观察间隔 {self.config.get('interval', 4.5):g} 秒。"
                "按 Ctrl+Alt+S 开始实时解说。",
            )

    def _update_ai_pill(self) -> None:
        if self.config.api_key:
            label = "豆包：已配置" if self.config.get("provider") == PROVIDER_DOUBAO else "AI：已配置"
            self.ai_pill.configure(text=label, fg=GREEN, bg="#12251C")
        else:
            self.ai_pill.configure(text="AI：未配置 API Key", fg=ACCENT, bg="#2A2410")

    def _on_escape(self, _event=None) -> None:
        if self.mini and self.mini.visible():
            self.show_main()

    # ------------------------------------------------------------------ 退出

    def on_close(self) -> None:
        if self._closing:
            return
        if self.narrator.live_active:
            try:
                if not messagebox.askyesno(
                    "退出听见画面", "实时解说正在进行。确定要退出吗？",
                    parent=self.root,
                ):
                    return
            except Exception:
                pass
        self._closing = True
        try:
            self.narrator.shutdown()
        except Exception:
            log.exception("停止解说失败")
        try:
            if self.hotkeys:
                self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.speech.shutdown()
        except Exception:
            pass
        try:
            self.config.save()
        except Exception:
            log.exception("保存配置失败")
        try:
            self.root.destroy()
        except Exception:
            pass


class SettingsWindow:
    def __init__(
        self,
        parent: tk.Tk,
        config: Config,
        speech: SpeechEngine,
        narrator: Narrator,
        *,
        on_saved: Callable[[], None],
        initial_tab: int = 0,
        scale: float | None = None,
    ) -> None:
        self.parent = parent
        self.config = config
        self.speech = speech
        self.narrator = narrator
        self.on_saved = on_saved
        self.alive = True
        self.scale = max(0.82, float(scale or 1.0))
        self.font_family = "Microsoft YaHei UI"
        try:
            if self.font_family not in tkfont.families(parent):
                self.font_family = "Microsoft YaHei"
        except Exception:
            self.font_family = "Segoe UI"
        self._test_queue: queue.Queue[tuple] = queue.Queue()
        self._test_running = False
        self._voices = speech.voices()
        self._voice_choices: list[tuple[str, str]] = [("", "系统默认（自动选择中文）")]
        for voice in self._voices:
            self._voice_choices.append((voice["id"], voice["name"]))

        self.win = tk.Toplevel(parent)
        self.win.title("听见画面 · 设置")
        self.win.configure(bg=BG)
        self.win.transient(parent)
        width = self.px(840)
        height = self.px(660)
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        width = min(width, max(self.px(700), sw - self.px(40)))
        height = min(height, max(self.px(520), sh - self.px(80)))
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2 - self.px(10))
        self.win.geometry(f"{width}x{height}+{x}+{y}")
        self.win.minsize(self.px(720), self.px(520))
        self.win.protocol("WM_DELETE_WINDOW", lambda: self.close(save=False))
        self.win.bind("<Escape>", lambda _e: self.close(save=False))

        self._build_header()
        self._build_notebook()
        self._build_footer()
        self.win.update_idletasks()
        try:
            frame = _frame_hwnd(self.win)
            winapi.set_capture_exclusion(frame, True)
            winapi.set_dark_titlebar(frame)
            self.win.grab_set()
            self.win.focus_force()
        except Exception:
            pass
        self.notebook.select(max(0, min(3, int(initial_tab))))
        self.win.after(120, self._poll_test_queue)
        if int(initial_tab) == 1:
            self.win.after(160, lambda: self.api_key_entry.focus_set())
        elif int(initial_tab) == 3:
            self.win.after(160, lambda: self.help_text.focus_set())
        else:
            self.win.after(160, lambda: self.notebook.focus_set())

    def px(self, value: int | float) -> int:
        return int(round(float(value) * self.scale))

    # ------------------------------------------------------------------ 框架

    def _build_header(self) -> None:
        header = tk.Frame(self.win, bg=BG, padx=self.px(24), pady=self.px(14))
        header.pack(fill="x")
        _label(header, "设置", size=18, bold=True).pack(anchor="w")
        _label(
            header, "所有设置都保存在本机。API Key 会加密保存，不会写进日志。",
            size=9, fg=MUTED,
        ).pack(anchor="w", pady=(self.px(2), 0))

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self.win)
        self.notebook.pack(fill="both", expand=True, padx=self.px(20),
                           pady=(self.px(2), self.px(4)))
        self.tab_guide = tk.Frame(self.notebook, bg=BG)
        self.tab_ai = tk.Frame(self.notebook, bg=BG)
        self.tab_voice = tk.Frame(self.notebook, bg=BG)
        self.tab_help = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_guide, text="解说")
        self.notebook.add(self.tab_ai, text="AI 服务")
        self.notebook.add(self.tab_voice, text="语音")
        self.notebook.add(self.tab_help, text="帮助")
        self._build_guide_tab()
        self._build_ai_tab()
        self._build_voice_tab()
        self._build_help_tab()

    def _build_footer(self) -> None:
        footer = tk.Frame(self.win, bg=BG, padx=self.px(24), pady=self.px(12))
        footer.pack(fill="x")
        self.save_btn = _make_button(
            footer, "保存并关闭", self.save, kind="accent",
            font=(self.font_family, 10, "bold"), padx=self.px(20),
            pady=self.px(7),
        )
        self.save_btn.pack(side="right")
        _make_button(
            footer, "取消", lambda: self.close(save=False), kind="dark",
            font=(self.font_family, 10), padx=self.px(18), pady=self.px(7),
        ).pack(side="right", padx=(0, self.px(8)))
        self.footer_note = _label(
            footer, f"版本 {APP_VERSION} · 配置与日志保存在程序目录的 data 文件夹",
            size=8, fg=DIM,
        )
        self.footer_note.pack(side="left")

    # ------------------------------------------------------------------ 解说页

    def _build_guide_tab(self) -> None:
        page = self.tab_guide
        inner = tk.Frame(page, bg=BG, padx=self.px(18), pady=self.px(14))
        inner.pack(fill="both", expand=True)

        interval_head = tk.Frame(inner, bg=BG)
        interval_head.pack(fill="x")
        _label(interval_head, "观察间隔", size=10, bold=True).pack(side="left")
        self.interval_label = _label(interval_head, "", size=9, fg=MUTED)
        self.interval_label.pack(side="right")

        def interval_changed(value: float) -> None:
            self.interval_label.configure(text=f"每 {value:g} 秒观察一次")

        self.interval_row = PresetRow(
            inner,
            [("3 秒", 3.0), ("4.5 秒", 4.5), ("6 秒", 6.0), ("8 秒", 8.0)],
            float(self.config.get("interval", 4.5)),
            on_change=interval_changed,
        )
        self.interval_row.pack(anchor="w", pady=(self.px(5), self.px(3)))
        interval_changed(self.interval_row.get())
        _label(
            inner, "实时追剧建议 4～5 秒；想看得更细可以选 6 或 8 秒。",
            size=8, fg=DIM,
        ).pack(anchor="w")

        _label(inner, "解说详细程度", size=10, bold=True).pack(
            anchor="w", pady=(self.px(12), self.px(4))
        )
        self.style_var = tk.StringVar(value=str(self.config.get("style", "concise")))
        style_row = tk.Frame(inner, bg=BG)
        style_row.pack(anchor="w")
        self.style_buttons: list[ChoiceButton] = []
        for text, value in (
            ("简洁：2～3 句，适合实时追视频（推荐）", "concise"),
            ("详细：最多 6 句，适合看懂一张画面", "detailed"),
        ):
            btn = ChoiceButton(
                style_row, text, self.style_var, value,
                lambda: [b.refresh() for b in self.style_buttons],
                padx=self.px(10), pady=self.px(4),
            )
            btn.pack(side="left", padx=(0, self.px(7)))
            self.style_buttons.append(btn)
        for btn in self.style_buttons:
            btn.refresh()

        _label(inner, "行为", size=10, bold=True).pack(
            anchor="w", pady=(self.px(12), self.px(4))
        )
        self.change_only_var = tk.BooleanVar(value=bool(self.config.get("change_only", True)))
        self.interrupt_var = tk.BooleanVar(value=bool(self.config.get("interrupt", True)))
        self.hide_var = tk.BooleanVar(value=bool(self.config.get("hide_window", True)))
        self.auto_start_var = tk.BooleanVar(value=bool(self.config.get("auto_start", False)))
        self.topmost_var = tk.BooleanVar(value=bool(self.config.get("always_on_top", False)))
        self.dialogue_var = tk.BooleanVar(value=bool(self.config.get("read_dialogue", False)))
        for text, var in (
            ("画面几乎没变时跳过请求，节省费用", self.change_only_var),
            ("新解说立即打断上一句，保持同步", self.interrupt_var),
            ("开始实时解说后收起主窗口，显示迷你悬浮条", self.hide_var),
            ("启动软件后自动开始实时解说", self.auto_start_var),
            ("主窗口始终置顶", self.topmost_var),
            ("视频没有声音：同时朗读字幕和台词", self.dialogue_var),
        ):
            ToggleButton(
                inner, text, var,
                font=(self.font_family, 9),
                padx=self.px(9), pady=self.px(3),
            ).pack(fill="x", pady=self.px(1))

        _label(
            inner,
            "提示：视频原声已经能听到时，不要勾选“朗读字幕”，"
            "AI 会专注于画面、动作和表情，避免重复台词。",
            size=8, fg=DIM, wraplength=self.px(720),
        ).pack(anchor="w", pady=(self.px(8), 0))

    # ------------------------------------------------------------------ AI 页

    def _build_ai_tab(self) -> None:
        page = self.tab_ai
        inner = tk.Frame(page, bg=BG, padx=self.px(18), pady=self.px(12))
        inner.pack(fill="both", expand=True)

        row = tk.Frame(inner, bg=BG)
        row.pack(fill="x")
        _label(row, "服务预设", size=9, bold=True).pack(side="left")
        provider = str(self.config.get("provider", PROVIDER_DOUBAO))
        self.provider_var = tk.StringVar(
            value=PROVIDER_LABELS.get(provider, PROVIDER_LABELS[PROVIDER_DOUBAO])
        )
        self.provider_combo = ttk.Combobox(
            row, textvariable=self.provider_var,
            values=list(PROVIDER_LABELS.values()), state="readonly",
            width=30, takefocus=True,
        )
        self.provider_combo.pack(side="left", padx=(self.px(8), 0))
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)
        _label(
            row, "豆包视觉模型可识别视频画面、字幕和屏幕文字",
            size=8, fg=DIM,
        ).pack(side="right")

        self.base_var = tk.StringVar(value=str(self.config.get("base_url", "")))
        self.model_var = tk.StringVar(value=str(self.config.get("model", "")))
        self.key_var = tk.StringVar(value=self.config.api_key)
        self.timeout_var = tk.StringVar(value=str(self.config.get("timeout", 35)))

        self._field(inner, "API 地址（Base URL）", self.base_var,
                    hint="豆包填 https://ark.cn-beijing.volces.com/api/v3")
        self._field(inner, "模型 ID 或推理接入点 ID", self.model_var,
                    hint="例如 doubao-seed-1-6-vision-250815 或 ep- 开头的接入点")
        key_row = tk.Frame(inner, bg=BG)
        key_row.pack(fill="x", pady=(self.px(8), 0))
        _label(key_row, "API Key", size=9, bold=True).pack(anchor="w")
        key_inner = tk.Frame(key_row, bg=BG)
        key_inner.pack(fill="x", pady=(self.px(3), 0))
        self.api_key_entry = _entry(key_inner, self.key_var, show="●")
        self.api_key_entry.pack(side="left", fill="x", expand=True, ipady=self.px(6))
        self.show_key_var = tk.BooleanVar(value=False)
        ToggleButton(
            key_inner, "显示 Key", self.show_key_var,
            command=self._toggle_key_show,
            font=(self.font_family, 8), padx=self.px(8), pady=self.px(3),
        ).pack(side="left", padx=(self.px(8), 0))
        _label(
            inner, "Key 只会加密保存在本机；不会上传到除你填写的 AI 接口之外的任何地方。",
            size=8, fg=DIM,
        ).pack(anchor="w", pady=(self.px(3), 0))

        options = tk.Frame(inner, bg=BG)
        options.pack(fill="x", pady=(self.px(8), 0))
        self.fast_var = tk.BooleanVar(value=bool(self.config.get("fast_mode", True)))
        ToggleButton(
            options, "快速模式（关闭豆包深度思考，响应更快）", self.fast_var,
            font=(self.font_family, 8), padx=self.px(8), pady=self.px(3),
        ).pack(side="left")
        _label(options, "超时", size=9, fg=MUTED).pack(side="left", padx=(self.px(16), 0))
        tk.Spinbox(
            options, from_=15, to=90, increment=5, width=5,
            textvariable=self.timeout_var, bg=FIELD, fg=TEXT,
            buttonbackground=SURFACE_2, insertbackground=TEXT,
            relief="flat", highlightthickness=1, highlightbackground=BORDER,
            font=(self.font_family, 9),
        ).pack(side="left", padx=(self.px(5), 0))
        _label(options, "秒", size=9, fg=MUTED).pack(side="left", padx=(self.px(3), 0))

        test_row = tk.Frame(inner, bg=BG)
        test_row.pack(fill="x", pady=(self.px(10), 0))
        self.test_btn = _make_button(
            test_row, "测试连接", self.test_connection, kind="blue",
            font=(self.font_family, 9, "bold"), padx=self.px(16), pady=self.px(5),
        )
        self.test_btn.pack(side="left")
        _make_button(
            test_row, "打开火山方舟控制台", self._open_console, kind="dark",
            font=(self.font_family, 9), padx=self.px(14), pady=self.px(5),
        ).pack(side="left", padx=(self.px(8), 0))
        self.test_status = _label(
            test_row, "", size=8, fg=MUTED, wraplength=self.px(360)
        )
        self.test_status.pack(side="left", padx=(self.px(10), 0), fill="x", expand=True)

        _label(
            inner,
            "首次使用：在火山方舟开通“豆包视觉理解”模型，创建 API Key，"
            "再把控制台里的模型 ID 填到上面。测试成功后再保存。",
            size=8, fg=DIM, wraplength=self.px(720),
        ).pack(anchor="w", pady=(self.px(8), 0))

    def _field(self, parent: tk.Misc, label: str, var: tk.Variable,
               hint: str = "") -> None:
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", pady=(self.px(7), 0))
        _label(box, label, size=9, bold=True).pack(anchor="w")
        entry = _entry(box, var)
        entry.pack(fill="x", ipady=self.px(6), pady=(self.px(3), 0))
        if hint:
            _label(box, hint, size=8, fg=DIM).pack(anchor="w", pady=(self.px(2), 0))

    def _toggle_key_show(self) -> None:
        self.api_key_entry.configure(show="" if self.show_key_var.get() else "●")

    def _on_provider_changed(self, _event=None) -> None:
        key = PROVIDER_KEYS.get(self.provider_var.get(), PROVIDER_DOUBAO)
        preset = PROVIDER_PRESETS.get(key)
        if not preset:
            return
        self.base_var.set(preset["base_url"])
        self.model_var.set(preset["model"])
        self.test_status.configure(text=f"已填入 {preset['label']} 的默认地址，可手动修改。",
                                   fg=MUTED)

    def _open_console(self) -> None:
        provider = PROVIDER_KEYS.get(self.provider_var.get(), PROVIDER_DOUBAO)
        url = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS[PROVIDER_DOUBAO])["help_url"]
        import webbrowser

        try:
            webbrowser.open(url)
        except Exception:
            messagebox.showinfo("打开控制台", url, parent=self.win)

    def _collect_ai_config(self) -> dict[str, Any]:
        provider = PROVIDER_KEYS.get(self.provider_var.get(), PROVIDER_DOUBAO)
        try:
            timeout = int(float(self.timeout_var.get()))
        except Exception:
            timeout = 35
        return {
            "provider": provider,
            "base_url": self.base_var.get().strip(),
            "model": self.model_var.get().strip(),
            "api_key": self.key_var.get().strip(),
            "timeout": max(15, min(90, timeout)),
            "fast_mode": bool(self.fast_var.get()),
            "read_dialogue": bool(self.dialogue_var.get()),
        }

    def test_connection(self) -> None:
        if self._test_running:
            return
        cfg = self._collect_ai_config()
        if not cfg["api_key"]:
            self.test_status.configure(text="请先填写 API Key。", fg=RED)
            return
        if not cfg["model"]:
            self.test_status.configure(text="请先填写模型 ID。", fg=RED)
            return
        self._test_running = True
        self.test_btn.configure(state="disabled", text="正在测试…")
        self.test_status.configure(text="正在截取画面并请求 AI，通常需要几秒…", fg=BLUE)

        def worker() -> None:
            client = None
            try:
                capturer = ScreenCapturer(int(self.config.get("max_image_side", 1440)))
                result = capturer.capture("screen")
                image_bytes = capturer.to_jpeg_bytes(result.image)
                client = AIClient(cfg)
                started = time.monotonic()
                text = client.test_connection(image_bytes)
                self._test_queue.put(("ok", text, time.monotonic() - started))
            except AIError as exc:
                self._test_queue.put(("error", exc.user_message, 0.0))
            except Exception as exc:
                log.exception("连接测试失败")
                self._test_queue.put(("error", f"测试时发生错误：{exc}", 0.0))
            finally:
                if client:
                    client.close()

        threading.Thread(target=worker, name="ai-test", daemon=True).start()

    def _poll_test_queue(self) -> None:
        if not self.alive:
            return
        try:
            kind, text, elapsed = self._test_queue.get_nowait()
        except queue.Empty:
            self.win.after(120, self._poll_test_queue)
            return
        self._test_running = False
        self.test_btn.configure(state="normal", text="测试连接")
        if kind == "ok":
            short = text if len(text) <= 90 else text[:89] + "…"
            self.test_status.configure(
                text=f"连接成功（{elapsed:.1f} 秒）：{short}", fg=GREEN
            )
            self.speech.speak("连接成功。", interrupt=True)
        else:
            self.test_status.configure(text=text, fg=RED)
            self.speech.speak("连接失败。" + text.split("\n", 1)[0], interrupt=True)
        self.win.after(120, self._poll_test_queue)

    # ------------------------------------------------------------------ 语音页

    def _build_voice_tab(self) -> None:
        page = self.tab_voice
        inner = tk.Frame(page, bg=BG, padx=self.px(18), pady=self.px(14))
        inner.pack(fill="both", expand=True)

        _label(inner, "朗读声音", size=10, bold=True).pack(anchor="w")
        current_id = str(self.config.get("voice_id", ""))
        current_name = self._voice_choices[0][1]
        for vid, name in self._voice_choices[1:]:
            if vid == current_id:
                current_name = name
                break
        self.voice_var = tk.StringVar(value=current_name)
        self.voice_combo = ttk.Combobox(
            inner, textvariable=self.voice_var,
            values=[name for _vid, name in self._voice_choices],
            state="readonly", width=52, takefocus=True,
        )
        self.voice_combo.pack(anchor="w", pady=(self.px(4), 0), fill="x")
        if not self._voices:
            _label(inner, self.speech.error or "没有检测到可用语音。",
                   size=8, fg=RED, wraplength=self.px(700)).pack(anchor="w",
                                                                  pady=(self.px(4), 0))

        rate_head = tk.Frame(inner, bg=BG)
        rate_head.pack(fill="x", pady=(self.px(14), 2))
        _label(rate_head, "语速", size=10, bold=True).pack(side="left")
        self.rate_label = _label(rate_head, "正常语速", size=9, fg=MUTED)
        self.rate_label.pack(side="right")

        def rate_changed(value: float) -> None:
            names = {-4: "较慢", 0: "正常语速", 3: "较快", 6: "很快"}
            self.rate_label.configure(text=names.get(int(value), f"语速 {value:+g}"))

        self.rate_row = PresetRow(
            inner,
            [("较慢", -4), ("正常", 0), ("较快", 3), ("很快", 6)],
            float(self.config.get("tts_rate", 0)),
            on_change=rate_changed,
        )
        self.rate_row.pack(anchor="w", pady=(self.px(3), 0))
        rate_changed(self.rate_row.get())

        volume_head = tk.Frame(inner, bg=BG)
        volume_head.pack(fill="x", pady=(self.px(12), 2))
        _label(volume_head, "音量", size=10, bold=True).pack(side="left")
        self.volume_label = _label(volume_head, "", size=9, fg=MUTED)
        self.volume_label.pack(side="right")

        def volume_changed(value: float) -> None:
            self.volume_label.configure(text=f"{int(value)}%")

        self.volume_row = PresetRow(
            inner,
            [("50%", 50), ("75%", 75), ("90%", 90), ("100%", 100)],
            float(self.config.get("tts_volume", 100)),
            on_change=volume_changed,
        )
        self.volume_row.pack(anchor="w", pady=(self.px(3), 0))
        volume_changed(self.volume_row.get())

        test_row = tk.Frame(inner, bg=BG)
        test_row.pack(fill="x", pady=(self.px(14), 0))
        _make_button(
            test_row, "试听这段声音", self.test_voice, kind="blue",
            font=(self.font_family, 9, "bold"), padx=self.px(16), pady=self.px(5),
        ).pack(side="left")
        _make_button(
            test_row, "停止朗读", self.speech.stop, kind="dark",
            font=(self.font_family, 9), padx=self.px(14), pady=self.px(5),
        ).pack(side="left", padx=(self.px(8), 0))
        _label(
            inner,
            "使用 Windows 自带的离线语音，不额外收费。若声音列表为空，"
            "请在“设置 → 时间和语言 → 语音”中安装中文语音包。",
            size=8, fg=DIM, wraplength=self.px(720),
        ).pack(anchor="w", pady=(self.px(10), 0))

    def test_voice(self) -> None:
        vid = self._selected_voice_id()
        self.speech.configure(
            voice_id=vid, rate=int(self.rate_row.get()),
            volume=int(self.volume_row.get()),
        )
        self.speech.speak(
            "这是听见画面的试听。画面里，一个人正沿着黄昏的街道向前走，"
            "远处的霓虹灯刚刚亮起。",
            interrupt=True,
        )

    def _selected_voice_id(self) -> str:
        name = self.voice_var.get()
        for vid, label in self._voice_choices:
            if label == name:
                return vid
        return ""

    # ------------------------------------------------------------------ 帮助页

    def _build_help_tab(self) -> None:
        page = self.tab_help
        inner = tk.Frame(page, bg=BG, padx=self.px(14), pady=self.px(10))
        inner.pack(fill="both", expand=True)
        text = tk.Text(
            inner, wrap="word", bg=FIELD, fg=TEXT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER,
            font=(self.font_family, 9), padx=self.px(14), pady=self.px(12),
            spacing1=self.px(2), spacing3=self.px(4), height=20,
        )
        text.pack(side="left", fill="both", expand=True)
        nav = tk.Frame(inner, bg=BG)
        nav.pack(side="right", fill="y", padx=(self.px(7), 0))
        _make_button(
            nav, "向上翻页", lambda: text.yview_scroll(-1, "pages"), kind="dark",
            font=(self.font_family, 8), padx=self.px(8), pady=self.px(4),
        ).pack(fill="x", pady=(0, self.px(5)))
        _make_button(
            nav, "向下翻页", lambda: text.yview_scroll(1, "pages"), kind="dark",
            font=(self.font_family, 8), padx=self.px(8), pady=self.px(4),
        ).pack(fill="x", pady=(0, self.px(5)))
        _make_button(
            nav, "朗读帮助", self.speak_help, kind="blue",
            font=(self.font_family, 8), padx=self.px(8), pady=self.px(4),
        ).pack(fill="x", pady=(self.px(10), 0))
        text.bind("<Prior>", lambda _e: text.yview_scroll(-1, "pages"))
        text.bind("<Next>", lambda _e: text.yview_scroll(1, "pages"))
        text.insert("1.0", HELP_TEXT)
        text.tag_configure("head", foreground=ACCENT, font=(self.font_family, 10, "bold"))
        for line in HELP_TEXT.splitlines():
            if line.startswith("【") or line.startswith("听见画面 1.0"):
                start = text.search(line, "1.0", stopindex="end")
                if start:
                    end = f"{start}+{len(line)}c"
                    text.tag_add("head", start, end)
        text.configure(state="disabled")
        self.help_text = text

    def speak_help(self) -> None:
        self.speech.speak(
            "听见画面使用说明。按 Control Alt S 开始或停止实时解说，"
            "按 Control Alt D 描述当前画面，按 Control Alt P 暂停，"
            "按 Control Alt X 停止朗读，按 Control Alt M 显示或收起悬浮条。"
            "首次使用请在 AI 服务中填写 API Key。",
            interrupt=True,
        )

    # ------------------------------------------------------------------ 保存

    def save(self) -> None:
        cfg = self._collect_ai_config()
        if not cfg["base_url"]:
            messagebox.showwarning("无法保存", "请填写 API 地址。", parent=self.win)
            return
        if not cfg["model"]:
            messagebox.showwarning("无法保存", "请填写模型 ID。", parent=self.win)
            return
        interval = float(self.interval_row.get())
        voice_id = self._selected_voice_id()
        values = {
            **cfg,
            "interval": max(2.0, min(12.0, interval)),
            "style": self.style_var.get(),
            "change_only": bool(self.change_only_var.get()),
            "interrupt": bool(self.interrupt_var.get()),
            "hide_window": bool(self.hide_var.get()),
            "auto_start": bool(self.auto_start_var.get()),
            "always_on_top": bool(self.topmost_var.get()),
            "read_dialogue": bool(self.dialogue_var.get()),
            "voice_id": voice_id,
            "tts_rate": int(self.rate_row.get()),
            "tts_volume": int(self.volume_row.get()),
        }
        self.config.update(values)
        self.config.api_key = cfg["api_key"]
        self.config.clamp()
        try:
            self.config.save()
        except Exception as exc:
            log.exception("保存配置失败")
            messagebox.showerror("保存失败", f"无法写入配置文件：{exc}", parent=self.win)
            return
        self.speech.configure(
            voice_id=voice_id,
            rate=int(self.rate_row.get()),
            volume=int(self.volume_row.get()),
        )
        self.narrator.update_config()
        try:
            self.on_saved()
        except Exception:
            log.exception("保存后的界面刷新失败")
        self.close(save=False, refresh=False)

    def close(self, save: bool = False, refresh: bool = True) -> None:
        if not self.alive:
            return
        if save:
            self.save()
            return
        self.alive = False
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.parent.winfo_exists():
            try:
                self.parent.focus_force()
            except Exception:
                pass


# 供 main.py 使用
def create_app(root: tk.Tk, config: Config, speech: SpeechEngine) -> HearScreenApp:
    return HearScreenApp(root, config, speech)
