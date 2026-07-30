"""Scrape textbook data from the UPenn Law Course Finder.


Pure logic module (no Streamlit imports) so the parser can be unit-tested offline.


The Course Finder exposes a per-section detail page whose URL looks like:


    https://goat.law.upenn.edu/cf/coursefinder/course-details/?course=<slug>&sec=LAW <sec>&term=<term>


Each detail page lists that section's required/optional textbooks inside
``table tr td p`` blocks. A single ``<p>`` may hold several book records, each of
the shape::


    "<em>Title</em>" by Author
    Edition: 13th  Publisher: Foundation Press  ISBN: 9781636594644  Required


This module fetches those pages, parses every book record, and (best-effort)
lists the course catalog for a term so the UI can offer a browsable picker.
"""


from __future__ import annotations


import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable
from urllib.parse import quote, urljoin


import requests
from bs4 import BeautifulSoup


BASE = "https://goat.law.upenn.edu"
DETAIL_URL = BASE + "/cf/coursefinder/course-details/"
CATALOG_URL = BASE + "/cf/coursefinder/"
DEFAULT_TERM = "2026C"


# Be a polite scraper: identify ourselves and pace requests. The tutorial the
# notebook was based on stresses respecting robots.txt and rate limits.
USER_AGENT = (
    "Mozilla/5.0 (compatible; upenn-textbooks/1.0; personal course-planning tool)"
)
REQUEST_DELAY_S = 1.0


# Matches a 10- or 13-digit ISBN (last char may be X for ISBN-10).
_ISBN_RE = re.compile(r"ISBN:\s*([0-9]{13}|[0-9]{9}[0-9Xx])")
_EDITION_RE = re.compile(r"Edition:\s*(.+?)\s*(?:<br|Publisher:|ISBN:|$)", re.I)
_PUBLISHER_RE = re.compile(r"Publisher:\s*(.+?)\s*(?:<br|ISBN:|Edition:|$)", re.I)




@dataclass
class Book:
    title: str
    author: str = ""
    edition: str = ""
    publisher: str = ""
    isbn: str = ""
    required: bool = True
    course: str = ""  # human label of the section this book belongs to


    @property
    def amazon_url(self) -> str:
        return f"https://www.amazon.com/s?k={quote(self.isbn or self.title)}"


    # (Amazon search by ISBN; falls back to the title only if a record has no ISBN.)


    @property
    def google_books_url(self) -> str:
        if self.isbn:
            return f"https://books.google.com/books?vid=ISBN{self.isbn}"
        return f"https://books.google.com/books?q={quote(self.title)}"


    @property
    def penn_bookstore_url(self) -> str:
        return f"https://upenn.bncollege.com/search/?text={quote(self.isbn or self.title)}"


    def to_dict(self) -> dict:
        d = asdict(self)
        d["amazon_url"] = self.amazon_url
        d["google_books_url"] = self.google_books_url
        d["penn_bookstore_url"] = self.penn_bookstore_url
        return d




@dataclass
class Course:
    slug: str
    section: str  # e.g. "LAW 601001"
    title: str = ""
    instructor: str = ""
    credits: str = ""
    meeting_times: str = ""


    @property
    def label(self) -> str:
        bits = [self.title or self.slug, self.section]
        if self.instructor:
            bits.append(self.instructor)
        return " — ".join(b for b in bits if b)




def slugify(title: str) -> str:
    """Turn a course title into a URL slug (e.g. 'Administrative Law' -> 'administrative-law').


    Note: in the Course Finder detail URL the ``course`` slug is cosmetic — the
    ``sec`` + ``term`` params drive the actual lookup (proven in the source
    notebook, where every section was fetched with course=administrative-law and
    still returned the correct textbooks). So an imperfect slug here is harmless.
    """
    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower())
    return s.strip("-") or "course"




# Course Finder encodes a term as <year><season>, where A=Spring, B=Summer, C=Fall.
# Match on a prefix so abbreviations work too (e.g. "Spr '26", "Sum", "Fa").
_SEASON_PREFIXES = [("spr", "A"), ("sum", "B"), ("fal", "C"), ("fa", "C")]




def term_label_to_code(label: str, default: str = DEFAULT_TERM) -> str:
    """Map a human term label (e.g. "Fall '26", "Spr '26", "Summer 2027") to a
    Course Finder code (e.g. "2026C", "2026A"). Returns ``default`` if unparseable."""
    if not label:
        return default
    text = str(label).strip().lower()
    season = next((code for prefix, code in _SEASON_PREFIXES if prefix in text), None)
    m = re.search(r"'?(\d{2,4})", text)
    if not season or not m:
        return default
    yr = m.group(1)
    year = int(yr) + 2000 if len(yr) == 2 else int(yr)
    return f"{year}{season}"




def read_term_label(path: str) -> str:
    """Return the human term label from an Excel's Term column (first non-empty value)."""
    import pandas as pd


    df = pd.read_excel(path)
    if "Term" in df.columns:
        vals = df["Term"].dropna().astype(str)
        for v in vals:
            v = v.strip()
            if v and v.lower() != "nan":
                return v
    return ""




def load_courses_from_excel(path: str) -> list[Course]:
    """Load the class list from a Course Finder Excel export.


    Expected columns: Course_Section, Title, Term, Meeting_Times, Instructor, Credits.
    (Deferred import of pandas so this module stays importable without it.)
    """
    import pandas as pd


    df = pd.read_excel(path)
    courses: list[Course] = []
    for _, row in df.iterrows():
        section = str(row.get("Course_Section", "")).strip()
        title = str(row.get("Title", "")).strip()
        if not section or section.lower() == "nan":
            continue
        courses.append(
            Course(
                slug=slugify(title),
                section=section,
                title=title,
                instructor=str(row.get("Instructor", "") or "").strip(),
                credits=str(row.get("Credits", "") or "").strip(),
                meeting_times=str(row.get("Meeting_Times", "") or "").strip(),
            )
        )
    return courses




def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s




def _clean(text: str) -> str:
    """Collapse whitespace and unescape a small HTML fragment."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()




def parse_book_record(fragment: str) -> Book | None:
    """Parse a single book record from a chunk of HTML starting at ``"<em>``.


    Returns None if the fragment has no recognizable title.
    """
    m_title = re.search(r"<em>(.*?)</em>", fragment, re.S)
    if not m_title:
        return None
    title = _clean(m_title.group(1))
    if not title:
        return None


    # Author: text between the closing quote after </em> and the first <br>.
    author = ""
    m_author = re.search(r"</em>\s*\"?\s*by\s+(.*?)(?:<br|$)", fragment, re.S | re.I)
    if m_author:
        author = _clean(m_author.group(1))


    edition = ""
    m_ed = _EDITION_RE.search(fragment)
    if m_ed:
        edition = _clean(m_ed.group(1))


    publisher = ""
    m_pub = _PUBLISHER_RE.search(fragment)
    if m_pub:
        publisher = _clean(m_pub.group(1))


    isbn = ""
    m_isbn = _ISBN_RE.search(fragment)
    if m_isbn:
        isbn = m_isbn.group(1).upper()


    # Required/Optional: an "Optional" marker overrides the default of required.
    required = re.search(r"\bOptional\b", fragment, re.I) is None


    return Book(
        title=title,
        author=author,
        edition=edition,
        publisher=publisher,
        isbn=isbn,
        required=required,
    )




def parse_textbooks(html: bytes | str, course_label: str = "") -> list[Book]:
    """Parse all textbook records from a course-detail page's HTML."""
    soup = BeautifulSoup(html, "html.parser")
    books: list[Book] = []
    for p in soup.select("table tr td p"):
        inner = p.decode_contents()
        # A single <p> can list several books; each starts with a "<em> tag.
        # Split on the em-open marker and parse each resulting chunk.
        chunks = re.split(r'(?="?\s*<em>)', inner)
        for chunk in chunks:
            if "<em>" not in chunk:
                continue
            book = parse_book_record(chunk)
            if book and book.isbn:
                book.course = course_label
                books.append(book)
    return books




def detail_url(course_slug: str, section: str, term: str = DEFAULT_TERM, page: int = 1) -> str:
    """Build a course-detail URL. ``section`` may be "LAW 601001" or "601001"."""
    section = section.strip()
    if not section.upper().startswith("LAW"):
        section = "LAW " + section
    params = f"?course={quote(course_slug)}&sec={quote(section)}&term={quote(term)}&page={page}"
    return DETAIL_URL + params




def get_textbooks(
    course_slug: str,
    section: str,
    term: str = DEFAULT_TERM,
    session: requests.Session | None = None,
    course_label: str = "",
) -> list[Book]:
    """Fetch and parse the textbooks for one section."""
    session = session or make_session()
    url = detail_url(course_slug, section, term)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_S)
    label = course_label or f"{course_slug} — {section}"
    return parse_textbooks(resp.content, course_label=label)




def parse_catalog(html: bytes | str) -> list[Course]:
    """Extract courses/sections from a Course Finder listing page.


    The listing markup is not confirmed offline, so this scans for links to
    ``course-details`` pages and pulls the ``course`` slug and ``sec`` params
    out of each href — which is robust to most layouts. Returns [] if none found.
    """
    soup = BeautifulSoup(html, "html.parser")
    courses: dict[tuple[str, str], Course] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "course-details" not in href:
            continue
        m_course = re.search(r"[?&]course=([^&]+)", href)
        m_sec = re.search(r"[?&]sec=([^&]+)", href)
        if not (m_course and m_sec):
            continue
        from urllib.parse import unquote


        slug = unquote(m_course.group(1))
        sec = unquote(m_sec.group(1))
        title = _clean(a.get_text())
        key = (slug, sec)
        if key not in courses:
            courses[key] = Course(slug=slug, section=sec, title=title)
    return list(courses.values())




def _catalog_cache_path(term: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"catalog_{term}.json")




def list_catalog(
    term: str = DEFAULT_TERM,
    session: requests.Session | None = None,
    use_cache: bool = True,
) -> list[Course]:
    """List courses/sections for a term, caching results to a local JSON file.


    Best-effort: if the listing page can't be scraped, returns whatever was
    found (possibly empty) and the UI falls back to manual entry.
    """
    cache = _catalog_cache_path(term)
    if use_cache and os.path.exists(cache):
        with open(cache) as f:
            return [Course(**c) for c in json.load(f)]


    session = session or make_session()
    resp = session.get(CATALOG_URL, params={"term": term}, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_S)
    courses = parse_catalog(resp.content)


    if courses:
        with open(cache, "w") as f:
            json.dump([asdict(c) for c in courses], f, indent=2)
    return courses



