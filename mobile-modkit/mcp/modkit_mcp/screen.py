"""Screen capture for agents: raw `screencap` (fast — no on-device PNG encode), letterbox auto-crop,
downscale + JPEG, and a coordinate transform so taps can be given in screenshot or fractional space."""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from PIL import Image, ImageChops

from .adb import Adb


def capture(adb: Adb, timeout: float = 15.0) -> Image.Image:
    r = adb.run("exec-out", "screencap", timeout=timeout)
    data = r.stdout
    if r.ok and len(data) >= 12:
        w, h, _fmt = struct.unpack_from("<III", data, 0)
        hdr = len(data) - w * h * 4
        if w and h and hdr in (12, 16):
            return Image.frombuffer("RGBA", (w, h), data[hdr:], "raw", "RGBA", 0, 1).convert("RGB")
    r = adb.run("exec-out", "screencap", "-p", timeout=timeout)         # fallback: PNG
    if not r.ok:
        raise RuntimeError("screencap failed: " + ("timeout" if r.timed_out else r.err.strip()))
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def content_box(img: Image.Image, tol: int = 12) -> tuple[int, int, int, int]:
    """Bounding box of everything that differs from the corner (letterbox/pillarbox) colour. Falls back
    to the full frame when the result would be implausibly small (e.g. a dark loading screen)."""
    w, h = img.size
    corner = img.getpixel((0, 0))
    diff = ImageChops.difference(img, Image.new("RGB", img.size, corner)).convert("L")
    bbox = diff.point(lambda v: 255 if v > tol else 0).getbbox()
    if not bbox:
        return (0, 0, w, h)
    x0, y0, x1, y1 = bbox
    if (x1 - x0) * (y1 - y0) < 0.3 * w * h:
        return (0, 0, w, h)
    return bbox


@dataclass
class ShotTransform:
    """Maps screenshot pixels / fractions back to device pixels."""
    box: tuple[int, int, int, int]      # crop box in device pixels
    scale: float                        # shot_px = device_px * scale (after the crop)

    def to_device(self, x: float, y: float, space: str) -> tuple[int, int]:
        x0, y0, x1, y1 = self.box
        if space == "device":
            return int(round(x)), int(round(y))
        if space == "shot":
            return int(round(x0 + x / self.scale)), int(round(y0 + y / self.scale))
        if space == "frac":
            return int(round(x0 + x * (x1 - x0))), int(round(y0 + y * (y1 - y0)))
        raise ValueError(f"space must be device|shot|frac, not {space!r}")


def render(img: Image.Image, max_width: int, crop: bool, fmt: str, quality: int):
    box = content_box(img) if crop else (0, 0, img.width, img.height)
    part = img.crop(box)
    scale = 1.0
    if max_width and part.width > max_width:
        scale = max_width / part.width
        part = part.resize((max_width, max(1, round(part.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    fmt = fmt.lower()
    if fmt in ("jpg", "jpeg"):
        part.save(buf, "JPEG", quality=quality, optimize=True)
        fmt = "jpeg"
    else:
        part.save(buf, "PNG", optimize=True)
        fmt = "png"
    return buf.getvalue(), fmt, part.size, ShotTransform(box, scale)


def diff_ratio(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference of two frames, 0..1 (compared on small grayscale thumbnails)."""
    size = (160, max(1, round(160 * a.height / a.width)))
    ga, gb = a.convert("L").resize(size), b.convert("L").resize(size)
    hist = ImageChops.difference(ga, gb).histogram()
    total = sum(i * c for i, c in enumerate(hist))
    return total / (255.0 * size[0] * size[1])
