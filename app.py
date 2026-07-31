"""Streamlit app: pick your UPenn Law classes, see every textbook you need to buy.

The class list comes from a Course Finder Excel export in this folder
(default: Course_Finder_Results_115443.xls). Run on your host machine (where
goat.law.upenn.edu is reachable):

    pip install -r requirements.txt
    streamlit run app.py
"""
from __future__ import annotations

import glob
import os

import pandas as pd
import streamlit as st

import scraper
import schedule as schedule_mod

# Live "search as you type" needs the streamlit-keyup component, which reruns on
# every keystroke. Fall back to the built-in text box (Enter/blur to search) if
# it isn't installed.
try:
    from st_keyup import st_keyup

    HAS_KEYUP = True
except ImportError:  # pragma: no cover
    HAS_KEYUP = False

st.set_page_config(page_title="My Law Textbooks", page_icon="📚", layout="wide")
st.title("📚 My Law Textbooks")
st.caption("Check the classes you're taking and get the combined list of books to buy.")

HERE = os.path.dirname(os.path.abspath(__file__))


def find_class_files() -> list[str]:
    """All Course Finder Excel exports in this folder (each is one term)."""
    return sorted(glob.glob(os.path.join(HERE, "*.xls"))) + sorted(
        glob.glob(os.path.join(HERE, "*.xlsx"))
    )


@st.cache_data(show_spinner=False)
def term_options(paths: tuple[str, ...], mtimes: tuple[float, ...]) -> list[dict]:
    """Build (file, label, code) options, one per Excel.

    Label = the file's name (e.g. "Fall 2026"). Term code = derived from the
    file's Term column, falling back to the filename.
    """
    opts = []
    for p in paths:
        label = os.path.splitext(os.path.basename(p))[0]
        term_label = scraper.read_term_label(p) or label
        opts.append(
            {"path": p, "label": label, "code": scraper.term_label_to_code(term_label)}
        )
    return opts


@st.cache_data(show_spinner=False)
def load_courses(path: str, mtime: float) -> list[dict]:
    # mtime is part of the cache key so edits to the file refresh the list.
    return [vars(c) for c in scraper.load_courses_from_excel(path)]


@st.cache_data(show_spinner=False)
def fetch_books(slug: str, section: str, term: str, label: str) -> list[dict]:
    books = scraper.get_textbooks(slug, section, term, course_label=label)
    return [b.to_dict() for b in books]


class_files = find_class_files()
if not class_files:
    st.error(
        "No class list found. Put a Course Finder Excel export (a .xls/.xlsx file) "
        "in this folder next to app.py."
    )
    st.stop()

options = term_options(tuple(class_files), tuple(os.path.getmtime(p) for p in class_files))

with st.sidebar:
    st.header("Setup")
    # Pick which term (= which Excel file) to load. Each option is labeled by its
    # file name, e.g. "Fall 2026". Default to Fall 2026 if present.
    labels = [o["label"] for o in options]
    default_idx = next((i for i, lbl in enumerate(labels) if lbl.lower() == "fall 2026"), 0)
    idx = st.selectbox("Term", range(len(options)), format_func=lambda i: labels[i], index=default_idx)
    chosen = options[idx]
    class_file = chosen["path"]

    # The scrape needs a Course Finder term code, derived from the selected file's
    # term label (A=Spring, B=Summer, C=Fall).
    term = chosen["code"]
    st.caption(f"File: `{os.path.basename(class_file)}`")
    st.divider()
    st.subheader("Filter")
    which = st.radio("Show", ["All", "Required only", "Optional only"], index=0)

courses = [scraper.Course(**c) for c in load_courses(class_file, os.path.getmtime(class_file))]
st.caption(f"Loaded {len(courses)} classes from `{os.path.basename(class_file)}` ({chosen['label']}).")

# --- Selectable class table ------------------------------------------------
st.subheader("Select your classes")

# Checked classes persist in session state (keyed by section), so filtering the
# table never loses selections for rows that are currently hidden. Reset them
# when the term/file changes so selections from another term don't linger.
if st.session_state.get("_active_file") != class_file:
    st.session_state.taken = set()
    st.session_state._active_file = class_file
if "taken" not in st.session_state:
    st.session_state.taken = set()

table = pd.DataFrame(
    {
        "Section": [c.section for c in courses],
        "Title": [c.title for c in courses],
        "Instructor": [c.instructor for c in courses],
        "Credits": [c.credits for c in courses],
        "Meeting Times": [c.meeting_times.replace("\r", " ").replace("\n", " ").replace("\t", " ") for c in courses],
    }
)

# Filter narrows the visible rows as you type. Selections live in session state,
# so none are lost while you search. With streamlit-keyup the table updates on
# every keystroke; otherwise it updates on Enter/blur.
_placeholder = "type a title, section, or instructor to narrow the table — checked classes are kept"
if HAS_KEYUP:
    search = st_keyup("Filter the list (optional)", placeholder=_placeholder, debounce=150, key="search")
else:
    search = st.text_input("Filter the list (optional)", placeholder=_placeholder, key="search")
search = search or ""

# Precompute each course's parsed meetings once, keyed by section.
meetings_by_section = {
    c.section: schedule_mod.parse_meetings(c.meeting_times, c.title, c.section) for c in courses
}

if search:
    # While searching, show ALL classes matching the query.
    mask = (
        table["Title"].str.contains(search, case=False, na=False)
        | table["Section"].str.contains(search, case=False, na=False)
        | table["Instructor"].str.contains(search, case=False, na=False)
    )
else:
    # With no search, show only classes that don't time-conflict with the ones
    # already selected — plus the selected classes themselves (so they stay
    # visible and can be unchecked). If nothing is selected, show everything.
    taken = st.session_state.taken
    selected_meetings = [m for s in taken for m in meetings_by_section.get(s, [])]

    def _keep(section: str) -> bool:
        if section in taken:
            return True
        return not schedule_mod.meetings_conflict(
            meetings_by_section.get(section, []), selected_meetings
        )

    mask = table["Section"].map(_keep) if taken else pd.Series(True, index=table.index)

view = table[mask].copy()
view.insert(0, "Take", view["Section"].isin(st.session_state.taken))

if not search and st.session_state.taken:
    st.caption(
        f"Showing {len(view)} classes that don't conflict with your selection "
        "(plus your selected classes). Search to see all classes."
    )

edited = st.data_editor(
    view,
    hide_index=True,
    use_container_width=True,
    height=420,
    disabled=["Section", "Title", "Instructor", "Credits", "Meeting Times"],
    column_config={"Take": st.column_config.CheckboxColumn("Take", help="Check the classes you're taking")},
    # Fresh key per filter so edit deltas can't misapply to different rows after
    # a search; the baseline "Take" is rehydrated from session state each time.
    key=f"class_table_{search}",
)

# Sync only the rows currently visible; hidden selections stay untouched.
visible = set(view["Section"])
checked_now = set(edited.loc[edited["Take"], "Section"])
st.session_state.taken = (st.session_state.taken - visible) | checked_now

selected = [c for c in courses if c.section in st.session_state.taken]

if selected:
    def _bold_label(c):
        # Bold the class name (title), keep section/instructor plain.
        rest = [c.section]
        if c.instructor:
            rest.append(c.instructor)
        return f"**{c.title or c.slug}** — " + " — ".join(rest)

    st.markdown("Selected: " + ", ".join(_bold_label(c) for c in selected))
else:
    st.info("Check one or more classes above to see their textbooks and schedule.")
    st.stop()

st.divider()

# --- Weekly schedule PDF ---------------------------------------------------
st.subheader("🗓️ Weekly schedule")
st.caption("A landscape weekly grid of your selected classes — when they meet and which room.")

# Preview the parsed meetings so it's clear what the PDF will contain.
sched_rows = []
for c in selected:
    ms = schedule_mod.parse_meetings(c.meeting_times, c.title, c.section)
    if ms:
        for m in ms:
            sched_rows.append(
                {
                    "Class": c.title,
                    "Section": c.section,
                    "Day": schedule_mod.DAY_NAMES[m.day],
                    "Start": m.start_label,
                    "End": m.end_label,
                    "Room": m.room,
                }
            )
    else:
        sched_rows.append(
            {"Class": c.title, "Section": c.section, "Day": "TBA", "Start": "", "End": "", "Room": ""}
        )

st.dataframe(pd.DataFrame(sched_rows), hide_index=True, use_container_width=True)

try:
    pdf_bytes = schedule_mod.build_schedule_pdf(selected, title="My Class Schedule")
    st.download_button(
        "⬇️ Download schedule PDF",
        data=pdf_bytes,
        file_name=f"schedule_{term}.pdf",
        mime="application/pdf",
    )
except Exception as e:  # noqa: BLE001
    st.error(f"Couldn't build the schedule PDF: {e}")

st.divider()

# --- Fetch & display textbooks --------------------------------------------
all_books: list[dict] = []
errors: list[str] = []
progress = st.progress(0.0, text="Fetching textbooks…")
for i, course in enumerate(selected, start=1):
    try:
        all_books.extend(fetch_books(course.slug, course.section, term, course.label))
    except Exception as e:  # noqa: BLE001
        errors.append(f"{course.label}: {e}")
    progress.progress(i / len(selected), text=f"Fetching textbooks… ({i}/{len(selected)})")
progress.empty()

for err in errors:
    st.error(err)

if not all_books:
    st.warning("No textbooks listed for the selected classes.")
    st.stop()

df = pd.DataFrame(all_books)

if which == "Required only":
    df = df[df["required"]]
elif which == "Optional only":
    df = df[~df["required"]]

# De-duplicate books shared across sections (same ISBN), merging their classes.
if not df.empty:
    df = (
        df.sort_values("course")
        .groupby("isbn", as_index=False, sort=False)
        .agg(
            {
                "title": "first",
                "author": "first",
                "edition": "first",
                "publisher": "first",
                "required": "max",
                "course": lambda s: ", ".join(sorted(set(s))),
                "amazon_url": "first",
                "google_books_url": "first",
                "penn_bookstore_url": "first",
            }
        )
    )

st.subheader(f"{len(df)} book(s) to buy")

# Show a red check mark for required books, blank for optional.
df = df.assign(required_mark=df["required"].map(lambda r: "✔" if r else ""))

display = df[
    [
        "course",
        "title",
        "author",
        "edition",
        "publisher",
        "isbn",
        "required_mark",
        "amazon_url",
        "google_books_url",
        "penn_bookstore_url",
    ]
].rename(
    columns={
        "course": "Class(es)",
        "title": "Title",
        "author": "Author",
        "edition": "Edition",
        "publisher": "Publisher",
        "isbn": "ISBN",
        "required_mark": "Required",
        "amazon_url": "Amazon",
        "google_books_url": "Google Books",
        "penn_bookstore_url": "Penn Bookstore",
    }
)

styled = display.style.map(
    lambda _: "color: red; font-weight: bold; text-align: center", subset=["Required"]
)

st.dataframe(
    styled,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Amazon": st.column_config.LinkColumn("Amazon", display_text="search"),
        "Google Books": st.column_config.LinkColumn("Google Books", display_text="find"),
        "Penn Bookstore": st.column_config.LinkColumn("Penn Bookstore", display_text="buy"),
        "Required": st.column_config.TextColumn("Required", help="✔ = required, blank = optional"),
    },
)

st.download_button(
    "⬇️ Download as CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name=f"textbooks_{term}.csv",
    mime="text/csv",
)
