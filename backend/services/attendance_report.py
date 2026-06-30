"""Team attendance report for the "show my team" flow.

Produces the same attendance report the standalone Attendance Dashboard shows,
scoped to the logged-in manager's direct reports, over a chosen date range.

The two apps must never disagree, so the logic here is a faithful port of the
dashboard's pure builders plus the Odoo schedule/holiday resolution they depend
on. Data sources match the dashboard exactly:

  - punches            → shared Supabase `attendance` table (emp_code, punch_time)
  - working days       → Odoo `resource.calendar` / `resource.calendar.attendance`
                         per the member's `resource_calendar_id`
  - on leave           → Odoo `hr.leave` (approved full-day leaves)
  - public holidays    → Odoo `resource.calendar.leaves` (company-wide), matched
                         to a member via their `company_id`

Status precedence on any given day mirrors the dashboard: Holiday > On leave >
Absent. Worked time wins the column whenever the member actually punched and is
not excused. Only days the member is SCHEDULED to work appear (Fri/Sat etc. for
a Sun–Thu calendar are hidden), matching the dashboard's per-day schedule
filtering. A scheduled day with no punch and no excuse, once settled (strictly
before today), is shown as Absent.

Layout mirrors the dashboard:
  - single day (start == end) → flat one-row-per-member table
  - a range                   → grouped per-member sections (banner + per-day)

Builders are pure (no I/O); the fetchers wrap Nasma's existing Supabase / Odoo
plumbing; the widget builder emits the standard `{columns, rows}` table-widget
shape the frontend `renderTable` consumes, with status cells rendered as the
same pills the team-overview "Punched In" column uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, time
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

# Reuse the punch/emp_code/Supabase/Odoo plumbing and the status-pill renderer
# rather than duplicating them.
try:  # package-style import (matches app.py's primary import block)
    from .manager_helper import (
        ATTENDANCE_TABLE,
        ATTENDANCE_SELECT_COLS,
        _attendance_config,
        _attendance_supabase_client,
        _attendance_pill,
        _make_odoo_request,
        _normalize_emp_code,
        _parse_punch_datetime,
    )
except Exception:  # script-style import (running from backend/)
    from manager_helper import (  # type: ignore
        ATTENDANCE_TABLE,
        ATTENDANCE_SELECT_COLS,
        _attendance_config,
        _attendance_supabase_client,
        _attendance_pill,
        _make_odoo_request,
        _normalize_emp_code,
        _parse_punch_datetime,
    )

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

# Defensive cap mirroring the dashboard's range endpoint.
MAX_RANGE_DAYS = 95

# Prezlab's standard work week: Sun–Thu = {Mon=0, Tue=1, Wed=2, Thu=3, Sun=6}.
# Used as the neutral default when a calendar can't be resolved (matches the
# dashboard's PREZLAB_DEFAULT_DAYS).
PREZLAB_DEFAULT_DAYS: FrozenSet[int] = frozenset({0, 1, 2, 3, 6})

_DAY_NAMES: Dict[str, int] = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


# --------------------------------------------------------------------------- #
# Calendar-name parsing (ported from the dashboard's calendar_parser.py)
# --------------------------------------------------------------------------- #

def _normalize_token(token: str) -> Optional[int]:
    return _DAY_NAMES.get(token.strip().lower())


def _expand_range(start: int, end: int) -> FrozenSet[int]:
    if start <= end:
        return frozenset(range(start, end + 1))
    return frozenset(list(range(start, 7)) + list(range(0, end + 1)))


def _parse_working_segment(segment: str) -> Optional[FrozenSet[int]]:
    import re
    segment = segment.strip()
    range_match = re.match(r"^([A-Za-z]+)\s*-\s*([A-Za-z]+)$", segment)
    if range_match:
        s = _normalize_token(range_match.group(1))
        e = _normalize_token(range_match.group(2))
        if s is not None and e is not None:
            return _expand_range(s, e)
    if "," in segment:
        tokens = [t for t in segment.split(",") if t.strip()]
        days = {_normalize_token(t) for t in tokens}
        if days and None not in days:
            return frozenset(d for d in days if d is not None)
    return None


def parse_working_days(calendar_name: str) -> FrozenSet[int]:
    """Weekdays a calendar covers, parsed from its display name.

    Fails safe to the Prezlab default (Sun–Thu) rather than all-7-days, matching
    the dashboard. Never returns an empty set.
    """
    if not calendar_name or not str(calendar_name).strip():
        return PREZLAB_DEFAULT_DAYS
    parts = [p.strip() for p in str(calendar_name).split("|")]
    for segment in (parts[1:-1] if len(parts) >= 3 else []):
        days = _parse_working_segment(segment)
        if days is not None:
            return days
    return PREZLAB_DEFAULT_DAYS


def _many2one_id(value: Any) -> Optional[int]:
    """Extract an id from an Odoo many2one shape: [id, name] | int | False."""
    if value is False or value is None:
        return None
    if isinstance(value, list) and value and isinstance(value[0], int):
        return value[0]
    if isinstance(value, int):
        return value
    return None


def _coerce_dayofweek(value: Any) -> Optional[int]:
    if value is False or value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 6 else None


# --------------------------------------------------------------------------- #
# Pure builders (ported from the dashboard; schedule + leave + holiday)
# --------------------------------------------------------------------------- #

STATUS_WORKED = "worked"      # show the punch times / duration
STATUS_ABSENT = "absent"
STATUS_ON_LEAVE = "on_leave"
STATUS_HOLIDAY = "holiday"


@dataclass(frozen=True)
class Member:
    """A team member's attendance-relevant identity + schedule."""

    emp_code: str
    name: str
    working_days: FrozenSet[int] = PREZLAB_DEFAULT_DAYS  # weekday ints Mon=0..Sun=6
    company_id: Optional[int] = None
    employee_id: Optional[int] = None


@dataclass(frozen=True)
class DayRow:
    """One scheduled day for a member."""

    day: date
    status: str  # one of STATUS_*
    punch_in: Optional[datetime] = None
    punch_out: Optional[datetime] = None
    worked_minutes: Optional[int] = None


@dataclass(frozen=True)
class MemberRange:
    """A member's scheduled days across the range, plus rollup totals."""

    emp_code: str
    name: str
    days_worked: int
    total_worked_minutes: int
    days: List[DayRow] = field(default_factory=list)


def _sort_key(emp_code: str):
    """emp_code descending, numeric where possible (dashboard convention)."""
    try:
        return (0, -int(emp_code))
    except (TypeError, ValueError):
        return (1, emp_code)


def _build_day_row(
    member: Member,
    day: date,
    punches: List[datetime],
    *,
    on_leave: bool,
    on_holiday: bool,
    today: date,
) -> Optional[DayRow]:
    """Resolve one scheduled day to a DayRow, or None if not scheduled.

    Status precedence matches the dashboard: Holiday > On leave > (worked |
    Absent). A day with punches always shows the times; the status flag only
    governs the worked-time column. Absent fires only on a settled day (strictly
    before `today`) with no punch and no excuse — the current day with no punch
    yet stays blank rather than wrongly absent.
    """
    if day.weekday() not in member.working_days:
        return None  # not a scheduled working day → hidden, like the dashboard

    times = sorted(punches)
    if times:
        first: Optional[datetime] = times[0]
        last = times[-1]
        punch_out = last if last != first else None
    else:
        first = None
        punch_out = None
    worked_minutes = (
        int((punch_out - first).total_seconds() // 60)
        if (punch_out is not None and first is not None)
        else None
    )

    if on_holiday:
        status = STATUS_HOLIDAY
    elif on_leave:
        status = STATUS_ON_LEAVE
    elif times:
        status = STATUS_WORKED
    elif day < today:
        status = STATUS_ABSENT
    else:
        # Scheduled, no punch yet, but not settled (today/future) → show blank,
        # not Absent.
        status = STATUS_WORKED

    return DayRow(
        day=day,
        status=status,
        punch_in=first,
        punch_out=punch_out,
        worked_minutes=worked_minutes,
    )


def build_member_range(
    members: List[Member],
    days: List[date],
    *,
    punch_by_day: Mapping[date, Mapping[str, List[datetime]]],
    on_leave_by_day: Mapping[date, Set[str]],
    on_holiday_by_day: Mapping[date, Set[str]],
    today: date,
) -> List[MemberRange]:
    """One entry per member with a DayRow per SCHEDULED day in the range.

    days_worked / total_worked_minutes count genuinely-worked days only (status
    == worked AND a real duration); excused or absent days never contribute.
    """
    out: List[MemberRange] = []
    for m in members:
        rows: List[DayRow] = []
        days_worked = 0
        total_minutes = 0
        for day in days:
            day_punches = punch_by_day.get(day, {}).get(m.emp_code, [])
            row = _build_day_row(
                m,
                day,
                day_punches,
                on_leave=m.emp_code in on_leave_by_day.get(day, set()),
                on_holiday=m.emp_code in on_holiday_by_day.get(day, set()),
                today=today,
            )
            if row is None:
                continue
            rows.append(row)
            if row.status == STATUS_WORKED and row.worked_minutes is not None:
                days_worked += 1
                total_minutes += row.worked_minutes
        out.append(
            MemberRange(
                emp_code=m.emp_code,
                name=m.name,
                days_worked=days_worked,
                total_worked_minutes=total_minutes,
                days=rows,
            )
        )
    out.sort(key=lambda r: _sort_key(r.emp_code))
    return out


# --------------------------------------------------------------------------- #
# Formatters
# --------------------------------------------------------------------------- #

def _fmt_time(value: Optional[datetime]) -> str:
    return "—" if value is None else value.strftime("%H:%M")


def _fmt_worked_minutes(minutes: Optional[int]) -> str:
    if minutes is None:
        return "—"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m"


def _fmt_weekday_date(day: date) -> str:
    return f"{day.strftime('%a')} {day.strftime('%d-%m-%Y')}"


def _week_bounds(day: date) -> Tuple[date, date]:
    """(Sunday, Saturday) of the week containing `day`.

    Sun-anchored to match the Attendance Dashboard's week grouping. Python's
    weekday() is Mon=0..Sun=6, so days since Sunday = (weekday + 1) % 7.
    """
    since_sunday = (day.weekday() + 1) % 7
    start = day - timedelta(days=since_sunday)
    return start, start + timedelta(days=6)


def _fmt_week_label(day: date) -> str:
    """'WEEK OF 31 MAY – 6 JUN 2026' for the Sun–Sat week containing `day`.

    Mirrors the dashboard's week divider: drops the start year when both ends
    share it, and drops the start month when both ends share it.
    """
    start, end = _week_bounds(day)
    end_str = f"{end.day} {end.strftime('%b').upper()} {end.year}"
    if start.year != end.year:
        start_str = f"{start.day} {start.strftime('%b').upper()} {start.year}"
    elif start.month != end.month:
        start_str = f"{start.day} {start.strftime('%b').upper()}"
    else:
        start_str = f"{start.day}"
    return f"WEEK OF {start_str} – {end_str}"


def _worked_cell(row: DayRow) -> str:
    """The Worked-time column cell — a status pill or the formatted duration."""
    if row.status == STATUS_HOLIDAY:
        return _attendance_pill("Holiday", "holiday")
    if row.status == STATUS_ON_LEAVE:
        return _attendance_pill("On leave", "on_leave")
    if row.status == STATUS_ABSENT:
        return _attendance_pill("Absent", "absent")
    return _fmt_worked_minutes(row.worked_minutes)


# --------------------------------------------------------------------------- #
# Data fetch (Nasma-wired)
# --------------------------------------------------------------------------- #

def _iter_days(start_date: date, end_date: date) -> List[date]:
    days: List[date] = []
    d = start_date
    while d <= end_date:
        days.append(d)
        d += timedelta(days=1)
    return days


def _local_today(tzname: str) -> date:
    try:
        if ZoneInfo and tzname:
            return datetime.now(ZoneInfo(tzname)).date()
    except Exception:
        pass
    return datetime.now().date()


def fetch_member_schedules(
    odoo_service,
    employee_ids: List[int],
) -> Tuple[bool, Any]:
    """Return (ok, {employee_id: (resource_calendar_id, company_id)}).

    Reads hr.employee directly (not the cached direct-reports helper, which
    doesn't carry these fields) so we can resolve each member's working days and
    holiday company.
    """
    try:
        ids = [i for i in employee_ids if isinstance(i, int)]
        if not ids:
            return True, {}
        params = {
            "args": [[("id", "in", ids)]],
            "kwargs": {
                "fields": ["id", "resource_calendar_id", "company_id"],
                "limit": len(ids) + 1,
            },
        }
        ok, rows = _make_odoo_request(odoo_service, "hr.employee", "search_read", params)
        if not ok:
            return False, rows
        out: Dict[int, Tuple[Optional[int], Optional[int]]] = {}
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            eid = r.get("id")
            if not isinstance(eid, int):
                continue
            out[eid] = (
                _many2one_id(r.get("resource_calendar_id")),
                _many2one_id(r.get("company_id")),
            )
        return True, out
    except Exception as e:
        return False, f"Schedule lookup error: {e}"


def fetch_working_days_by_calendar(
    odoo_service,
    calendar_ids: List[int],
) -> Tuple[bool, Any]:
    """Return (ok, {calendar_id: frozenset[weekday]}) for the given calendars.

    Tier 1: structured resource.calendar.attendance rows (dayofweek, Mon=0).
    Tier 2: parse the calendar display name. Unknown/empty → Prezlab default.
    active_test=False so archived calendars/attendance rows are included (the
    dashboard learned this the hard way — archived calendars otherwise fall
    through to a wrong default and mis-flag weekends).
    """
    try:
        ids = sorted({c for c in calendar_ids if isinstance(c, int)})
        if not ids:
            return True, {}

        ctx = {"active_test": False}
        ok_cal, calendars = _make_odoo_request(
            odoo_service, "resource.calendar", "search_read",
            {
                "args": [[("id", "in", ids)]],
                "kwargs": {"fields": ["id", "name"], "context": ctx, "limit": len(ids) + 1},
            },
        )
        if not ok_cal:
            return False, calendars
        names_by_id: Dict[int, str] = {
            r["id"]: str(r.get("name") or "")
            for r in (calendars or []) if isinstance(r, dict) and "id" in r
        }

        ok_att, att_rows = _make_odoo_request(
            odoo_service, "resource.calendar.attendance", "search_read",
            {
                "args": [[("calendar_id", "in", ids)]],
                "kwargs": {"fields": ["calendar_id", "dayofweek"], "context": ctx, "limit": 1000},
            },
        )
        structured: Dict[int, Set[int]] = {}
        if ok_att:
            for r in att_rows or []:
                if not isinstance(r, dict):
                    continue
                cal_id = _many2one_id(r.get("calendar_id"))
                dow = _coerce_dayofweek(r.get("dayofweek"))
                if cal_id is None or dow is None:
                    continue
                structured.setdefault(cal_id, set()).add(dow)

        days_by_calendar: Dict[int, FrozenSet[int]] = {}
        for cal_id, name in names_by_id.items():
            if structured.get(cal_id):
                days_by_calendar[cal_id] = frozenset(structured[cal_id])
            else:
                days_by_calendar[cal_id] = parse_working_days(name)
        return True, days_by_calendar
    except Exception as e:
        return False, f"Calendar lookup error: {e}"


def _range_bounds(start_date: date, end_date: date) -> Tuple[datetime, datetime]:
    lo = datetime.combine(start_date, time.min)
    hi = datetime.combine(end_date + timedelta(days=1), time.min)
    return lo, hi


def fetch_punches_grouped_by_day(
    emp_codes: List[str],
    start_date: date,
    end_date: date,
) -> Tuple[bool, Any]:
    """Return (ok, {date: {emp_code: [punch_datetime, ...]}}) for the range."""
    try:
        codes = sorted({c for c in (_normalize_emp_code(c) for c in emp_codes) if c})
        if not codes:
            return True, {}

        url, key, _tz = _attendance_config()
        client = _attendance_supabase_client(url, key)
        lo, hi = _range_bounds(start_date, end_date)

        rows_out: List[Dict[str, Any]] = []
        page_size = 1000
        offset = 0
        while True:
            response = (
                client.table(ATTENDANCE_TABLE)
                .select(ATTENDANCE_SELECT_COLS)
                .in_("emp_code", codes)
                .gte("punch_time", lo.isoformat())
                .lt("punch_time", hi.isoformat())
                .order("punch_time", desc=False)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                break
            rows_out.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size

        grouped: Dict[date, Dict[str, List[datetime]]] = {}
        for row in rows_out:
            if not isinstance(row, dict):
                continue
            code = _normalize_emp_code(row.get("emp_code"))
            if not code:
                continue
            try:
                dt = _parse_punch_datetime(row.get("punch_time"))
            except Exception:
                continue
            grouped.setdefault(dt.date(), {}).setdefault(code, []).append(dt)
        return True, grouped
    except Exception as e:
        return False, f"Attendance lookup error: {e}"


def _parse_odoo_date(value: Any) -> Optional[date]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def fetch_on_leave_by_day(
    odoo_service,
    emp_code_by_employee_id: Mapping[int, str],
    start_date: date,
    end_date: date,
) -> Tuple[bool, Any]:
    """Return (ok, {date: {emp_code, ...}}) of approved full-day leaves in range."""
    try:
        employee_ids = [eid for eid in emp_code_by_employee_id if eid]
        if not employee_ids:
            return True, {}
        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        domain = [
            ("employee_id", "in", employee_ids),
            ("state", "=", "validate"),
            ("request_date_from", "<=", end_date.isoformat()),
            ("request_date_to", ">=", start_date.isoformat()),
        ]
        params = {
            "args": [domain],
            "kwargs": {
                "fields": ["id", "employee_id", "request_date_from", "request_date_to"],
                "limit": 1000,
            },
        }
        ok, data = _make_odoo_request(odoo_service, "hr.leave", "search_read", params)
        if not ok:
            return False, data

        by_day: Dict[date, Set[str]] = {}
        for lv in data or []:
            if not isinstance(lv, dict):
                continue
            emp_id = _many2one_id(lv.get("employee_id"))
            code = emp_code_by_employee_id.get(emp_id) if emp_id is not None else None
            if not code:
                continue
            lv_from = _parse_odoo_date(lv.get("request_date_from"))
            lv_to = _parse_odoo_date(lv.get("request_date_to")) or lv_from
            if lv_from is None:
                continue
            d = max(lv_from, start_date)
            end_clip = min(lv_to, end_date)
            while d <= end_clip:
                by_day.setdefault(d, set()).add(code)
                d += timedelta(days=1)
        return True, by_day
    except Exception as e:
        return False, f"Leave lookup error: {e}"


def fetch_holiday_by_day(
    odoo_service,
    members: List[Member],
    start_date: date,
    end_date: date,
    tzname: str,
) -> Tuple[bool, Any]:
    """Return (ok, {date: {emp_code, ...}}) of company-wide public holidays.

    Reads resource.calendar.leaves company-wide rows (resource_id = False),
    converts Odoo's UTC datetimes to local days, then expands each holiday's
    company → the emp_codes of members in that company. Ported from the
    dashboard's OdooHolidayRepository.
    """
    try:
        members_by_company: Dict[int, Set[str]] = {}
        for m in members:
            if m.company_id is not None:
                members_by_company.setdefault(m.company_id, set()).add(m.emp_code)
        if not members_by_company:
            return True, {}

        ok_session, msg = odoo_service.ensure_active_session()
        if not ok_session:
            return False, msg

        # Pad the fetch window by a day each side (UTC vs local skew); the exact
        # day marking below, after tz conversion, keeps the result precise.
        fetch_from = (start_date - timedelta(days=1)).isoformat() + " 00:00:00"
        fetch_to = (end_date + timedelta(days=1)).isoformat() + " 23:59:59"
        domain = [
            ("resource_id", "=", False),
            ("date_from", "<=", fetch_to),
            ("date_to", ">=", fetch_from),
        ]
        params = {
            "args": [domain],
            "kwargs": {
                "fields": ["company_id", "resource_id", "date_from", "date_to"],
                "limit": 1000,
            },
        }
        ok, rows = _make_odoo_request(odoo_service, "resource.calendar.leaves", "search_read", params)
        if not ok:
            return False, rows

        tz = None
        try:
            if ZoneInfo and tzname:
                tz = ZoneInfo(tzname)
        except Exception:
            tz = None

        def _coerce_local_date(raw: Any) -> Optional[date]:
            text = str(raw or "").strip()
            if not text:
                return None
            if len(text) == 10:
                return _parse_odoo_date(text)
            try:
                naive = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return _parse_odoo_date(text)
            if tz is not None:
                from datetime import timezone as _tz
                return naive.replace(tzinfo=_tz.utc).astimezone(tz).date()
            return naive.date()

        by_day: Dict[date, Set[str]] = {}
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            if _many2one_id(r.get("resource_id")) is not None:
                continue  # individual leave, not a company holiday
            company_id = _many2one_id(r.get("company_id"))
            if company_id is None or company_id not in members_by_company:
                continue
            d_from = _coerce_local_date(r.get("date_from"))
            d_to = _coerce_local_date(r.get("date_to")) or d_from
            if d_from is None or d_to is None or d_to < d_from:
                continue
            affected = members_by_company[company_id]
            d = max(d_from, start_date)
            end_clip = min(d_to, end_date)
            while d <= end_clip:
                by_day.setdefault(d, set()).update(affected)
                d += timedelta(days=1)
        return True, by_day
    except Exception as e:
        return False, f"Holiday lookup error: {e}"


# --------------------------------------------------------------------------- #
# Widget builder (standard Nasma table-widget shape)
# --------------------------------------------------------------------------- #

# Columns whose cell values are raw HTML (status pills) and must NOT be escaped
# by the frontend. The frontend keys off this list.
HTML_COLUMNS = ["worked"]


def build_widget_from_ranges(
    ranges: List[MemberRange],
    start_date: date,
    end_date: date,
) -> Dict[str, Any]:
    """Build the `{columns, rows, html_columns}` table widget from MemberRanges.

    Single day → flat one-row-per-member (only members scheduled that day). Range
    → grouped per-member sections (banner + one row per scheduled day). Status
    cells in the `worked` column are pill HTML (Holiday/On leave/Absent).
    """
    if start_date == end_date:
        columns = [
            {"key": "member", "label": "Member"},
            {"key": "punch_in", "label": "Punch in", "align": "center"},
            {"key": "punch_out", "label": "Punch out", "align": "center"},
            {"key": "worked", "label": "Worked time", "align": "center"},
        ]
        rows: List[Dict[str, Any]] = []
        for mr in ranges:
            # Single day → at most one DayRow (and none if not scheduled today).
            if not mr.days:
                continue
            d = mr.days[0]
            rows.append({
                "member": mr.name,
                "punch_in": _fmt_time(d.punch_in),
                "punch_out": _fmt_time(d.punch_out),
                "worked": _worked_cell(d),
            })
        return {"columns": columns, "rows": rows, "html_columns": HTML_COLUMNS}

    columns = [
        # No "Member / Day" header label — the member name lives in the section
        # banner and the day labels are self-evident, matching the dashboard.
        {"key": "member", "label": ""},
        {"key": "punch_in", "label": "Punch in", "align": "center"},
        {"key": "punch_out", "label": "Punch out", "align": "center"},
        {"key": "worked", "label": "Worked time", "align": "center"},
    ]
    rows = []
    for mr in ranges:
        days_label = "1 day" if mr.days_worked == 1 else f"{mr.days_worked} days"
        total = _fmt_worked_minutes(mr.total_worked_minutes)
        rows.append({
            "_section": True,
            "member": mr.name,
            "punch_in": "",
            "punch_out": "",
            "worked": f"{days_label} · {total}",
        })
        # Inject a lightweight "WEEK OF …" divider whenever the week changes
        # within this member's days (Sun–Sat weeks, like the dashboard).
        current_week: Optional[date] = None
        for d in mr.days:
            week_start, _ = _week_bounds(d.day)
            if week_start != current_week:
                current_week = week_start
                rows.append({
                    "_week": True,
                    "member": _fmt_week_label(d.day),
                    "punch_in": "",
                    "punch_out": "",
                    "worked": "",
                })
            rows.append({
                "member": _fmt_weekday_date(d.day),
                "punch_in": _fmt_time(d.punch_in),
                "punch_out": _fmt_time(d.punch_out),
                "worked": _worked_cell(d),
            })
    return {"columns": columns, "rows": rows, "html_columns": HTML_COLUMNS}


def build_attendance_report_widget(
    members: List[Member],
    punch_by_day: Mapping[date, Mapping[str, List[datetime]]],
    on_leave_by_day: Mapping[date, Set[str]],
    on_holiday_by_day: Mapping[date, Set[str]],
    start_date: date,
    end_date: date,
    today: date,
) -> Dict[str, Any]:
    """Convenience wrapper: build MemberRanges from raw data, then the widget.

    Kept so callers/tests can go straight from raw maps to a widget in one step;
    the production orchestrator uses `_gather_member_ranges` + `build_widget_from_ranges`
    so it can reuse the ranges for the export too.
    """
    ranges = build_member_range(
        members,
        _iter_days(start_date, end_date),
        punch_by_day=punch_by_day,
        on_leave_by_day=on_leave_by_day,
        on_holiday_by_day=on_holiday_by_day,
        today=today,
    )
    return build_widget_from_ranges(ranges, start_date, end_date)


# --------------------------------------------------------------------------- #
# Orchestrator (single entry point for the route)
# --------------------------------------------------------------------------- #

def _gather_member_ranges(
    odoo_service,
    employee_service,
    start_date: date,
    end_date: date,
) -> Tuple[bool, Any, date]:
    """Fetch + assemble the team's scheduled attendance over the range.

    Returns (ok, ranges_or_error, today) where ranges is the list of
    MemberRange the widget builder and the export adapter both consume. All
    the Odoo/Supabase round-trips live here so the screen view and the export
    are built from exactly the same data — they can't disagree.
    """
    _url, _key, tzname = _attendance_config()
    today = _local_today(tzname)

    ok_team, team = employee_service.get_direct_reports_current_user()
    if not ok_team:
        return False, team, today
    if not isinstance(team, list) or not team:
        return True, [], today

    # Base identity from the team list.
    base: Dict[str, Dict[str, Any]] = {}  # emp_code -> partial member dict
    emp_code_by_employee_id: Dict[int, str] = {}
    employee_ids: List[int] = []
    for m in team:
        if not isinstance(m, dict):
            continue
        code = _normalize_emp_code(m.get("emp_code"))
        if not code:
            continue
        eid = m.get("id") if isinstance(m.get("id"), int) else None
        base[code] = {"name": m.get("name") or code, "employee_id": eid}
        if eid is not None:
            emp_code_by_employee_id[eid] = code
            employee_ids.append(eid)

    # Schedules (calendar + company) per member.
    ok_sched, sched = fetch_member_schedules(odoo_service, employee_ids)
    if not ok_sched:
        sched = {}  # degrade: default schedule for everyone
    calendar_by_employee: Dict[int, Optional[int]] = {}
    company_by_employee: Dict[int, Optional[int]] = {}
    for eid, (cal_id, comp_id) in (sched or {}).items():
        calendar_by_employee[eid] = cal_id
        company_by_employee[eid] = comp_id

    # Resolve calendars → working weekday sets.
    cal_ids = [c for c in calendar_by_employee.values() if isinstance(c, int)]
    ok_cal, days_by_calendar = fetch_working_days_by_calendar(odoo_service, cal_ids)
    if not ok_cal:
        days_by_calendar = {}

    # Assemble Member objects.
    members: List[Member] = []
    for code, info in base.items():
        eid = info["employee_id"]
        cal_id = calendar_by_employee.get(eid) if eid is not None else None
        comp_id = company_by_employee.get(eid) if eid is not None else None
        working = days_by_calendar.get(cal_id, PREZLAB_DEFAULT_DAYS) if cal_id is not None else PREZLAB_DEFAULT_DAYS
        members.append(Member(
            emp_code=code,
            name=info["name"],
            working_days=working,
            company_id=comp_id,
            employee_id=eid,
        ))

    # Punches.
    ok_punch, punch_by_day = fetch_punches_grouped_by_day(
        [m.emp_code for m in members], start_date, end_date
    )
    if not ok_punch:
        return False, punch_by_day, today

    # Leave (supplementary — degrade gracefully).
    ok_leave, on_leave_by_day = fetch_on_leave_by_day(
        odoo_service, emp_code_by_employee_id, start_date, end_date
    )
    if not ok_leave:
        on_leave_by_day = {}

    # Holidays (supplementary — degrade gracefully).
    ok_hol, on_holiday_by_day = fetch_holiday_by_day(
        odoo_service, members, start_date, end_date, tzname
    )
    if not ok_hol:
        on_holiday_by_day = {}

    days = _iter_days(start_date, end_date)
    ranges = build_member_range(
        members,
        days,
        punch_by_day=punch_by_day,
        on_leave_by_day=on_leave_by_day,
        on_holiday_by_day=on_holiday_by_day,
        today=today,
    )
    return True, ranges, today


def get_team_attendance_report(
    odoo_service,
    employee_service,
    start_date: date,
    end_date: date,
) -> Tuple[bool, Any]:
    """Assemble the team attendance report widget for the current manager."""
    try:
        ok, ranges, _today = _gather_member_ranges(
            odoo_service, employee_service, start_date, end_date
        )
        if not ok:
            return False, ranges
        widget = build_widget_from_ranges(ranges, start_date, end_date)
        return True, widget
    except Exception as e:
        return False, f"Error building team attendance report: {e}"
