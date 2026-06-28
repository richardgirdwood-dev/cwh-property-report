"""
CWH Surveyors LLP — Internal Tools
  Tab 1: Property Environmental Report
  Tab 2: Survey Quote Generator
"""

import os
import re
import json
import tempfile
import datetime
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CWH Surveyors Tools",
    page_icon="🏠",
    layout="centered",
)

LOGO_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cwh_logo.png")
PROCESSED_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed_enquiries.json")
POSTCODE_RE   = re.compile(r'^[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}$', re.IGNORECASE)
POPUP_RE      = re.compile(r'new submission from popup', re.IGNORECASE)
NAME_RE       = re.compile(r'Dear\s+([A-Za-z\-]+)', re.IGNORECASE)

SIGNATURE = """\n\nKind regards,\n\nRichard\n\nRichard Girdwood MRICS\nPartner & Chartered Surveyor\nCambridgeshire, Rutland, South Lincs and Northamptonshire office\n\nUnit One, Hill Court\nTurnpike Close\nGrantham\nLincolnshire\nNG31 7XY\n\nTel: 01476 584 190\nwww.cwhsurveyors.co.uk"""

L2_BODY = """\
Hi {name},

Thank you for your enquiry and the opportunity to quote for {address}.

For a property of this age and type, I recommend the Level 2 Home Survey.

In summary, the Level 2 Home Survey covers all key elements of the property, including roof coverings, windows, doors, walls, and the damp proof course. Internally, an inspection of the roof space, where accessible, is conducted, including an assessment of the condition of the timbers and insulation levels. The report then covers all remaining internal elements of the property.

Gardens and grounds are included. A visual inspection of the services is conducted; however, we always recommend that up-to-date test certificates be provided for these. We would also lift any accessible drain covers on site to assess the condition.

Each section is assigned a condition rating (Green, Amber, or Red). Red sections indicate those that require further investigation or pose health and safety concerns. Amber indicates work that is required but not urgent, and Green indicates areas in satisfactory condition with regular maintenance required.

There is also an overview of legal items which your advisors should check as part of the purchase.

The report aims to enable you to make a fully informed decision about your purchase and to conduct any necessary investigations before exchanging contracts.

Upon completion of the report, we would be happy to answer any further questions or discuss any particular concerns to provide more information if necessary.

The fee for the Level 2 Home Survey, excluding valuation, will be \xa3{fee} + VAT.

We have availability from {availability}. Following the inspection, we will issue the completed report within 3-5 working days. If there are any cancellations, we would consider bringing this date forward.

If you are happy to proceed on this basis, could you please confirm the following:

1. Who the report should be addressed to (jointly with a partner or spouse if applicable)
2. Your current home address

We will then issue the Terms and Conditions and an invoice via email. Payment can be made via bank transfer, and our bank details will be provided on the invoice.

Further information and a sample report can be found on our website:
https://www.cwhsurveyors.co.uk/services/home-survey-reports/

Our Google reviews:
https://tinyurl.com/CWH-Surveyors

Feel free to contact me at any time via phone or email if there is anything you would like to discuss further.\
"""

L3_BODY = """\
Hi {name},

Thank you for your enquiry and the opportunity to quote for {address}.

The Level 3 Building Survey will be the best option for a property of this age and type.

In summary, the Level 3 Survey encompasses all key elements of the property, including the roof covering, windows, doors, walls, and the damp proof course. Internally, an inspection of the roof space, where accessible, is conducted, including an assessment of the condition of the timbers and insulation levels. The report then covers all remaining internal elements of the property.

Gardens and grounds are included. A visual inspection of the services is conducted; however, we always recommend that up-to-date test certificates are provided for these. We would also lift any accessible drain covers on site to assess the condition.

Each section is assigned a condition rating (Green, Amber, or Red). Red sections indicate those that require further investigation or raise health and safety concerns. Amber indicates work that is needed but not urgent, and Green indicates areas in satisfactory condition requiring normal maintenance.

Where necessary, we can also use a drone to inspect less accessible parts of the property.

Upon completion of the report, we would be happy to answer any further questions or discuss any particular concerns to provide additional information if necessary.

We are currently booking for {availability}. The completed report will be issued within 5-7 working days after the inspection. If there are any cancellations we will look to bring this forward for you.

The fee for the Level 3 Building Survey, excluding valuation, will be \xa3{fee} + VAT.

If you are happy to proceed on this basis, could you please confirm the following:

1. Who the report should be addressed to (jointly with a partner or spouse if applicable)
2. Your current home address

We will then email the Terms and Conditions for signature. Payment can be made via bank transfer; our bank details will be provided on the invoice.

Our Google reviews:
https://tinyurl.com/CWH-Surveyors

Please feel free to contact me at your convenience via phone or email if you would like to discuss anything further.\
"""

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_processed():
    if os.path.exists(PROCESSED_LOG):
        with open(PROCESSED_LOG) as f:
            return set(json.load(f))
    return set()

def save_processed(entry_ids: set):
    with open(PROCESSED_LOG, "w") as f:
        json.dump(list(entry_ids), f)

def scan_inbox():
    """Return list of dicts for unprocessed New Submission emails."""
    try:
        import win32com.client
        processed = load_processed()
        outlook   = win32com.client.Dispatch("Outlook.Application")
        ns        = outlook.GetNamespace("MAPI")
        inbox     = ns.GetDefaultFolder(6)
        items     = inbox.Items
        items.Sort("[ReceivedTime]", True)

        # Look back 60 days
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%d/%m/%Y %H:%M")
        filtered = items.Restrict(f"[ReceivedTime] >= '{cutoff}'")

        results = []
        for item in filtered:
            try:
                subj = item.Subject or ""
                if not POPUP_RE.search(subj):
                    continue
                eid = item.EntryID
                if eid in processed:
                    continue
                # Extract client name from body
                name_match = NAME_RE.search(item.Body or "")
                client_name = name_match.group(1).strip() if name_match else ""
                # Property address: everything after "Popup" in subject
                addr_match = re.search(r'popup[:\s]+(.+)', subj, re.IGNORECASE)
                property_addr = addr_match.group(1).strip() if addr_match else subj
                results.append({
                    "entry_id":    eid,
                    "subject":     subj,
                    "received":    str(item.ReceivedTime)[:10],
                    "client_name": client_name,
                    "property":    property_addr,
                })
            except Exception:
                continue
        return results
    except Exception as e:
        return []

def create_outlook_draft(to_email, subject, body):
    """Create a plain-text draft in Outlook Drafts folder."""
    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)   # 0 = olMailItem
    mail.To          = to_email
    mail.Subject     = subject
    mail.BodyFormat  = 1           # 1 = plain text — best for deliverability
    mail.Body        = body
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

    # ── Inbox scan ────────────────────────────────────────────────────────────
    with st.expander("📥 Import from inbox (New submission from Popup)", expanded=True):
        if st.button("Scan inbox for new enquiries", use_container_width=True):
            with st.spinner("Scanning inbox…"):
                enquiries = scan_inbox()
            st.session_state["enquiries"] = enquiries

        enquiries = st.session_state.get("enquiries", [])
        if enquiries:
            st.success(f"Found {len(enquiries)} unprocessed enquiry/enquiries.")
            options = {
                f"{e['received']}  —  {e['property']}  ({e['client_name'] or 'name unknown'})": e
                for e in enquiries
            }
            chosen_label = st.selectbox("Select enquiry to quote", list(options.keys()))
            chosen = options[chosen_label]
            st.session_state["prefill"] = {
                "client_name": chosen["client_name"],
                "property":    chosen["property"],
                "entry_id":    chosen["entry_id"],
            }
        elif "enquiries" in st.session_state:
            st.info("No new enquiries found in the last 60 days.")

    st.markdown("---")

    # ── Quote form ────────────────────────────────────────────────────────────
    prefill = st.session_state.get("prefill", {})

    st.markdown("#### Quote details")
    col1, col2 = st.columns(2)
    with col1:
        client_name = st.text_input("Client first name",
                                    value=prefill.get("client_name", ""))
        client_email = st.text_input("Client email address",
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
        availability = st.text_input("Availability",
                                     placeholder="e.g. week commencing 20th July")

    notes = st.text_area("Personal note (optional — inserted after greeting)",
                          placeholder="e.g. It looks like a lovely property and we would be delighted to assist.",
                          height=80)

    # ── Preview ───────────────────────────────────────────────────────────────
    if st.button("Preview quote", use_container_width=False):
        if not client_name or not fee or not availability:
            st.warning("Please fill in client name, fee, and availability before previewing.")
        else:
            template = L2_BODY if "Level 2" in survey_type else L3_BODY
            body = template.format(
                name=client_name,
                address=property_addr or "the property",
                fee=fee,
                availability=availability,
            )
            if notes.strip():
                # Insert personal note after the greeting line
                lines = body.split("\n")
                insert_at = 2  # after "Hi {name}," and blank line
                lines.insert(insert_at, notes.strip() + "\n")
                body = "\n".join(lines)
            full_body = body + SIGNATURE
            st.session_state["preview_body"] = full_body
            st.session_state["preview_subject"] = property_addr or "Survey quotation"

    if "preview_body" in st.session_state:
        st.markdown("**Preview:**")
        st.text_area("Email body", value=st.session_state["preview_body"],
                     height=500, label_visibility="collapsed")

        # ── Create draft ──────────────────────────────────────────────────────
        st.markdown("---")
        if st.button("✉️ Create Outlook draft", type="primary", use_container_width=True):
            if not client_email:
                st.error("Please enter the client email address.")
            else:
                try:
                    create_outlook_draft(
                        to_email=client_email,
                        subject=st.session_state["preview_subject"],
                        body=st.session_state["preview_body"],
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
                    st.session_state.pop("preview_body", None)
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
