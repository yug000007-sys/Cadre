import streamlit as st
import pandas as pd
import zipfile
import os
from io import BytesIO
from utils.state import init_state, log_event
from utils.extractor import process_msg_bytes, pdf_bytes_to_text, extract_quote_with_groq, build_rows, validate_rows
from utils.excel_io import append_rows, quote_exists, XLSX_PATH

PDF_STORE = "data/pdfs"  # folder where extracted PDFs are saved


def render():
    init_state()
    st.title("📤 Upload Quotes")

    api_key     = st.session_state.get("groq_api_key", "")
    model       = st.session_state.get("groq_model", "llama-3.3-70b-versatile")
    output_path = st.session_state.get("output_xlsx", XLSX_PATH)

    if not api_key:
        st.error("⚠️ Go to **⚙️ Settings** tab, paste your Groq API key and click Save.")
        return

    uploaded = st.file_uploader(
        "Upload a zip file containing PDF quotes",
        type=None,
        accept_multiple_files=False,
    )

    if not uploaded:
        st.info("Upload a `.zip` file containing your PDF quotes (e.g. `Cadre Wire Group_123565.pdf` inside).")
        return

    uploaded.seek(0)
    raw = uploaded.read()

    # Detect zip vs single PDF vs MSG
    is_zip = raw[:2] == b"PK"
    is_pdf = raw[:4] == b"%PDF"

    if is_zip:
        try:
            zf = zipfile.ZipFile(BytesIO(raw))
            pdf_files = [n for n in zf.namelist() if n.lower().endswith(".pdf") and not n.startswith("__")]
        except Exception as e:
            st.error(f"Could not open zip: {e}")
            return

        if not pdf_files:
            st.error("No PDF files found inside the zip.")
            return

        st.success(f"Found **{len(pdf_files)} PDF(s)** in the zip — click Process to extract.")
        with st.expander("Files in zip"):
            for name in pdf_files:
                st.text(f"• {name}")

        if st.button("🚀 Process all PDFs", type="primary"):
            os.makedirs(PDF_STORE, exist_ok=True)
            progress = st.progress(0)
            all_results = []

            for i, pdf_name in enumerate(pdf_files):
                progress.progress((i + 1) / len(pdf_files), text=f"Processing {pdf_name}…")
                pdf_bytes = zf.read(pdf_name)
                basename  = os.path.basename(pdf_name)

                with st.spinner(f"Extracting {basename}…"):
                    result = _process_pdf(pdf_bytes, api_key, model)

                result["filename"]  = basename
                result["pdf_bytes"] = pdf_bytes
                all_results.append(result)

            progress.empty()
            _show_results(all_results, output_path)

    elif is_pdf:
        basename = uploaded.name
        st.info(f"Single PDF detected: `{basename}`")
        if st.button("🚀 Process", type="primary"):
            os.makedirs(PDF_STORE, exist_ok=True)
            with st.spinner(f"Extracting {basename}…"):
                result = _process_pdf(raw, api_key, model)
            result["filename"]  = basename
            result["pdf_bytes"] = raw
            _show_results([result], output_path)

    else:
        # MSG file
        basename = uploaded.name
        st.info(f"MSG file detected: `{basename}`")
        if st.button("🚀 Process", type="primary"):
            os.makedirs(PDF_STORE, exist_ok=True)
            with st.spinner(f"Extracting {basename}…"):
                result = process_msg_bytes(raw, api_key, model)
            from utils.extractor import extract_pdf_from_msg
            result["filename"]  = basename
            result["pdf_bytes"] = extract_pdf_from_msg(raw)
            _show_results([result], output_path)


def _process_pdf(pdf_bytes: bytes, api_key: str, model: str) -> dict:
    try:
        text   = pdf_bytes_to_text(pdf_bytes)
        data   = extract_quote_with_groq(text, api_key, model)
        q_num  = data.get("quote_number", "UNKNOWN")
        rows   = build_rows(data, None, q_num)
        issues = validate_rows(data, rows)
        return {"rows": rows, "data": data, "issues": issues, "pdf_text": text}
    except Exception as e:
        return {"rows": [], "data": {}, "issues": [str(e)], "pdf_text": ""}


def _show_results(all_results: list, output_path: str):
    for result in all_results:
        fname     = result["filename"]
        data      = result.get("data", {})
        rows      = result.get("rows", [])
        issues    = result.get("issues", [])
        pdf_bytes = result.get("pdf_bytes")
        q_num     = data.get("quote_number", "UNKNOWN")
        items     = [r for r in rows if r.get("item_id") != "Tax"]

        with st.expander(f"📄 {fname} — Quote #{q_num} ({len(items)} items)", expanded=True):
            for iss in issues:
                st.warning(iss)

            if rows:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Company",       data.get("company", "—"))
                c2.metric("Quote #",       q_num)
                c3.metric("Valid through", data.get("quote_valid_through", "—"))
                c4.metric("Total",         f"${data.get('grand_total', 0):,.2f}")

                st.dataframe(
                    pd.DataFrame([{
                        "item_id":    r.get("item_id"),
                        "item_desc":  r.get("item_desc"),
                        "Unit Price": r.get("Unit Price"),
                        "TotalSales": r.get("TotalSales"),
                    } for r in rows]),
                    use_container_width=True,
                    height=min(220, 45 + len(items) * 36),
                )

                # PDF download
                if pdf_bytes:
                    pdf_filename = f"Cadre Wire Group_{q_num}.pdf"
                    st.download_button(
                        label=f"⬇️ Download {pdf_filename}",
                        data=pdf_bytes,
                        file_name=pdf_filename,
                        mime="application/pdf",
                        key=f"dl_{q_num}",
                    )

                # Save to Excel
                if quote_exists(q_num, output_path):
                    st.info(f"Quote #{q_num} already exists in spreadsheet.")
                    col1, col2 = st.columns(2)
                    if col1.button("Overwrite", key=f"ow_{q_num}"):
                        _save(rows, pdf_bytes, q_num, output_path, fname, overwrite=True)
                    if col2.button("Skip", key=f"sk_{q_num}"):
                        st.info("Skipped.")
                else:
                    if st.button(f"💾 Save quote #{q_num} to Excel", key=f"save_{q_num}"):
                        _save(rows, pdf_bytes, q_num, output_path, fname)

            else:
                st.error("No data extracted from this PDF.")
                if result.get("pdf_text"):
                    with st.expander("Debug — raw text"):
                        st.text(result["pdf_text"][:2000])


def _save(rows, pdf_bytes, q_num, output_path, fname, overwrite=False):
    if pdf_bytes:
        os.makedirs(PDF_STORE, exist_ok=True)
        pdf_path = os.path.join(PDF_STORE, f"Cadre Wire Group_{q_num}.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

    append_rows(rows, output_path)
    action = "Overwritten" if overwrite else "Saved"
    log_event(q_num, "success", f"{action} {len(rows)} rows from {fname}")
    st.success(f"✅ {len(rows)} rows saved + PDF stored as `Cadre Wire Group_{q_num}.pdf`")
