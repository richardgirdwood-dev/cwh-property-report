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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image, KeepTogether
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

HDR  = S("HDR",  fontSize=20, textColor=WHITE,     fontName="Helvetica-Bold")
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
            "cert_date":    after_label("Certificate number", "N/A"),
            "floor_area":   after_label("Total floor area"),
            "prop_type":    after_label("Property type"),
            "annual_cost":  in_sentence(r"spend\s*([\xc2\xa3\£]\S+\s+per year)", "N/A"),
            "co2":          in_sentence(r"This property produces\s*\n\s*(\S+\s+tonnes of CO2)", "N/A"),
            "primary_use":  in_sentence(r"primary energy use for this property per year is (.+?)\.", "N/A"),
            "features":     features,
        }
    except Exception as e:
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
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

def _overpass(query, timeout=100, retries=3):
    """POST an Overpass query, trying every mirror, and retrying the whole
    mirror list up to `retries` times if all of them fail first time round —
    the free Overpass mirrors intermittently time out or rate-limit, and a
    short retry clears most of those transient failures.

    Two silent-failure traps this guards against:
      * A busy server can take 30s+ just to start a query, so a short client
        timeout makes every attempt fail. Queries should carry a server-side
        [timeout:90] and this waits up to `timeout` seconds for the answer.
      * Overpass answers HTTP 200 with an empty `elements` list and a
        `remark` like "runtime error: Query timed out" when it gives up part
        way. That is a failure, not "nothing nearby", so it is retried.
    """
    for attempt in range(retries):
        for url in OVERPASS_MIRRORS:
            try:
                r = requests.post(url, data={"data": query},
                                  headers=OVERPASS_HEADERS, timeout=timeout)
                if r.status_code != 200 or not r.text.strip().startswith("{"):
                    continue
                data = r.json()
                remark = (data.get("remark") or "").lower()
                if "error" in remark or "timed out" in remark or "out of memory" in remark:
                    continue
                return data
            except Exception:
                continue
        if attempt < retries - 1:
            time.sleep(5 * (attempt + 1))
    return {"elements": []}


def _el_latlng(el):
    """Coordinates of an Overpass node, or the centre of a way/relation."""
    la = el.get("lat")
    lo = el.get("lon")
    if la is None:
        c = el.get("center") or {}
        la, lo = c.get("lat"), c.get("lon")
    return la, lo

def fetch_rail_stations(lat, lng):
    """Returns up to 4 nearest National Rail stations as
    (name, distance_miles, lat, lng) tuples.

    Only stations with a CRS code (the 3-letter National Rail code, e.g. CBG)
    are kept, which excludes heritage/tourist railways, Underground/metro,
    tram and miniature railway stops."""
    try:
        query = (f'[out:json][timeout:90];nwr[railway=station]["ref:crs"]'
                 f'(around:20000,{lat},{lng});out center tags;')
        data = _overpass(query)
        nearest = {}
        for el in data.get("elements", []):
            tags = el.get("tags", {})
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
                nearest[crs] = (name, dist, plat, plon)
        stations = sorted(nearest.values(), key=lambda x: x[1])
        return stations[:4]
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
                            f"{tex} — {soil_texture.get('soil_group_desc', '')}".rstrip(" —"))
        else:
            return ("NO", f"UKSO soil texture (1:50,000) at this location is recorded as "
                           f"{tex}, not clay-bearing.")

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
    """Schools, shops, GPs and pubs from OpenStreetMap.

    Run as four small queries, two at a time (the public Overpass servers allow
    two concurrent requests per client), rather than one large one. The old
    single query asked for every shop of any kind within 3 km. In a town that
    is thousands of features, and on a busy server it timed out and the
    report silently said "No amenity data found nearby". Shop types are now
    filtered server-side.

    Every result keeps its coordinates (`lat`, `lng`) for the location map."""
    from concurrent.futures import ThreadPoolExecutor

    shop_re = "|".join(sorted(SHOP_TYPES))
    queries = {
        "schools": f'nwr[amenity=school](around:3000,{lat},{lng});',
        "shops":   f'nwr[shop~"^({shop_re})$"](around:3000,{lat},{lng});',
        "doctors": f'nwr[amenity=doctors](around:6000,{lat},{lng});',
        "pubs":    f'nwr[amenity=pub](around:2000,{lat},{lng});',
    }

    def run(body):
        return _overpass(f"[out:json][timeout:90];({body});out tags center;")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(zip(queries, pool.map(run, queries.values())))

    def items(key):
        out = []
        for el in results.get(key, {}).get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name", "")
            if not name:
                continue
            el_lat, el_lng = _el_latlng(el)
            dist = haversine_miles(lat, lng, el_lat, el_lng) if el_lat is not None else None
            out.append({"name": name, "dist": dist, "lat": el_lat, "lng": el_lng, "tags": tags})
        out.sort(key=lambda x: x["dist"] if x["dist"] is not None else 99)
        return out

    schools, seen = [], set()
    for s in items("schools"):
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        s["url"] = f"https://reports.ofsted.gov.uk/search?q={requests.utils.quote(s['name'])}"
        schools.append(s)

    amenities = {"shops": items("shops"), "doctors": items("doctors"), "pubs": items("pubs")}
    return schools[:6], amenities


def _rdp(points, eps):
    """Douglas-Peucker line simplification on (x, y) points."""
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-12
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dx * (py - y1) - dy * (px - x1)) / norm
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _rdp(points[:idx + 1], eps)[:-1] + _rdp(points[idx:], eps)
    return [points[0], points[-1]]


def fetch_map_roads(lat, lng, radius_m):
    """Road, rail and river lines around the property for the location map
    basemap, as [{"c": class, "g": [(lat, lng), ...]}]. Minor roads are only
    included when the map is zoomed in, to keep the query and the PDF small.
    Returns [] on failure; the map is still drawn, just without a basemap."""
    classes = "motorway|trunk|primary|secondary|tertiary"
    if radius_m <= 3500:
        classes += "|unclassified|residential"
    r = int(radius_m)
    query = (f"[out:json][timeout:120];("
             f'way[highway~"^({classes})$"](around:{r},{lat},{lng});'
             f"way[railway=rail](around:{r},{lat},{lng});"
             f'way[waterway~"^(river|canal)$"](around:{r},{lat},{lng});'
             f");out geom;")
    try:
        data = _overpass(query, timeout=150)
    except Exception:
        return []
    k = math.cos(math.radians(lat))
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        cls = tags.get("highway") or ("rail" if tags.get("railway") else "river")
        pts = [(p["lon"] * k, p["lat"]) for p in el.get("geometry", []) if "lat" in p]
        pts = _rdp(pts, 0.00006)          # ~7 m
        if len(pts) > 1:
            out.append({"c": cls, "g": [(y, x / k) for x, y in pts]})
    return out

# ── PDF BUILDER ────────────────────────────────────────────────────────────────

def _section_header(story, icon, title):
    story.append(Paragraph(f"{icon}  {title}", SECH))
    story.append(HRFlowable(width="100%", thickness=1.2, color=MID_BLUE, spaceAfter=2))

def _rows_table(rows, col_widths=(45*mm, 100*mm, 35*mm)):
    RATING_COLORS = {"Very Good": GREEN, "Good": colors.HexColor("#2E7D32"),
                     "Average": AMBER, "Poor": RED, "N/A": colors.HexColor("#888888")}
    data = []
    cmds = [
        ("BACKGROUND",    (0,0),(-1,-1), LIGHT_GREY),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [LIGHT_GREY, WHITE]),
        ("LEFTPADDING",   (0,0),(-1,-1), 2.5*mm),
        ("RIGHTPADDING",  (0,0),(-1,-1), 2*mm),
        ("TOPPADDING",    (0,0),(-1,-1), 2*mm),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2*mm),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("BOX",           (0,0),(-1,-1), 0.3, colors.HexColor("#DDDDDD")),
        ("LINEAFTER",     (0,0),(0,-1),  0.3, colors.HexColor("#CCCCCC")),
    ]
    for i, (label, value, badge, badge_color) in enumerate(rows):
        badge_cell = Paragraph(
            f"<b>{badge}</b>",
            S(f"BG{i}", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold", alignment=1)
        ) if badge else ""
        data.append([Paragraph(label, LBL), Paragraph(value, VAL), badge_cell])
        bc = badge_color if badge else LIGHT_GREY
        cmds.append(("BACKGROUND", (2,i),(2,i), bc))
    t = Table(data, colWidths=list(col_widths))
    t.setStyle(TableStyle(cmds))
    return t

def _section(story, icon, title, rows, note=None, col_widths=(45*mm, 100*mm, 35*mm)):
    _section_header(story, icon, title)
    story.append(_rows_table(rows, col_widths))
    if note:
        story.append(Spacer(1, 1*mm))
        story.append(Paragraph(note, NOTE))
    story.append(Spacer(1, 3.5*mm))

# ── LOCATION MAP ───────────────────────────────────────────────────────────────

MAP_SCHOOL  = PURPLE
MAP_SHOP    = MID_BLUE
MAP_GP      = GREEN
MAP_PUB     = AMBER
MAP_STATION = TEAL
MAP_PROPERTY = RED

_ROAD_STYLE = {  # class -> (stroke width pt, colour, draw order)
    "motorway": (2.4, "#C8B48A", 3), "trunk": (2.2, "#C8B48A", 3),
    "primary": (1.9, "#D4C09A", 3), "secondary": (1.5, "#D9CBB0", 3),
    "tertiary": (1.1, "#CFCFCF", 2), "unclassified": (0.6, "#DEDEDE", 1),
    "residential": (0.5, "#E2E2E2", 1), "rail": (0.9, "#8A8A8A", 2),
    "river": (1.2, "#9CC3E6", 0),
}
_MI = 1609.344


def _short(text, n):
    """Truncate to n characters with an ellipsis."""
    return text if len(text) <= n else text[:n - 1].rstrip() + "\u2026"


def _pin_label(num, color):
    """Coloured map-pin number to prefix a table row label ('' if not pinned)."""
    if not num:
        return ""
    return f'<font color="{color.hexval().replace("0x", "#")}"><b>{num}</b></font>&nbsp;&nbsp;'


def _project_m(lat, lng, lat0, lng0):
    """Local equirectangular projection to metres east/north of the property."""
    x = math.radians(lng - lng0) * 6371000.0 * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * 6371000.0
    return x, y


def _clip_segment(x1, y1, x2, y2, xmin, ymin, xmax, ymax):
    """Liang-Barsky clip of one segment to a rectangle; None if outside."""
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - xmin), (dx, xmax - x1), (-dy, y1 - ymin), (dy, ymax - y1)):
        if p == 0:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return None
                t0 = max(t0, t)
            else:
                if t < t0:
                    return None
                t1 = min(t1, t)
    return (x1 + t0 * dx, y1 + t0 * dy, x1 + t1 * dx, y1 + t1 * dy)


def _spread(points, min_sep, fixed=0):
    """Nudge overlapping pin positions apart. The first `fixed` points never move."""
    pos = [list(p) for p in points]
    for _ in range(80):
        moved = False
        for a in range(len(pos)):
            for b in range(a + 1, len(pos)):
                dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
                d = math.hypot(dx, dy)
                if d >= min_sep:
                    continue
                if d < 1e-6:
                    dx, dy, d = 1.0, 0.3, math.hypot(1.0, 0.3)
                push = (min_sep - d) + 0.5
                ux, uy = dx / d, dy / d
                if a < fixed and b < fixed:
                    continue
                if a < fixed:
                    pos[b][0] += ux * push; pos[b][1] += uy * push
                elif b < fixed:
                    pos[a][0] -= ux * push; pos[a][1] -= uy * push
                else:
                    pos[a][0] -= ux * push / 2; pos[a][1] -= uy * push / 2
                    pos[b][0] += ux * push / 2; pos[b][1] += uy * push / 2
                moved = True
        if not moved:
            break
    return pos


def _map_frame(pins, lat0, lng0):
    """(cx, cy, half) in metres: a square window framing the property and the
    local pins. Pins further than 2.5 km that are rail stations are allowed to
    fall off the edge (they get an arrow) so one distant station doesn't shrink
    the whole map."""
    pts = [(0.0, 0.0)]
    for p in pins:
        if p["dist"] is None:
            continue
        if p.get("kind") == "station" and p["dist"] * _MI > 2500:
            continue
        pts.append(_project_m(p["lat"], p["lng"], lat0, lng0))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 * 1.12 + 250
    half = min(max(half, 700), 9000)
    cx = max(min(cx, half * 0.72), -half * 0.72)   # keep the property well inside
    cy = max(min(cy, half * 0.72), -half * 0.72)
    return cx, cy, half


def map_fetch_radius(pins, lat0, lng0):
    """Radius (m) from the property needed to cover the whole map window."""
    cx, cy, half = _map_frame(pins, lat0, lng0)
    return math.hypot(abs(cx) + half, abs(cy) + half) + 100


def build_location_map(lat0, lng0, pins, roads, width=180*mm, height=118*mm):
    """Vector location map as a ReportLab Drawing.

    pins: [{"num", "lat", "lng", "dist" (miles), "color", "kind"}], numbered to
    match the schools / amenities / stations tables. roads: from fetch_map_roads."""
    from reportlab.graphics.shapes import Drawing, Rect, PolyLine, Circle, String, Line, Polygon

    pins = [p for p in pins if p.get("lat") is not None and p.get("lng") is not None]
    cx, cy, half = _map_frame(pins, lat0, lng0)
    # non-square drawing: widen the window horizontally to fill it
    hx = half * max(1.0, width / height)
    hy = half * max(1.0, height / width)
    xmin, xmax, ymin, ymax = cx - hx, cx + hx, cy - hy, cy + hy
    sx = width / (xmax - xmin)
    sy = height / (ymax - ymin)
    s = min(sx, sy)

    def to_pt(x, y):
        return (x - xmin) * s, (y - ymin) * s

    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#F7F6F2"),
               strokeColor=colors.HexColor("#BBBBBB"), strokeWidth=0.6))

    # basemap
    for rd in sorted(roads or [], key=lambda r: _ROAD_STYLE.get(r["c"], (0, "", 1))[2]):
        w, col, _ = _ROAD_STYLE.get(rd["c"], (0.5, "#E5E5E5", 1))
        xy = [_project_m(a, b, lat0, lng0) for a, b in rd["g"]]
        run = []
        for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
            seg = _clip_segment(x1, y1, x2, y2, xmin, ymin, xmax, ymax)
            if seg is None:
                if len(run) > 2:
                    d.add(PolyLine(run, strokeColor=colors.HexColor(col), strokeWidth=w,
                                   strokeLineCap=1, strokeLineJoin=1,
                                   strokeDashArray=[3, 1.5] if rd["c"] == "rail" else None))
                run = []
                continue
            a = to_pt(seg[0], seg[1]); b = to_pt(seg[2], seg[3])
            if run and abs(run[-2] - a[0]) < 0.01 and abs(run[-1] - a[1]) < 0.01:
                run += [b[0], b[1]]
            else:
                if len(run) > 2:
                    d.add(PolyLine(run, strokeColor=colors.HexColor(col), strokeWidth=w,
                                   strokeLineCap=1, strokeLineJoin=1,
                                   strokeDashArray=[3, 1.5] if rd["c"] == "rail" else None))
                run = [a[0], a[1], b[0], b[1]]
        if len(run) > 2:
            d.add(PolyLine(run, strokeColor=colors.HexColor(col), strokeWidth=w,
                           strokeLineCap=1, strokeLineJoin=1,
                           strokeDashArray=[3, 1.5] if rd["c"] == "rail" else None))

    # distance rings (miles), centred on the property
    px0, py0 = to_pt(0, 0)
    span_mi = max(hx, hy) / _MI
    rings = [0.25, 0.5, 0.75, 1] if span_mi < 1.2 else \
            [0.5, 1, 1.5, 2] if span_mi < 2.6 else [1, 2, 3, 4, 5]
    for r_mi in rings:
        rpt = r_mi * _MI * s
        far = max(math.hypot(px0 - cxp, py0 - cyp) for cxp in (0, width) for cyp in (0, height))
        if rpt > far:
            continue
        # drawn as a clipped polyline: ReportLab drawings don't clip to their bounds
        ring = [(px0 + rpt * math.cos(math.radians(a)), py0 + rpt * math.sin(math.radians(a)))
                for a in range(0, 361, 3)]
        run = []
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            seg = _clip_segment(x1, y1, x2, y2, 0, 0, width, height)
            if seg is None:
                if len(run) > 2:
                    d.add(PolyLine(run, strokeColor=colors.HexColor("#A0A0A0"), strokeWidth=0.5,
                                   strokeDashArray=[1.5, 2]))
                run = []
                continue
            if not run:
                run = [seg[0], seg[1]]
            run += [seg[2], seg[3]]
        if len(run) > 2:
            d.add(PolyLine(run, strokeColor=colors.HexColor("#A0A0A0"), strokeWidth=0.5,
                           strokeDashArray=[1.5, 2]))
        for ang in (-40, -140, 40, -90, 140, 0, 180):
            lx = px0 + rpt * math.cos(math.radians(ang))
            ly = py0 + rpt * math.sin(math.radians(ang))
            if 6 < lx < width - 22 and 6 < ly < height - 8 and not (lx < 52 * mm and ly > height - 24 * mm):
                lab = f"{r_mi:g} mi"
                d.add(Rect(lx + 1, ly - 7.5, len(lab) * 3.4 + 2, 8, fillColor=colors.HexColor("#F7F6F2"),
                           strokeColor=None))
                d.add(String(lx + 2, ly - 5.5, lab, fontName="Helvetica", fontSize=6,
                             fillColor=colors.HexColor("#777777")))
                break

    # pins: on-map ones are spread apart (with a leader line to the true spot);
    # off-map ones become an arrow at the edge with their number and distance
    R = 5.2   # pin radius (pt)
    on, off = [], []
    for p in pins:
        x, y = to_pt(*_project_m(p["lat"], p["lng"], lat0, lng0))
        (on if 3 < x < width - 3 and 3 < y < height - 3 else off).append((p, x, y))

    placed = _spread([(px0, py0)] + [(x, y) for _, x, y in on], min_sep=2 * R + 1.5, fixed=1)[1:]
    for (p, tx, ty), (x, y) in zip(on, placed):
        x = min(max(x, R + 1), width - R - 1)
        y = min(max(y, R + 1), height - R - 1)
        col = p["color"]
        if math.hypot(x - tx, y - ty) > 1.5:
            d.add(Line(tx, ty, x, y, strokeColor=col, strokeWidth=0.6))
            d.add(Circle(tx, ty, 1.3, fillColor=col, strokeColor=None))
        d.add(Circle(x, y, R, fillColor=col, strokeColor=colors.white, strokeWidth=0.8))
        n = str(p["num"])
        d.add(String(x, y - 2.3, n, fontName="Helvetica-Bold", fontSize=6.3 if len(n) < 2 else 5.6,
                     fillColor=colors.white, textAnchor="middle"))

    labels = []
    for p, x, y in off:
        ux, uy = x - px0, y - py0
        n = math.hypot(ux, uy) or 1.0
        ux, uy = ux / n, uy / n
        ts = []
        for u, lo, hi, c0 in ((ux, 4, width - 4, px0), (uy, 4, height - 4, py0)):
            if abs(u) > 1e-9:
                ts.append(((hi if u > 0 else lo) - c0) / u)
        t = min(t for t in ts if t > 0)
        ex, ey = px0 + ux * t, py0 + uy * t
        labels.append([p, ex, ey, ex - ux * 26, ey - uy * 26])
    lab_pos = _spread([(l[3], l[4]) for l in labels], min_sep=13)
    for (p, ex, ey, _, _), (lx, ly) in zip(labels, lab_pos):
        col = p["color"]
        d.add(Line(lx, ly, ex, ey, strokeColor=col, strokeWidth=1))
        ang = math.atan2(ey - ly, ex - lx)
        hx_, hy_ = math.cos(ang), math.sin(ang)
        d.add(Polygon([ex, ey, ex - 5 * hx_ + 2.5 * hy_, ey - 5 * hy_ - 2.5 * hx_,
                       ex - 5 * hx_ - 2.5 * hy_, ey - 5 * hy_ + 2.5 * hx_],
                      fillColor=col, strokeColor=None))
        txt = f"{p['num']}  {p['dist']:.1f} mi"
        tw = len(txt) * 3.3 + 6
        d.add(Rect(lx - tw / 2, ly - 5, tw, 10, rx=2, ry=2, fillColor=colors.white,
                   strokeColor=col, strokeWidth=0.6))
        d.add(String(lx, ly - 2.3, txt, fontName="Helvetica-Bold", fontSize=6.3,
                     fillColor=col, textAnchor="middle"))

    # property star
    pts = []
    for i in range(10):
        r = 7.5 if i % 2 == 0 else 3.2
        a = math.radians(90 + i * 36)
        pts += [px0 + r * math.cos(a), py0 + r * math.sin(a)]
    d.add(Polygon(pts, fillColor=MAP_PROPERTY, strokeColor=colors.white, strokeWidth=0.8))

    # north arrow
    nx, ny = width - 12, height - 30
    d.add(Polygon([nx, ny + 16, nx - 4, ny + 6, nx + 4, ny + 6], fillColor=DARK_BLUE, strokeColor=None))
    d.add(Line(nx, ny, nx, ny + 8, strokeColor=DARK_BLUE, strokeWidth=1.2))
    d.add(String(nx, ny - 8, "N", fontName="Helvetica-Bold", fontSize=8, fillColor=DARK_BLUE,
                 textAnchor="middle"))

    # scale bar (miles)
    bar_mi = next(b for b in (2, 1, 0.5, 0.25, 0.1) if b * _MI * s <= width / 4)
    blen = bar_mi * _MI * s
    d.add(Rect(8, 6, blen + 8, 15, fillColor=colors.HexColor("#F7F6F2"), strokeColor=None))
    d.add(Line(12, 10, 12 + blen, 10, strokeColor=TEXT, strokeWidth=1.6))
    d.add(Line(12, 8, 12, 12, strokeColor=TEXT, strokeWidth=0.8))
    d.add(Line(12 + blen, 8, 12 + blen, 12, strokeColor=TEXT, strokeWidth=0.8))
    d.add(String(12 + blen / 2, 13.5, f"{bar_mi:g} mile" + ("" if bar_mi == 1 else "s"),
                 fontName="Helvetica", fontSize=6.5, fillColor=TEXT, textAnchor="middle"))

    # legend
    kinds = [("Property", MAP_PROPERTY, "star")]
    for kind, label, col in (("school", "Schools", MAP_SCHOOL), ("shop", "Shops", MAP_SHOP),
                             ("gp", "GP", MAP_GP), ("pub", "Pubs", MAP_PUB),
                             ("station", "Rail stations", MAP_STATION)):
        if any(p.get("kind") == kind for p in pins):
            kinds.append((label, col, "dot"))
    lw, lh = 30 * mm, 4 + 9 * len(kinds)
    lx0, ly0 = 5, height - 5 - lh
    d.add(Rect(lx0, ly0, lw, lh, fillColor=colors.white, strokeColor=colors.HexColor("#D0D0D0"),
               strokeWidth=0.5, fillOpacity=0.92))
    for i, (label, col, mark) in enumerate(kinds):
        yy = ly0 + lh - 8 - i * 9
        if mark == "star":
            st = []
            for k in range(10):
                r = 3.6 if k % 2 == 0 else 1.5
                a = math.radians(90 + k * 36)
                st += [lx0 + 7 + r * math.cos(a), yy + 1.5 + r * math.sin(a)]
            d.add(Polygon(st, fillColor=col, strokeColor=None))
        else:
            d.add(Circle(lx0 + 7, yy + 1.5, 2.8, fillColor=col, strokeColor=None))
        d.add(String(lx0 + 14, yy - 0.5, label, fontName="Helvetica", fontSize=6.8, fillColor=TEXT))

    return d

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

    # Step 2 — parallel-ish data fetching
    conservation_area = fetch_conservation_area(lat, lng)
    listed_building   = fetch_listed_building(lat, lng, house_number)
    coal_risk         = fetch_coal_mining_risk(lat, lng)
    epc               = fetch_epc(postcode, house_number)
    sales, rm_url     = fetch_sales_history(postcode, house_number)
    stations          = fetch_rail_stations(lat, lng)
    soil_data, soil_url = fetch_soil(lat, lng)
    soil_texture       = fetch_uk_soil_texture(lat, lng)
    geology            = fetch_bedrock_geology(lat, lng)
    schools, amenities = fetch_schools_and_amenities(lat, lng)

    # Location map pins, numbered in the order the rows appear in the tables
    # (schools, then shops / GPs / pubs, then the two nearest stations)
    map_pins = []
    def _pin(item, color, kind):
        if item.get("lat") is None:
            return None
        n = len(map_pins) + 1
        map_pins.append({"num": n, "lat": item["lat"], "lng": item["lng"],
                         "dist": item.get("dist"), "color": color, "kind": kind})
        return n
    for sch in schools[:5]:
        sch["num"] = _pin(sch, MAP_SCHOOL, "school")
    for key, col, kind in (("shops", MAP_SHOP, "shop"), ("doctors", MAP_GP, "gp"),
                           ("pubs", MAP_PUB, "pub")):
        for item in amenities[key][:2]:
            item["num"] = _pin(item, col, kind)
    station_nums = []
    for i, st_ in enumerate(stations):
        station_nums.append(_pin({"lat": st_[2], "lng": st_[3], "dist": st_[1]},
                                 MAP_STATION, "station") if i < 2 else None)
    map_roads = fetch_map_roads(lat, lng, map_fetch_radius(map_pins, lat, lng))

    print(f"  Data fetched. Building PDF...")

    # ── Build PDF ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )
    story = []
    try:
        today = datetime.now().strftime("%#d %B %Y")  # Windows strips leading zero
    except Exception:
        today = datetime.now().strftime("%d %B %Y")

    # Header — logo on right, address on left
    import os as _os
    logo_cell = ""
    if _os.path.exists(LOGO_PATH):
        try:
            logo_img = Image(LOGO_PATH, height=14*mm, width=45*mm)
            logo_img.hAlign = "RIGHT"
            logo_cell = logo_img
        except Exception:
            logo_cell = Paragraph("CWH Surveyors LLP", S("LGO", fontSize=9, textColor=WHITE, fontName="Helvetica-Bold", alignment=2))

    hdr_table = Table([[
        Paragraph(f"{address},  {postcode}", HDR),
        logo_cell,
    ],[
        Paragraph(la_name, SUB),
        Paragraph("Property Environmental Report", S("DR", fontSize=8, textColor=colors.HexColor("#BDD7EE"), fontName="Helvetica", alignment=2)),
    ]], colWidths=[130*mm, 50*mm])
    hdr_table.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), DARK_BLUE),
        ("LEFTPADDING",  (0,0),(-1,-1), 5*mm),
        ("RIGHTPADDING", (0,0),(-1,-1), 4*mm),
        ("TOPPADDING",   (0,0),(-1,-1), 3*mm),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3*mm),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("ALIGN",        (1,0),(1,-1),  "RIGHT"),
    ]))
    story.append(hdr_table)
    story.append(Spacer(1, 3*mm))

    # Property strip
    floor_area = epc.get("floor_area", "N/A") if epc else "N/A"
    prop_type  = epc.get("prop_type",  "N/A") if epc else "N/A"
    strip_items = [
        f"<b>Postcode</b><br/>{postcode}",
        f"<b>Local Authority</b><br/>{la_name}",
        f"<b>Region</b><br/>{region or 'England'}",
        f"<b>Property Type</b><br/>{prop_type}",
        f"<b>Floor Area</b><br/>{floor_area}",
    ]
    strip = Table([[Paragraph(t, S(f"SI{j}", fontSize=8.5, textColor=DARK_BLUE, fontName="Helvetica", leading=13))
                   for j, t in enumerate(strip_items)]], colWidths=[W/5]*5)
    strip.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), LIGHT_BLUE),
        ("LEFTPADDING",  (0,0),(-1,-1), 3*mm),
        ("TOPPADDING",   (0,0),(-1,-1), 2.5*mm),
        ("BOTTOMPADDING",(0,0),(-1,-1), 2.5*mm),
        ("LINEAFTER",    (0,0),(3,0), 0.4, colors.HexColor("#BBCCDD")),
        ("BOX",          (0,0),(-1,-1), 0.4, colors.HexColor("#BBCCDD")),
    ]))
    story.append(strip)
    story.append(Spacer(1, 4*mm))

    # Flood Risk
    pc_enc = postcode.replace(" ", "+")
    _section(story, "🌊", "Flood Risk  (Environment Agency)", [
        ("Rivers & Sea",  "Check required — see link below", "CHECK", AMBER),
        ("Surface Water", "Check required — see link below", "CHECK", AMBER),
        ("Direct Link",   link(f"https://check-long-term-flood-risk.service.gov.uk/postcode?postcode={pc_enc}",
                               "check-long-term-flood-risk.service.gov.uk"), "", None),
    ], note="EA flood risk service requires CAPTCHA verification — follow the link above.",
    col_widths=(40*mm, 98*mm, 20*mm))

    # Radon
    radon_desc, (radon_hex, radon_badge) = fetch_radon_status(lat, lng)
    radon_color = colors.HexColor(radon_hex)
    _section(story, "☢", "Radon  (UKHSA)", [
        ("Indicative Status", radon_desc, radon_badge, radon_color),
        ("UKHSA Action Level","200 Bq/m³ for homes", "", None),
        ("Interactive Map",   link("www.ukradon.org/information/ukmaps"), "", None),
    ], note="For a definitive address-level result use the UKHSA paid address search at ukradon.org (£3.90).",
    col_widths=(40*mm, 98*mm, 20*mm))

    # Conservation Area
    if conservation_area:
        ca_status = f"WITHIN designated conservation area"
        ca_rows = [
            ("Status",            ca_status, "DESIGNATED", PURPLE),
            ("Conservation Area", conservation_area, "", None),
            ("Planning Data Map", link("www.planning.data.gov.uk/map/?dataset=conservation-area"), "", None),
        ]
        ca_note = "Conservation area designation restricts permitted development rights. Confirm boundary with the LPA."
    else:
        ca_rows = [
            ("Status",            "No conservation area recorded at this location (indicative)", "", None),
            ("Planning Data Map", link("www.planning.data.gov.uk/map/?dataset=conservation-area"), "", None),
        ]
        ca_note = "National dataset is not exhaustive — confirm with the LPA for definitive status."
    _section(story, "🏛", "Conservation Area  (Historic England / Planning Data)",
             ca_rows, note=ca_note, col_widths=(40*mm, 98*mm, 20*mm))

    # Listed Building
    if listed_building:
        grade = listed_building.get("grade", "")
        grade_color = {"I": RED, "II*": RED, "II": AMBER}.get(grade, PURPLE)
        lb_rows = [
            ("Status",  f"LISTED BUILDING — Grade {grade}", f"GRADE {grade}", grade_color),
            ("Name",    listed_building.get("name", ""), "", None),
        ]
        if listed_building.get("url"):
            lb_rows.append(("Historic England", link(listed_building["url"], "View listing"), "", None))
        lb_note = "Listed building status imposes restrictions on alterations. Consent required from LPA for most works."
    else:
        lb_rows = [("Status", "No listed building recorded at this location (indicative)", "", None),
                   ("Source", link("https://historicengland.org.uk/listing/the-list/", "Historic England — The List"), "", None)]
        lb_note = "Confirm with Historic England if listing status is critical to the survey."
    _section(story, "🏰", "Listed Building Status  (Historic England)", lb_rows,
             note=lb_note, col_widths=(40*mm, 98*mm, 20*mm))

    # Coal Mining Risk
    coal_gov_link = link(
        "https://www.gov.uk/government/publications/coal-mining-data-development-high-risk-areas",
        "gov.uk — coal mining data"
    )
    if coal_risk == "high":
        cm_rows = [
            ("Status", "WITHIN a Coal Mining Development High Risk Area", "HIGH RISK", RED),
            ("Source", coal_gov_link, "", None),
        ]
        cm_note = ("A Coal Mining Report (CON29M) is recommended prior to purchase — instruct via a "
                   "solicitor or directly with the Mining Remediation Authority (coal.gov.uk).")
    elif coal_risk == "low":
        cm_rows = [
            ("Status", "WITHIN a Coal Mining Development Low Risk Area", "LOW RISK", AMBER),
            ("Source", coal_gov_link, "", None),
        ]
        cm_note = "Low risk area — a Coal Mining Report is not routinely required but may still be advisable."
    else:
        cm_rows = [
            ("Status", "Not within a recorded coal mining development risk area (indicative)", "", None),
            ("Source", coal_gov_link, "", None),
        ]
        cm_note = ("Based on Mining Remediation Authority (formerly the Coal Authority) risk area data — "
                   "confirm via a CON29M search if in doubt.")
    _section(story, "⛏", "Coal Mining Risk  (Mining Remediation Authority)", cm_rows,
             note=cm_note, col_widths=(40*mm, 98*mm, 20*mm))

    # Planning History
    _section(story, "📋", "Planning History", [
        ("Local Planning Authority", la_name, "", None),
        ("Planning Portal",          link("www.gov.uk/search-register-planning-decisions"), "", None),
    ], col_widths=(40*mm, 118*mm, 0*mm))

    # EPC
    if epc:
        epc_rating = epc.get("rating", "N/A")
        rating_badge_color = {"A": GREEN, "B": GREEN, "C": colors.HexColor("#5D8A27"),
                              "D": AMBER,  "E": RED,   "F": RED, "G": RED}.get(epc_rating, AMBER)
        epc_rows = [
            ("Property",           f"{address}, {postcode}  |  {epc.get('prop_type','N/A')}  |  {epc.get('floor_area','N/A')}", "", None),
            ("Current EPC Rating", f"{epc_rating}  (Score: {epc.get('score','N/A')})  —  Potential: {epc.get('pot_rating','N/A')} ({epc.get('pot_score','N/A')})",
             f"{epc_rating} RATED", rating_badge_color),
            ("Annual Energy Cost", epc.get("annual_cost", "N/A"), "", None),
            ("CO2 Emissions",      epc.get("co2", "N/A"), "", None),
            ("Primary Energy Use", epc.get("primary_use", "N/A"), "", None),
            ("Certificate Date",   f"{epc.get('cert_date','N/A')}  |  Valid until: {epc.get('valid_until','N/A')}", "", None),
            ("Certificate No.",    link(f"https://find-energy-certificate.service.gov.uk/energy-certificate/{epc['cert_id']}",
                                        epc["cert_id"]), "", None),
        ]
        _section(story, "⚡", "Energy Performance Certificate  (Gov.uk / MHCLG)",
                 epc_rows, col_widths=(40*mm, 105*mm, 23*mm))

        # EPC building features table
        features = epc.get("features", [])
        if features:
            RATING_COL = {"Very good": GREEN, "Good": GREEN, "Average": AMBER,
                          "Poor": RED, "Very poor": RED, "N/A": colors.HexColor("#888888")}
            feat_hdr = S("FH", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")
            feat_data = [[Paragraph(h, feat_hdr) for h in ["Feature", "Description", "Rating"]]]
            feat_cmds = [
                ("BACKGROUND",   (0,0),(-1,0), DARK_BLUE),
                ("ROWBACKGROUNDS",(0,1),(-1,-1), [LIGHT_GREY, WHITE]),
                ("LEFTPADDING",  (0,0),(-1,-1), 2.5*mm),
                ("RIGHTPADDING", (0,0),(-1,-1), 2*mm),
                ("TOPPADDING",   (0,0),(-1,-1), 2*mm),
                ("BOTTOMPADDING",(0,0),(-1,-1), 2*mm),
                ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
                ("BOX",          (0,0),(-1,-1), 0.3, colors.HexColor("#CCCCCC")),
                ("INNERGRID",    (0,0),(-1,-1), 0.3, colors.HexColor("#DDDDDD")),
            ]
            for i, f in enumerate(features, start=1):
                rc = RATING_COL.get(f["rating"], AMBER)
                rating_p = Paragraph(f"<b>{f['rating']}</b>",
                                     S(f"FR{i}", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold", alignment=1))
                feat_data.append([
                    Paragraph(f["feature"], LBL),
                    Paragraph(f["desc"],    VAL),
                    rating_p,
                ])
                feat_cmds.append(("BACKGROUND", (2,i),(2,i), rc))
            feat_table = Table(feat_data, colWidths=[38*mm, 112*mm, 30*mm])
            feat_table.setStyle(TableStyle(feat_cmds))
            story.append(feat_table)
            story.append(Spacer(1, 3.5*mm))
    else:
        _section(story, "⚡", "Energy Performance Certificate", [
            ("Status", f"No EPC found for {house_number} at postcode {postcode}", "", None),
            ("Search", link(f"https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-postcode?postcode={pc_enc}",
                            "Search EPC register"), "", None),
        ], col_widths=(40*mm, 118*mm, 0*mm))

    # Sales History
    rm_link_text = postcode.replace(" ", "-").lower()
    _section_header(story, "🏡", "Sales History  (Rightmove / Land Registry)")
    sh_hdr = S("SHH", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")
    if sales:
        sh_data = [[Paragraph(h, sh_hdr) for h in ["<b>Address</b>","<b>Sale Price</b>","<b>Date Sold</b>","<b>Type</b>","<b>Tenure</b>"]]]
        for s in sales:
            sh_data.append([
                Paragraph(f"<b>{s['address']}</b>", S("SA", fontSize=9, fontName="Helvetica-Bold", textColor=TEXT)),
                Paragraph(f"<b>{s['price']}</b>",   S("SP", fontSize=9, fontName="Helvetica-Bold", textColor=TEXT, alignment=2)),
                Paragraph(s["date"],   S("SD", fontSize=9, fontName="Helvetica", textColor=TEXT)),
                Paragraph(s["type"],   S("ST", fontSize=9, fontName="Helvetica", textColor=TEXT)),
                Paragraph(s["tenure"], S("SN", fontSize=9, fontName="Helvetica", textColor=TEXT)),
            ])
        sh_table = Table(sh_data, colWidths=[70*mm, 28*mm, 28*mm, 28*mm, 26*mm])
        sh_table.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,0), DARK_BLUE),
            ("BACKGROUND",   (0,1),(-1,-1), colors.HexColor("#FFF8E1")),
            ("LEFTPADDING",  (0,0),(-1,-1), 2.5*mm),
            ("RIGHTPADDING", (0,0),(-1,-1), 2*mm),
            ("TOPPADDING",   (0,0),(-1,-1), 2.5*mm),
            ("BOTTOMPADDING",(0,0),(-1,-1), 2.5*mm),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
            ("BOX",          (0,0),(-1,-1), 0.3, colors.HexColor("#CCCCCC")),
            ("INNERGRID",    (0,0),(-1,-1), 0.3, colors.HexColor("#DDDDDD")),
            ("ALIGN",        (1,0),(1,-1),  "RIGHT"),
            ("LINEABOVE",    (0,1),(-1,1),  1.0, colors.HexColor("#D4A017")),
            ("LINEBELOW",    (0,-1),(-1,-1), 1.0, colors.HexColor("#D4A017")),
        ]))
        story.append(sh_table)
    else:
        story.append(Paragraph(f"No sold price records found — check Rightmove directly.", VAL))
    story.append(Spacer(1, 1.5*mm))
    story.append(Paragraph(f'Full history: {link(rm_url, f"rightmove.co.uk/house-prices/{rm_link_text}.html")}  |  Source: Rightmove / HM Land Registry.', NOTE))
    story.append(Spacer(1, 3.5*mm))

    # Schools + Amenities side by side
    schools_block = [
        Paragraph("🎓  Local Schools  (Ofsted)", SECH),
        HRFlowable(width="100%", thickness=1.2, color=MID_BLUE, spaceAfter=2),
    ]
    if schools:
        sch_rows = []
        for sch in schools[:5]:
            dist_text = f"{sch['dist']:.1f} mi" if sch.get("dist") is not None else ""
            sch_rows.append((
                _pin_label(sch.get("num"), MAP_SCHOOL) + link(sch["url"], sch["name"][:32]),
                dist_text,
                "",
                None,
            ))
        schools_block.append(_rows_table(sch_rows, (62*mm, 15*mm, 13*mm)))
    else:
        schools_block.append(Paragraph(f'Search: {link(f"https://reports.ofsted.gov.uk/search?q={postcode}&tab=schools", "reports.ofsted.gov.uk")}', NOTE))

    amenities_block = [
        Paragraph("🛒  Local Amenities  (OpenStreetMap)", SECH),
        HRFlowable(width="100%", thickness=1.2, color=MID_BLUE, spaceAfter=2),
    ]
    amenity_rows = []
    for shop in amenities["shops"][:2]:
        d = f"{shop['dist']:.1f} mi" if shop.get("dist") is not None else ""
        amenity_rows.append((_pin_label(shop.get("num"), MAP_SHOP) + _short(shop["name"], 27), d, "SHOP", MID_BLUE))
    for gp in amenities["doctors"][:2]:
        d = f"{gp['dist']:.1f} mi" if gp.get("dist") is not None else ""
        amenity_rows.append((_pin_label(gp.get("num"), MAP_GP) + _short(gp["name"], 27), d, "GP", GREEN))
    for pub in amenities["pubs"][:2]:
        d = f"{pub['dist']:.1f} mi" if pub.get("dist") is not None else ""
        amenity_rows.append((_pin_label(pub.get("num"), MAP_PUB) + _short(pub["name"], 27), d, "PUB", AMBER))
    if amenity_rows:
        amenities_block.append(_rows_table(amenity_rows, (49*mm, 14*mm, 15*mm)))
    else:
        amenities_block.append(Paragraph("No amenity data found nearby.", NOTE))
    amenities_block.append(Paragraph("Source: OpenStreetMap — verify current trading status locally.", NOTE))

    two_col2 = Table([[schools_block, Spacer(4*mm,1), amenities_block]], colWidths=[91*mm, 4*mm, 85*mm])
    two_col2.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(two_col2)
    story.append(Spacer(1, 3.5*mm))

    # Rail + Soil side by side
    rail_block = [
        Paragraph("🚉  Nearest Railway Stations  (National Rail)", SECH),
        HRFlowable(width="100%", thickness=1.2, color=MID_BLUE, spaceAfter=2),
    ]
    if stations:
        rail_rows = [(_pin_label(station_nums[i], MAP_STATION) + name, f"{dist:.1f} miles",
                      "NEAREST" if i==0 else "", TEAL if i==0 else None)
                     for i, (name, dist, _slat, _slng) in enumerate(stations)]
        rail_rows.append(("National Rail", link("www.nationalrail.co.uk"), "", None))
        rail_block.append(_rows_table(rail_rows, (35*mm, 43*mm, 18*mm)))
    else:
        rail_block.append(Paragraph("Station data unavailable — check nationalrail.co.uk", NOTE))
    rail_block.append(Paragraph("Straight-line distances. Driving distances will be greater.", NOTE))

    soil_block = [
        Paragraph("🌱  Soil Texture  (BGS UK Soil Observatory)", SECH),
        HRFlowable(width="100%", thickness=1.2, color=MID_BLUE, spaceAfter=2),
    ]
    if soil_texture:
        soil_rows = [
            ("Texture",         soil_texture.get("texture", "N/A"),         "", None),
            ("Soil Group",      soil_texture.get("soil_group", "N/A"),      "", None),
            ("Depth",           soil_texture.get("depth", "N/A"),           "", None),
            ("Calcareous",      soil_texture.get("calcareous", "N/A"),      "", None),
            ("Parent Material", soil_texture.get("parent_material", "N/A"), "", None),
        ]
        soil_block.append(_rows_table(soil_rows, (28*mm, 44*mm, 14*mm)))
    elif soil_data:
        # Legacy LandIS Soilscapes path — kept in case Cranfield reinstates it
        soil_rows = [
            ("Classification", soil_data.get("name", "N/A"),                      f"TYPE {soil_data.get('ssid','')}", BROWN),
            ("Texture",        soil_data.get("texture",  "N/A"),                   "", None),
            ("Drainage",       soil_data.get("drainage", "N/A"),                   "", None),
            ("Fertility",      soil_data.get("fertility","N/A"),                   "", None),
            ("Land Cover",     soil_data.get("landcover","N/A"),                   "", None),
        ]
        soil_block.append(_rows_table(soil_rows, (28*mm, 44*mm, 14*mm)))
    else:
        soil_block.append(Paragraph("Soil texture data unavailable for this location.", NOTE))
    soil_block.append(Paragraph("Source: BGS UK Soil Observatory, 1:50,000 Parent Material Model.", NOTE))

    two_col3 = Table([[rail_block, Spacer(4*mm,1), soil_block]], colWidths=[96*mm, 4*mm, 80*mm])
    two_col3.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(two_col3)
    story.append(Spacer(1, 4*mm))

    # Location map — pins numbered to match the tables above
    map_block = [
        Paragraph("🗺  Location Map  (OpenStreetMap)", SECH),
        HRFlowable(width="100%", thickness=1.2, color=MID_BLUE, spaceAfter=2),
    ]
    if map_pins:
        map_block.append(build_location_map(lat, lng, map_pins, map_roads))
        map_block.append(Spacer(1, 1*mm))
        basemap_note = "" if map_roads else " Road basemap unavailable for this run."
        map_block.append(Paragraph(
            "Numbered pins match the schools, amenities and stations tables above; arrows point to "
            "stations beyond the map edge. Rings are straight-line distances from the property."
            f"{basemap_note} Map data © OpenStreetMap contributors, ODbL.", NOTE))
    else:
        map_block.append(Paragraph(
            "Map unavailable — no location data was returned for this property from OpenStreetMap.", NOTE))
    story.append(KeepTogether(map_block))
    story.append(Spacer(1, 4*mm))

    # Bedrock & Superficial Geology
    bedrock     = geology.get("bedrock")
    superficial = geology.get("superficial")
    geo_rows = []
    if bedrock:
        type_suffix = f", {bedrock['type']}" if bedrock.get("type") else ""
        geo_rows.append((
            "Bedrock",
            f"{bedrock.get('lithology','N/A')} — {bedrock.get('age','N/A')} ({bedrock.get('period','N/A')}{type_suffix})",
            "", None,
        ))
    else:
        geo_rows.append(("Bedrock", "No bedrock record found at this location", "", None))
    if superficial:
        geo_rows.append((
            "Superficial Deposits",
            f"{superficial.get('lithology','N/A')} — {superficial.get('age','N/A')}",
            "", None,
        ))
    else:
        geo_rows.append(("Superficial Deposits", "None recorded — bedrock at or near surface", "", None))
    clay_verdict, clay_reason = assess_clay_soil(geology, soil_texture)
    clay_badge_color = {"YES": RED, "NO": GREEN, "UNKNOWN": colors.HexColor("#888888")}[clay_verdict]
    geo_rows.append(("Clay Soil Indicator", clay_reason, clay_verdict, clay_badge_color))
    geo_rows.append(("BGS Geology Viewer", link("https://www.bgs.ac.uk/map-viewers/bgs-geology-viewer/", "View on BGS Geology Viewer"), "", None))
    _section(story, "🪨", "Bedrock & Superficial Geology  (British Geological Survey)", geo_rows,
             note="Bedrock/superficial data is 1:625,000 scale (regional, not site-specific). The Clay Soil "
                  "Indicator uses the finer 1:50,000 UKSO soil texture data above when available (falling back "
                  "to this coarser dataset only if that lookup fails) — even so, for foundation-critical "
                  "decisions a site-specific trial pit or geotechnical survey is still recommended. "
                  "Distinct from the soil texture classification above, which covers agricultural/drainage "
                  "soil type rather than underlying geology.",
             col_widths=(40*mm, 98*mm, 20*mm))
    story.append(Spacer(1, 2*mm))

    # Disclaimer
    story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#CCCCCC"), spaceAfter=2))
    story.append(Paragraph(
        "<b>Disclaimer:</b> This report is indicative only and generated from publicly available data sources "
        "(Environment Agency, UKHSA, Cranfield University, Ofsted, National Rail, OpenStreetMap, Rightmove / HM Land Registry, "
        f"Gov.uk EPC Register). It does not constitute a formal environmental search. "
        f"Data correct as at {today}.", DISC))

    doc.build(story)
    print(f"  PDF saved: {output_path}")
    return output_path


if __name__ == "__main__":
    # Quick test
    generate_report(
        address="197 High Street, Cottenham, Cambridge",
        postcode="CB24 8RX",
        output_path=r"C:\Users\Richard Girdwood\Desktop\Property Information Report\TEST_CB24_8RX.pdf"
    )
