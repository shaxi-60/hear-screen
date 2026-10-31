"""屏幕/窗口捕获、画面变化检测与图片压缩。"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageStat

from . import winapi

log = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    pass


@dataclass
class CaptureResult:
    image: Image.Image
    bbox: tuple[int, int, int, int]
    source: str
    hwnd: int = 0
    timestamp: float = 0.0


@dataclass
class FrameSignature:
    whole_hash: int
    bottom_hash: int
    mean: float
    stddev: float

    @property
    def is_black(self) -> bool:
        return self.mean < 7.0 and self.stddev < 4.0


def _dhash(image: Image.Image, size: tuple[int, int] = (17, 16)) -> int:
    small = image.convert("L").resize(size, Image.Resampling.BILINEAR)
    pixels = list(small.getdata())
    width, height = size
    value = 0
    for y in range(height):
        row = y * width
        for x in range(width - 1):
            value = (value << 1) | (1 if pixels[row + x] > pixels[row + x + 1] else 0)
    return value


def _hamming(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


class ScreenCapturer:
    """在有多个显示器、负坐标和缩放的情况下抓取指定区域。"""

    def __init__(self, max_side: int = 1440) -> None:
        self.max_side = max(600, int(max_side))
        self.last_external_hwnd = 0
        self.last_source = ""

    def _grab_bbox(self, bbox: tuple[int, int, int, int]) -> Image.Image:
        import mss

        left, top, right, bottom = (int(v) for v in bbox)
        width, height = right - left, bottom - top
        if width < 16 or height < 16:
            raise CaptureError("截取区域太小，请重新框选。")
        monitor = {"left": left, "top": top, "width": width, "height": height}
        try:
            with mss.mss() as sct:
                shot = sct.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as exc:
            log.exception("屏幕捕获失败")
            raise CaptureError("无法截取屏幕。请确认没有开启安全桌面或受保护内容。") from exc
        return image

    def _window_bbox(self, hwnd: int) -> tuple[int, int, int, int] | None:
        rect = winapi.get_window_rect(hwnd)
        if not rect:
            return None
        left, top, right, bottom = rect
        # 裁掉 DWM 可能留下的透明边
        pad = 0
        left += pad
        top += pad
        right -= pad
        bottom -= pad
        if right - left < 160 or bottom - top < 120:
            return None
        return left, top, right, bottom

    def capture(self, mode: str, region: list[int] | None = None) -> CaptureResult:
        source = ""
        hwnd = 0
        if mode == "region":
            if not region or len(region) != 4:
                raise CaptureError("还没有框选区域。请点击“框选区域”并拖动鼠标。")
            left, top, right, bottom = (int(v) for v in region)
            vx1, vy1, vx2, vy2 = winapi.get_virtual_screen()
            left = max(vx1, left)
            top = max(vy1, top)
            right = min(vx2, right)
            bottom = min(vy2, bottom)
            if right - left < 16 or bottom - top < 16:
                raise CaptureError("框选区域不在屏幕范围内，请重新框选。")
            bbox = (left, top, right, bottom)
            source = "自定义区域"
        elif mode == "screen":
            bbox = winapi.get_cursor_monitor()
            source = "全屏"
        else:
            hwnd = winapi.find_candidate_window(self.last_external_hwnd)
            bbox = self._window_bbox(hwnd) if hwnd else None
            if not bbox:
                bbox = winapi.get_cursor_monitor()
                source = "全屏（未找到可跟随的窗口）"
                hwnd = 0
            else:
                self.last_external_hwnd = hwnd
                title = winapi.get_window_title(hwnd)
                source = f"窗口：{title[:36]}" if title else "当前窗口"

        image = self._grab_bbox(bbox)
        return CaptureResult(
            image=image,
            bbox=bbox,
            source=source,
            hwnd=hwnd,
            timestamp=time.time(),
        )

    def signature(self, image: Image.Image) -> FrameSignature:
        width, height = image.size
        bottom = image.crop((0, int(height * 0.58), width, height))
        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        return FrameSignature(
            whole_hash=_dhash(gray),
            bottom_hash=_dhash(bottom, (17, 12)),
            mean=float(stat.mean[0]),
            stddev=float(stat.stddev[0]),
        )

    @staticmethod
    def changed(previous: FrameSignature | None, current: FrameSignature,
                whole_threshold: int = 7, bottom_threshold: int = 5) -> bool:
        if previous is None:
            return True
        if _hamming(previous.whole_hash, current.whole_hash) >= whole_threshold:
            return True
        if _hamming(previous.bottom_hash, current.bottom_hash) >= bottom_threshold:
            return True
        return abs(previous.mean - current.mean) >= 8.0

    def prepare_for_ai(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        longest = max(width, height)
        if longest > self.max_side:
            ratio = self.max_side / float(longest)
            image = image.resize(
                (max(1, int(width * ratio)), max(1, int(height * ratio))),
                Image.Resampling.LANCZOS,
            )
        if image.mode != "RGB":
            image = image.convert("RGB")
        return image

    def to_jpeg_bytes(self, image: Image.Image, quality: int = 74) -> bytes:
        prepared = self.prepare_for_ai(image)
        buf = io.BytesIO()
        prepared.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()

    def save_snapshot(self, path: str) -> tuple[int, int]:
        result = self.capture("screen")
        result.image.save(path, format="JPEG", quality=88)
        return result.image.size


def format_bbox(bbox: tuple[int, int, int, int] | list[int]) -> str:
    try:
        left, top, right, bottom = (int(v) for v in bbox)
        return f"{right - left} × {bottom - top}（位置 {left}, {top}）"
    except Exception:
        return "未设置"
