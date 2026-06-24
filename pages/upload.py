"""
Upload Quotes page — drag-and-drop .msg or .pdf files,
preview extracted data, save to Excel.
"""

import streamlit as st
import pandas as pd

from utils.state import init_state, log_event
from utils.extractor import process_msg_bytes, pdf_bytes_to_text, COLUMNS
from utils.excel_io import append_rows, quote_exists, XLSX_PATH


def render():
    init_state()
    st.title("📤 Upload Quotes")
    st.caption("Upload Outlook `.msg` files (or raw `.pdf` quote files) to extract and save quote data.")

    api_key     = st.session_state.get("groq_api_key", "")
    model       = st.session_state.get("groq_model", "llama-3.3-70b-versatile")
    output_path = st.session_state.get("output_xlsx", XLSX_PATH)

    if not api_key:
        st.warning("⚠️ Groq API key not set. Go to **⚙️ Settings** to add it.")
        return

    # ── File uploader — no type filter so .msg files are accepted ────────────
    uploaded = st.file_uploader(
        "Drop files here (.msg or .pdf)",
        type=None,                     # No restriction — .msg would be blocked otherwise
        accept_multiple_files=True,
        help="Select Outlook .msg files exported from your inbox, or raw .pdf quote files",
    )

    # Filter to only .msg and .pdf silently
    if uploaded:
        valid   = [f for f in uploaded if f.name.lower().endswith((".msg", ".pdf"))]
        skipped = [f for f in uploaded if not f.name.lower().endswith((".msg", ".pdf"))]
        if skipped:
            st.warning(f"⚠️ Skipped {len(skipped)} unsupported file(s): {', '.join(f.name for f in skipped)}")
        uploaded = valid

    if not uploaded:
        st.info("Upload `.msg` or `.pdf` files above to get started.")
        return

    st.markdown(f"**{len(uploaded)} file(s) ready.** Click Process to extract data with Groq AI.")

    if st.button("🚀 Process all files", type="primary"):
        all_results = []
        progress = st.progress(0, text="Starting…")

        for i, file in enumerate(uploaded):
            progress.progress(i / len(uploaded), text=f"Processing {file.name}…")
            file.seek(0)
            raw_bytes = file.read()

            with st.spinner(f"Extracting {file.name}…"):
                if file.name.lower().endswith(".pdf"):
                    # Direct PDF — wrap it so process_msg_bytes can handle it
                    result = _process_pdf_bytes(raw_bytes, api_key, model)
                else:
                    result = process_msg_bytes(raw_bytes, api_key, model)

            result["filename"] = file.name
            all_results.append(result)

        progress.progress(1.0, text="Done!")

        # ── Show results per file ────────────────────────────────────────────
        for result in all_results:
            fname  = result["filename"]
            data   = result.get("data", {})
            rows   = result.get("rows", [])
            issues = result.get("issues", [])
            q_num  = data.get("quote_number", "UNKNOWN")

            with st.expander(
                f"📄 {fname}  —  Quote #{q_num}  ({len([r for r in rows if r.get('item_id') != 'Tax'])} line items)",
                expanded=True,
            ):
                for iss in issues:
                    st.warning(f"⚠️ {iss}")

                if rows:
                    # Header summary
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Company",      data.get("company", "—"))
                    c2.metric("Quote #",      q_num)
                    c3.metric("Valid through", data.get("quote_valid_through", "—"))
                    c4.metric("Grand total",  f"${data.get('grand_total', 0):,.2f}")

                    # Line items preview
                    preview_cols = ["item_id", "item_desc", "Unit Price", "TotalSales"]
                    preview_df = pd.DataFrame([{c: r.get(c) for c in preview_cols} for r in rows])
                    st.dataframe(preview_df, use_container_width=True, height=min(200, 40 + len(rows) * 35))

                    # Save / overwrite / skip
                    already = quote_exists(q_num, output_path)
                    if already:
                        st.info(f"ℹ️ Quote #{q_num} already exists in the spreadsheet.")
                        col_ow, col_sk = st.columns(2)
                        if col_ow.button(f"Overwrite #{q_num}", key=f"ow_{q_num}_{fname}"):
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Overwritten {len(rows)} rows from {fname}")
                            st.success(f"✅ Overwritten {len(rows)} rows for quote #{q_num}")
                        if col_sk.button(f"Skip #{q_num}", key=f"sk_{q_num}_{fname}"):
                            log_event(q_num, "skipped", f"Skipped {fname}")
                            st.info("Skipped.")
                    else:
                        if st.button(f"💾 Save quote #{q_num} to Excel", key=f"save_{q_num}_{fname}"):
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Saved {len(rows)} rows from {fname}")
                            st.success(f"✅ {len(rows)} rows saved for quote #{q_num}")

                else:
                    st.error("❌ No data could be extracted.")
                    if result.get("pdf_text"):
                        with st.expander("Raw PDF text (debug)"):
                            st.text(result["pdf_text"][:3000])


def _process_pdf_bytes(pdf_bytes: bytes, api_key: str, model: str) -> dict:
    """Handle a raw PDF file (no MSG wrapper)."""
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
