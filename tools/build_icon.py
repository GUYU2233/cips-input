"""Build the cips-input icon.

Windows extracts small icons through the legacy ``ExtractIconEx`` path, which
only understands BMP-format ICO frames. Pillow writes PNG frames for every
size, so the shell falls back to the generic language glyph. This script writes
the ICO container itself: uncompressed 32bpp BMP frames for the small sizes and
a PNG frame for 256x256, which is the same layout the bundled rime icon uses.

Run with the bundled interpreter:
    python tools\\build_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PLATE = (32, 82, 149, 255)
GLYPH = (255, 255, 255, 255)
TEXT = "IPT"
BMP_SIZES = (16, 20, 24, 32, 48, 64, 128)
PNG_SIZES = (256,)
SUPERSAMPLE = 4

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
)


def _render(size: int) -> Image.Image:
    """Draw the plate at ``size`` with supersampling, then downsample."""
    scale = size * SUPERSAMPLE
    canvas = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    radius = round(scale * 0.18)
    draw.rounded_rectangle((0, 0, scale - 1, scale - 1), radius=radius, fill=PLATE)

    font = None
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            font = ImageFont.truetype(candidate, round(scale * 0.52))
            break
    if font is None:
        font = ImageFont.load_default()

    left, top, right, bottom = draw.textbbox((0, 0), TEXT, font=font)
    draw.text(
        ((scale - (right - left)) / 2 - left, (scale - (bottom - top)) / 2 - top),
        TEXT,
        font=font,
        fill=GLYPH,
    )
    return canvas.resize((size, size), Image.LANCZOS)


def _bmp_frame(image: Image.Image) -> bytes:
    """Serialize one ICO frame as a 32bpp BMP (DIB) image."""
    width, height = image.size
    pixels = image.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGRA")

    header = struct.pack(
        "<IiiHHIIiiII",
        40,            # biSize
        width,         # biWidth
        height * 2,    # biHeight: XOR bitmap plus AND mask
        1,             # biPlanes
        32,            # biBitCount
        0,             # biCompression: BI_RGB
        len(pixels),   # biSizeImage
        0, 0, 0, 0,
    )
    mask_row = ((width + 31) // 32) * 4
    mask = bytes(mask_row * height)
    return header + pixels + mask


def _png_frame(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build(output: Path) -> None:
    frames: list[tuple[int, bytes]] = []
    for size in BMP_SIZES:
        frames.append((size, _bmp_frame(_render(size))))
    for size in PNG_SIZES:
        frames.append((size, _png_frame(_render(size))))

    header = struct.pack("<HHH", 0, 1, len(frames))
    directory = b""
    payload = b""
    offset = len(header) + 16 * len(frames)
    for size, data in frames:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + directory + payload)
    print(f"wrote {output} ({len(header) + len(directory) + len(payload)} bytes, {len(frames)} frames)")


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1] / "src" / "input_methods" / "cips" / "icon.ico")
