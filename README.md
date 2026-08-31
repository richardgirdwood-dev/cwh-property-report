# CWH Property Environmental Report

Generates a PDF environmental report for any UK residential property, and
includes a proofreading tool for RICS survey reports.

## What it includes

- Flood risk (Environment Agency)
- Radon (live UKHSA data)
- Conservation area & listed building status (Historic England)
- EPC rating and building features
- Sales history (Land Registry / Rightmove)
- Nearest railway stations
- Soil type (Cranfield LandIS)
- Local schools, shops, doctors and pubs

## Survey proofreading

The "Survey Proofreading" tab lets a surveyor upload a survey report PDF
and flags common drafting mistakes: invalid condition ratings (RICS uses
1, 2, 3 or NI), leftover template placeholder text (e.g. `[insert]`,
`TBC`, Lorem ipsum), standard sections that appear to be missing, and
(optionally) spelling & grammar issues via the free LanguageTool web
service — this last check needs an internet connection. These are
heuristic checks on extracted PDF text, not a formal RICS compliance
review.

Before any check runs, client names, addresses, postcodes, emails and
phone numbers are redacted from the extracted text — so nothing
identifying is ever sent to the external spelling/grammar service. This
redaction is regex-based rather than true NER, so treat it as a strong
privacy safeguard rather than a guarantee, and don't rely on it as a
substitute for keeping sensitive PDFs off shared machines.

## Setup

**Requires Python 3.9+**

```
pip install -r requirements.txt
```

Place `cwh_logo.png` in the same folder as the scripts.

## Run

```
streamlit run streamlit_app.py
```

Then open http://localhost:8501 in your browser.

## Data sources

Environment Agency · UKHSA · Gov.uk EPC Register · Land Registry · OpenStreetMap · LandIS Cranfield · Historic England · National Rail
