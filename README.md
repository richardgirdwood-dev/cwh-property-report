# CWH Property Environmental Report

Generates a PDF environmental report for any UK residential property.

## What it includes

- Flood risk (Environment Agency)
- Radon (live UKHSA data)
- Conservation area & listed building status (Historic England)
- EPC rating and building features
- Sales history (Land Registry / Rightmove)
- Nearest railway stations
- Soil type (Cranfield LandIS)
- Local schools, shops, doctors and pubs

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
