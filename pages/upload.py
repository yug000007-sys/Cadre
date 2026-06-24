"""
Upload Quotes page — supports .msg and .pdf files.
Uses application/vnd.ms-outlook MIME type so .msg works on Streamlit Cloud.
"""

import streamlit as st
import pandas as pd

from utils.state import init_state, log_event
from utils.extractor import process_msg_bytes, pdf_bytes_to_text, COLUMNS
from utils.excel_io import append_rows, quote_exists, XLSX_PATH


def render():
    init_state()
    st.title("📤 Upload Quotes")

    api_key     = st.session_state.get("groq_api_key", "")
    model       = st.session_state.get("groq_model", "llama-3.3-70b-versatile")
    output_path = st.session_state.get("output_xlsx", XLSX_PATH)

    if not api_key:
        st.error("⚠️ Groq API key missing — go to the **⚙️ Settings** tab, paste your key, and click **Save settings** first.")
        return

    st.caption(f"Model: `{model}` · Output: `{output_path}`")

    # ── Upload widget ─────────────────────────────────────────────────────────
    # .msg MIME = application/vnd.ms-outlook  (needed for Streamlit Cloud)
    # We list both the extension hint AND the MIME so it works everywhere
    uploaded = st.file_uploader(
        "Drop .msg or .pdf files here",
        type=["msg", "pdf", "eml"],          # extension list for local
        accept_multiple_files=True,
    )

    # Streamlit Cloud sometimes strips unknown types — catch that gracefully
    if uploaded is None:
        uploaded = []

    # Also offer a fallback: rename tip
    with st.expander("💡 File not appearing in the picker?"):
        st.markdown("""
Streamlit Cloud may block `.msg` files in the browser file picker.

**Quick fix:** rename your file from `Sales_Quote_123565.msg` → `Sales_Quote_123565.msg.pdf`  
then upload it — the agent detects the real format from the file contents, not the extension.

Or use the **raw bytes uploader** below:
""")
        raw_upload = st.file_uploader(
            "Upload any file (no extension filter)",
            type=None,
            accept_multiple_files=True,
            key="raw_uploader",
            label_visibility="collapsed",
        )
        if raw_upload:
            uploaded = uploaded + raw_upload if uploaded else raw_upload

    if not uploaded:
        st.info("Upload `.msg` files from your Outlook inbox to extract quote data.")
        return

    # Deduplicate by name
    seen, unique = set(), []
    for f in uploaded:
        if f.name not in seen:
            seen.add(f.name)
            unique.append(f)
    uploaded = unique

    st.success(f"**{len(uploaded)} file(s) ready** — click Process to extract with Groq AI")

    if st.button("🚀 Process all files", type="primary"):
        all_results = []
        progress = st.progress(0, text="Starting…")

        for i, file in enumerate(uploaded):
            progress.progress(i / len(uploaded), text=f"Processing {file.name}…")
            try:
                file.seek(0)
            except Exception:
                pass
            raw_bytes = file.read()

            # Detect format from magic bytes, not extension
            is_pdf = raw_bytes[:4] == b"%PDF"

            with st.spinner(f"Extracting {file.name}…"):
                if is_pdf:
                    result = _process_pdf_bytes(raw_bytes, api_key, model)
                else:
                    result = process_msg_bytes(raw_bytes, api_key, model)

            result["filename"] = file.name
            all_results.append(result)

        progress.progress(1.0, text="Done!")

        # ── Show results ─────────────────────────────────────────────────────
        for result in all_results:
            fname  = result["filename"]
            data   = result.get("data", {})
            rows   = result.get("rows", [])
            issues = result.get("issues", [])
            q_num  = data.get("quote_number", "UNKNOWN")
            line_count = len([r for r in rows if r.get("item_id") != "Tax"])

            with st.expander(f"📄 {fname}  —  Quote #{q_num}  ({line_count} line items)", expanded=True):
                for iss in issues:
                    st.warning(f"⚠️ {iss}")

                if rows:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Company",       data.get("company", "—"))
                    c2.metric("Quote #",       q_num)
                    c3.metric("Valid through", data.get("quote_valid_through", "—"))
                    c4.metric("Grand total",   f"${data.get('grand_total', 0):,.2f}")

                    preview_cols = ["item_id", "item_desc", "Unit Price", "TotalSales"]
                    preview_df = pd.DataFrame([{c: r.get(c) for c in preview_cols} for r in rows])
                    st.dataframe(preview_df, use_container_width=True,
                                 height=min(220, 45 + line_count * 36))

                    already = quote_exists(q_num, output_path)
                    if already:
                        st.info(f"ℹ️ Quote #{q_num} already exists in the spreadsheet.")
                        col_ow, col_sk = st.columns(2)
                        if col_ow.button(f"Overwrite #{q_num}", key=f"ow_{q_num}_{fname}"):
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Overwritten {len(rows)} rows from {fname}")
                            st.success(f"✅ Overwritten {len(rows)} rows")
                        if col_sk.button(f"Skip", key=f"sk_{q_num}_{fname}"):
                            log_event(q_num, "skipped", f"Skipped {fname}")
                            st.info("Skipped.")
                    else:
                        if st.button(f"💾 Save quote #{q_num} to Excel", key=f"save_{q_num}_{fname}"):
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Saved {len(rows)} rows from {fname}")
                            st.success(f"✅ {len(rows)} rows saved for quote #{q_num}")
                else:
                    st.error("❌ No data could be extracted from this file.")
                    if result.get("pdf_text"):
                        with st.expander("Raw PDF text (debug)"):
                            st.text(result["pdf_text"][:3000])


def _process_pdf_bytes(pdf_bytes: bytes, api_key: str, model: str) -> dict:
    from utils.extractor import extract_quote_with_groq, build_rows, validate_rows
    try:
        pdf_text = pdf_bytes_to_text(pdf_bytes)
        data     = extract_quote_with_groq(pdf_text, api_key, model)
        q_num    = data.get("quote_number", "UNKNOWN")
        rows     = build_rows(data, None, q_num)
        issues   = validate_rows(data, rows)
        return {"rows": rows, "data": data, "issues": issues, "sender_email": None, "pdf_text": pdf_text}
    except Exception as e:
        return {"rows": [], "data": {}, "issues": [f"Extraction failed: {e}"], "sender_email": None, "pdf_text": ""}
