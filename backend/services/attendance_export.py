"""Export the team attendance report as .xlsx or .pdf.

This is a faithful port of the standalone Attendance Dashboard's report
template (`features/exports/{report,excel,pdf,chart}.py`): a branded Summary
sheet/page (hero KEY METRICS tiles, arrival/departure pattern tables with
charts, the per-employee overview, TOP RANKINGS podiums) plus one sheet/page
per employee (PROFILE, SUMMARY tile grid, DAILY LOG with status pills, and a
STATUS KEY legend). The layout lives in `attendance_export_excel.py` /
`attendance_export_pdf.py`; the report shape in `attendance_export_report.py`
— all reproduced verbatim from the dashboard so a file exported from Nasma
matches one exported from the dashboard.

The only Nasma-specific pieces here:

  - the adapter (`_adapt_ranges`) that converts Nasma's `MemberRange` data
    (built by `attendance_report._gather_member_ranges`, the exact same data
    the on-screen widget renders) into the report builder's inputs, and
  - the profile lookups (department from the cached direct-reports list,
    Odoo `x_studio_pod` for the "Pool / Team" field — both degrade to blank).

Shift timing (start 09:00, 15m grace, 8h day) mirrors the dashboard's
`config/shift_rules.yaml`. One deliberate deviation: the dashboard's phase-1
config marks Thursday as org-wide WFH; Nasma resolves real per-employee Odoo
schedules and its on-screen table shows those Thursdays as Absent, so the
export keeps `wfh_weekdays` empty to stay consistent with the screen (see
`ShiftRule` in `attendance_export_report.py`).

No attendance logic here — just reshaping + orchestration.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, FrozenSet, List, Mapping, Set, Tuple

try:  # package-style import (matches app.py's primary import block)
    from .manager_helper import _make_odoo_request, _normalize_emp_code
    from .attendance_report import (
        MemberRange,
        STATUS_ABSENT as NASMA_STATUS_ABSENT,
        STATUS_HOLIDAY as NASMA_STATUS_HOLIDAY,
        STATUS_ON_LEAVE as NASMA_STATUS_ON_LEAVE,
        _iter_days,
    )
    from .attendance_export_report import (
        EmployeesWeekResponse,
        EmployeeWeek,
        EmployeeWeekDay,
        ShiftRule,
        build_attendance_report,
    )
    from .attendance_export_excel import render_xlsx
except Exception:  # script-style import (running from backend/)
    from manager_helper import _make_odoo_request, _normalize_emp_code  # type: ignore
    from attendance_report import (  # type: ignore
        MemberRange,
        STATUS_ABSENT as NASMA_STATUS_ABSENT,
        STATUS_HOLIDAY as NASMA_STATUS_HOLIDAY,
        STATUS_ON_LEAVE as NASMA_STATUS_ON_LEAVE,
        _iter_days,
    )
    from attendance_export_report import (  # type: ignore
        EmployeesWeekResponse,
        EmployeeWeek,
        EmployeeWeekDay,
        ShiftRule,
        build_attendance_report,
    )
    from attendance_export_excel import render_xlsx  # type: ignore


# Org-wide shift timing — mirrors the dashboard's config/shift_rules.yaml
# except `wfh_weekdays` (see the module docstring).
DEFAULT_SHIFT_RULE = ShiftRule(
    start="09:00",
    grace_minutes=15,
    full_day_minutes=480,
    wfh_weekdays=(),
)


# --------------------------------------------------------------------------- #
# Adapter: Nasma MemberRange[] → report-builder inputs
# --------------------------------------------------------------------------- #

def _adapt_ranges(
    ranges: List[MemberRange],
    days: List[date],
    rule: ShiftRule,
) -> Tuple[EmployeesWeekResponse, Dict[date, FrozenSet[str]]]:
    """Convert MemberRanges into the report's response + schedule map.

    A `MemberRange` carries one `DayRow` per SCHEDULED day, so the per-day
    working sets fall straight out of the rows — a day with no row for a
    member is their weekend, which the report builder then labels itself.

    Row conversion mirrors the dashboard's employees service:
      - Holiday / On leave / Absent rows keep their flags (punch times stay
        visible when present — someone who popped in on a leave day).
      - A worked row with no punch yet (today pre-cutoff, or a future
        scheduled day) emits NO row: the builder renders the gap as
        upcoming ("—") instead of a false Present.
      - An absence on an org WFH weekday emits no row either, so the gap
        reads as WFH — no office attendance is expected that day.
    """
    working_by_day: Dict[date, Set[str]] = {day: set() for day in days}
    rows: List[EmployeeWeek] = []

    for mr in ranges:
        day_rows: List[EmployeeWeekDay] = []
        for d in mr.days:
            if d.day in working_by_day:
                working_by_day[d.day].add(mr.emp_code)

            if d.status == NASMA_STATUS_HOLIDAY:
                flags = {"on_holiday": True}
            elif d.status == NASMA_STATUS_ON_LEAVE:
                flags = {"on_leave": True}
            elif d.status == NASMA_STATUS_ABSENT:
                if d.day.weekday() in rule.wfh_weekdays:
                    continue  # WFH weekday — not an office absence
                flags = {"absent": True}
            else:  # worked
                if d.punch_in is None:
                    continue  # unsettled (today/future) → upcoming gap
                flags = {}

            day_rows.append(
                EmployeeWeekDay(
                    date=d.day.isoformat(),
                    punch_in=d.punch_in,
                    punch_out=d.punch_out,
                    worked_minutes=d.worked_minutes,
                    **flags,
                )
            )
        rows.append(EmployeeWeek(emp_code=mr.emp_code, name=mr.name, days=day_rows))

    return (
        EmployeesWeekResponse(rows=rows),
        {day: frozenset(codes) for day, codes in working_by_day.items()},
    )


# --------------------------------------------------------------------------- #
# Profile lookups (department + Odoo pod) — degrade to blank, never fail
# --------------------------------------------------------------------------- #

def _tag_name(value: Any) -> str:
    """Display name for a Studio tag field, "" when unset.

    Studio tag fields aren't one consistent shape over XML-RPC: depending on
    how the field was defined it arrives as a many2one `[id, "Name"]`, a
    many2many id list (no names in the payload), or a plain selection/char
    string. Read the ones that carry a name and fall back to "" for the
    id-list form rather than rendering a bare id. (Same coercion as the
    dashboard's Odoo roster.)
    """
    if isinstance(value, list):
        if len(value) >= 2 and isinstance(value[1], str):
            return value[1].strip()
        return ""
    if value is False or value is None:
        return ""
    return str(value).strip()


def _profile_maps(
    odoo_service, employee_service
) -> Tuple[Mapping[str, str], Mapping[str, str]]:
    """(department_by_emp_code, pod_by_emp_code) for the manager's team.

    Departments come from the cached direct-reports list the range gatherer
    already used. Pods (`x_studio_pod`, the export's "Pool / Team" field)
    need one extra hr.employee read; both maps degrade to empty on any
    failure — the renderers show "—" for missing values.
    """
    departments: Dict[str, str] = {}
    pods: Dict[str, str] = {}
    try:
        ok, team = employee_service.get_direct_reports_current_user()
        if not ok or not isinstance(team, list):
            return departments, pods

        code_by_employee_id: Dict[int, str] = {}
        for m in team:
            if not isinstance(m, dict):
                continue
            code = _normalize_emp_code(m.get("emp_code"))
            if not code:
                continue
            departments[code] = str(m.get("department") or "")
            eid = m.get("id")
            if isinstance(eid, int):
                code_by_employee_id[eid] = code

        if code_by_employee_id:
            params = {
                "args": [[("id", "in", list(code_by_employee_id))]],
                "kwargs": {
                    "fields": ["id", "x_studio_pod"],
                    "limit": len(code_by_employee_id) + 1,
                },
            }
            ok2, rows = _make_odoo_request(
                odoo_service, "hr.employee", "search_read", params
            )
            if ok2:
                for r in rows or []:
                    if not isinstance(r, dict):
                        continue
                    code = code_by_employee_id.get(r.get("id"))
                    if code:
                        pods[code] = _tag_name(r.get("x_studio_pod"))
    except Exception:
        # Supplementary data only — the report renders without it.
        pass
    return departments, pods


# --------------------------------------------------------------------------- #
# Captions + filename (unchanged conventions)
# --------------------------------------------------------------------------- #

def _period_label(start: date, end: date) -> str:
    """Human-readable caption, DD-MM-YYYY (matches the dashboard route)."""
    if start == end:
        return start.strftime("%A, %d-%m-%Y")
    return f"{start.strftime('%d-%m-%Y')} to {end.strftime('%d-%m-%Y')}"


def export_filename(start: date, end: date, ext: str) -> str:
    """Descriptive, range-stamped download filename (ISO dates, sortable).

    Single day → prezlab-attendance_2026-06-29.xlsx
    Range      → prezlab-attendance_2026-06-01_to_2026-06-30.pdf
    """
    if start == end:
        stamp = start.isoformat()
    else:
        stamp = f"{start.isoformat()}_to_{end.isoformat()}"
    return f"prezlab-attendance_{stamp}.{ext}"


# --------------------------------------------------------------------------- #
# Orchestrator (single entry point for the export route)
# --------------------------------------------------------------------------- #

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF_MEDIA = "application/pdf"


def get_team_attendance_export(
    odoo_service,
    employee_service,
    start_date: date,
    end_date: date,
    fmt: str,
) -> Tuple[bool, Any]:
    """Build the export file. Returns (ok, (bytes, media_type, filename)) or (False, err).

    Reuses the exact same data gathering as the on-screen report so the file
    matches what the manager sees.
    """
    try:
        # Imported lazily to avoid a circular import at module load.
        try:
            from .attendance_report import _gather_member_ranges
        except Exception:
            from attendance_report import _gather_member_ranges  # type: ignore

        ok, ranges, today = _gather_member_ranges(
            odoo_service, employee_service, start_date, end_date
        )
        if not ok:
            return False, ranges

        days = _iter_days(start_date, end_date)
        rule = DEFAULT_SHIFT_RULE
        response, working_by_day = _adapt_ranges(ranges, days, rule)
        departments, pods = _profile_maps(odoo_service, employee_service)

        report = build_attendance_report(
            response,
            days=days,
            rule=rule,
            period_label=_period_label(start_date, end_date),
            generated_on=today,
            department_by_emp_code=departments,
            pod_by_emp_code=pods,
            working_emp_codes_by_day=working_by_day,
        )

        if fmt == "pdf":
            # Lazy import: a missing reportlab/Pillow disables ONLY the PDF
            # path — Excel export (openpyxl) must keep working regardless.
            try:
                try:
                    from .attendance_export_pdf import render_pdf
                except Exception:
                    from attendance_export_pdf import render_pdf  # type: ignore
            except Exception as import_err:
                return False, (
                    "PDF export is unavailable because its dependencies "
                    "(reportlab / Pillow) are not installed in this "
                    "environment. Please export as Excel instead "
                    f"(original import error: {import_err})."
                )
            payload = render_pdf(report)
            return True, (payload, _PDF_MEDIA, export_filename(start_date, end_date, "pdf"))

        payload = render_xlsx(report)
        return True, (payload, _XLSX_MEDIA, export_filename(start_date, end_date, "xlsx"))
    except Exception as e:
        return False, f"Error building attendance export: {e}"
