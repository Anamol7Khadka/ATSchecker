# ATSchecker — CV Analysis, ATS Compatibility & Job Matching Tool

A Python toolkit that analyzes your CV PDFs for ATS (Applicant Tracking System) compatibility, scrapes relevant job postings from multiple sources, and scores your acceptance likelihood for each position.

## Features

- **ATS Compatibility Checker** — 14 automated checks: text parsability, images, tables, fonts, special characters, section headings, contact info, file size, page count, text order, keyword density, hyperlinks, PDF validity, header/footer analysis
- **Internet-Wide Job Discovery** — Scrapes Arbeitnow/Adzuna/RemoteOK, major boards, OVGU/JobTeaser, and company career portals via multi-engine search
- **Parallel Scraping Pipeline** — Source-level + city-level concurrency with retry/backoff and URL verification workers for faster runs
- **CV ↔ Job Matching** — TF-IDF cosine similarity + keyword overlap + heuristic bonuses (role type, location, language requirements)
- **Skills Gap Analysis** — Identifies skills frequently requested in jobs but missing from your CV
- **HTML Dashboard** — Includes separate Fresh, Thesis, Interesting (DS/ML/Engineering), and Precious opportunity sections
- **Incremental Analysis** — Only re-analyzes changed/new PDFs; caches job results for 24 hours

## Quick Start

### Prerequisites

- Python 3.10+
- Google Chrome (for Selenium-based scrapers)

### Setup

```bash
cd ATSchecker

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Usage

1. **Copy your CV PDFs** into the `cvs/` folder
2. **Run a full scan:**

```bash
python scripts/main.py scan
```

This runs the complete pipeline:
1. Parses and analyzes all PDFs in `cvs/`
2. Runs 14 ATS compatibility checks
3. Scrapes jobs from multiple boards + company/university portals across configured cities
4. Matches your CV against all jobs and scores acceptance likelihood
5. Generates an HTML dashboard in `reports/`
6. Opens the report in your browser

### Commands

| Command | Description |
|---------|-------------|
| `python scripts/main.py scan` | Full pipeline: ATS + scrape + match + report |
| `python scripts/main.py ats` | ATS checks only |
| `python scripts/main.py jobs` | Scrape jobs only |
| `python scripts/main.py jobs --fresh` | Scrape ignoring cache |
| `python scripts/main.py match` | Match CVs against cached jobs |
| `python scripts/main.py help` | Show usage |

### Workflow for New CVs

Just drop a new/updated PDF into the `cvs/` folder and re-run:

```bash
python scripts/main.py scan
```

The tool automatically detects new/changed files and only re-analyzes those.

## Configuration

Edit `config.yaml` to customize:

```yaml
# Target cities
cities:
  - Berlin
  - Wolfsburg
  - Leipzig
  - Magdeburg

# Job types
job_types:
  - Werkstudent
  - Working Student
  - Internship
  - Praktikum

# Search keywords (aligned with your CV)
search_keywords:
  - Data Engineering
  - Backend Development
  - Python Developer
  # ...

# Scraper settings
scraping:
  max_results_per_source: 30
  selenium_headless: true
  cache_expiry_hours: 24
```

## Project Structure

```
ATSchecker/
├── cvs/                        # Drop PDF CVs here
├── reports/                    # Generated HTML dashboards
├── scripts/
│   ├── main.py                 # CLI entry point
│   ├── cv_parser.py            # PDF → structured data
│   ├── ats_checker.py          # 14 ATS compatibility checks
│   ├── job_scraper.py          # Multi-source scraper orchestrator
│   ├── cv_job_matcher.py       # TF-IDF + keyword matching
│   ├── report_generator.py     # HTML dashboard (Jinja2)
│   └── scrapers/
│       ├── base.py             # Abstract base + JobPosting dataclass
│       ├── arbeitnow.py        # Arbeitnow API (free, no key)
│       ├── google_jobs.py      # Google search aggregation
│       ├── linkedin.py         # LinkedIn (Selenium)
│       ├── indeed.py           # Indeed Germany (requests)
│       ├── stepstone.py        # StepStone (Selenium)
│       ├── xing.py             # XING (Selenium + Google fallback)
│       ├── jobteaser.py        # Jobteaser with OVGU/campus-focused variants
│       └── company_portals.py  # Company + university portal discovery
├── config.yaml                 # Configuration
├── requirements.txt
└── README.md
```

## ATS Checks Performed

| # | Check | What It Does |
|---|-------|-------------|
| 1 | Text Extractability | Verifies text can be parsed from the PDF |
| 2 | Image Detection | Flags photos/logos that confuse ATS |
| 3 | Table Detection | Identifies tables that may scramble content |
| 4 | Font Compatibility | Checks for standard, embedded fonts |
| 5 | Special Characters | Detects Unicode chars ATS may garble |
| 6 | Section Headings | Validates standard ATS-recognizable labels |
| 7 | Contact Info | Verifies email/phone/LinkedIn are parseable |
| 8 | File Size | Flags files over 2 MB |
| 9 | Page Count | Warns if over 2 pages |
| 10 | Text Order | Validates logical section ordering |
| 11 | Keyword Density | Scores presence of role-relevant keywords |
| 12 | Hyperlinks | Validates URL formatting |
| 13 | PDF Validity | Checks for encryption/corruption |
| 14 | Header/Footer | Detects critical info in skip-prone regions |

## Troubleshooting

- **Selenium errors**: Make sure Google Chrome is installed. The `webdriver-manager` package auto-downloads the correct ChromeDriver.
- **Job scraping returns few results**: Some sites (LinkedIn, XING) may block automated access. The tool gracefully falls back to Google search aggregation.
- **ATS score seems low**: This is intentional — the checker is strict. A score of 60+ is acceptable; 75+ is well-optimized.

## Quick fixes: Fonts & Special Characters (apply before recompiling CV)

If the ATS report shows "Font Compatibility" issues (non-standard fonts) or "Special Characters" issues (weird quotes, bullets, pipes), follow these steps to sanitize your `.tex` and regenerate the PDF in an ATS-friendly way.

1) Sanitize your `.tex` files (this replaces fancy quotes, bullets and math-mode separators used as visual separators):

```bash
source venv/bin/activate
python scripts/sanitize_tex.py new_CV_copilot/main.tex
```

2) Ensure LaTeX embeds standard fonts: compile with `pdflatex` after the added packages (`lmodern`, `fontenc`, `inputenc` were added to the preamble). Run:

```bash
cd new_CV_copilot
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

Notes:
- We added `\usepackage[utf8]{inputenc}`, `\usepackage[T1]{fontenc}`, `\usepackage{lmodern}` and `\usepackage{textcomp}` to the preamble in `main.tex` so fonts are standard and embedded. This reduces the risk of subset font names (like `MULJMX+SFRM1000`) appearing in the PDF.
- The `sanitize_tex.py` script replaces occurrences like `$|$` and `$\cdot$` with text-safe `\textbar{}` and `\textperiodcentered{}` which avoids creation of unusual glyphs.
- If you prefer `xelatex` (better Unicode support), you can switch to `xelatex` and `fontspec` but that requires more preamble changes.

3) Re-run the ATS check and regenerate reports:

```bash
# place the new PDF in cvs/ or copy it there
cp new_CV_copilot/main.pdf cvs/
python scripts/main.py scan
```

## License

MIT

To launch: 
source venv/bin/activate && python app.py 
