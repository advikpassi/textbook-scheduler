"""Parse Course Finder meeting times and render a weekly schedule PDF.


Meeting-time strings look like::


    "MWR 1:30PM - 2:45PM \r\n\t Gittis Hall 1"
    "R 10:30AM - 11:45AM \r\n\t Gittis Hall 214 , MW 1:30PM - 2:45PM \r\n\t Gittis Hall 214"
    "TBA  TBA"


i.e. one or more comma-separated meetings, each ``<DAYS> <START> - <END> <room>``.
Days are single letters: M T W R F S U (Mon..Sun).
"""
from __future__ import annotations


import io
import re
from dataclasses import dataclass


# Day letter -> (order, full name). R = Thursday, U = Sunday (registrar convention).
DAY_ORDER = ["M", "T", "W", "R", "F", "S", "U"]
DAY_NAMES = {
    "M": "Monday",
    "T": "Tuesday",
    "W": "Wednesday",
    "R": "Thursday",
    "F": "Friday",
    "S": "Saturday",
    "U": "Sunday",
}


_MEETING_RE = re.compile(
    r"([MTWRFSU]+)\s+(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)\s*(.*)",
    re.I | re.S,
)




@dataclass
class Meeting:
    day: str          # single day letter
    start_min: int    # minutes since midnight
    end_min: int
    room: str
    title: str = ""
    section: str = ""


    @property
    def start_label(self) -> str:
        return _fmt(self.start_min)


    @property
    def end_label(self) -> str:
        return _fmt(self.end_min)




def _to_minutes(t: str) -> int:
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP])M", t.strip(), re.I)
    if not m:
        raise ValueError(f"bad time: {t!r}")
    h = int(m.group(1)) % 12
    if m.group(3).upper() == "P":
        h += 12
    return h * 60 + int(m.group(2))




def _fmt(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    ampm = "AM" if h < 12 else "PM"
    hh = h % 12 or 12
    return f"{hh}:{m:02d} {ampm}"




def _clean_room(raw: str) -> str:
    room = re.sub(r"[\r\n\t]+", " ", raw)
    room = re.sub(r"\s+", " ", room).strip().strip(",").strip()
    return room




def parse_meetings(meeting_times: str, title: str = "", section: str = "") -> list[Meeting]:
    """Parse a meeting-times cell into individual per-day Meeting entries.


    Returns [] for TBA/blank or anything without a parseable time range.
    """
    if not meeting_times:
        return []
    text = str(meeting_times)
    meetings: list[Meeting] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk or chunk.upper().startswith("TBA"):
            continue
        m = _MEETING_RE.match(chunk)
        if not m:
            continue
        days, start, end, room = m.groups()
        try:
            s, e = _to_minutes(start), _to_minutes(end)
        except ValueError:
            continue
        room = _clean_room(room)
        for d in days.upper():
            if d in DAY_NAMES:
                meetings.append(
                    Meeting(day=d, start_min=s, end_min=e, room=room, title=title, section=section)
                )
    return meetings




def build_schedule_pdf(courses: list, title: str = "My Class Schedule") -> bytes:
    """Render a landscape weekly-grid PDF for the given courses.


    ``courses`` is any iterable of objects with ``.title``, ``.section`` and
    ``.meeting_times`` attributes (e.g. scraper.Course). Returns PDF bytes.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas


    # Collect meetings and any TBA/unscheduled courses (listed separately).
    meetings: list[Meeting] = []
    unscheduled: list[str] = []
    for c in courses:
        ms = parse_meetings(getattr(c, "meeting_times", ""), c.title, c.section)
        if ms:
            meetings.extend(ms)
        else:
            unscheduled.append(f"{c.section} — {c.title}")


    # Which day columns to show: Mon-Fri always, weekends only if used.
    used_days = {m.day for m in meetings}
    days = [d for d in DAY_ORDER if d in {"M", "T", "W", "R", "F"} or d in used_days]


    # Time bounds (rounded to the hour), with a sensible default if empty.
    if meetings:
        start_min = min(m.start_min for m in meetings) // 60 * 60
        end_min = -(-max(m.end_min for m in meetings) // 60) * 60  # ceil to hour
    else:
        start_min, end_min = 8 * 60, 18 * 60


    buf = io.BytesIO()
    page_w, page_h = landscape(letter)
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))


    margin = 0.5 * inch
    header_h = 0.5 * inch  # page title
    col_header_h = 0.28 * inch  # day-name row
    time_col_w = 0.9 * inch


    grid_top = page_h - margin - header_h
    grid_bottom = margin + (0.9 * inch if unscheduled else 0.0)
    grid_left = margin + time_col_w
    grid_right = page_w - margin


    grid_h = grid_top - col_header_h - grid_bottom
    grid_w = grid_right - grid_left
    col_w = grid_w / max(len(days), 1)
    total_min = max(end_min - start_min, 60)
    px_per_min = grid_h / total_min


    def y_for(minute: int) -> float:
        # Earlier time -> higher on the page.
        return (grid_top - col_header_h) - (minute - start_min) * px_per_min


    # Page title.
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, page_h - margin - 0.28 * inch, title)


    # Day-column headers.
    c.setFont("Helvetica-Bold", 11)
    for i, d in enumerate(days):
        x = grid_left + i * col_w
        c.drawCentredString(x + col_w / 2, grid_top - col_header_h + 0.07 * inch, DAY_NAMES[d])


    # Horizontal hour lines + time labels.
    c.setFont("Helvetica", 8)
    for minute in range(start_min, end_min + 1, 60):
        y = y_for(minute)
        c.setStrokeColor(colors.lightgrey)
        c.line(grid_left, y, grid_right, y)
        c.setFillColor(colors.grey)
        c.drawRightString(grid_left - 4, y - 3, _fmt(minute))
    c.setFillColor(colors.black)


    # Vertical day separators + outer border.
    c.setStrokeColor(colors.grey)
    for i in range(len(days) + 1):
        x = grid_left + i * col_w
        c.line(x, y_for(start_min), x, grid_top - col_header_h)
    c.rect(grid_left, y_for(end_min), grid_w, (grid_top - col_header_h) - y_for(end_min))


    # A palette so different courses get different block colors.
    palette = [
        colors.HexColor("#4C78A8"), colors.HexColor("#F58518"),
        colors.HexColor("#54A24B"), colors.HexColor("#B279A2"),
        colors.HexColor("#E45756"), colors.HexColor("#72B7B2"),
        colors.HexColor("#EECA3B"), colors.HexColor("#FF9DA6"),
    ]
    color_by_section: dict[str, colors.Color] = {}


    def color_for(section: str) -> colors.Color:
        if section not in color_by_section:
            color_by_section[section] = palette[len(color_by_section) % len(palette)]
        return color_by_section[section]


    # Draw each meeting as a filled block.
    day_index = {d: i for i, d in enumerate(days)}
    for m in meetings:
        if m.day not in day_index:
            continue
        i = day_index[m.day]
        x = grid_left + i * col_w + 1.5
        w = col_w - 3
        y_top = y_for(m.start_min)
        y_bot = y_for(m.end_min)
        h = max(y_top - y_bot, 10)
        fill = color_for(m.section)
        c.setFillColor(fill)
        c.setStrokeColor(colors.white)
        c.rect(x, y_bot, w, h, fill=1, stroke=1)


        # Label text (title / time / room), clipped to the block.
        c.setFillColor(colors.white)
        pad = 2
        tx = x + pad
        ty = y_top - 9
        c.saveState()
        p = c.beginPath()
        p.rect(x, y_bot, w, h)
        c.clipPath(p, stroke=0)
        c.setFont("Helvetica-Bold", 7)
        for line in _wrap(c, m.title, w - 2 * pad, "Helvetica-Bold", 7):
            c.drawString(tx, ty, line)
            ty -= 8
        c.setFont("Helvetica", 6.5)
        for line in [f"{m.start_label}–{m.end_label}", m.room]:
            if line and ty > y_bot + 2:
                c.drawString(tx, ty, line)
                ty -= 7.5
        c.restoreState()


    # Footer: any TBA / unscheduled courses.
    if unscheduled:
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin, margin + 0.55 * inch, "Unscheduled / TBA:")
        c.setFont("Helvetica", 8)
        c.drawString(margin, margin + 0.38 * inch, "; ".join(unscheduled)[:180])


    c.showPage()
    c.save()
    return buf.getvalue()




def _wrap(c, text: str, max_w: float, font: str, size: float) -> list[str]:
    """Greedy word-wrap for a canvas at the given font/size."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if c.stringWidth(trial, font, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]  # cap so a long title can't overflow a small block



