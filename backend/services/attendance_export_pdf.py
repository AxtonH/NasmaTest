"""Render an `AttendanceReport` to a PDF (reportlab).

Nasma port of the Attendance Dashboard's `features/exports/pdf.py`,
reproduced verbatim (only the import style differs). Layout mirrors the
Excel renderer: a summary page (headline stats + arrival/departure charts +
per-employee overview table) followed by one page per employee (info block,
stat block, day-by-day table). reportlab is pure-Python, so this deploys
without native libs. No attendance logic here — just layout.

This module imports reportlab and Pillow at load time; the orchestrator in
`attendance_export.py` imports it lazily inside the PDF branch so a missing
dependency disables ONLY the PDF path, never Excel.
"""

from __future__ import annotations

from io import BytesIO
from typing import Dict, List
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as PdfImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

try:  # package-style import (matches app.py's primary import block)
    from . import attendance_export_chart as chart
    from .attendance_export_report import (
        STATUS_ABSENT,
        STATUS_DAY_OFF,
        STATUS_HOLIDAY,
        STATUS_LATE,
        STATUS_ON_LEAVE,
        STATUS_UPCOMING,
        STATUS_WEEKEND,
        STATUS_WFH,
        AttendanceReport,
        EmployeeSection,
    )
except Exception:  # script-style import (running from backend/)
    import attendance_export_chart as chart  # type: ignore
    from attendance_export_report import (  # type: ignore
        STATUS_ABSENT,
        STATUS_DAY_OFF,
        STATUS_HOLIDAY,
        STATUS_LATE,
        STATUS_ON_LEAVE,
        STATUS_UPCOMING,
        STATUS_WEEKEND,
        STATUS_WFH,
        AttendanceReport,
        EmployeeSection,
    )

# Prezlab dark header band / shading — match the Excel renderer.
_HEADER_BG = colors.HexColor("#1F2430")
_HEADER_FG = colors.white
_GRID = colors.HexColor("#E5E7EB")
_MUTED = colors.HexColor("#6B7280")
_FAINT = colors.HexColor("#9CA3AF")

# Status text colors — same palette as the frontend pills / Excel renderer.
_STATUS_COLORS: Dict[str, colors.Color] = {
    STATUS_LATE: colors.HexColor("#92400E"),
    STATUS_ABSENT: colors.HexColor("#991B1B"),
    STATUS_ON_LEAVE: colors.HexColor("#1E40AF"),
    STATUS_HOLIDAY: colors.HexColor("#065F46"),
    STATUS_WFH: colors.HexColor("#8C8C90"),
    STATUS_WEEKEND: colors.HexColor("#8C8C90"),
    STATUS_DAY_OFF: colors.HexColor("#8C8C90"),
    STATUS_UPCOMING: colors.HexColor("#C4C4C8"),
}
_MUTED_STATUSES = (STATUS_WFH, STATUS_WEEKEND, STATUS_DAY_OFF, STATUS_UPCOMING)

_STYLES = getSampleStyleSheet()
_TITLE = ParagraphStyle(
    "RptTitle", parent=_STYLES["Title"], fontSize=16, alignment=0, spaceAfter=2
)
_SUBTITLE = ParagraphStyle(
    "RptSubtitle", parent=_STYLES["Normal"], fontSize=9.5, textColor=_MUTED, spaceAfter=2
)
_H2 = ParagraphStyle(
    "RptH2",
    parent=_STYLES["Heading2"],
    fontSize=13,
    spaceBefore=0,
    spaceAfter=2,
)
# Text cells inside tables. Plain strings in a reportlab Table never wrap —
# a long employee name just overflows into the next column — so free-text
# columns are rendered as Paragraphs, which wrap within their column width.
_CELL = ParagraphStyle(
    "RptCell", parent=_STYLES["Normal"], fontSize=8, leading=9.5
)
_CELL_MUTED = ParagraphStyle("RptCellMuted", parent=_CELL, textColor=_MUTED)

# Portrait A4 content width is ~190mm (10mm margins).
_OVERVIEW_WIDTHS_MM = [48, 30, 16, 12, 14, 16, 20, 20]
_DAY_WIDTHS_MM = [24, 13, 22, 19, 19, 19, 15]


def _text_cell(value: str, style: ParagraphStyle = _CELL) -> Paragraph:
    """A wrapping table cell for user-supplied text.

    Escaped because Paragraph parses inline markup — a department like
    "People & Culture" must render literally, not as a broken entity.
    """
    return Paragraph(escape(value), style)


def render_pdf(report: AttendanceReport) -> bytes:
    """Build the PDF and return it as bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        title=report.title,
    )

    elements: list = [
        Paragraph(report.title, _TITLE),
        Paragraph(report.period_label, _SUBTITLE),
        Paragraph(report.generated_label, _SUBTITLE),
        Spacer(1, 6 * mm),
        _stats_table(report),
        Spacer(1, 6 * mm),
    ]
    if report.arrival_buckets and report.departure_buckets:
        # The Excel summary's side-by-side arrival/departure bar charts,
        # rendered as PNGs over the same fixed bucket windows.
        elements.append(Paragraph("Arrival &amp; departure pattern", _H2))
        elements.append(Spacer(1, 2 * mm))
        elements.append(_pattern_charts(report))
        elements.append(Spacer(1, 6 * mm))
    if report.overview_rows:
        elements.append(_overview_table(report))
    else:
        elements.append(Paragraph("No employees in this report.", _SUBTITLE))

    for section in report.sections:
        elements.append(PageBreak())
        elements.extend(_employee_page(section, report.period_label))

    doc.build(elements)
    return buffer.getvalue()


def _pattern_charts(report: AttendanceReport) -> Table:
    """Arrival and departure bucket charts side by side, equal widths."""
    img_w = 92 * mm
    img_h = img_w * chart.HEIGHT / chart.WIDTH
    cells = [
        PdfImage(
            BytesIO(chart.render_bucket_chart_png(bins, title=title)),
            width=img_w,
            height=img_h,
        )
        for title, bins in (
            ("Arrivals", report.arrival_buckets),
            ("Departures", report.departure_buckets),
        )
    ]
    tbl = Table([cells], colWidths=[95 * mm, 95 * mm], hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return tbl


def _stats_table(report: AttendanceReport) -> Table:
    """Headline stats as a compact label/value/detail list."""
    data = [[s.label, s.value, s.detail] for s in report.stats]
    tbl = Table(data, colWidths=[34 * mm, 30 * mm, 100 * mm])
    tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
                ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTSIZE", (1, 0), (1, -1), 11),
                ("TEXTCOLOR", (2, 0), (2, -1), _FAINT),
                ("FONTSIZE", (2, 0), (2, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, _GRID),
            ]
        )
    )
    return tbl


def _overview_table(report: AttendanceReport) -> Table:
    # Employee and Department are free text and must wrap inside their
    # columns; the remaining columns are short fixed-format values.
    rows = [
        [_text_cell(row[0]), _text_cell(row[1], _CELL_MUTED), *row[2:]]
        for row in report.overview_rows
    ]
    tbl = Table(
        [report.overview_columns, *rows],
        colWidths=[w * mm for w in _OVERVIEW_WIDTHS_MM],
        repeatRows=1,
    )
    tbl.setStyle(TableStyle(_table_base_style()))
    return tbl


def _employee_page(section: EmployeeSection, period_label: str) -> list:
    # Name and department are user data headed into Paragraphs — escape so
    # markup-significant characters (& < >) render literally.
    dept = f" · {escape(section.department)}" if section.department else ""
    elements: list = [
        Paragraph(escape(section.name), _H2),
        Paragraph(f"Emp code {escape(section.emp_code)}{dept}", _SUBTITLE),
        Paragraph(period_label, _SUBTITLE),
        Spacer(1, 4 * mm),
    ]

    # Stat block: two label/value pairs per row, mirroring the Excel sheet.
    pairs = [
        ("Days expected", str(section.days_expected)),
        ("Avg arrival", section.avg_arrival),
        ("Attended", str(section.days_attended)),
        ("Total worked", section.total_worked),
        ("Late arrivals", str(section.days_late)),
        ("Avg worked / day", section.avg_worked),
        ("Absent", str(section.days_absent)),
        ("Missing sign-outs", str(section.missing_signouts)),
        ("On leave", str(section.days_on_leave)),
        ("Attendance", section.attendance_pct),
        ("Holidays", str(section.days_holiday)),
    ]
    rows = [pairs[i : i + 2] for i in range(0, len(pairs), 2)]
    data = []
    for pair in rows:
        left, right = pair[0], pair[1] if len(pair) > 1 else ("", "")
        data.append([left[0], left[1], right[0], right[1]])
    stat_tbl = Table(data, colWidths=[34 * mm, 26 * mm, 34 * mm, 26 * mm])
    stat_tbl.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
                ("TEXTCOLOR", (2, 0), (2, -1), _MUTED),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("FONTNAME", (3, 0), (3, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(stat_tbl)
    elements.append(Spacer(1, 5 * mm))
    elements.append(_day_table(section))
    return elements


def _day_table(section: EmployeeSection) -> Table:
    columns = [
        "Date",
        "Day",
        "Status",
        "Check-in",
        "Check-out",
        "Worked",
        "Missing hours",
    ]
    data = [columns]
    style = _table_base_style()

    for i, line in enumerate(section.days, start=1):
        data.append(
            [
                line.date,
                line.weekday,
                line.status,
                line.check_in,
                line.check_out,
                line.worked,
                line.missing_hours,
            ]
        )
        color = _STATUS_COLORS.get(line.status)
        if color is not None:
            style.append(("TEXTCOLOR", (2, i), (2, i), color))
            if line.status not in _MUTED_STATUSES:
                style.append(("FONTNAME", (2, i), (2, i), "Helvetica-Bold"))
        if line.status in _MUTED_STATUSES:
            style.append(("TEXTCOLOR", (0, i), (-1, i), _FAINT))

    tbl = Table(data, colWidths=[w * mm for w in _DAY_WIDTHS_MM], repeatRows=1)
    tbl.setStyle(TableStyle(style))
    return tbl


def _table_base_style() -> list:
    """Shared header-band + grid treatment for data tables."""
    return [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_FG),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
