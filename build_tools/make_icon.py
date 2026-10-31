"""生成应用图标（无需外部素材）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"
PNG = ROOT / "assets" / "icon.png"


def make(size: int = 256) -> Image.Image:
    scale = size / 256.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def s(v: float) -> int:
        return int(round(v * scale))

    # 深色圆角底
    d.rounded_rectangle(
        (s(4), s(4), s(252), s(252)), radius=s(54),
        fill=(18, 28, 37, 255), outline=(58, 78, 96, 255), width=max(1, s(3)),
    )
    # 眼睛外轮廓
    eye = [
        (s(42), s(128)), (s(80), s(80)), (s(120), s(66)),
        (s(160), s(80)), (s(202), s(128)),
        (s(160), s(176)), (s(120), s(190)), (s(80), s(176)),
    ]
    d.polygon(eye, fill=(51, 41, 12, 255), outline=(255, 210, 74, 255))
    d.line(eye + [eye[0]], fill=(255, 210, 74, 255), width=max(2, s(7)), joint="curve")
    # 瞳孔
    d.ellipse((s(102), s(101), s(138), s(155)), fill=(255, 210, 74, 255))
    d.ellipse((s(114), s(114), s(128), s(132)), fill=(18, 28, 37, 255))
    # 右侧声波
    for radius, width in ((30, 7), (54, 7)):
        box = (s(190 - radius), s(128 - radius), s(190 + radius), s(128 + radius))
        d.arc(box, start=-58, end=58, fill=(255, 210, 74, 255), width=max(2, s(width)))
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base = make(256)
    base.save(PNG)
    base.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUT)


if __name__ == "__main__":
    main()
