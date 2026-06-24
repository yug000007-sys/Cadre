"""
Upload Quotes page — drag-and-drop .msg files, preview extracted data, save to Excel.
"""

import streamlit as st
import pandas as pd

from utils.state import init_state, log_event
from utils.extractor import process_msg_bytes, COLUMNS
from utils.excel_io import append_rows, quote_exists, XLSX_PATH


def render():
    init_state()
    st.title("📤 Upload Quotes")
    st.caption("Upload one or more Outlook `.msg` files to extract and save quote data.")

    api_key = st.session_state.get("groq_api_key", "")
    model   = st.session_state.get("groq_model", "llama-3.3-70b-versatile")
    output_path = st.session_state.get("output_xlsx", XLSX_PATH)

    if not api_key:
        st.warning("⚠️ Groq API key not set. Go to **⚙️ Settings** to configure it.")
        return

    uploaded = st.file_uploader(
        "Drop .msg files here",
        type=["msg"],
        accept_multiple_files=True,
        help="Select one or more Outlook .msg files exported from your inbox",
    )

    if not uploaded:
        st.info("Upload `.msg` files above to get started.")
        return

    st.markdown(f"**{len(uploaded)} file(s) selected.** Click Process to extract data.")

    if st.button("🚀 Process all files", type="primary"):
        all_results = []

        progress = st.progress(0, text="Starting...")
        for i, file in enumerate(uploaded):
            progress.progress((i) / len(uploaded), text=f"Processing {file.name}…")
            msg_bytes = file.read()

            with st.spinner(f"Extracting {file.name}…"):
                result = process_msg_bytes(msg_bytes, api_key, model)

            result["filename"] = file.name
            all_results.append(result)

        progress.progress(1.0, text="Done!")

        # ── Show results per file ────────────────────────────────────────────
        for result in all_results:
            fname = result["filename"]
            data  = result.get("data", {})
            rows  = result.get("rows", [])
            issues = result.get("issues", [])
            q_num = data.get("quote_number", "UNKNOWN")

            with st.expander(f"📄 {fname}  —  Quote #{q_num}  ({len(rows)} line items)", expanded=True):
                if issues:
                    for iss in issues:
                        st.warning(f"⚠️ {iss}")

                if rows:
                    # Preview table
                    preview_cols = ["item_id", "item_desc", "Unit Price", "TotalSales"]
                    preview_df = pd.DataFrame([{c: r.get(c) for c in preview_cols} for r in rows])
                    st.dataframe(preview_df, use_container_width=True, height=200)

                    # Quote header summary
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Company", data.get("company", "—"))
                    c2.metric("Quote #", q_num)
                    c3.metric("Valid through", data.get("quote_valid_through", "—"))
                    c4.metric("Total", f"${data.get('grand_total', 0):,.2f}")

                    already = quote_exists(q_num, output_path)
                    if already:
                        st.info(f"ℹ️ Quote #{q_num} already exists in the spreadsheet.")
                        col1, col2 = st.columns(2)
                        overwrite = col1.button(f"Overwrite #{q_num}", key=f"ow_{q_num}")
                        skip = col2.button(f"Skip #{q_num}", key=f"sk_{q_num}")
                        if overwrite:
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Overwritten {len(rows)} rows from {fname}")
                            st.success(f"✅ Overwritten {len(rows)} rows for quote #{q_num}")
                        elif skip:
                            log_event(q_num, "skipped", f"Skipped {fname}")
                            st.info("Skipped.")
                    else:
                        if st.button(f"💾 Save quote #{q_num} to Excel", key=f"save_{q_num}"):
                            append_rows(rows, output_path)
                            log_event(q_num, "success", f"Saved {len(rows)} rows from {fname}")
                            st.success(f"✅ {len(rows)} rows saved for quote #{q_num}")
                else:
                    st.error("❌ No data could be extracted from this file.")
                    if result.get("pdf_text"):
                        with st.expander("Raw PDF text (for debugging)"):
                            st.text(result["pdf_text"][:3000])
