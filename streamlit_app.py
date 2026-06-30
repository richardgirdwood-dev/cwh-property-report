"""
CWH Surveyors LLP — Internal Tools
  Tab 1: Property Environmental Report
  Tab 2: Survey Quote Generator
"""

import os
import re
import json
import base64
import tempfile
import datetime
import streamlit as st

SIG_IMG_PATH = (
    r"C:\Users\Richard Girdwood\AppData\Roaming\Microsoft\Signatures"
    r"\Office (richard.girdwood@cwhsurveyors.co.uk)_files\image001.png"
)

def _sig_img_tag():
    """Return an <img> tag with the signature logo embedded as base64."""
    try:
        with open(SIG_IMG_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f'<img src="data:image/png;base64,{b64}" width="260" height="47" alt="CWH Surveyors" style="display:block;border:0;">'
    except Exception:
        return ""

HTML_SIGNATURE = """\
<p style="margin:0;font-family:Aptos,Calibri,Arial,sans-serif;font-size:11pt;">Kind regards,</p>
<br>
<p style="margin:0;font-family:Aptos,Calibri,Arial,sans-serif;font-size:11pt;">Richard</p>
<br>
<p style="margin:0;font-family:Aptos,Calibri,Arial,sans-serif;font-size:10pt;color:#1F497D;">
  <b>Richard Girdwood MRICS</b><br>
  <b>Partner &amp; Chartered Surveyor</b><br>
  <b>Cambridgeshire, Rutland, South Lincs and Northamptonshire office</b>
</p>
<br>
{logo}
<br>
<p style="margin:0;font-family:Aptos,Calibri,Arial,sans-serif;font-size:10pt;color:#1F497D;">
  Unit One, Hill Court<br>
  Turnpike Close<br>
  Grantham<br>
  Lincolnshire<br>
  NG31 7XY
</p>
<br>
<p style="margin:0;font-family:Aptos,Calibri,Arial,sans-serif;font-size:10pt;color:#1F497D;">
  Tel: 01476 584190<br>
  Mob: 07766 140112<br>
  Fax: 01476 584191
</p>
<br>
<p style="margin:0;font-family:Aptos,Calibri,Arial,sans-serif;font-size:10pt;">
  <a href="http://www.cwhsurveyors.co.uk" style="color:#C00000;font-size:16pt;font-weight:bold;text-decoration:none;">www.cwhsurveyors.co.uk</a>
</p>
"""

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CWH Surveyors Tools",
    page_icon="🏠",
    layout="centered",
)

LOGO_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cwh_logo.png")
PROCESSED_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed_enquiries.json")
POSTCODE_RE   = re.compile(r'^[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}$', re.IGNORECASE)
PC_FIND_RE    = re.compile(r'\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b', re.IGNORECASE)
NAME_RE       = re.compile(r'Dear\s+([A-Za-z\-]+)', re.IGNORECASE)

def _extract_from_email(subject, body, sender_name, sender_email):
    """Best-effort extraction of client name, property address, and email."""
    # Client name: "Dear X" in body, else first word of sender display name
    name_match  = NAME_RE.search(body or "")
    client_name = name_match.group(1).strip() if name_match else (sender_name or "").split()[0]

    # Property address: postcode found in subject first, then body
    for text in (subject, body or ""):
        pc_match = PC_FIND_RE.search(text)
        if pc_match:
            pc  = pc_match.group(1).strip().upper()
            pc  = re.sub(r'\s+', ' ', pc)
            if len(pc) > 4 and ' ' not in pc:
                pc = pc[:-3] + ' ' + pc[-3:]
            # Grab the text before the postcode on the same line as the address
            line = text[:pc_match.start()].rstrip(', \n\r')
            last_line = line.split('\n')[-1].split('\r')[-1].strip().rstrip(',')
            property_addr = f"{last_line}, {pc}".strip(', ') if last_line else pc
            break
    else:
        # No postcode found — use subject as fallback
        property_addr = re.sub(r'^(RE:|FW:|new submission from popup[:\s]*)', '', subject,
                                flags=re.IGNORECASE).strip()

    # Client email: sender's SMTP address (empty for internal senders)
    email = sender_email if sender_email and '@' in sender_email else ""

    return client_name, property_addr, email

def _p(text, last=False):
    """Wrap text in a simple HTML paragraph."""
    margin = "0" if last else "0 0 12px 0"
    return f'<p style="margin:{margin};font-family:Aptos,Calibri,Arial,sans-serif;font-size:11pt;">{text}</p>'

def _ol(*items):
    """Ordered list of items."""
    lis = "".join(f"<li>{i}</li>" for i in items)
    return f'<ol style="margin:0 0 12px 0;font-family:Aptos,Calibri,Arial,sans-serif;font-size:11pt;">{lis}</ol>'

L2_HTML = """\
{note_para}
{p_recommend}
{p_l2_desc}
{p_gardens}
{p_ratings}
{p_legal}
{p_aim}
{p_followup}
{p_fee}
{p_avail_l2}
{p_proceed}
{ol_confirm}
{p_terms_l2}
{p_website}
{p_reviews}
{p_close}
"""

L3_HTML = """\
{note_para}
{p_recommend_l3}
{p_l3_desc}
{p_gardens}
{p_ratings}
{p_drone}
{p_followup}
{p_avail_l3}
{p_fee}
{p_proceed}
{ol_confirm}
{p_terms_l3}
{p_reviews}
{p_close}
"""

def build_html_body(name, address, fee, availability, survey_type, notes):
    p = _p  # shorthand
    ol = _ol

    note_para = p(notes) if notes.strip() else ""
    p_gardens   = p("Gardens and grounds are included. A visual inspection of the services is conducted; however, we always recommend that up-to-date test certificates be provided for these. We would also lift any accessible drain covers on site to assess the condition.")
    p_ratings   = p("Each section is assigned a condition rating (Green, Amber, or Red). Red sections indicate those that require further investigation or pose health and safety concerns. Amber indicates work that is required but not urgent, and Green indicates areas in satisfactory condition with regular maintenance required.")
    p_followup  = p("Upon completion of the report, we would be happy to answer any further questions or discuss any particular concerns to provide more information if necessary.")
    p_proceed   = p("If you are happy to proceed on this basis, could you please confirm the following:")
    ol_confirm  = ol(
        "Who the report should be addressed to (jointly with a partner or spouse if applicable)",
        "Your current home address",
    )
    p_close     = p("Feel free to contact me at any time via phone or email if there is anything you would like to discuss further.", last=True)
    p_reviews   = p('Our Google reviews: <a href="https://tinyurl.com/CWH-Surveyors">https://tinyurl.com/CWH-Surveyors</a>')
    p_fee_bold  = p(f"The fee for the {'Level 2 Home Survey' if survey_type == 'l2' else 'Level 3 Building Survey'}, excluding valuation, will be <b>&pound;{fee} + VAT</b>.")

    if survey_type == "l2":
        greeting    = p(f"Hi {name},")
        p_intro     = p(f"Thank you for your enquiry and the opportunity to quote for {address}.")
        p_recommend = p("For a property of this age and type, I recommend the Level 2 Home Survey.")
        p_l2_desc   = p("In summary, the Level 2 Home Survey covers all key elements of the property, including roof coverings, windows, doors, walls, and the damp proof course. Internally, an inspection of the roof space, where accessible, is conducted, including an assessment of the condition of the timbers and insulation levels. The report then covers all remaining internal elements of the property.")
        p_legal     = p("There is also an overview of legal items which your advisors should check as part of the purchase.")
        p_aim       = p("The report aims to enable you to make a fully informed decision about your purchase and to conduct any necessary investigations before exchanging contracts.")
        p_avail     = p(f"We have availability from {availability}. Following the inspection, we will issue the completed report within 3-5 working days. If there are any cancellations, we would consider bringing this date forward.")
        p_terms     = p("We will then issue the Terms and Conditions and an invoice via email. Payment can be made via bank transfer, and our bank details will be provided on the invoice.")
        p_website   = p('Further information and a sample report can be found on our website: <a href="https://www.cwhsurveyors.co.uk/services/home-survey-reports/">https://www.cwhsurveyors.co.uk/services/home-survey-reports/</a>')
        body_parts  = [greeting, p_intro, note_para, p_recommend, p_l2_desc, p_gardens, p_ratings, p_legal, p_aim, p_followup, p_fee_bold, p_avail, p_proceed, ol_confirm, p_terms, p_website, p_reviews, p_close]
    else:
        greeting      = p(f"Hi {name},")
        p_intro       = p(f"Thank you for your enquiry and the opportunity to quote for {address}.")
        p_recommend   = p("The Level 3 Building Survey will be the best option for a property of this age and type.")
        p_l3_desc     = p("In summary, the Level 3 Survey encompasses all key elements of the property, including the roof covering, windows, doors, walls, and the damp proof course. Internally, an inspection of the roof space, where accessible, is conducted, including an assessment of the condition of the timbers and insulation levels. The report then covers all remaining internal elements of the property.")
        p_drone       = p("Where necessary, we can also use a drone to inspect less accessible parts of the property.")
        p_avail       = p(f"We are currently booking for {availability}. The completed report will be issued within 5-7 working days after the inspection. If there are any cancellations we will look to bring this forward for you.")
        p_terms       = p("We will then email the Terms and Conditions for signature. Payment can be made via bank transfer; our bank details will be provided on the invoice.")
        body_parts    = [greeting, p_intro, note_para, p_recommend, p_l3_desc, p_gardens, p_ratings, p_drone, p_followup, p_avail, p_fee_bold, p_proceed, ol_confirm, p_terms, p_reviews, p_close]

    sig = HTML_SIGNATURE.format(logo=_sig_img_tag())
    html = (
        '<html><head><meta charset="utf-8"></head><body>'
        + "".join(body_parts)
        + sig
        + "</body></html>"
    )
    return html

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_processed():
    if os.path.exists(PROCESSED_LOG):
        with open(PROCESSED_LOG) as f:
            return set(json.load(f))
    return set()

def save_processed(entry_ids: set):
    with open(PROCESSED_LOG, "w") as f:
        json.dump(list(entry_ids), f)

def search_inbox(keyword="", days=60, limit=50):
    """Return recent inbox emails, optionally filtered by keyword in subject or body."""
    try:
        import pythoncom, win32com.client
        pythoncom.CoInitialize()
        processed = load_processed()
        outlook   = win32com.client.Dispatch("Outlook.Application")
        ns        = outlook.GetNamespace("MAPI")
        inbox     = ns.GetDefaultFolder(6)
        items     = inbox.Items
        items.Sort("[ReceivedTime]", True)

        cutoff   = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%d/%m/%Y %H:%M")
        filtered = items.Restrict(f"[ReceivedTime] >= '{cutoff}'")

        kw = keyword.strip().lower()
        results = []
        for item in filtered:
            try:
                subj = item.Subject or ""
                body = item.Body   or ""
                if kw and kw not in subj.lower() and kw not in body.lower():
                    continue
                eid          = item.EntryID
                sender_name  = item.SenderName         or ""
                sender_email = item.SenderEmailAddress or ""
                # Resolve Exchange DN to SMTP where possible
                if sender_email.startswith("/O=") or sender_email.startswith("/o="):
                    try:
                        sender_email = item.Sender.GetExchangeUser().PrimarySmtpAddress
                    except Exception:
                        sender_email = ""
                client_name, property_addr, client_email = _extract_from_email(
                    subj, body, sender_name, sender_email)
                results.append({
                    "entry_id":     eid,
                    "subject":      subj,
                    "received":     str(item.ReceivedTime)[:10],
                    "sender":       sender_name,
                    "client_name":  client_name,
                    "client_email": client_email,
                    "property":     property_addr,
                    "used":         eid in processed,
                })
                if len(results) >= limit:
                    break
            except Exception:
                continue
        return results
    except Exception:
        return []

def create_outlook_draft(to_email, subject, html_body):
    """Create an HTML draft in Outlook Drafts folder."""
    import pythoncom, win32com.client
    pythoncom.CoInitialize()
    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)   # 0 = olMailItem
    mail.To          = to_email
    mail.Subject     = subject
    mail.BodyFormat  = 2           # 2 = HTML
    mail.HTMLBody    = html_body
    mail.Save()                    # saves to Drafts, does NOT send
    return True

def normalise_postcode(raw):
    pc = raw.strip().upper()
    pc = re.sub(r'\s+', ' ', pc)
    if len(pc) > 4 and ' ' not in pc:
        pc = pc[:-3] + ' ' + pc[-3:]
    return pc

# ── Header ────────────────────────────────────────────────────────────────────
col_logo, col_title = st.columns([1, 2])
with col_logo:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=200)
with col_title:
    st.markdown(
        """
        <div style="padding:10px 0 0 10px">
            <h2 style="color:#1B3A6B;margin:0;font-family:sans-serif">CWH Surveyors Tools</h2>
            <p style="color:#555;margin:4px 0 0;font-size:14px">
                Property reports &amp; survey quote generator.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")

tab1, tab2 = st.tabs(["📄 Property Report", "✉️ Quote Generator"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Property Environmental Report
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    with st.form("report_form"):
        address  = st.text_input("Property address",
                                 placeholder="e.g. 197 High Street, Cottenham, Cambridge",
                                 help="Full street address without the postcode.")
        postcode = st.text_input("Postcode", placeholder="e.g. CB24 8RX", max_chars=8)
        submitted = st.form_submit_button("Generate Report", type="primary",
                                          use_container_width=True)

    if submitted:
        address  = address.strip()
        postcode = normalise_postcode(postcode)
        errors   = []
        if not address:
            errors.append("Please enter a property address.")
        if not postcode:
            errors.append("Please enter a postcode.")
        elif not POSTCODE_RE.match(postcode):
            errors.append(f"'{postcode}' doesn't look like a valid UK postcode.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            with st.spinner("Checking postcode…"):
                try:
                    import requests as _req
                    pc_check  = _req.get(
                        f"https://api.postcodes.io/postcodes/{postcode.replace(' ','')}",
                        timeout=8).json()
                    pc_result = pc_check.get("result")
                except Exception:
                    pc_result = None

            if not pc_result:
                st.error(f"Could not resolve postcode **{postcode}** — please check it.")
            else:
                la     = pc_result.get("admin_district", "")
                region = pc_result.get("region", "")
                st.info(f"**{address}, {postcode}**  \nLocal Authority: **{la}** — Region: **{region}**")

                with st.spinner("Fetching data and building report — this takes about 30 seconds…"):
                    try:
                        from property_report_engine import generate_report
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                            tmp_path = tmp.name
                        result = generate_report(address, postcode, tmp_path)
                        if result and os.path.exists(tmp_path):
                            with open(tmp_path, "rb") as f:
                                pdf_bytes = f.read()
                            os.unlink(tmp_path)
                            safe_pc  = postcode.replace(" ", "_")
                            filename = f"{safe_pc}_Property_Report.pdf"
                            st.success(f"Report ready for **{address}, {postcode}**")
                            st.download_button(
                                label="Download PDF Report",
                                data=pdf_bytes,
                                file_name=filename,
                                mime="application/pdf",
                                use_container_width=True,
                                type="primary",
                            )
                        else:
                            st.error("Report generation failed — check the address and try again.")
                    except Exception as ex:
                        st.error(f"An error occurred: {ex}")
                        raise

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Quote Generator
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### Survey Quote Generator")
    st.markdown(
        "Scan your inbox for new enquiries, or enter details manually. "
        "Creates a plain-text draft in Outlook Drafts — review and send from there."
    )

    # ── Inbox search ──────────────────────────────────────────────────────────
    with st.expander("📥 Import details from an email", expanded=True):
        col_kw, col_btn = st.columns([3, 1])
        with col_kw:
            keyword = st.text_input("Search inbox (leave blank to show all recent)",
                                    placeholder="e.g. survey, postcode, client name…",
                                    label_visibility="collapsed")
        with col_btn:
            do_search = st.button("Search", use_container_width=True)

        if do_search:
            with st.spinner("Searching inbox…"):
                results = search_inbox(keyword=keyword)
            st.session_state["inbox_results"] = results

        results = st.session_state.get("inbox_results", [])
        if results:
            options = {}
            for e in results:
                used_tag = " ✓" if e["used"] else ""
                label = (f"{e['received']}  —  {e['subject'][:60]}  "
                         f"[{e['sender']}]{used_tag}")
                options[label] = e

            chosen_label = st.selectbox("Select email", list(options.keys()),
                                        label_visibility="collapsed")
            chosen = options[chosen_label]

            # Show what was extracted
            col_a, col_b, col_c = st.columns(3)
            col_a.caption(f"Name detected: **{chosen['client_name'] or '—'}**")
            col_b.caption(f"Email detected: **{chosen['client_email'] or '—'}**")
            col_c.caption(f"Address detected: **{chosen['property'] or '—'}**")

            if st.button("Use this email →", use_container_width=True):
                st.session_state["prefill"] = {
                    "client_name":  chosen["client_name"],
                    "client_email": chosen["client_email"],
                    "property":     chosen["property"],
                    "entry_id":     chosen["entry_id"],
                }
                st.success("Details imported — fill in the form below.")
        elif "inbox_results" in st.session_state:
            st.info("No emails found matching that search.")

    st.markdown("---")

    # ── Quote form ────────────────────────────────────────────────────────────
    prefill = st.session_state.get("prefill", {})

    st.markdown("#### Quote details")
    col1, col2 = st.columns(2)
    with col1:
        client_name = st.text_input("Client first name",
                                    value=prefill.get("client_name", ""))
        client_email = st.text_input("Client email address",
                                     value=prefill.get("client_email", ""),
                                     placeholder="e.g. john.smith@gmail.com")
    with col2:
        property_addr = st.text_input("Property address",
                                      value=prefill.get("property", ""),
                                      placeholder="e.g. 26 Holmes Avenue, Raunds NN9 6SZ")
        survey_type = st.selectbox("Survey type", ["Level 2 Home Survey", "Level 3 Building Survey"])

    col3, col4 = st.columns(2)
    with col3:
        fee = st.text_input("Fee (£, excluding VAT)", placeholder="e.g. 550")
    with col4:
        avail_date = st.text_input("Availability date",
                                   placeholder="e.g. 20th July",
                                   help="'Week commencing' is added automatically.")

    notes = st.text_area("Personal note (optional — inserted after greeting)",
                          placeholder="e.g. It looks like a lovely property and we would be delighted to assist.",
                          height=80)

    # ── Preview ───────────────────────────────────────────────────────────────
    if st.button("Preview quote", use_container_width=False):
        if not client_name or not fee or not avail_date:
            st.warning("Please fill in client name, fee, and availability date before previewing.")
        else:
            availability = f"week commencing {avail_date.strip()}"
            html_body = build_html_body(
                name        = client_name,
                address     = property_addr or "the property",
                fee         = fee,
                availability= availability,
                survey_type = "l2" if "Level 2" in survey_type else "l3",
                notes       = notes,
            )
            st.session_state["preview_html"]    = html_body
            st.session_state["preview_subject"] = property_addr or "Survey quotation"

    if "preview_html" in st.session_state:
        st.markdown("**Preview:**")
        st.components.v1.html(st.session_state["preview_html"], height=600, scrolling=True)

        # ── Create draft ──────────────────────────────────────────────────────
        st.markdown("---")
        if st.button("✉️ Create Outlook draft", type="primary", use_container_width=True):
            if not client_email:
                st.error("Please enter the client email address.")
            else:
                try:
                    create_outlook_draft(
                        to_email  = client_email,
                        subject   = st.session_state["preview_subject"],
                        html_body = st.session_state["preview_html"],
                    )
                    # Mark enquiry as processed
                    entry_id = prefill.get("entry_id")
                    if entry_id:
                        processed = load_processed()
                        processed.add(entry_id)
                        save_processed(processed)
                        st.session_state.pop("prefill", None)
                        st.session_state.pop("enquiries", None)

                    st.success(
                        f"Draft created in Outlook Drafts — addressed to **{client_email}**. "
                        f"Review and send from Outlook."
                    )
                    st.session_state.pop("preview_html", None)
                    st.session_state.pop("preview_subject", None)
                except Exception as ex:
                    st.error(f"Could not create Outlook draft: {ex}")

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    "<p style='font-size:11px;color:#999;text-align:center'>"
    "Data sourced from Environment Agency · UKHSA · Gov.uk EPC Register · "
    "Rightmove / HM Land Registry · OpenStreetMap · LandIS Cranfield · "
    "Historic England · National Rail. "
    "Reports are indicative only and do not constitute a formal environmental search."
    "</p>",
    unsafe_allow_html=True,
)
