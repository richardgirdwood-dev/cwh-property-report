"""
Survey Proofreading — RICS survey-report checks
CWH Surveyors LLP

Reads a RICS Home Survey (Level 2/3) PDF report and flags common
drafting mistakes: invalid condition ratings, leftover template
placeholder text, standard sections that appear to be missing, and
spelling/grammar issues.

Personal data and addresses are redacted from the extracted text
before any check runs — including before anything is sent to the
external spelling/grammar service — so the surveyor's client details
never leave the machine. This redaction is heuristic (regex-based,
not true NER) and covers the common cases in a survey report, but
should not be relied on as a legal-grade anonymisation guarantee.

These are heuristic checks on extracted text, not a formal RICS
compliance review — always read the flagged passages in context.
"""

import re
import time

import pdfplumber
import requests

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

# Surveying/property jargon that a general-purpose spellchecker doesn't know
# and would otherwise flag as misspelled on every report.
SURVEY_TERM_WHITELIST = {
    "purlin", "purlins", "fascia", "fascias", "soffit", "soffits",
    "spalling", "efflorescence", "dpc", "dpm", "epc", "eicr", "rics",
    "cwh", "valley", "valleys", "hip", "hips", "verge", "verges",
    "flashing", "flashings", "render", "rendering", "joist", "joists",
    "truss", "trusses", "lintel", "lintels", "upvc", "ewi", "wc",
    "woodworm", "subsidence", "heave", "downpipe", "downpipes",
    "ridge", "ridges", "parapet", "parapets", "gable", "gables",
    "mullion", "mullions", "transom", "transoms", "cavity", "cavities",
    "asbestos", "reinstatement", "conveyancing", "leasehold",
    "freehold", "hardstanding", "outbuilding", "outbuildings",
}

# ── Anonymisation ────────────────────────────────────────────────────────
# Tokens use guillemets so they can never collide with the placeholder
# patterns above (which match [...], <<...>>, {{...}}).
REDACTED_ADDRESS = "‹ADDRESS›"
REDACTED_POSTCODE = "‹POSTCODE›"
REDACTED_NAME = "‹NAME›"
REDACTED_EMAIL = "‹EMAIL›"
REDACTED_PHONE = "‹PHONE›"
REDACTION_TOKENS = {REDACTED_ADDRESS, REDACTED_POSTCODE, REDACTED_NAME, REDACTED_EMAIL, REDACTED_PHONE}

POSTCODE_RE = re.compile(r'\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b', re.IGNORECASE)

_STREET_SUFFIXES = (
    r'Road|Street|Avenue|Lane|Close|Drive|Way|Court|Place|Gardens|Grove|'
    r'Crescent|Terrace|Hill|Park|Row|Square|Mews|Walk|Rise|Green|Chase|'
    r'Meadow|Meadows|View|End|Common|Fields'
)
STREET_ADDRESS_RE = re.compile(
    r'\b\d{1,4}[A-Za-z]?\s+(?:[A-Z][a-zA-Z\'\-]*\s+){1,4}(?:' + _STREET_SUFFIXES + r')\b'
)

LABELLED_ADDRESS_RE = re.compile(
    r'(?P<label>\b(?:property|address|subject property|site address)\s*[:\-]\s*)'
    r'(?P<value>[^\n]{5,120}?)(?=[\.\n]|$)',
    re.IGNORECASE,
)

LABELLED_NAME_RE = re.compile(
    r'(?P<label>\b(?:surveyor|client|prepared by|inspected by|vendor|purchaser|'
    r'owner|occupier|solicitor|buyer|seller|instructed by)\s*[:\-]\s*)'
    r'(?P<value>(?:[A-Z]\.[ \t]*){0,2}[A-Za-z][A-Za-z\'\-]*(?:[ \t]+[A-Za-z][A-Za-z\'\-]*){0,3})',
    re.IGNORECASE,
)

HONORIFIC_NAME_RE = re.compile(
    r'\b(?:Mr|Mrs|Ms|Miss|Mx|Dr)\.?\s+[A-Z][a-zA-Z\'\-]+(?:\s+[A-Z][a-zA-Z\'\-]+){0,2}\b'
)

EMAIL_RE = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')

PHONE_RE = re.compile(r'(?:\+44\s?\d{2,4}|\(?0\d{2,4}\)?)[\s-]?\d{3,4}[\s-]?\d{3,4}\b')


def anonymise_text(text):
    """Redacts addresses, postcodes, names, emails and phone numbers from a page of text."""
    text = LABELLED_ADDRESS_RE.sub(lambda m: m.group('label') + REDACTED_ADDRESS, text)
    text = STREET_ADDRESS_RE.sub(REDACTED_ADDRESS, text)
    text = POSTCODE_RE.sub(REDACTED_POSTCODE, text)
    text = LABELLED_NAME_RE.sub(lambda m: m.group('label') + REDACTED_NAME, text)
    text = HONORIFIC_NAME_RE.sub(REDACTED_NAME, text)
    text = EMAIL_RE.sub(REDACTED_EMAIL, text)
    text = PHONE_RE.sub(REDACTED_PHONE, text)
    return text


def anonymise_pages(pages):
    return [anonymise_text(p) for p in pages]


LANGUAGETOOL_ENDPOINT = "https://api.languagetool.org/v2/check"
LANGUAGETOOL_LANG = "en-GB"
LANGUAGETOOL_ISSUE_LABELS = {
    "misspelling": "Spelling",
    "grammar": "Grammar",
    "typographical": "Punctuation",
}
MAX_PAGES_CHECKED = 40


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
    """
    Returns a list of page texts (str, may be empty string for unreadable
    pages), with personal data and addresses already redacted — every
    downstream check, and anything sent to the external spelling/grammar
    service, only ever sees the anonymised text.
    """
    pages = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return anonymise_pages(pages)


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


def check_missing_sections(pages):
    full_text_lower = "\n".join(pages).lower()
    missing = []
    for display_name, keywords in STANDARD_SECTIONS:
        if not any(kw in full_text_lower for kw in keywords):
            missing.append(display_name)
    return missing


def _languagetool_check(text):
    resp = requests.post(
        LANGUAGETOOL_ENDPOINT,
        data={"text": text, "language": LANGUAGETOOL_LANG},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("matches", [])


def check_spelling_grammar(pages, progress_cb=None):
    """
    Runs each page's text through the LanguageTool public API and returns
    (issues, service_unavailable). Requires internet access; on failure,
    returns whatever was found so far plus a flag so the caller can warn
    the user rather than silently showing zero issues.
    """
    issues = []
    service_unavailable = False
    checkable_pages = [
        (page_no, text) for page_no, text in enumerate(pages, start=1)
        if text.strip()
    ][:MAX_PAGES_CHECKED]

    for i, (page_no, text) in enumerate(checkable_pages):
        if progress_cb:
            progress_cb(i + 1, len(checkable_pages))
        try:
            matches = _languagetool_check(text)
        except Exception:
            service_unavailable = True
            break

        for match in matches:
            issue_type = (match.get("rule") or {}).get("issueType", "")
            label = LANGUAGETOOL_ISSUE_LABELS.get(issue_type)
            if not label:
                continue

            context = match.get("context") or {}
            ctx_text = context.get("text", "")
            ctx_offset = context.get("offset", 0)
            ctx_length = context.get("length", 0)
            flagged = ctx_text[ctx_offset:ctx_offset + ctx_length].strip()

            if flagged.strip(".,;:'\"").lower() in SURVEY_TERM_WHITELIST:
                continue
            if any(tok in flagged or tok in ctx_text for tok in REDACTION_TOKENS):
                continue  # our own redaction token, not a real spelling/grammar issue

            suggestions = [r["value"] for r in match.get("replacements", [])[:3]]
            message = match.get("message", "").strip()
            if suggestions:
                message += f" (suggestion: {', '.join(suggestions)})"

            issues.append({
                "page": page_no,
                "type": label,
                "message": message,
                "snippet": ctx_text.strip() or None,
            })

        if i < len(checkable_pages) - 1:
            time.sleep(3)  # public API rate limit: ~20 requests/minute

    return issues, service_unavailable


def proofread(pdf_bytes, check_spelling=True, progress_cb=None):
    """
    Runs all checks against an uploaded PDF (file-like object or bytes).
    Returns a dict with page count and grouped issue lists.
    """
    pages = extract_pages(pdf_bytes)
    non_empty_pages = sum(1 for p in pages if p.strip())

    spelling_grammar_issues = []
    spelling_grammar_unavailable = False
    if check_spelling and non_empty_pages:
        spelling_grammar_issues, spelling_grammar_unavailable = check_spelling_grammar(
            pages, progress_cb=progress_cb
        )

    result = {
        "page_count": len(pages),
        "text_pages": non_empty_pages,
        "condition_rating_issues": check_condition_ratings(pages),
        "placeholder_issues": check_placeholders(pages),
        "missing_sections": check_missing_sections(pages),
        "spelling_grammar_issues": spelling_grammar_issues,
        "spelling_grammar_unavailable": spelling_grammar_unavailable,
    }
    result["total_issues"] = (
        len(result["condition_rating_issues"])
        + len(result["placeholder_issues"])
        + len(result["missing_sections"])
        + len(result["spelling_grammar_issues"])
    )
    return result
