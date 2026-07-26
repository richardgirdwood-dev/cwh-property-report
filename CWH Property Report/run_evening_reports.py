"""
CWH Evening Report Runner
Reads tomorrow's Outlook appointments and generates a property report
for each one that has an address in the Location field.
Saves PDFs to the 'Property Information Report' folder on the Desktop.
"""

import os
import re
import sys
import datetime
import win32com.client

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = r"C:\Users\Richard Girdwood\Desktop\Property Information Report"
LOG_FILE    = os.path.join(SCRIPT_DIR, "evening_runner.log")

# ── Postcode regex ─────────────────────────────────────────────────────────────
POSTCODE_RE = re.compile(
    r'\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b', re.IGNORECASE
)

def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def parse_location(location):
    """Extract (address_without_postcode, postcode, house_number) from a location string."""
    location = location.strip()
    m = POSTCODE_RE.search(location)
    if not m:
        return None, None, None

    postcode = m.group(1).strip().upper()
    # Normalise postcode spacing
    postcode = re.sub(r'\s+', ' ', postcode)
    if len(postcode) > 4 and ' ' not in postcode:
        postcode = postcode[:-3] + ' ' + postcode[-3:]

    # Address is everything before the postcode
    address = location[:m.start()].rstrip(', ').strip()

    # House number: first token if it starts with digits
    house_number = ""
    first = address.split()[0] if address else ""
    if re.match(r'^\d+[A-Za-z]?$', first):
        house_number = first

    return address, postcode, house_number

def get_tomorrows_appointments():
    """Return list of (subject, location) for tomorrow's Outlook appointments."""
    outlook   = win32com.client.Dispatch("Outlook.Application")
    namespace = outlook.GetNamespace("MAPI")
    calendar  = namespace.GetDefaultFolder(9)   # 9 = olFolderCalendar
    items     = calendar.Items
    items.IncludeRecurrences = True
    items.Sort("[Start]")

    tomorrow       = datetime.date.today() + datetime.timedelta(days=1)
    start_filter   = tomorrow.strftime("%d/%m/%Y 00:00")
    end_filter     = tomorrow.strftime("%d/%m/%Y 23:59")
    restriction    = (
        f"[Start] >= '{start_filter}' AND [Start] <= '{end_filter}'"
    )
    filtered = items.Restrict(restriction)

    appointments = []
    for item in filtered:
        try:
            subject  = item.Subject  or ""
            location = item.Location or ""
            appointments.append((subject, location))
        except Exception:
            pass
    return appointments

def main():
    log("=" * 60)
    log("Evening report runner starting")
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    log(f"Looking for appointments on {tomorrow:%A %d %B %Y}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Import the report engine from the same folder
    sys.path.insert(0, SCRIPT_DIR)
    from property_report_engine import generate_report, generate_draft_paragraph

    appointments = get_tomorrows_appointments()
    log(f"Found {len(appointments)} appointment(s) tomorrow")

    generated = 0
    for subject, location in appointments:
        log(f"  Appointment: {subject!r}")
        log(f"  Location:    {location!r}")

        if not location.strip():
            log("  -> No location set, skipping")
            continue

        address, postcode, house_number = parse_location(location)
        if not postcode:
            log("  -> No postcode found in location, skipping")
            continue

        log(f"  -> Address: {address} | Postcode: {postcode} | House: {house_number}")

        # Build a safe filename from address + date
        safe_addr = re.sub(r'[\\/:*?"<>|]', '', address).strip().replace(' ', '_')
        safe_date = tomorrow.strftime("%Y-%m-%d")
        filename  = f"{safe_date}_{safe_addr}_Property_Report.pdf"
        out_path  = os.path.join(OUTPUT_DIR, filename)

        if os.path.exists(out_path):
            log(f"  -> Report already exists: {filename}, skipping")
            generated += 1
        else:
            log(f"  -> Generating report -> {filename}")
            try:
                result = generate_report(address, postcode, out_path)
                if result and os.path.exists(out_path):
                    log(f"  -> Saved: {filename}")
                    generated += 1
                else:
                    log(f"  -> Report generation returned no output")
            except Exception as e:
                log(f"  -> ERROR: {e}")

        # Draft "Local environment" paragraph for the surveyor to review/edit
        # (runs independently of the PDF check above, so it still gets
        # generated even if the PDF already existed from an earlier run)
        draft_filename = f"{safe_date}_{safe_addr}_Draft_Paragraphs.txt"
        draft_path = os.path.join(OUTPUT_DIR, draft_filename)
        if os.path.exists(draft_path):
            log(f"  -> Draft paragraph already exists: {draft_filename}, skipping")
        else:
            try:
                draft_text = generate_draft_paragraph(address, postcode)
                with open(draft_path, "w", encoding="utf-8") as f:
                    f.write(draft_text)
                log(f"  -> Draft paragraph saved: {draft_filename}")
            except Exception as e:
                log(f"  -> ERROR generating draft paragraph: {e}")

    log(f"Done. {generated} report(s) saved to {OUTPUT_DIR}")
    log("=" * 60)

if __name__ == "__main__":
    main()
