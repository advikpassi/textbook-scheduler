# My Law Textbooks


A small Streamlit app that scrapes the [UPenn Law Course Finder](https://goat.law.upenn.edu/cf/coursefinder/)
so you can pick the classes you're taking and get one combined list of every
textbook you need to buy — with ISBNs, a required/optional filter, purchase/search
links, and CSV export.



## Setup


```bash
pip install -r requirements.txt
streamlit run app.py
```


Then open the URL Streamlit prints (usually http://localhost:8501).


## Class list


The app reads the class list from **Course Finder Excel exports** in this folder —
**every** `.xls`/`.xlsx` here becomes a selectable term. Expected columns:
`Course_Section`, `Title`, `Term`, `Meeting_Times`, `Instructor`, `Credits`. To add a
term, re-export from Course Finder and drop the file here (name it whatever you like,
e.g. `Fall 2026.xls`); to update one, replace it.


## How to use


1. In the sidebar, pick your **Term** from the dropdown — one entry per Excel file,
   labeled by the **file name** (e.g. `Fall 2026`), which defaults to `Fall 2026` when
   present. The Course Finder term code used for scraping is derived automatically from
   the file's `Term` column (A=Spring, B=Summer, C=Fall + year). Selections reset when
   you switch terms.
2. In the **Select your classes** table, tick the **Take** checkbox for each class
   you're taking. Use the filter box above the table to narrow it by title, section,
   or instructor.
3. **🗓️ Weekly schedule** — a preview table of when/where your selected classes meet,
   plus a **Download schedule PDF** button. The PDF is a landscape weekly grid
   (Mon–Fri, weekend columns added only if used) with each class as a color-coded block
   showing title, time, and room. TBA/unscheduled classes are listed in a footer.
4. See the combined textbook table. Use the sidebar **Show** filter for
   All / Required only / Optional only, click a book's Amazon / Google Books / Penn
   Bookstore link, and **Download as CSV** to take the list shopping.


> The `course` slug in the scrape URL is cosmetic — the section code + term drive the
> textbook lookup (confirmed in the original notebook), so the app just needs the
> section codes from the Excel.


## Project layout


| File                     | Purpose                                                        |
| ------------------------ | -------------------------------------------------------------- |
| `app.py`                 | Streamlit UI                                                   |
| `scraper.py`             | Fetch + parse logic (no Streamlit deps; unit-testable)         |
| `schedule.py`            | Meeting-time parsing + landscape weekly-schedule PDF builder   |
| `Course_Finder_Results_*.xls` | Course Finder Excel export used as the class list         |
| `tests/test_scraper.py`  | Offline tests: parser + Excel loader                           |
| `tests/sample_detail.html` | Real detail-page HTML captured from the original notebook    |
| `notebook.ipynb`         | Original exploratory scraping notebook (reference)             |


## Notes


- The scraper identifies itself with a User-Agent and waits ~1s between requests to be
  polite. Textbook results are cached per session (`@st.cache_data`), so re-ticking a
  class you already fetched is instant.


## Tests


```bash
python3 tests/test_scraper.py
```



