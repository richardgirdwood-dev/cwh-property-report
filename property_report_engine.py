"""
Property Report Engine
Generates a Sprift-style PDF environmental report for any UK residential property.
Usage: generate_report(address, postcode, output_path)
"""

import os
import re
import math
import requests
from datetime import datetime
from bs4 import BeautifulSoup

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image
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
    """Returns lat, lng, admin_district, county, region from postcodes.io"""
    pc = postcode.replace(" ", "")
    try:
        r = requests.get(f"https://api.postcodes.io/postcodes/{pc}", timeout=10)
        d = r.json()["result"]
        return {
            "lat":    d["latitude"],
            "lng":    d["longitude"],
            "la":     d["admin_district"],
            "county": d.get("admin_county") or "",
            "region": d.get("region") or "",
        }
    except Exception:
        return {"lat": None, "lng": None, "la": "Unknown", "county": "", "region": ""}

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

def _radon_narrative(radon_desc):
    """Turn the raw radon status text into a RICS-style narrative sentence."""
    pct_match = re.search(r'(\d+-\d+%|>\d+%|<1%)', radon_desc)
    pct = pct_match.group(1) if pct_match else None

    if radon_desc.lower().startswith("low risk"):
        return (
            "The UK Health Security Agency identifies the area in which the property is "
            "situated as one of low radon risk, with less than 1% of homes at or above the "
            "Action Level. Radon protective measures are not routinely required, although "
            "testing can be arranged for reassurance if desired — see www.ukradon.org."
        )
    elif radon_desc.lower().startswith("elevated risk"):
        return (
            f"The UK Health Security Agency identifies the area in which the property is "
            f"situated as one where there is a moderate likelihood ({pct or 'an elevated level'}) "
            f"of elevated radon levels. This means that a small but notable proportion of "
            f"properties may be affected, and while remedial measures are not routinely "
            f"required, radon testing is advisable for reassurance. Further details on radon "
            f"gas and information on monitoring can be obtained from www.ukradon.org."
        )
    elif radon_desc.lower().startswith("high risk"):
        return (
            f"The UK Health Security Agency identifies the area in which the property is "
            f"situated as one of higher radon risk (maximum radon potential {pct or 'elevated'}). "
            f"Radon protective measures and/or testing are recommended prior to purchase — "
            f"further details can be obtained from www.ukradon.org."
        )
    else:
        return (
            "UKHSA radon data could not be retrieved automatically for this location — the "
            "interactive map at www.ukradon.org/information/ukmaps should be checked directly."
        )

def _clean_conservation_name(name):
    """Strip a trailing 'conservation area' from the fetched name (some sources
    already include it) and title-case the result, so it reads naturally when
    'Conservation Area' is appended once in the sentence."""
    stripped = re.sub(r'\s+conservation\s+area\s*$', '', name, flags=re.IGNORECASE).strip()
    if stripped.isupper() or stripped.islower():
        stripped = stripped.title()
    return stripped

def _join_list(items):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"

def _school_phrase(schools):
    """Describe school provision by age range only, not by name. Uses a simple
    keyword heuristic on the school name since OSM rarely tags age range
    reliably for UK schools."""
    has_primary = any(
        re.search(r'\b(primary|infant|junior)\b', s["name"], re.IGNORECASE) for s in schools
    )
    has_secondary = any(
        re.search(r'\b(secondary|high school|community college|comprehensive)\b', s["name"], re.IGNORECASE)
        for s in schools
    )
    if has_primary and has_secondary:
        return "schooling for all ages"
    elif has_primary:
        return "primary schooling"
    elif has_secondary:
        return "secondary schooling"
    else:
        return "a school" if len(schools) == 1 else "schools"

def _facilities_narrative(address, schools, amenities, stations, known_place=None):
    """Build a 'Facilities' paragraph from real amenities/schools/rail data —
    states only whether these facilities are present locally, not their names,
    and does not invent roads or distances to towns the tool has no data for.

    known_place: a real, geocoded settlement name (e.g. from the conservation
    area lookup) to use instead of guessing from the address string — the
    last comma-separated part of a multi-part address is often the county
    (e.g. "Rutland"), not the actual village."""
    if known_place:
        town = known_place
    else:
        town = address.split(",")[-1].strip() if "," in address else address

    categories = []
    if amenities.get("pubs"):
        categories.append("public houses")
    if amenities.get("shops"):
        categories.append("local shops")
    if amenities.get("doctors"):
        categories.append("a doctors' surgery")
    if schools:
        categories.append(_school_phrase(schools))

    if categories:
        para = (
            f"The property is located within or close to {town}, which provides "
            f"{_join_list(categories)} (source: OpenStreetMap — please verify current "
            f"provision)."
        )
    else:
        para = (
            f"No significant local amenities were identified within the immediate vicinity "
            f"of {town} via OpenStreetMap data — please verify on-site and add relevant "
            f"detail here."
        )

    # Trunk road isn't in any of the tool's data sources — always left as a
    # placeholder. Station name is filled in when known, otherwise also
    # left as a placeholder rather than dropping the sentence.
    station_name = stations[0][0] if stations else "[INSERT LOCATION]"
    para += (
        f" There are good transport links further afield via the railway station in "
        f"{station_name} and the nearby [INSERT ROAD] trunk road."
    )

    return para

def generate_draft_paragraph(address, postcode):
    """
    DRAFT text for the 'Facilities' and 'Local environment' sections of a
    RICS survey report, built from the same live data sources as the PDF
    report. This is a starting point for the surveyor to review and edit
    before it goes into the report — it does not cover on-site observations
    (grounds, parking, noise, etc.), only matters that can be checked from
    live desktop data (nearby amenities/schools/rail, radon, conservation
    area, listed building status).
    """
    address = _title_address(address)
    postcode = postcode.strip().upper()
    house_number = address.strip().split()[0].rstrip(",")

    pc_data = fetch_postcode(postcode)
    lat, lng = pc_data["lat"], pc_data["lng"]
    if lat is None:
        return f"Could not resolve postcode {postcode} — draft paragraph not generated."

    radon_desc, _ = fetch_radon_status(lat, lng)
    conservation_area = fetch_conservation_area(lat, lng)
    listed_building = fetch_listed_building(lat, lng, house_number)
    schools, amenities = fetch_schools_and_amenities(lat, lng)
    stations = fetch_rail_stations(lat, lng)

    ca_name = _clean_conservation_name(conservation_area) if conservation_area else None
    facilities_para = _facilities_narrative(address, schools, amenities, stations, known_place=ca_name)

    lines = []

    lines.append(_radon_narrative(radon_desc))

    if conservation_area:
        lines.append(
            f"The property is situated within the {ca_name} Conservation Area. "
            "You should take further advice from your legal advisers with regard to living in "
            "a Conservation Area, as this will restrict how the external appearance of the "
            "property can be altered and, in certain instances, internal aspects too — this "
            "may include replacement windows and doors, guttering, etc., and alterations may "
            "need specific planning permission."
        )
    else:
        lines.append(
            "The property does not appear to be situated within a designated conservation "
            "area (based on Planning Data / Historic England records — please verify)."
        )

    if listed_building:
        grade = listed_building.get("grade", "")
        ref = listed_building.get("ref", "")
        lines.append(
            f"The property appears to be a Grade {grade} listed building "
            f"(Historic England list entry {ref}) — see H1. Listed building consent will be "
            "required from the Local Planning Authority for most alterations."
        )
    else:
        lines.append(
            "No listed building record was identified at this address (based on Historic "
            "England's National Heritage List — please verify)."
        )

    lines.append(
        "Other than the matters noted above, I am not aware at present of any further "
        "planning or environmental factors (based on desktop research) that may adversely "
        "affect the property. However, your legal advisers should undertake the usual "
        "searches and enquiries."
    )

    header = (
        f"DRAFT — Facilities & Local environment paragraphs for {address}, {postcode}\n"
        f"(review and edit before inserting into the report — does not cover on-site "
        f"observations such as grounds, parking, or noise)\n"
        + "-" * 78 + "\n\n"
    )
    return (
        header
        + "FACILITIES\n\n" + facilities_para
        + "\n\nLOCAL ENVIRONMENT\n\n" + "\n\n".join(lines)
    )

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
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

def _overpass(query, timeout=30):
    """POST an Overpass query, trying mirrors until one succeeds."""
    for url in OVERPASS_MIRRORS:
        try:
            r = requests.post(url, data={"data": query},
                              headers=OVERPASS_HEADERS, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                return r.json()
        except Exception:
            continue
    return {"elements": []}

def fetch_rail_stations(lat, lng):
    """Returns up to 4 nearest stations with distances."""
    try:
        query = f"[out:json][timeout:30];node[railway=station](around:20000,{lat},{lng});out;"
        data = _overpass(query)
        stations = []
        for el in data.get("elements", []):
            name = el.get("tags", {}).get("name", "")
            if not name:
                continue
            dist = haversine_miles(lat, lng, el["lat"], el["lon"])
            stations.append((name, dist))
        stations.sort(key=lambda x: x[1])
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

SHOP_TYPES = {"supermarket", "convenience", "general", "department_store", "mall"}

def fetch_schools_and_amenities(lat, lng):
    """Single Overpass query for schools, shops, GPs, and pubs — avoids rate-limit issues."""
    query = (
        f"[out:json][timeout:30];"
        f"("
        f"node[amenity=school](around:3000,{lat},{lng});"
        f"way[amenity=school](around:3000,{lat},{lng});"
        f"node[shop](around:3000,{lat},{lng});"
        f"way[shop](around:3000,{lat},{lng});"
        f"node[amenity=doctors](around:6000,{lat},{lng});"
        f"way[amenity=doctors](around:6000,{lat},{lng});"
        f"node[amenity=pub](around:2000,{lat},{lng});"
        f"way[amenity=pub](around:2000,{lat},{lng});"
        f");out tags center;"
    )
    schools = []
    amenities = {"shops": [], "doctors": [], "pubs": []}
    try:
        data = _overpass(query)
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
        pass
    schools.sort(key=lambda x: x["dist"] or 99)
    for key in amenities:
        amenities[key].sort(key=lambda x: x["dist"] or 99)
    return schools[:6], amenities

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
    house_number = address.strip().split()[0].rstrip(",")

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
    epc               = fetch_epc(postcode, house_number)
    sales, rm_url     = fetch_sales_history(postcode, house_number)
    stations          = fetch_rail_stations(lat, lng)
    soil_data, soil_url = fetch_soil(lat, lng)
    schools, amenities = fetch_schools_and_amenities(lat, lng)

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
                link(sch["url"], sch["name"][:32]),
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
        amenity_rows.append((shop["name"][:32], d, "SHOP", MID_BLUE))
    for gp in amenities["doctors"][:2]:
        d = f"{gp['dist']:.1f} mi" if gp.get("dist") is not None else ""
        amenity_rows.append((gp["name"][:32], d, "GP", GREEN))
    for pub in amenities["pubs"][:2]:
        d = f"{pub['dist']:.1f} mi" if pub.get("dist") is not None else ""
        amenity_rows.append((pub["name"][:32], d, "PUB", AMBER))
    if amenity_rows:
        amenities_block.append(_rows_table(amenity_rows, (50*mm, 15*mm, 12*mm)))
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
        rail_rows = [(name, f"{dist:.1f} miles", "NEAREST" if i==0 else "", TEAL if i==0 else None)
                     for i, (name, dist) in enumerate(stations)]
        rail_rows.append(("National Rail", link("www.nationalrail.co.uk"), "", None))
        rail_block.append(_rows_table(rail_rows, (35*mm, 43*mm, 18*mm)))
    else:
        rail_block.append(Paragraph("Station data unavailable — check nationalrail.co.uk", NOTE))
    rail_block.append(Paragraph("Straight-line distances. Driving distances will be greater.", NOTE))

    soil_block = [
        Paragraph("🌱  Soil Type  (Cranfield / LandIS Soilscapes)", SECH),
        HRFlowable(width="100%", thickness=1.2, color=MID_BLUE, spaceAfter=2),
    ]
    if soil_data:
        soil_rows = [
            ("Classification", soil_data.get("name", "N/A"),                      f"TYPE {soil_data.get('ssid','')}", BROWN),
            ("Texture",        soil_data.get("texture",  "N/A"),                   "", None),
            ("Drainage",       soil_data.get("drainage", "N/A"),                   "", None),
            ("Fertility",      soil_data.get("fertility","N/A"),                   "", None),
            ("Land Cover",     soil_data.get("landcover","N/A"),                   "", None),
            ("Soilscapes Map", link("https://www.landis.org.uk/soilscapes/", "LandIS Soilscapes viewer"), "", None),
        ]
        soil_block.append(_rows_table(soil_rows, (28*mm, 44*mm, 14*mm)))
    else:
        soil_block.append(Paragraph(f'Check: {link("https://www.landis.org.uk/soilscapes/", "LandIS Soilscapes viewer")}', NOTE))
    soil_block.append(Paragraph("Regional soilscape classification — not a site-specific survey.", NOTE))

    two_col3 = Table([[rail_block, Spacer(4*mm,1), soil_block]], colWidths=[96*mm, 4*mm, 80*mm])
    two_col3.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(two_col3)
    story.append(Spacer(1, 4*mm))

    # Disclaimer
    story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#CCCCCC"), spaceAfter=2))
    story.append(Paragraph(
        "<b>Disclaimer:</b> This report is indicative only and generated from publicly available data sources "
        "(Environment Agency, UKHSA, Cranfield University, Ofsted, National Rail, Rightmove / HM Land Registry, "
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
