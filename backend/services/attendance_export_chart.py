"""Draw an arrival/departure bucket bar chart as a PNG (Pillow).

Nasma port of the Attendance Dashboard's `features/exports/chart.py`,
reproduced verbatim (only the import style differs). The PDF renderer's
counterpart of the Excel summary's native bar charts: solid bars in the
Prezlab deep-teal ink over the fixed time windows, the count above each bar,
the window label beneath. Drawn at 3× and downscaled for antialiasing
(Pillow's rectangle fill isn't antialiased at the edges of text). Pure
function of the bins — no I/O beyond the returned bytes.

The Excel renderer doesn't use this module; its charts are native Excel
charts anchored to the sheet grid.
"""

from __future__ import annotations

from io import BytesIO
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

try:  # package-style import (matches app.py's primary import block)
    from .attendance_export_report import HistogramBin
except Exception:  # script-style import (running from backend/)
    from attendance_export_report import HistogramBin  # type: ignore

_INK = (0, 37, 40)  # #002528 — Prezlab deep teal, same as the Excel bars
_MUTED = (98, 132, 140)  # #62848C — slate, secondary labels
_BASELINE = (213, 222, 224)  # #D5DEE0 — hairline under the bars

# Logical (embedded) size in px; drawn at _SCALE× then downscaled.
WIDTH = 560
HEIGHT = 240
_SCALE = 3


def render_bucket_chart_png(bins: List[HistogramBin], *, title: str) -> bytes:
    """One bucket distribution as PNG bytes. `bins` must be non-empty."""
    scale = _SCALE
    w, h = WIDTH * scale, HEIGHT * scale
    pad_side = 10 * scale
    title_band = 30 * scale
    count_band = 18 * scale  # the value above each bar
    label_band = 40 * scale  # two staggered rows of window labels
    plot_h = h - title_band - count_band - label_band
    baseline = title_band + count_band + plot_h

    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)

    title_font = _font(14 * scale, bold=True)
    count_font = _font(12 * scale)
    label_font = _font(12 * scale)

    _centered(draw, title, w / 2, 8 * scale, title_font, _INK)

    n = len(bins)
    max_count = max(b.count for b in bins) or 1
    slot = (w - 2 * pad_side) / n
    bar_w = slot * 0.55

    draw.line(
        [(pad_side, baseline), (w - pad_side, baseline)],
        fill=_BASELINE,
        width=scale,
    )

    for i, bin_ in enumerate(bins):
        cx = pad_side + slot * (i + 0.5)
        bar_h = plot_h * bin_.count / max_count
        if bin_.count:
            draw.rectangle(
                [cx - bar_w / 2, baseline - bar_h, cx + bar_w / 2, baseline],
                fill=_INK,
            )
        _centered(
            draw,
            str(bin_.count),
            cx,
            baseline - bar_h - 16 * scale,
            count_font,
            _INK if bin_.count else _MUTED,
        )
        # Window labels are wider than a slot at a readable size, so odd
        # and even labels sit on alternating rows instead of overlapping.
        label_top = baseline + (6 if i % 2 == 0 else 21) * scale
        _centered(draw, bin_.label, cx, label_top, label_font, _MUTED)

    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _centered(
    draw,
    text: str,
    cx: float,
    top: float,
    font,
    fill: Tuple[int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bbox[2] - bbox[0]) / 2, top), text, font=font, fill=fill)


def _font(size: int, *, bold: bool = False):
    """A small sans font: system TTFs when available (Windows dev, most
    Linux images), Pillow's bundled default otherwise."""
    names = (
        ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf")
        if bold
        else ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)
