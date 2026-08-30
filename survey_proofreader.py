"""
Survey Proofreading — RICS survey-report consistency checks
CWH Surveyors LLP

Reads a RICS Home Survey (Level 2/3) PDF report and flags common
drafting mistakes: invalid/inconsistent condition ratings, leftover
template placeholder text, inconsistent address/date/surveyor details
(a strong sign the wrong template was reused), and standard sections
that appear to be missing.

These are heuristic checks on extracted text, not a formal RICS
compliance review — always read the flagged passages in context.
"""

import re

import pdfplumber

VALID_RATINGS = {"1", "2", "3", "NI"}

RATING_LINE_RE = re.compile(
    r'condition\s*rating[s]?\s*[:\-]\s*([A-Za-z0-9/\*\.\s]{1,10}?)(?=[\.\n,;]|$)',
    re.IGNORECASE,
)

PLACEHOLDER_PATTERNS = [
    (re.compile(r'\[[^\]\n]{1,80}\]'), "leftover placeholder in square brackets"),
    (re.compile(r'<<[^>\n]{1,80}>>'), "leftover template tag"),
    (re.compile(r'\{\{[^}\n]{1,80}\}\}'), "leftover merge-field tag"),
    (re.compile(r'\bTBC\b', re.IGNORECASE), "'TBC' left in report"),
    (re.compile(r'\bTBD\b', re.IGNORECASE), "'TBD' left in report"),
    (re.compile(r'\bxxxx+\b', re.IGNORECASE), "'xxxx' placeholder text"),
    (re.compile(r'lorem ipsum', re.IGNORECASE), "Lorem ipsum placeholder text"),
    (re.compile(r'\bINSERT\b\s*[A-Z ]{0,20}', re.IGNORECASE), "'insert...' drafting note"),
    (re.compile(r'\bPLACEHOLDER\b', re.IGNORECASE), "'placeholder' drafting note"),
]

POSTCODE_RE = re.compile(r'\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b')

DATE_LABEL_RE = re.compile(
    r'(date of inspection|inspection date)\s*[:\-]?\s*'
    r'(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}'
    r'|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
    re.IGNORECASE,
)

SURVEYOR_LABEL_RE = re.compile(
    r'(?:surveyor|inspected by|prepared by)[ \t]*[:\-][ \t]*'
    r'([A-Za-z][A-Za-z\'\-]*(?:[ \t]+[A-Za-z][A-Za-z\'\-]*){1,3})',
    re.IGNORECASE,
)

STANDARD_SECTIONS = [
    ("About the report", ["about the report", "about this report"]),
    ("General information", ["general information"]),
    ("Description of the property", ["description of the property", "description of property"]),
    ("Overall opinion of the property", ["overall opinion"]),
    ("Condition ratings summary", ["condition rating"]),
    ("Outside the property", ["outside the property"]),
    ("Inside the property", ["inside the property"]),
    ("Services", ["services"]),
    ("Issues for your legal advisers", ["legal adviser"]),
    ("Risks", ["risks"]),
    ("What to do now", ["what to do now"]),
]


def _normalise_rating(raw):
    val = raw.strip().rstrip(".").upper()
    val = re.sub(r'\s+', '', val)
    if val in ("N/I", "N.I", "NOTINSPECTED"):
        val = "NI"
    return val


def _snippet(text, start, end, radius=60):
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    s = text[a:b].replace("\n", " ").strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def extract_pages(pdf_bytes):
    """Returns a list of page texts (str, may be empty string for unreadable pages)."""
    pages = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def check_condition_ratings(pages):
    issues = []
    seen_bad = set()
    for page_no, text in enumerate(pages, start=1):
        for m in RATING_LINE_RE.finditer(text):
            raw = m.group(1)
            rating = _normalise_rating(raw)
            if rating in VALID_RATINGS or rating.rstrip("*") in VALID_RATINGS:
                continue
            key = (page_no, rating)
            if key in seen_bad or not rating:
                continue
            seen_bad.add(key)
            issues.append({
                "page": page_no,
                "message": f"Unexpected condition rating value '{raw.strip()}' — expected 1, 2, 3 or NI.",
                "snippet": _snippet(text, m.start(), m.end()),
            })
    return issues


def check_placeholders(pages):
    issues = []
    for page_no, text in enumerate(pages, start=1):
        for pattern, label in PLACEHOLDER_PATTERNS:
            for m in pattern.finditer(text):
                issues.append({
                    "page": page_no,
                    "message": f"{label}: \"{m.group(0).strip()}\"",
                    "snippet": _snippet(text, m.start(), m.end()),
                })
    return issues


def check_consistency(pages):
    issues = []
    full_text = "\n".join(pages)

    postcodes = sorted(set(POSTCODE_RE.findall(full_text.upper())))
    if len(postcodes) > 1:
        issues.append({
            "page": None,
            "message": (
                f"Multiple different postcodes found in the document ({', '.join(postcodes)}) "
                "— check for leftover text from a different property's report."
            ),
            "snippet": None,
        })

    dates = sorted(set(m.group(2).strip() for m in DATE_LABEL_RE.finditer(full_text)))
    if len(dates) > 1:
        issues.append({
            "page": None,
            "message": f"Inconsistent inspection date across the document: {', '.join(dates)}.",
            "snippet": None,
        })

    surveyors = sorted(set(m.group(1).strip() for m in SURVEYOR_LABEL_RE.finditer(full_text)))
    if len(surveyors) > 1:
        issues.append({
            "page": None,
            "message": f"More than one surveyor name found in the document: {', '.join(surveyors)}.",
            "snippet": None,
        })

    return issues


def check_missing_sections(pages):
    full_text_lower = "\n".join(pages).lower()
    missing = []
    for display_name, keywords in STANDARD_SECTIONS:
        if not any(kw in full_text_lower for kw in keywords):
            missing.append(display_name)
    return missing


def proofread(pdf_bytes):
    """
    Runs all checks against an uploaded PDF (file-like object or bytes).
    Returns a dict with page count and grouped issue lists.
    """
    pages = extract_pages(pdf_bytes)
    non_empty_pages = sum(1 for p in pages if p.strip())

    result = {
        "page_count": len(pages),
        "text_pages": non_empty_pages,
        "condition_rating_issues": check_condition_ratings(pages),
        "placeholder_issues": check_placeholders(pages),
        "consistency_issues": check_consistency(pages),
        "missing_sections": check_missing_sections(pages),
    }
    result["total_issues"] = (
        len(result["condition_rating_issues"])
        + len(result["placeholder_issues"])
        + len(result["consistency_issues"])
        + len(result["missing_sections"])
    )
    return result
