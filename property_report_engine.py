"""
Property Report Engine
Generates a Sprift-style PDF environmental report for any UK residential property.
Usage: generate_report(address, postcode, output_path)
"""

import os
import re
import math
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from bs4 import BeautifulSoup

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image, KeepTogether, CondPageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cwh_logo.png")

W = 180 * mm

# ── Colours ────────────────────────────────────────────────────────────────────
DARK_BLUE  = colors.HexColor("#1B3A6B")
MID_BLUE   = colors.HexColor("#2D6EAA")
LIGHT_BLUE = colors.HexColor("#E8F1FA")
LIGHT_GREY = colors.HexColor("#F5F5F5")
WHITE      = colors.white
TEXT       = colors.HexColor("#222222")
GREY_TEXT  = colors.HexColor("#555555")
GREEN      = colors.HexColor("#19611C")
AMBER      = colors.HexColor("#D4720A")
RED        = colors.HexColor("#C0392B")
PURPLE     = colors.HexColor("#6A1A6A")
BROWN      = colors.HexColor("#6D4C24")
TEAL       = colors.HexColor("#00695C")

styles = getSampleStyleSheet()

def S(name, **kw):
    # Unique name per call to avoid ReportLab style cache collisions
    return ParagraphStyle(f"{name}_{id(kw)}", parent=styles["Normal"], **kw)

HDR  = S("HDR",  fontSize=20, textColor=WHITE,     fontName="Helvetica-Bold", leading=24)
SUB  = S("SUB",  fontSize=10, textColor=colors.HexColor("#BDD7EE"), fontName="Helvetica")
SECH = S("SECH", fontSize=11, textColor=DARK_BLUE, fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=2)
LBL  = S("LBL",  fontSize=8,  textColor=GREY_TEXT, fontName="Helvetica-Bold")
VAL  = S("VAL",  fontSize=9,  textColor=TEXT,      fontName="Helvetica")
NOTE = S("NOTE", fontSize=7.5, textColor=colors.HexColor("#666666"), fontName="Helvetica-Oblique", leading=11)
DISC = S("DISC", fontSize=7,   textColor=colors.HexColor("#888888"), fontName="Helvetica-Oblique", leading=10)

def link(url, text=None):
    display = text or url
    full_url = url if url.startswith("http") else f"https://{url}"
    return f'<link href="{full_url}" color="#2D6EAA">{display}</link>'

# ── DATA FETCHERS ──────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_postcode(postcode):
    """Returns lat, lng, admin_district, county, region, parish from postcodes.io.
    Retries before giving up: a single transient failure here aborts the whole
    report (callers treat lat=None as unresolvable), which has silently dropped
    reports in batch runs."""
    pc = postcode.replace(" ", "")
    for attempt in range(3):
        try:
            r = requests.get(f"https://api.postcodes.io/postcodes/{pc}", timeout=10)
            d = r.json()["result"]
            return {
                "lat":    d["latitude"],
                "lng":    d["longitude"],
                "la":     d["admin_district"],
                "county": d.get("admin_county") or "",
                "region": d.get("region") or "",
                "parish": d.get("parish") or "",
            }
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return {"lat": None, "lng": None, "la": "Unknown", "county": "", "region": "",
            "parish": ""}

def fetch_radon_status(lat, lng):
    """Returns (status_text, (badge_hex, badge_label)) from UKHSA live radon data via ArcGIS."""
    HIGH = ("#C0392B", "HIGH RISK")
    MED  = ("#D4720A", "MEDIUM RISK")
    LOW  = ("#19611C", "LOW RISK")
    try:
        url = (
            "https://services1.arcgis.com/0IrmI40n5ZYxTUrV/arcgis/rest/services/"
            "Maximum_radon_potential_UK_and_IoM/FeatureServer/0/query"
            f"?geometry={lng},{lat}&geometryType=esriGeometryPoint&inSR=4326"
            "&spatialRel=esriSpatialRelIntersects&outFields=CLASS_MAX,Descriptio,Advice"
            "&returnGeometry=false&f=json"
        )
        r = requests.get(url, timeout=10, headers=HEADERS)
        features = r.json().get("features", [])
        if features:
            a = features[0]["attributes"]
            cls = a.get("CLASS_MAX", 1)
            desc = a.get("Descriptio", "")
            advice = a.get("Advice", "")
            # Map CLASS_MAX (1-6) to risk band
            if cls == 1:
                badge = LOW
                text = f"Low risk — {advice}. Less than 1% of homes at or above the Action Level"
            elif cls in (2, 3):
                badge = MED
                pct = "1-3%" if cls == 2 else "3-5%"
                text = f"Elevated risk — maximum radon potential {pct} in this 1km grid square. {advice}"
            elif cls == 4:
                badge = MED
                text = f"Elevated risk — maximum radon potential 5-10% in this 1km grid square. {advice}"
            elif cls == 5:
                badge = HIGH
                text = f"High risk — maximum radon potential 10-30% in this 1km grid square. {advice}"
            else:  # cls == 6
                badge = HIGH
                text = f"High risk — maximum radon potential >30% in this 1km grid square. {advice}"
            return text, badge
    except Exception:
        pass
    # Fallback if API unavailable
    return "Indicative only — check UKHSA map for precise assessment", ("#D4720A", "CHECK REQUIRED")

def fetch_conservation_area(lat, lng):
    """Returns conservation area name or None"""
    try:
        url = f"https://www.planning.data.gov.uk/entity.json?dataset=conservation-area&longitude={lng}&latitude={lat}"
        r = requests.get(url, timeout=10)
        entities = r.json().get("entities", [])
        if entities:
            return entities[0].get("name", "Conservation Area")
        return None
    except Exception:
        return None

def fetch_listed_building(lat, lng, house_number=""):
    """Returns listed building details using Historic England's ArcGIS open data.
    Searches within 200m, prioritising entries whose name contains the house number."""
    try:
        url = (
            f"https://services-eu1.arcgis.com/ZOdPfBS3aqqDYPUQ/arcgis/rest/services/"
            f"National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer/0/query"
            f"?geometry={lng},{lat}&geometryType=esriGeometryPoint&inSR=4326"
            f"&spatialRel=esriSpatialRelIntersects&distance=200&units=esriSRUnit_Meter"
            f"&outFields=Name,Grade,ListEntry,hyperlink&returnGeometry=true&outSR=4326&f=json"
        )
        r = requests.get(url, timeout=10, headers=HEADERS)
        features = r.json().get("features", [])

        def _dist(elat_f, elng_f):
            R = 3958.8
            dlat = math.radians(elat_f - lat); dlon = math.radians(elng_f - lng)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(elat_f))*math.sin(dlon/2)**2
            return R * 2 * math.asin(math.sqrt(a))

        candidates = []
        for f in features:
            attrs = f.get("attributes", {})
            geom  = f.get("geometry", {})
            pts   = geom.get("points") or (geom.get("rings", [[]])[0] if geom.get("rings") else None)
            if not pts:
                continue
            elng_f, elat_f = pts[0][0], pts[0][1]
            candidates.append((attrs, _dist(elat_f, elng_f)))

        if not candidates:
            return None

        hn = str(house_number).strip()
        # First pass: look for an entry whose name contains the house number
        if hn:
            hn_matches = [(a, d) for a, d in candidates if re.search(rf'\b{re.escape(hn)}\b', a.get("Name", ""))]
            if hn_matches:
                best_attrs, _ = min(hn_matches, key=lambda x: x[1])
                entry = best_attrs.get("ListEntry", "")
                return {
                    "grade": best_attrs.get("Grade", ""),
                    "name":  best_attrs.get("Name", "Listed building"),
                    "ref":   str(entry),
                    "url":   best_attrs.get("hyperlink", "") or f"https://historicengland.org.uk/listing/the-list/list-entry/{entry}",
                }
        return None   # don't guess if no house number match
    except Exception:
        return None

COAL_RISK_LAYERS = (
    ("high", "DevelopmentHighRiskArea"),
    ("low",  "DevelopmentLowRiskArea"),
)

def fetch_coal_mining_risk(lat, lng):
    """Returns 'high', 'low', or None using the Mining Remediation Authority's
    (formerly the Coal Authority) official development risk area layers."""
    base = "https://services-eu1.arcgis.com/Qn4lKcPDVHNivEyr/arcgis/rest/services"
    for risk, layer in COAL_RISK_LAYERS:
        try:
            url = (
                f"{base}/{layer}/FeatureServer/0/query"
                f"?geometry={lng},{lat}&geometryType=esriGeometryPoint&inSR=4326"
                f"&spatialRel=esriSpatialRelIntersects&outFields=HYPERLINK"
                f"&returnGeometry=false&f=json"
            )
            r = requests.get(url, timeout=10, headers=HEADERS)
            if r.json().get("features"):
                return risk
        except Exception:
            continue
    return None

def fetch_epc(postcode, house_number):
    """Finds the EPC certificate matching house_number at postcode. Returns dict of EPC data."""
    try:
        url = f"https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-postcode?postcode={postcode.replace(' ', '+')}"
        r = requests.get(url, timeout=15, headers=HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")
        # Certificate links contain the address as their text; search all of them
        cert_links = soup.find_all("a", href=re.compile(r"/energy-certificate/"))
        cert_id = None
        hn = house_number.strip().lower()
        for a in cert_links:
            addr_text = a.get_text(strip=True).lower()
            # Match if house number appears as a word at start or after "flat"/"apartment"
            if re.search(rf'(?:^|\bflat\b|\bapartment\b)\s*{re.escape(hn)}\b', addr_text):
                cert_id = a["href"].split("/")[-1]
                break
        # Broader fallback: any address that starts with the house number
        if not cert_id:
            for a in cert_links:
                addr_text = a.get_text(strip=True).lower()
                if addr_text.startswith(hn + " ") or addr_text.startswith(hn + ","):
                    cert_id = a["href"].split("/")[-1]
                    break
        if not cert_id:
            return None

        # Fetch the full certificate
        cert_url = f"https://find-energy-certificate.service.gov.uk/energy-certificate/{cert_id}"
        r2 = requests.get(cert_url, timeout=15, headers=HEADERS)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        text = soup2.get_text(separator="\n")
        # Build clean line list for label-then-value parsing
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        def after_label(label, default="N/A"):
            """Return the line immediately after the first line matching label (case-insensitive)."""
            lbl = label.lower()
            for i, ln in enumerate(lines):
                if ln.lower() == lbl and i + 1 < len(lines):
                    return lines[i + 1]
            return default

        def in_sentence(pattern, default="N/A"):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        # Rating/score: page shows "62 D" and "89 B" on consecutive lines after chart
        current_score, current_rating, pot_score, pot_rating = "N/A", "N/A", "N/A", "N/A"
        score_block = re.search(r"(\d+)\s+([A-G])\s*\n\s*(\d+)\s+([A-G])", text)
        if score_block:
            current_score, current_rating = score_block.group(1), score_block.group(2)
            pot_score,     pot_rating     = score_block.group(3), score_block.group(4)
        else:
            # Fallback: look for standalone rating line
            rm = re.search(r"^Energy rating\s*$\s*^([A-G])$", text, re.MULTILINE)
            if rm:
                current_rating = rm.group(1)

        # Building features: lines after "Feature / Description / Rating" header come in triplets
        FEATURE_LABELS = ["Wall", "Roof", "Window", "Main heating", "Main heating control",
                          "Hot water", "Lighting", "Floor", "Secondary heating"]
        features = []
        i = 0
        while i < len(lines) - 2:
            if lines[i] in FEATURE_LABELS:
                features.append({
                    "feature": lines[i],
                    "desc":    lines[i + 1],
                    "rating":  lines[i + 2] if lines[i + 2] in
                               ("Very good", "Good", "Average", "Poor", "Very poor", "N/A") else "N/A",
                })
                i += 3
            else:
                i += 1

        return {
            "cert_id":      cert_id,
            "cert_url":     cert_url,
            "rating":       current_rating,
            "score":        current_score,
            "pot_rating":   pot_rating,
            "pot_score":    pot_score,
            "valid_until":  after_label("Valid until"),
            "cert_date":    after_label("Date of certificate", after_label("Date of assessment")),
            "floor_area":   after_label("Total floor area"),
            "prop_type":    after_label("Property type"),
            "annual_cost":  in_sentence(r"spend\s*([\xc2\xa3\£]\S+\s+per year)", "N/A"),
            "co2":          in_sentence(r"This property produces\s*\n\s*(\S+\s+tonnes of CO2)", "N/A"),
            "primary_use":  in_sentence(r"primary energy use for this property per year is (.+?)\.", "N/A"),
            "features":     features,
        }
    except Exception as e:
        return None

_PPD_TYPES   = {"D": "Detached", "S": "Semi-detached", "T": "Terraced", "F": "Flat", "O": "Other"}
_PPD_TENURES = {"F": "Freehold", "L": "Leasehold"}

def _norm_words(text):
    return " " + re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper()).replace("  ", " ") + " "

def fetch_land_registry_sales(postcode, address):
    """Sold prices for the subject property from HM Land Registry Price Paid
    Data (official, free CSV endpoint). Much more reliable than scraping
    Rightmove, and handles flats properly: Land Registry stores a flat's
    number in SAON (e.g. "93") and the building in PAON (e.g. "THE ASH
    BUILDING"), so both must appear in the address for a match."""
    url = (f"https://landregistry.data.gov.uk/app/ppd/ppd_data.csv"
           f"?postcode={postcode.replace(' ', '+')}&header=true&limit=all")
    try:
        import csv, io
        r = requests.get(url, timeout=30, headers=HEADERS)
        if r.status_code != 200:
            return None
        addr = _norm_words(address)
        sales = []
        for row in csv.DictReader(io.StringIO(r.text)):
            if row.get("transaction_category") == "D":   # deleted/corrected records
                continue
            paon = _norm_words(row.get("paon")).strip()
            saon = _norm_words(row.get("saon")).strip()
            saon_core = re.sub(r"^(FLAT|APARTMENT|UNIT|APT)\s+", "", saon)
            if not paon or f" {paon} " not in addr:
                continue
            if saon and f" {saon_core} " not in addr:
                continue
            parts = [p.title() for p in (row.get("saon"), row.get("paon")) if p]
            street = (row.get("street") or "").title()
            # "197 High Street" for a numbered house; "93 The Ash Building, Rudduck Way" for a named building
            paon_txt = (row.get("paon") or "").strip()
            sep = " " if re.match(r"^\d+[A-Z]?(-\d+[A-Z]?)?$", paon_txt) else ", "
            label = " ".join(parts) + (f"{sep}{street}" if street else "")
            try:
                d = datetime.strptime(row["deed_date"][:10], "%Y-%m-%d")
                date_txt = d.strftime("%d %b %Y").lstrip("0")
            except Exception:
                d, date_txt = None, row.get("deed_date", "")
            try:
                price_txt = f"£{int(row['price_paid']):,}"
            except Exception:
                price_txt = row.get("price_paid", "")
            sales.append({
                "address": label,
                "date":    date_txt,
                "price":   price_txt,
                "type":    _PPD_TYPES.get(row.get("property_type"), "") +
                           (" (new build)" if row.get("new_build") == "Y" else ""),
                "tenure":  _PPD_TENURES.get(row.get("estate_type"), ""),
                "_sort":   d or datetime.min,
            })
        sales.sort(key=lambda s: s["_sort"], reverse=True)
        return sales
    except Exception:
        return None

def fetch_sales_history(postcode, house_number):
    """Scrapes Rightmove for the subject property's sale(s).
    Rightmove page structure: address in an <h3>/<h2> heading, then transaction
    rows (<tr>) immediately follow. Col 0 = date, col 1 = price, col 2 = type, col 3 = tenure.
    """
    pc_slug = postcode.replace(" ", "-").lower()
    url = f"https://www.rightmove.co.uk/house-prices/{pc_slug}.html"
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")
        sales = []
        hn = house_number.strip().lower()

        # Each property block has a heading then transaction rows.
        # The heading and rows share a common ancestor — walk up until we find one with <tr>.
        for heading in soup.find_all(["h2", "h3", "h4", "dt", "strong"]):
            heading_text = heading.get_text(strip=True).lower()
            if not re.match(rf'^{re.escape(hn)}\b', heading_text):
                continue
            # Walk up ancestors until we find one that contains <tr> elements
            ancestor = heading.parent
            for _ in range(8):
                if ancestor.find("tr"):
                    break
                ancestor = ancestor.parent
            for row in ancestor.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    price_text = cells[1].get_text(strip=True)
                    # Skip non-sale rows (e.g. "See what it's worth now")
                    if not re.search(r'[\£\$\€]\s*[\d,]+', price_text):
                        continue
                    sales.append({
                        "address": heading.get_text(strip=True),
                        "date":    cells[0].get_text(strip=True),
                        "price":   price_text,
                        "type":    cells[2].get_text(strip=True) if len(cells) > 2 else "",
                        "tenure":  cells[3].get_text(strip=True) if len(cells) > 3 else "",
                    })
            if sales:
                break

        # Wider fallback: scan all <tr> where any cell contains the house number
        if not sales:
            for row in soup.find_all("tr"):
                cells = row.find_all("td")
                row_text = row.get_text(" ", strip=True).lower()
                if (len(cells) >= 2 and
                        re.search(rf'\b{re.escape(hn)}\b', row_text) and
                        any(c for c in cells if "£" in c.get_text())):
                    sales.append({
                        "address": postcode,
                        "date":    cells[0].get_text(strip=True),
                        "price":   cells[1].get_text(strip=True),
                        "type":    cells[2].get_text(strip=True) if len(cells) > 2 else "",
                        "tenure":  cells[3].get_text(strip=True) if len(cells) > 3 else "",
                    })

        return sales, url
    except Exception:
        return [], url

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

OVERPASS_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "PropertyReportEngine/1.0 (property survey tool)",
}
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

import threading
_OVERPASS_LOCK = threading.Lock()

def _overpass(query, timeout=40, retries=2):
    # The free Overpass servers limit concurrent requests per user, so when
    # generate_report fetches data in parallel the Overpass calls still take
    # turns (two simultaneous queries were getting rejected/timing out).
    with _OVERPASS_LOCK:
        return _overpass_unlocked(query, timeout, retries)

def _overpass_unlocked(query, timeout=40, retries=2):
    """POST an Overpass query, trying every mirror, and retrying the whole
    mirror list up to `retries` times if all of them fail first time round —
    the free Overpass mirrors intermittently time out or rate-limit, and a
    short retry clears most of those transient failures. If every attempt
    fails the result carries "_failed": True so the report can say the
    service was unavailable rather than implying nothing exists nearby."""
    for attempt in range(retries):
        for url in OVERPASS_MIRRORS:
            try:
                r = requests.post(url, data={"data": query},
                                  headers=OVERPASS_HEADERS, timeout=timeout)
                if r.status_code == 200 and r.text.strip():
                    return r.json()
            except Exception:
                continue
        if attempt < retries - 1:
            time.sleep(3)
    return {"elements": [], "_failed": True}

def _stations_from_elements(elements, lat, lng):
    """Nearest 4 National Rail stations from Overpass elements.
    Only stations with a CRS code (the 3-letter National Rail code, e.g. CBG)
    are kept, which excludes heritage/tourist railways, Underground/metro,
    tram and miniature railway stops."""
    nearest = {}
    for el in elements:
        tags = el.get("tags", {})
        if tags.get("railway") != "station" or "ref:crs" not in tags:
            continue
        name = tags.get("name", "")
        if not name or tags.get("usage") == "tourism" or tags.get("railway:preserved") == "yes":
            continue
        # Underground/metro stops can carry pseudo-CRS codes (e.g. ZOC) too
        if tags.get("station") in ("subway", "light_rail", "monorail", "miniature", "funicular"):
            continue
        plat = el.get("lat", el.get("center", {}).get("lat"))
        plon = el.get("lon", el.get("center", {}).get("lon"))
        if plat is None or plon is None:
            continue
        dist = haversine_miles(lat, lng, plat, plon)
        crs = tags["ref:crs"]
        if crs not in nearest or dist < nearest[crs][1]:
            nearest[crs] = (name, dist)
    return sorted(nearest.values(), key=lambda x: x[1])[:4]

def fetch_rail_stations(lat, lng):
    """Returns up to 4 nearest National Rail stations with distances."""
    try:
        query = (f'[out:json][timeout:30];nwr[railway=station]["ref:crs"]'
                 f'(around:20000,{lat},{lng});out center tags;')
        return _stations_from_elements(_overpass(query).get("elements", []), lat, lng)
    except Exception:
        return []

def _wgs84_to_osgb(lat, lon):
    """Approximate WGS84 -> OSGB36 easting/northing (accurate to ~1 m for England)."""
    a, b = 6378137.0, 6356752.3142
    F0 = 0.9996012717
    lat0, lon0 = math.radians(49), math.radians(-2)
    N0, E0 = -100000, 400000
    e2 = 1 - (b / a) ** 2
    n = (a - b) / (a + b)
    la = math.radians(lat); lo = math.radians(lon)
    sinla = math.sin(la); cosla = math.cos(la)
    nu  = a * F0 / math.sqrt(1 - e2 * sinla ** 2)
    rho = a * F0 * (1 - e2) / (1 - e2 * sinla ** 2) ** 1.5
    eta2 = nu / rho - 1
    M = b * F0 * (
        ((1 + n + 5/4*n**2 + 5/4*n**3) * (la - lat0))
        - ((3*n + 3*n**2 + 21/8*n**3) * math.sin(la - lat0) * math.cos(la + lat0))
        + ((15/8*n**2 + 15/8*n**3) * math.sin(2*(la - lat0)) * math.cos(2*(la + lat0)))
        - (35/24*n**3 * math.sin(3*(la - lat0)) * math.cos(3*(la + lat0)))
    )
    I    = M + N0
    II   = nu / 2 * sinla * cosla
    III  = nu / 24 * sinla * cosla ** 3 * (5 - math.tan(la) ** 2 + 9 * eta2)
    IIIA = nu / 720 * sinla * cosla ** 5 * (61 - 58 * math.tan(la) ** 2 + math.tan(la) ** 4)
    IV   = nu * cosla
    V    = nu / 6 * cosla ** 3 * (nu / rho - math.tan(la) ** 2)
    VI   = nu / 120 * cosla ** 5 * (5 - 18*math.tan(la)**2 + math.tan(la)**4 + 14*eta2 - 58*math.tan(la)**2*eta2)
    dlon = lo - lon0
    N = I  + II  * dlon ** 2 + III  * dlon ** 4 + IIIA * dlon ** 6
    E = E0 + IV  * dlon      + V    * dlon ** 3  + VI   * dlon ** 5
    return E, N

def fetch_soil(lat, lng):
    """Returns soil type info from LandIS Soilscapes (Cranfield University)."""
    try:
        E, N = _wgs84_to_osgb(lat, lng)
        url = f"https://www.landis.org.uk/soilscapes/get_data_json.php?latlong={E:.0f},{N:.0f}"
        r = requests.get(url, timeout=15, headers={
            **HEADERS,
            "Referer": "https://www.landis.org.uk/soilscapes/index.cfm",
        })
        d = r.json()
        ssid = d.get("ss_id", "")
        name = d.get("soilscape", "")
        if not name:
            return None, None
        return {
            "name":      name,
            "ssid":      ssid,
            "texture":   d.get("texture",          "N/A"),
            "drainage":  d.get("drainage",          "N/A"),
            "fertility": d.get("fertility",         "N/A"),
            "carbon":    d.get("carbon",            "N/A"),
            "landcover": d.get("landcover",         "N/A"),
            "water":     d.get("water_protection",  "N/A"),
        }, "https://www.landis.org.uk/soilscapes/"
    except Exception:
        return None, None

UKSO_BGS_WMS_URL = "https://map.bgs.ac.uk/arcgis/services/UKSO/UKSO_BGS/MapServer/WMSServer"

def fetch_uk_soil_texture(lat, lng):
    """Returns detailed 1:50,000-scale soil texture/parent material data from
    the BGS UK Soil Observatory (UKSO) — the site's ArcGIS REST endpoint
    blocks direct query/identify (capabilities locked to 'Map' only), but
    its WMS GetFeatureInfo interface is open and returns the same underlying
    Parent Material Model data the interactive map uses. Much finer-grained
    than the 625k bedrock/superficial geology above — this is what actually
    picks up localised clay pockets (e.g. clay-with-flints on chalk) that
    the coarser dataset misses."""
    try:
        d = 0.001
        bbox = f"{lng-d},{lat-d},{lng+d},{lat+d}"
        url = (
            f"{UKSO_BGS_WMS_URL}?service=WMS&version=1.1.1&request=GetFeatureInfo"
            f"&layers=Parent.Material.Soil.texture&query_layers=Parent.Material.Soil.texture"
            f"&srs=EPSG:4326&bbox={bbox}&width=101&height=101&x=50&y=50"
            f"&info_format=application/json"
        )
        r = requests.get(url, timeout=10, headers=HEADERS)
        root = ET.fromstring(r.text)
        fields = root.find("{http://www.esri.com/wms}FIELDS")
        if fields is None:
            fields = root.find("FIELDS")
        if fields is None or not fields.get("SIMPLE_TEX"):
            return None
        a = fields.attrib
        return {
            "texture":          a.get("SIMPLE_TEX", ""),
            "soil_group":       a.get("SOIL_GROUP", ""),
            "soil_group_desc":  a.get("SOIL_GROUP_DESC", ""),
            "depth":            a.get("SOIL_DEPTH", ""),
            "depth_desc":       a.get("SOIL_DEPTH_DESC", ""),
            "calcareous":       a.get("CACO3_RANK", ""),
            "parent_material":  a.get("ESB_DESC", ""),
        }
    except Exception:
        return None

BGS_GEOLOGY_URL = "https://services3.arcgis.com/7bJVHfju2RXdGZa4/arcgis/rest/services/BGS_625k_geology/FeatureServer"

def fetch_bedrock_geology(lat, lng):
    """Returns bedrock and superficial geology from the British Geological
    Survey's official 625k Geology dataset (Living Atlas). Distinct from
    Soilscapes above — this is the underlying geological substrate (bedrock
    lithology/age and any superficial/drift deposits), not agricultural soil
    classification."""
    result = {"bedrock": None, "superficial": None}
    for layer_id, key in ((1, "bedrock"), (2, "superficial")):
        try:
            url = (
                f"{BGS_GEOLOGY_URL}/{layer_id}/query"
                f"?geometry={lng},{lat}&geometryType=esriGeometryPoint&inSR=4326"
                f"&spatialRel=esriSpatialRelIntersects&outFields=*"
                f"&returnGeometry=false&f=json"
            )
            r = requests.get(url, timeout=10, headers=HEADERS)
            features = r.json().get("features", [])
            if features:
                a = features[0]["attributes"]
                result[key] = {
                    "lithology":   a.get("LITHOLOGY", ""),
                    "age":         a.get("AGE", ""),
                    "period":      a.get("PERIOD", ""),
                    "type":        a.get("TYPE", ""),
                    "environment": a.get("ENVIRONMENT", ""),
                }
        except Exception:
            continue
    return result

CLAY_KEYWORDS = ("clay", "till", "diamicton", "mudstone", "boulder clay")

def assess_clay_soil(geology, soil_texture=None):
    """Practical clay/no-clay indicator. Prefers the fine-grained (1:50,000)
    UKSO soil texture data (soil_texture, from fetch_uk_soil_texture) when
    available, since it catches localised clay pockets — e.g. clay-with-
    flints on chalk — that the coarser 1:625,000 bedrock/superficial geology
    dataset misses. Falls back to a keyword read of that coarser geology
    data only if the UKSO texture lookup failed."""
    if soil_texture and soil_texture.get("texture"):
        tex = soil_texture["texture"]
        tex_lower = tex.lower()
        if "clay" in tex_lower:
            return ("YES", f"UKSO soil texture (1:50,000) at this location is recorded as "
                            f"{_sentence_case(tex).lower()} — {soil_texture.get('soil_group_desc', '')}".rstrip(" —"))
        else:
            return ("NO", f"UKSO soil texture (1:50,000) at this location is recorded as "
                           f"{_sentence_case(tex).lower()}, not clay-bearing.")

    # Fallback: coarser 625k bedrock/superficial geology, if the UKSO lookup failed
    bedrock     = geology.get("bedrock") or {}
    superficial = geology.get("superficial") or {}
    b_lith = (bedrock.get("lithology") or "").lower()
    s_lith = (superficial.get("lithology") or "").lower()

    b_clay = any(kw in b_lith for kw in CLAY_KEYWORDS)
    s_clay = any(kw in s_lith for kw in CLAY_KEYWORDS)

    if s_clay and b_clay:
        return ("YES", f"Both the superficial deposits ({superficial.get('lithology')}) "
                        f"and bedrock ({bedrock.get('lithology')}) are clay-bearing. "
                        f"(1:625,000 regional data — UKSO detailed lookup was unavailable.)")
    elif s_clay:
        return ("YES", f"The superficial deposits at this location are recorded as "
                        f"{superficial.get('lithology')}, which is clay-bearing. "
                        f"(1:625,000 regional data — UKSO detailed lookup was unavailable.)")
    elif b_clay:
        return ("YES", f"The bedrock at this location is recorded as {bedrock.get('lithology')}, "
                        f"which is clay-bearing. "
                        f"(1:625,000 regional data — UKSO detailed lookup was unavailable.)")
    elif superficial or bedrock:
        basis = superficial.get('lithology') if superficial else bedrock.get('lithology')
        return ("NO", f"Neither the recorded superficial deposits nor bedrock indicate clay — "
                       f"nearest surface material is {basis}. "
                       f"(1:625,000 regional data — UKSO detailed lookup was unavailable.)")
    else:
        return ("UNKNOWN", "No soil texture, bedrock, or superficial geology record available at this location.")

SHOP_TYPES = {"supermarket", "convenience", "general", "department_store", "mall"}

def fetch_schools_and_amenities(lat, lng):
    """Single Overpass query for schools, shops, GPs, and pubs — avoids rate-limit issues.

    Shops are filtered to food/general shop types inside the query itself.
    Previously every shop of any kind within 3 km was requested, which in a
    city (e.g. central Cambridge) is thousands of results and made the query
    time out on every mirror, leaving the amenities section blank.
    Returns (schools, amenities, ok) — ok is False if the service failed."""
    shop_re = "|".join(sorted(SHOP_TYPES))
    query = (
        f"[out:json][timeout:40];"
        f"("
        f"nwr[amenity=school](around:3000,{lat},{lng});"
        f'nwr[shop~"^({shop_re})$"](around:3000,{lat},{lng});'
        f"nwr[amenity=doctors](around:6000,{lat},{lng});"
        f"nwr[amenity=pub](around:2000,{lat},{lng});"
        f");out tags center;"
    )
    schools = []
    amenities = {"shops": [], "doctors": [], "pubs": []}
    ok = True
    try:
        data = _overpass(query)
        ok = not data.get("_failed")
        seen_schools = set()
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name", "")
            if not name:
                continue
            el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
            el_lng = el.get("lon") or (el.get("center") or {}).get("lon")
            dist = haversine_miles(lat, lng, el_lat, el_lng) if el_lat else None
            amenity_tag = tags.get("amenity", "")
            shop_tag    = tags.get("shop", "")
            if amenity_tag == "school" and name not in seen_schools:
                seen_schools.add(name)
                schools.append({
                    "name": name,
                    "dist": dist,
                    "url":  f"https://reports.ofsted.gov.uk/search?q={requests.utils.quote(name)}",
                })
            elif shop_tag in SHOP_TYPES:
                amenities["shops"].append({"name": name, "dist": dist})
            elif amenity_tag == "doctors":
                amenities["doctors"].append({"name": name, "dist": dist})
            elif amenity_tag == "pub":
                amenities["pubs"].append({"name": name, "dist": dist})
    except Exception:
        ok = False
    schools.sort(key=lambda x: x["dist"] or 99)
    for key in ("shops", "doctors", "pubs"):
        amenities[key].sort(key=lambda x: x["dist"] or 99)
    return schools[:6], amenities, ok

# ── PDF BUILDER ────────────────────────────────────────────────────────────────
# Design tokens for the polished layout (Nov 2026 redesign)
NAVY      = DARK_BLUE
ACCENT    = MID_BLUE
LABEL_BG  = colors.HexColor("#F3F6FA")
TILE_BG   = colors.HexColor("#F7F9FC")
RULE      = colors.HexColor("#E1E6EE")
MUTED     = colors.HexColor("#6B7280")
INK       = colors.HexColor("#1F2937")
OK_GREEN  = colors.HexColor("#2E7D32")
NEUTRAL   = colors.HexColor("#9CA3AF")

LBL  = S("LBL2",  fontSize=8,   textColor=colors.HexColor("#4B5563"), fontName="Helvetica-Bold", leading=10)
VAL  = S("VAL2",  fontSize=9,   textColor=INK, fontName="Helvetica", leading=11.5)
NOTE = S("NOTE2", fontSize=7.3, textColor=MUTED, fontName="Helvetica-Oblique", leading=9.5)
DISC = S("DISC2", fontSize=6.8, textColor=MUTED, fontName="Helvetica", leading=9)

def _pill(text, color, width=22*mm):
    """Rounded coloured status badge."""
    p = Paragraph(f"<b>{text}</b>", S("PILL", fontSize=7.2, textColor=WHITE,
                                      fontName="Helvetica-Bold", alignment=1, leading=8.5))
    t = Table([[p]], colWidths=[width], cornerRadii=[2.5, 2.5, 2.5, 2.5])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), color),
        ("TOPPADDING",    (0,0),(-1,-1), 2.2),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2.8),
        ("LEFTPADDING",   (0,0),(-1,-1), 2),
        ("RIGHTPADDING",  (0,0),(-1,-1), 2),
    ]))
    return t

def _heading(title, width=W):
    """Section heading: accent bar, navy title, small-caps source on the right."""
    main, _, src = title.partition("  (")
    src = src.rstrip(")")
    txt = f'<font name="Helvetica-Bold" size="11" color="#1B3A6B">{main.strip()}</font>'
    src_p = Paragraph(src.upper(), S("HSRC", fontSize=6.5, textColor=MUTED,
                                     fontName="Helvetica", alignment=2, leading=8)) if src else ""
    src_w = 62*mm if (src and width > 120*mm) else 0
    if src and not src_w:      # narrow (half-width) column: source under the title
        txt += f'<br/><font size="6.5" color="#6B7280">{src.upper()}</font>'
    cells = [["", Paragraph(txt, S("HT", leading=13)), src_p if src_w else ""]]
    t = Table(cells, colWidths=[1.3*mm, width - 1.3*mm - src_w, src_w])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(0,0), ACCENT),
        ("LEFTPADDING",   (0,0),(0,0), 0), ("RIGHTPADDING", (0,0),(0,0), 0),
        ("LEFTPADDING",   (1,0),(1,0), 2.5*mm),
        ("RIGHTPADDING",  (2,0),(2,0), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 1),
        ("BOTTOMPADDING", (0,0),(-1,-1), 1.5),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    return [t, Spacer(1, 2*mm)]

def _section_header(story, icon, title, width=W):
    story.extend(_heading(title, width))

def _rows_table(rows, col_widths=None, width=W):
    """Label / value / optional status-badge table.

    rows: (label, value, badge_text, badge_colour). The badge column is only
    created when at least one row has a badge, so tables without badges use
    the full width instead of showing an empty grey column."""
    has_badge = any(r[2] for r in rows)
    if col_widths:
        cw = list(col_widths)
        if len(cw) == 3 and not has_badge:
            cw = [cw[0], cw[1] + cw[2]]
    else:
        cw = [44*mm, width - 44*mm - 27*mm, 27*mm] if has_badge else [44*mm, width - 44*mm]
    data = []
    for label, value, badge, badge_color in rows:
        row = [Paragraph(label, LBL), Paragraph(value, VAL)]
        if len(cw) == 3:
            row.append(_pill(badge, badge_color or NEUTRAL, width=cw[2] - 4*mm) if badge else "")
        data.append(row)
    t = Table(data, colWidths=cw)
    cmds = [
        ("BACKGROUND",    (0,0),(0,-1), LABEL_BG),
        ("LEFTPADDING",   (0,0),(-1,-1), 3*mm),
        ("RIGHTPADDING",  (0,0),(-1,-1), 2.5*mm),
        ("TOPPADDING",    (0,0),(-1,-1), 2.1*mm),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2.1*mm),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("LINEABOVE",     (0,0),(-1,0),  0.6, RULE),
        ("LINEBELOW",     (0,0),(-1,-1), 0.6, RULE),
    ]
    if len(cw) == 3:
        cmds += [("LEFTPADDING", (2,0),(2,-1), 2*mm), ("RIGHTPADDING", (2,0),(2,-1), 2*mm)]
    t.setStyle(TableStyle(cmds))
    return t

def _section(story, icon, title, rows, note=None, col_widths=None):
    # KeepTogether stops a section heading being stranded at the foot of a
    # page with its table starting on the next one.
    block = _heading(title)
    block.append(_rows_table(rows, col_widths))
    if note:
        block.append(Spacer(1, 1.2*mm))
        block.append(Paragraph(note, NOTE))
    story.append(KeepTogether(block))
    story.append(Spacer(1, 5*mm))

def _part(story, num, title):
    """Numbered part divider (e.g. '02  FACILITIES & TRANSPORT'). Starts a new
    page if there isn't room for the divider plus a reasonable first section,
    so it never sits alone at the foot of a page."""
    story.append(CondPageBreak(60*mm))
    p = Paragraph(f'<font color="#2D6EAA">{num:02d}</font>&nbsp;&nbsp;&nbsp;{title.upper()}',
                  S("PART", fontSize=9.5, textColor=NAVY, fontName="Helvetica-Bold", leading=12))
    t = Table([[p]], colWidths=[W])
    t.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 1*mm),
        ("BOTTOMPADDING", (0,0),(-1,-1), 1.6*mm),
        ("LINEBELOW",     (0,0),(-1,-1), 1.4, NAVY),
    ]))
    story.append(t)
    story.append(Spacer(1, 3.5*mm))

def _tile(label, value, color, sub="", width=43*mm):
    """'At a glance' summary tile with a coloured top edge."""
    content = [
        Paragraph(label.upper(), S("TL", fontSize=6.5, textColor=MUTED, fontName="Helvetica-Bold", leading=8)),
        Spacer(1, 1.2*mm),
        Paragraph(value, S("TV", fontSize=10.5, textColor=INK, fontName="Helvetica-Bold", leading=12.5)),
    ]
    content.append(Paragraph(sub or "&nbsp;", S("TS", fontSize=7, textColor=MUTED, fontName="Helvetica", leading=9)))
    t = Table([[content]], colWidths=[width], rowHeights=[19*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), TILE_BG),
        ("LINEABOVE",     (0,0),(-1,0),  2.4, color),
        ("LEFTPADDING",   (0,0),(-1,-1), 3*mm),
        ("RIGHTPADDING",  (0,0),(-1,-1), 2*mm),
        ("TOPPADDING",    (0,0),(-1,-1), 2.4*mm),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2*mm),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
    ]))
    return t

def _tile_grid(tiles, per_row=4, gap=3*mm):
    tw = (W - gap * (per_row - 1)) / per_row
    cells, widths = [], []
    rows = [tiles[i:i + per_row] for i in range(0, len(tiles), per_row)]
    data = []
    for r in rows:
        line = []
        for j, spec in enumerate(r):
            if j:
                line.append("")
            line.append(_tile(*spec, width=tw))
        data.append(line)
    widths = []
    for j in range(per_row):
        if j:
            widths.append(gap)
        widths.append(tw)
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), gap),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
    ]))
    return t

def _data_table(header, rows, col_widths, align_right_cols=()):
    """Header-row table used for EPC features and sales history."""
    hs = S("DTH", fontSize=7.5, textColor=WHITE, fontName="Helvetica-Bold", leading=9)
    data = [[Paragraph(h.upper(), hs) for h in header]] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    cmds = [
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("LEFTPADDING",   (0,0),(-1,-1), 3*mm),
        ("RIGHTPADDING",  (0,0),(-1,-1), 2.5*mm),
        ("TOPPADDING",    (0,0),(-1,-1), 1.9*mm),
        ("BOTTOMPADDING", (0,0),(-1,-1), 1.9*mm),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("LINEBELOW",     (0,1),(-1,-1), 0.6, RULE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, TILE_BG]),
    ]
    for c in align_right_cols:
        cmds.append(("ALIGN", (c,0),(c,-1), "RIGHT"))
    t.setStyle(TableStyle(cmds))
    return t

def _sentence_case(text):
    """BGS soil data comes back in capitals (e.g. 'CLAYEY LOAM TO SANDY LOAM')."""
    t = (text or "").strip()
    if not t or t == "N/A":
        return t
    t = re.sub(r"(?<=\w)\(", " (", t.lower())
    return t[0].upper() + t[1:]

def _footer_canvas(footer_text):
    """Canvas class that stamps a footer with 'Page X of Y' on every page."""
    from reportlab.pdfgen import canvas as _canvas

    class _NumberedCanvas(_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                self.saveState()
                self.setStrokeColor(RULE)
                self.setLineWidth(0.6)
                self.line(15*mm, 11.5*mm, 195*mm, 11.5*mm)
                self.setFont("Helvetica", 6.8)
                self.setFillColor(MUTED)
                self.drawString(15*mm, 7.5*mm, footer_text)
                self.drawRightString(195*mm, 7.5*mm, f"Page {self._pageNumber} of {total}")
                self.restoreState()
                super().showPage()
            super().save()

    return _NumberedCanvas

# ── MAIN ENTRY POINT ───────────────────────────────────────────────────────────

_SMALL_WORDS = {"and", "or", "of", "the", "a", "an", "in", "on", "at", "to",
                "for", "by", "with", "upon", "via", "near"}

def _cap_word(word, force=False):
    """Capitalise a single word, handling hyphenated parts."""
    if "-" in word:
        parts = word.split("-")
        return "-".join(_cap_word(p, force=(i == 0 or p.lower() not in _SMALL_WORDS))
                        for i, p in enumerate(parts))
    base = word.rstrip(",.;")
    suffix = word[len(base):]
    if not force and base.lower() in _SMALL_WORDS:
        return base.lower() + suffix
    return base.capitalize() + suffix

def _title_address(text):
    """Title-case an address: capitalise all words except prepositions mid-address."""
    words = text.strip().split()
    result = []
    for i, word in enumerate(words):
        # Always capitalise first word and any word after a digit-only token (e.g. "4 The Grove")
        prev_is_number = i > 0 and re.match(r'^\d+,?$', words[i - 1])
        force = (i == 0) or prev_is_number
        result.append(_cap_word(word, force=force))
    return " ".join(result)

_GENERIC_NAME_PREFIXES = {"the", "a", "an"}

def _extract_house_identifier(address):
    """Returns the identifier used to match this property against sold-price,
    EPC, and listed-building records. For numbered houses this is just the
    house number (e.g. '12', '3a'). For named properties — very common in
    rural UK addresses ('The Haybarn', 'The Old Vicarage', 'The Barn') — a
    bare first word like 'The' is a near-useless wildcard that matches
    almost any named property sharing the postcode (this caused a real bug:
    Rightmove sales-history matching on 'the' alone picked up an unrelated
    property, 'The Old Vicarage', for a search for 'The Haybarn'). Include
    the next word too when the first word is just a generic article."""
    words = address.strip().split()
    if not words:
        return ""
    first = words[0].rstrip(",")
    if first.lower() in _GENERIC_NAME_PREFIXES and len(words) > 1:
        second = words[1].rstrip(",")
        return f"{first} {second}"
    return first

def generate_report(address, postcode, output_path):
    """
    Fetch all data and generate a PDF report.
    address  : e.g. "197 High Street, Cottenham, Cambridge"
    postcode : e.g. "CB24 8RX"
    output_path : full path for the output PDF
    """
    address  = _title_address(address)
    postcode = postcode.strip().upper()
    print(f"  Fetching data for {address}, {postcode}...")

    # Extract house number/name from address
    house_number = _extract_house_identifier(address)

    # Step 1 — coordinates
    pc_data = fetch_postcode(postcode)
    lat     = pc_data["lat"]
    lng     = pc_data["lng"]
    la_name = pc_data["la"]
    county  = pc_data["county"]
    region  = pc_data["region"]
    if lat is None:
        print(f"  ERROR: Could not resolve postcode {postcode}")
        return None

    # Step 2 — fetch everything in parallel (each source is independent, and
    # running them side by side cuts generation time from minutes to roughly
    # the time of the slowest single lookup)
    from concurrent.futures import ThreadPoolExecutor

    def _get_sales():
        rm = f"https://www.rightmove.co.uk/house-prices/{postcode.replace(' ', '-').lower()}.html"
        lr = fetch_land_registry_sales(postcode, address)
        if lr:
            return lr, "HM Land Registry Price Paid Data", rm
        rm_sales, rm = fetch_sales_history(postcode, house_number)
        return rm_sales, ("Rightmove" if rm_sales else "HM Land Registry Price Paid Data"), rm

    with ThreadPoolExecutor(max_workers=10) as ex:
        f_ca     = ex.submit(fetch_conservation_area, lat, lng)
        f_lb     = ex.submit(fetch_listed_building, lat, lng, house_number)
        f_coal   = ex.submit(fetch_coal_mining_risk, lat, lng)
        f_epc    = ex.submit(fetch_epc, postcode, house_number)
        f_sales  = ex.submit(_get_sales)
        f_rail   = ex.submit(fetch_rail_stations, lat, lng)
        f_soil   = ex.submit(fetch_soil, lat, lng)
        f_tex    = ex.submit(fetch_uk_soil_texture, lat, lng)
        f_geo    = ex.submit(fetch_bedrock_geology, lat, lng)
        f_fac    = ex.submit(fetch_schools_and_amenities, lat, lng)
        f_radon  = ex.submit(fetch_radon_status, lat, lng)
        conservation_area = f_ca.result()
        listed_building   = f_lb.result()
        coal_risk         = f_coal.result()
        epc               = f_epc.result()
        sales, sales_source, rm_url = f_sales.result()
        stations          = f_rail.result()
        soil_data, soil_url = f_soil.result()
        soil_texture      = f_tex.result()
        geology           = f_geo.result()
        schools, amenities, amenities_ok = f_fac.result()
        radon_desc, (radon_hex, radon_badge) = f_radon.result()

    clay_verdict, clay_reason = assess_clay_soil(geology, soil_texture)
    print(f"  Data fetched. Building PDF...")

    # ── Build PDF ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=13*mm, bottomMargin=18*mm,
        title=f"Property Environmental Report — {address}, {postcode}",
        author="CWH Surveyors LLP",
    )
    story = []
    try:
        today = datetime.now().strftime("%#d %B %Y")  # Windows strips leading zero
    except Exception:
        today = datetime.now().strftime("%d %B %Y")
    pc_enc = postcode.replace(" ", "+")

    # ── Masthead: logo on white, then navy address band ──────────────────────
    logo_cell = Paragraph("<b>CWH Surveyors LLP</b>", S("LGO", fontSize=12, textColor=NAVY))
    if os.path.exists(LOGO_PATH):
        try:
            logo_cell = Image(LOGO_PATH, height=12.5*mm, width=12.5*mm * 2484 / 451)
            logo_cell.hAlign = "LEFT"
        except Exception:
            pass
    masthead = Table([[
        logo_cell,
        Paragraph(f'<font name="Helvetica-Bold" size="8" color="#1B3A6B">PROPERTY ENVIRONMENTAL REPORT</font>'
                  f'<br/><font size="7.5" color="#6B7280">Prepared {today}</font>',
                  S("MR", alignment=2, leading=11)),
    ]], colWidths=[100*mm, 80*mm])
    masthead.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "BOTTOM"),
        ("LEFTPADDING",   (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), 2.5*mm),
    ]))
    story.append(masthead)
    story.append(Spacer(1, 2*mm))

    band = Table([
        [Paragraph(address, S("ADDR", fontSize=17, textColor=WHITE, fontName="Helvetica-Bold", leading=21))],
        [Paragraph(f"{postcode}&nbsp;&nbsp;·&nbsp;&nbsp;{la_name}&nbsp;&nbsp;·&nbsp;&nbsp;{region or 'England'}",
                   S("ADDR2", fontSize=9.5, textColor=colors.HexColor("#C9DBF0"), fontName="Helvetica", leading=12))],
    ], colWidths=[W], cornerRadii=[3, 3, 3, 3])
    band.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), NAVY),
        ("LEFTPADDING",   (0,0),(-1,-1), 6*mm), ("RIGHTPADDING", (0,0),(-1,-1), 6*mm),
        ("TOPPADDING",    (0,0),(0,0),   5*mm), ("BOTTOMPADDING", (0,0),(0,0), 1.5*mm),
        ("TOPPADDING",    (0,1),(0,1),   0),    ("BOTTOMPADDING", (0,1),(0,1), 5*mm),
    ]))
    story.append(band)
    story.append(Spacer(1, 3*mm))

    # Property strip
    floor_area = epc.get("floor_area", "N/A") if epc else "N/A"
    prop_type  = epc.get("prop_type",  "N/A") if epc else "N/A"
    tenure     = sales[0]["tenure"] if sales and sales[0].get("tenure") else "Not recorded"
    strip_items = [("Property Type", prop_type), ("Floor Area", floor_area), ("Tenure", tenure),
                   ("Local Authority", la_name), ("Region", region or "England")]
    strip = Table([[
        [Paragraph(k.upper(), S(f"SK{j}", fontSize=6.5, textColor=MUTED, fontName="Helvetica-Bold", leading=8)),
         Spacer(1, 0.8*mm),
         Paragraph(v, S(f"SV{j}", fontSize=9, textColor=NAVY, fontName="Helvetica-Bold", leading=11))]
        for j, (k, v) in enumerate(strip_items)
    ]], colWidths=[W/5]*5)
    strip.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 3*mm),
        ("TOPPADDING",    (0,0),(-1,-1), 2.2*mm), ("BOTTOMPADDING", (0,0),(-1,-1), 2.4*mm),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("LINEAFTER",     (0,0),(3,0),   0.6, RULE),
        ("LINEBELOW",     (0,0),(-1,0),  0.6, RULE),
    ]))
    story.append(strip)
    story.append(Spacer(1, 5*mm))

    # ── At a glance ───────────────────────────────────────────────────────────
    RATING_COLOURS = {"A": OK_GREEN, "B": OK_GREEN, "C": colors.HexColor("#689F38"),
                      "D": AMBER, "E": colors.HexColor("#E65100"), "F": RED, "G": RED}
    if epc:
        epc_rating = epc.get("rating", "N/A")
        t_epc = ("Energy Rating", f"{epc_rating}  <font size='8' color='#6B7280'>({epc.get('score','—')})</font>",
                 RATING_COLOURS.get(epc_rating, NEUTRAL),
                 f"Potential {epc.get('pot_rating','—')} ({epc.get('pot_score','—')})")
    else:
        t_epc = ("Energy Rating", "Not found", NEUTRAL, "No EPC on register")
    if conservation_area:
        t_ca = ("Conservation Area", "Within", PURPLE, _sentence_case(conservation_area)[:34])
    else:
        t_ca = ("Conservation Area", "None recorded", OK_GREEN, "Indicative — confirm with LPA")
    if listed_building:
        g = listed_building.get("grade", "")
        t_lb = ("Listed Building", f"Grade {g}", RED if g in ("I", "II*") else AMBER, "Historic England")
    else:
        t_lb = ("Listed Building", "Not listed", OK_GREEN, "Historic England")
    t_coal = {"high": ("Coal Mining", "High risk", RED, "CON29M recommended"),
              "low":  ("Coal Mining", "Low risk", AMBER, "Development low risk area")}.get(
              coal_risk, ("Coal Mining", "Not in risk area", OK_GREEN, "Mining Remediation Authority"))
    t_clay = {"YES": ("Clay Soils", "Clay indicated", RED, "See ground conditions"),
              "NO":  ("Clay Soils", "Not indicated", OK_GREEN, "See ground conditions")}.get(
              clay_verdict, ("Clay Soils", "Unknown", NEUTRAL, "No data at this location"))
    if sales:
        t_sale = ("Last Sale", sales[0]["price"], NAVY, sales[0]["date"])
    else:
        t_sale = ("Last Sale", "No record", NEUTRAL, "HM Land Registry")
    tiles = [
        t_epc,
        ("Flood Risk", "Check required", AMBER, "EA long-term flood service"),
        ("Radon", radon_badge.title(), colors.HexColor(radon_hex), "UKHSA indicative"),
        t_clay,
        t_ca, t_lb, t_coal, t_sale,
    ]
    story.extend(_heading("At a Glance"))
    story.append(_tile_grid(tiles))
    story.append(Spacer(1, 4*mm))

    # ══ 1. ENERGY PERFORMANCE ══════════════════════════════════════════════════
    _part(story, 1, "Energy Performance")
    if epc:
        co2         = epc.get("co2", "N/A")
        primary_use = epc.get("primary_use", "N/A")
        primary_use = re.sub(r"^The primary energy use for this property per year is\s*", "", primary_use)
        epc_rows = [
            ("Current Rating",     f"<b>{epc_rating}</b> (score {epc.get('score','N/A')})  —  potential "
                                   f"{epc.get('pot_rating','N/A')} ({epc.get('pot_score','N/A')})",
             f"{epc_rating} RATED", RATING_COLOURS.get(epc_rating, AMBER)),
            ("Annual Energy Cost", epc.get("annual_cost", "N/A"), "", None),
            ("CO2 Emissions",      co2, "", None),
            ("Primary Energy Use", primary_use, "", None),
            ("Certificate Date",   f"{epc.get('cert_date','N/A')}  ·  valid until {epc.get('valid_until','N/A')}", "", None),
            ("Certificate No.",    link(f"https://find-energy-certificate.service.gov.uk/energy-certificate/{epc['cert_id']}",
                                        epc["cert_id"]), "", None),
        ]
        epc_note = None
        if re.search(r"(^|\s)-\d", f"{co2} {primary_use}"):
            epc_note = ("Negative CO2 / primary energy figures are as recorded on the certificate. They usually arise "
                        "where the property is credited for low-carbon community heating or on-site renewable "
                        "generation.")
        _section(story, "", "Energy Performance Certificate  (Gov.uk EPC Register)", epc_rows, note=epc_note)

        features = epc.get("features", [])
        if features:
            RATING_COL = {"Very good": OK_GREEN, "Good": OK_GREEN, "Average": AMBER,
                          "Poor": RED, "Very poor": RED, "N/A": NEUTRAL}
            frows = [[Paragraph(f["feature"], LBL), Paragraph(f["desc"], VAL),
                      _pill(f["rating"], RATING_COL.get(f["rating"], AMBER), width=22*mm)]
                     for f in features]
            feat_block = _heading("Building Features  (as assessed for the EPC)")
            feat_block.append(_data_table(["Feature", "Description", "Rating"], frows,
                                          [40*mm, 113*mm, 27*mm]))
            # Allowed to split across pages (header row repeats) rather than
            # being pushed whole onto the next page and leaving a gap.
            story.append(CondPageBreak(40*mm))   # heading + header row + a few rows
            story.extend(feat_block)
            story.append(Spacer(1, 5*mm))
    else:
        _section(story, "", "Energy Performance Certificate  (Gov.uk EPC Register)", [
            ("Status", f"No EPC found for {house_number} at postcode {postcode}", "", None),
            ("Search", link(f"https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-postcode?postcode={pc_enc}",
                            "Search the EPC register"), "", None),
        ])

    # ══ 2. FACILITIES & TRANSPORT ══════════════════════════════════════════════
    _part(story, 2, "Facilities & Transport")
    COL = 88*mm
    schools_block = _heading("Local Schools  (Ofsted)", width=COL)
    ofsted_search = link(f"https://reports.ofsted.gov.uk/search?q={postcode}&tab=schools", "reports.ofsted.gov.uk")
    if schools:
        sch_rows = [(link(s["url"], s["name"][:70]),
                     f"{s['dist']:.1f} mi" if s.get("dist") is not None else "", "", None)
                    for s in schools[:5]]
        schools_block.append(_rows_table(sch_rows, (70*mm, 18*mm)))
        schools_block.append(Spacer(1, 1.2*mm))
        schools_block.append(Paragraph("Select a school name to view its Ofsted reports.", NOTE))
    elif not amenities_ok:
        schools_block.append(Paragraph(f"School data temporarily unavailable — search {ofsted_search}.", NOTE))
    else:
        schools_block.append(Paragraph(f"No schools found within 2 miles — search {ofsted_search}.", NOTE))

    amenities_block = _heading("Local Amenities  (OpenStreetMap)", width=COL)
    amenity_rows = []
    for key, tag, colr in (("shops", "SHOP", ACCENT), ("doctors", "GP", OK_GREEN), ("pubs", "PUB", AMBER)):
        for a in amenities[key][:2]:
            d = f"{a['dist']:.1f} mi" if a.get("dist") is not None else ""
            amenity_rows.append((a["name"][:60], d, tag, colr))
    if amenity_rows:
        amenities_block.append(_rows_table(amenity_rows, (52*mm, 18*mm, 18*mm)))
        amenities_block.append(Spacer(1, 1.2*mm))
        amenities_block.append(Paragraph("Verify current trading status locally.", NOTE))
    elif not amenities_ok:
        amenities_block.append(Paragraph("Amenity data temporarily unavailable (OpenStreetMap did not respond) — "
                                         "re-run the report or check locally.", NOTE))
    else:
        amenities_block.append(Paragraph("No shops, GP surgeries or pubs recorded nearby.", NOTE))

    two_col = Table([[schools_block, "", amenities_block]], colWidths=[COL, W - 2*COL, COL])
    two_col.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(two_col)
    story.append(Spacer(1, 5*mm))

    if stations:
        rail_rows = [(name, f"{dist:.1f} miles", "NEAREST" if i == 0 else "", TEAL if i == 0 else None)
                     for i, (name, dist) in enumerate(stations)]
        rail_rows.append(("Journey Planner", link("www.nationalrail.co.uk"), "", None))
    else:
        rail_rows = [("Status", "Station data temporarily unavailable — check nationalrail.co.uk", "", None),
                     ("Journey Planner", link("www.nationalrail.co.uk"), "", None)]
    _section(story, "", "Nearest Railway Stations  (National Rail)", rail_rows,
             note="Straight-line distances. Driving distances will be greater.")

    # ══ 3. PLANNING & DESIGNATIONS ═════════════════════════════════════════════
    _part(story, 3, "Planning & Designations")
    if conservation_area:
        ca_rows = [
            ("Status",            "Within a designated conservation area", "DESIGNATED", PURPLE),
            ("Conservation Area", conservation_area, "", None),
            ("Planning Data Map", link("www.planning.data.gov.uk/map/?dataset=conservation-area"), "", None),
        ]
        ca_note = "Conservation area designation restricts permitted development rights. Confirm boundary with the LPA."
    else:
        ca_rows = [
            ("Status",            "No conservation area recorded at this location (indicative)", "NONE", OK_GREEN),
            ("Planning Data Map", link("www.planning.data.gov.uk/map/?dataset=conservation-area"), "", None),
        ]
        ca_note = "National dataset is not exhaustive — confirm with the LPA for definitive status."
    _section(story, "", "Conservation Area  (Historic England / Planning Data)", ca_rows, note=ca_note)

    if listed_building:
        grade = listed_building.get("grade", "")
        grade_color = {"I": RED, "II*": RED, "II": AMBER}.get(grade, PURPLE)
        lb_rows = [
            ("Status",  f"Listed building — Grade {grade}", f"GRADE {grade}", grade_color),
            ("Name",    listed_building.get("name", ""), "", None),
        ]
        if listed_building.get("url"):
            lb_rows.append(("Historic England", link(listed_building["url"], "View listing"), "", None))
        lb_note = "Listed building status imposes restrictions on alterations. Consent required from LPA for most works."
    else:
        lb_rows = [("Status", "No listed building recorded at this location (indicative)", "NOT LISTED", OK_GREEN),
                   ("Source", link("https://historicengland.org.uk/listing/the-list/", "Historic England — The List"), "", None)]
        lb_note = "Confirm with Historic England if listing status is critical to the survey."
    _section(story, "", "Listed Building Status  (Historic England)", lb_rows, note=lb_note)

    _section(story, "", "Planning History  (Local Planning Authority)", [
        ("Planning Authority", la_name, "", None),
        ("Planning Portal",    link("www.gov.uk/search-register-planning-decisions"), "", None),
    ])

    # ══ 4. ENVIRONMENT & GROUND CONDITIONS ═════════════════════════════════════
    _part(story, 4, "Environment & Ground Conditions")
    _section(story, "", "Flood Risk  (Environment Agency)", [
        ("Rivers & Sea",  "Check required — see link below", "CHECK", AMBER),
        ("Surface Water", "Check required — see link below", "CHECK", AMBER),
        ("Direct Link",   link(f"https://check-long-term-flood-risk.service.gov.uk/postcode?postcode={pc_enc}",
                               "check-long-term-flood-risk.service.gov.uk"), "", None),
    ], note="The EA flood risk service requires CAPTCHA verification — follow the link above.")

    _section(story, "", "Radon  (UKHSA)", [
        ("Indicative Status",  radon_desc, radon_badge, colors.HexColor(radon_hex)),
        ("UKHSA Action Level", "200 Bq/m³ for homes", "", None),
        ("Interactive Map",    link("www.ukradon.org/information/ukmaps"), "", None),
    ], note="For a definitive address-level result use the UKHSA paid address search at ukradon.org (£3.90).")

    coal_gov_link = link("https://www.gov.uk/government/publications/coal-mining-data-development-high-risk-areas",
                         "gov.uk — coal mining data")
    if coal_risk == "high":
        cm_rows = [("Status", "Within a Coal Mining Development High Risk Area", "HIGH RISK", RED),
                   ("Source", coal_gov_link, "", None)]
        cm_note = ("A Coal Mining Report (CON29M) is recommended prior to purchase — instruct via a "
                   "solicitor or directly with the Mining Remediation Authority (coal.gov.uk).")
    elif coal_risk == "low":
        cm_rows = [("Status", "Within a Coal Mining Development Low Risk Area", "LOW RISK", AMBER),
                   ("Source", coal_gov_link, "", None)]
        cm_note = "Low risk area — a Coal Mining Report is not routinely required but may still be advisable."
    else:
        cm_rows = [("Status", "Not within a recorded coal mining development risk area (indicative)", "NOT IN AREA", OK_GREEN),
                   ("Source", coal_gov_link, "", None)]
        cm_note = ("Based on Mining Remediation Authority (formerly the Coal Authority) risk area data — "
                   "confirm via a CON29M search if in doubt.")
    _section(story, "", "Coal Mining Risk  (Mining Remediation Authority)", cm_rows, note=cm_note)

    if soil_texture:
        soil_rows = [
            ("Texture",         _sentence_case(soil_texture.get("texture", "N/A")),         "", None),
            ("Soil Group",      _sentence_case(soil_texture.get("soil_group", "N/A")),      "", None),
            ("Depth",           _sentence_case(soil_texture.get("depth", "N/A")),           "", None),
            ("Calcareous",      _sentence_case(soil_texture.get("calcareous", "N/A")),      "", None),
            ("Parent Material", _sentence_case(soil_texture.get("parent_material", "N/A")), "", None),
        ]
    elif soil_data:
        soil_rows = [
            ("Classification", soil_data.get("name", "N/A"),      f"TYPE {soil_data.get('ssid','')}", BROWN),
            ("Texture",        soil_data.get("texture",  "N/A"),  "", None),
            ("Drainage",       soil_data.get("drainage", "N/A"),  "", None),
            ("Fertility",      soil_data.get("fertility","N/A"),  "", None),
            ("Land Cover",     soil_data.get("landcover","N/A"),  "", None),
        ]
    else:
        soil_rows = [("Status", "Soil texture data unavailable for this location.", "", None)]
    _section(story, "", "Soil Texture  (BGS UK Soil Observatory)", soil_rows,
             note="BGS UK Soil Observatory, 1:50,000 Parent Material Model.")

    bedrock     = geology.get("bedrock")
    superficial = geology.get("superficial")
    geo_rows = []
    if bedrock:
        type_suffix = f", {bedrock['type'].lower()}" if bedrock.get("type") else ""
        geo_rows.append(("Bedrock",
                         f"{bedrock.get('lithology','N/A')} — {bedrock.get('age','N/A')} "
                         f"({bedrock.get('period','N/A')}{type_suffix})", "", None))
    else:
        geo_rows.append(("Bedrock", "No bedrock record found at this location", "", None))
    if superficial and (superficial.get("lithology") or "").strip():
        geo_rows.append(("Superficial Deposits",
                         f"{superficial.get('lithology')} — {superficial.get('age','N/A')}", "", None))
    else:
        geo_rows.append(("Superficial Deposits", "None recorded — bedrock at or near surface", "", None))
    clay_badge_color = {"YES": RED, "NO": OK_GREEN, "UNKNOWN": NEUTRAL}[clay_verdict]
    geo_rows.append(("Clay Soil Indicator", clay_reason, clay_verdict, clay_badge_color))
    geo_rows.append(("BGS Geology Viewer", link("https://www.bgs.ac.uk/map-viewers/bgs-geology-viewer/",
                                                "View on BGS Geology Viewer"), "", None))
    _section(story, "", "Bedrock & Superficial Geology  (British Geological Survey)", geo_rows,
             note="Bedrock/superficial data is 1:625,000 scale (regional, not site-specific). The Clay Soil "
                  "Indicator uses the finer 1:50,000 soil texture data above where available. For "
                  "foundation-critical decisions a site-specific trial pit or geotechnical survey is recommended.")

    # ══ 5. SALES HISTORY (last) ════════════════════════════════════════════════
    _part(story, 5, "Sales History")
    rm_link_text = postcode.replace(" ", "-").lower()
    sales_block = _heading("Recorded Sales  (HM Land Registry)")
    if sales:
        srows = [[Paragraph(f"<b>{s['address']}</b>", VAL),
                  Paragraph(f"<b>{s['price']}</b>", S("SP", fontSize=9, textColor=INK, fontName="Helvetica-Bold", alignment=2)),
                  Paragraph(s["date"], VAL), Paragraph(s["type"], VAL), Paragraph(s["tenure"], VAL)]
                 for s in sales]
        sales_block.append(_data_table(["Address", "Price", "Date", "Type", "Tenure"], srows,
                                       [64*mm, 26*mm, 26*mm, 38*mm, 26*mm], align_right_cols=(1,)))
    else:
        sales_block.append(Paragraph("No recorded sales found for this property — it may not have changed hands "
                                     "since 1995, or the address may be recorded differently.", VAL))
    sales_block.append(Spacer(1, 1.2*mm))
    sales_block.append(Paragraph(
        f'Source: {sales_source}. Full street history: '
        f'{link(rm_url, f"rightmove.co.uk/house-prices/{rm_link_text}.html")}', NOTE))
    story.append(KeepTogether(sales_block))
    story.append(Spacer(1, 7*mm))

    # Disclaimer
    disc = Table([[Paragraph(
        "<b>Disclaimer.</b> This report is indicative only and has been compiled from publicly available data "
        "sources (Gov.uk EPC Register, OpenStreetMap, Ofsted, National Rail, Historic England, Planning Data, "
        "Environment Agency, UKHSA, Mining Remediation Authority, British Geological Survey and HM Land Registry). "
        f"It does not constitute a formal environmental search. Data correct as at {today}.", DISC)]],
        colWidths=[W], cornerRadii=[3, 3, 3, 3])
    disc.setStyle(TableStyle([
        ("BACKGROUND",  (0,0),(-1,-1), LABEL_BG),
        ("LEFTPADDING", (0,0),(-1,-1), 4*mm), ("RIGHTPADDING", (0,0),(-1,-1), 4*mm),
        ("TOPPADDING",  (0,0),(-1,-1), 3*mm), ("BOTTOMPADDING", (0,0),(-1,-1), 3*mm),
    ]))
    story.append(disc)

    doc.build(story, canvasmaker=_footer_canvas(
        f"CWH Surveyors LLP  ·  Property Environmental Report  ·  {address}, {postcode}"))
    print(f"  PDF saved: {output_path}")
    return output_path


if __name__ == "__main__":
    # Quick test
    generate_report(
        address="197 High Street, Cottenham, Cambridge",
        postcode="CB24 8RX",
        output_path=r"C:\Users\Richard Girdwood\Desktop\Property Information Report\TEST_CB24_8RX.pdf"
    )
