"""
Property Environmental Report — Streamlit web app
CWH Surveyors LLP
"""

import os
import re
import tempfile
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CWH Property Environmental Report",
    page_icon="🏠",
    layout="centered",
)

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cwh_logo.png")
POSTCODE_RE = re.compile(r'^[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}$', re.IGNORECASE)

# ── Header ────────────────────────────────────────────────────────────────────
col_logo, col_title = st.columns([1, 2])
with col_logo:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=200)
with col_title:
    st.markdown(
        """
        <div style="padding:10px 0 0 10px">
            <h2 style="color:#1B3A6B;margin:0;font-family:sans-serif">Property Environmental Report</h2>
            <p style="color:#555;margin:4px 0 0;font-size:14px">
                Enter a property address to generate a PDF environmental report.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")

tab_report, tab_proofread = st.tabs(["Property Report", "Survey Proofreading"])

# ── Tab 1: Property Report ──────────────────────────────────────────────────
with tab_report:
    # ── Input form ────────────────────────────────────────────────────────────
    with st.form("report_form"):
        address = st.text_input(
            "Property address",
            placeholder="e.g. 197 High Street, Cottenham, Cambridge",
            help="Full street address without the postcode.",
        )
        postcode = st.text_input(
            "Postcode",
            placeholder="e.g. CB24 8RX",
            max_chars=8,
        )
        submitted = st.form_submit_button("Generate Report", type="primary", use_container_width=True)

    # ── Validation & confirmation ─────────────────────────────────────────────
    if submitted:
        address  = address.strip()
        postcode = postcode.strip().upper()
        postcode = re.sub(r'\s+', ' ', postcode)
        if len(postcode) > 4 and ' ' not in postcode:
            postcode = postcode[:-3] + ' ' + postcode[-3:]

        errors = []
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
            # Confirm postcode resolves and show local authority before generating
            with st.spinner("Checking postcode…"):
                try:
                    import requests as _req
                    pc_check = _req.get(
                        f"https://api.postcodes.io/postcodes/{postcode.replace(' ','')}",
                        timeout=8
                    ).json()
                    pc_result = pc_check.get("result")
                except Exception:
                    pc_result = None

            if not pc_result:
                st.error(f"Could not resolve postcode **{postcode}** — please check it and try again.")
            else:
                la   = pc_result.get("admin_district", "")
                region = pc_result.get("region", "")
                st.info(
                    f"**{address}, {postcode}**  \n"
                    f"Local Authority: **{la}** — Region: **{region}**"
                )

                # ── Generate ───────────────────────────────────────────────────
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

# ── Tab 2: Survey Proofreading ──────────────────────────────────────────────
with tab_proofread:
    st.markdown(
        "Upload a survey report PDF to check it for common drafting mistakes: "
        "invalid condition ratings, leftover template placeholder text, "
        "spelling and grammar issues, and standard sections that appear to "
        "be missing.\n\n"
        "Client names, addresses, postcodes, emails and phone numbers are "
        "redacted from the report text before anything is checked — including "
        "before anything is sent to the spelling/grammar service — so personal "
        "data never leaves this machine.\n\n"
        "_These are heuristic checks, not a formal RICS compliance review — "
        "always read flagged passages in context before relying on them. "
        "Redaction is regex-based, not guaranteed complete, so don't treat it "
        "as a substitute for not sharing sensitive PDFs elsewhere._"
    )

    uploaded_pdf = st.file_uploader("Survey report (PDF)", type=["pdf"])
    check_spelling = st.checkbox(
        "Also check spelling & grammar",
        value=True,
        help="Uses the free LanguageTool web service, so it needs an internet "
             "connection and takes a few seconds per page.",
    )
    run_proofread = st.button("Proofread report", type="primary", use_container_width=True)

    if run_proofread:
        if not uploaded_pdf:
            st.error("Please upload a PDF first.")
        else:
            progress_bar = st.progress(0.0, text="Reading report…") if check_spelling else None

            def _progress(done, total):
                progress_bar.progress(
                    done / total, text=f"Checking spelling & grammar — page {done} of {total}…"
                )

            with st.spinner("Reading and checking the report…"):
                try:
                    from survey_proofreader import proofread
                    results = proofread(
                        uploaded_pdf,
                        check_spelling=check_spelling,
                        progress_cb=_progress if progress_bar else None,
                    )
                except Exception as ex:
                    results = None
                    st.error(f"Could not read this PDF: {ex}")

            if progress_bar is not None:
                progress_bar.empty()

            if results is not None:
                if results["text_pages"] == 0:
                    st.warning(
                        "No extractable text was found in this PDF — it may be a scanned "
                        "image without a text layer, which these checks cannot read."
                    )
                else:
                    if results["spelling_grammar_unavailable"]:
                        st.info(
                            "The spelling & grammar service couldn't be reached, so those "
                            "results may be incomplete — check your internet connection and "
                            "try again if needed."
                        )

                    if results["total_issues"] == 0:
                        st.success(
                            f"No issues found across {results['page_count']} pages. "
                            "This does not guarantee the report is complete or correct — "
                            "it only means none of the automated checks were triggered."
                        )
                    else:
                        st.warning(
                            f"{results['total_issues']} issue(s) flagged across "
                            f"{results['page_count']} pages — review below."
                        )

                    def _issue_list(issues):
                        for issue in issues:
                            loc = f"Page {issue['page']}" if issue["page"] else "Document-wide"
                            prefix = f"[{issue['type']}] " if issue.get("type") else ""
                            st.markdown(f"- **{loc}** — {prefix}{issue['message']}")
                            if issue.get("snippet"):
                                st.caption(issue["snippet"])

                    if results["condition_rating_issues"]:
                        st.subheader(f"Condition ratings ({len(results['condition_rating_issues'])})")
                        _issue_list(results["condition_rating_issues"])

                    if results["placeholder_issues"]:
                        st.subheader(f"Leftover placeholder text ({len(results['placeholder_issues'])})")
                        _issue_list(results["placeholder_issues"])

                    if results["spelling_grammar_issues"]:
                        st.subheader(f"Spelling & grammar ({len(results['spelling_grammar_issues'])})")
                        _issue_list(results["spelling_grammar_issues"])

                    if results["missing_sections"]:
                        st.subheader(f"Possibly missing sections ({len(results['missing_sections'])})")
                        for section in results["missing_sections"]:
                            st.markdown(f"- {section}")

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
