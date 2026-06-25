"""
Cadre Wire Group — Quote Parser
Uses extract-msg + pymupdf + Groq AI (llama-3.1-8b-instant)
"""

import re
import os
import json
import tempfile
import shutil
import gc

try:
    import fitz
    def _pdf_to_text(pdf_bytes):
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(page.get_text("text") for page in doc)
except ImportError:
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    from io import BytesIO
    def _pdf_to_text(pdf_bytes):
        out = BytesIO()
        extract_text_to_fp(BytesIO(pdf_bytes), out, laparams=LAParams(), output_type="text")
        return out.getvalue().decode("utf-8", errors="ignore")

try:
    import extract_msg
except Exception:
    extract_msg = None

try:
    import streamlit as st
    def _get_groq_key():
        try:
            return st.secrets["groq"]["api_key"]
        except Exception:
            return os.environ.get("GROQ_API_KEY", "")
except Exception:
    def _get_groq_key():
        return os.environ.get("GROQ_API_KEY", "")


SALESPERSON_MAP = {
    "regina deavers":   "rdeavers@cadrewire.com",
    "dara august":      "daugust@cadrewire.com",
    "andrew smith":     "Asmith@cadrewire.com",
    "industrial sales": "rdeavers@cadrewire.com",
}

HEADERS = [
    "ReferralManager", "ReferralEmail", "Brand", "QuoteNumber", "QuoteDate",
    "Company", "FirstName", "LastName", "ContactEmail", "ContactPhone",
    "Address", "County", "City", "State", "ZipCode", "Country",
    "item_id", "item_desc", "Unit Price", "TotalSales",
    "QuoteValidDate", "CustomerNumber", "manufacturer_Name", "PDF", "DemoQuote",
]

PROMPT = """You are a data extraction agent for Cadre Wire Group.
Extract ALL fields from this sales quote PDF text. Return ONLY valid JSON, no markdown, no explanation.

Return exactly this structure:
{
  "quote_number":        "string",
  "quote_date":          "MM/DD/YYYY",
  "quote_valid_through": "MM/DD/YYYY",
  "customer_number":     "string",
  "company":             "string",
  "contact_first_name":  "string",
  "contact_last_name":   "string",
  "contact_email":       "string or empty",
  "contact_phone":       "string or empty",
  "address":             "string (street only, no city/state/zip)",
  "city":                "string",
  "state":               "string (2-letter)",
  "zip_code":            "string",
  "country":             "string (default USA)",
  "salesperson":         "string (full name)",
  "line_items": [
    {
      "item_id":    "string (part number only, e.g. HS.1635F1-C48)",
      "item_desc":  "string (full description)",
      "ordered":    number,
      "unit_price": number,
      "extension":  number
    }
  ],
  "product_total": number,
  "tax":           number,
  "grand_total":   number
}

Rules:
- Include Tax as a line item: item_id="Tax", item_desc="", ordered=1, unit_price=tax_amount, extension=tax_amount
- For cable priced per MFT keep unit_price as the MFT rate (e.g. 4190.0)
- country default is "USA"
- Return ONLY the raw JSON object

PDF TEXT:
"""


def _clean(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()


def _referral_email(salesperson):
    return SALESPERSON_MAP.get(_clean(salesperson).lower(), "rdeavers@cadrewire.com")


def _make_pdf_name(q):
    s = _clean(q)
    return f"Cadre Wire Group_{s}.pdf" if s else "Cadre Wire Group_unknown.pdf"


def _is_quote_pdf(filename):
    return bool(re.match(r"quote\s*\d+", filename.lower().strip()))


def _trim_pdf_text(text, max_chars=3500):
    """
    Remove boilerplate footer text to stay within model token limits.
    Keeps everything up to and including the last line item / tax row.
    """
    # Cut at Terms & Conditions or page footers — pure boilerplate after that
    for marker in ["Terms and Conditions", "Terms & Conditions",
                   "TERMS AND CONDITIONS", "Thank you for",
                   "This quote is valid"]:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
            break
    # Hard cap at max_chars
    if len(text) > max_chars:
        text = text[:max_chars]
    return text.strip()


def _extract_with_groq(pdf_text):
    from groq import Groq
    pdf_text = _trim_pdf_text(pdf_text)
    client = Groq(api_key=_get_groq_key())
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": PROMPT + pdf_text}],
        temperature=0.0,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def _build_rows(data, pdf_name):
    salesperson = _clean(data.get("salesperson", ""))
    base = {
        "ReferralManager":   "",
        "ReferralEmail":     _referral_email(salesperson),
        "Brand":             "Cadre Wire Group",
        "QuoteNumber":       _clean(data.get("quote_number", "")),
        "QuoteDate":         _clean(data.get("quote_date", "")),
        "Company":           _clean(data.get("company", "")),
        "FirstName":         _clean(data.get("contact_first_name", "")),
        "LastName":          _clean(data.get("contact_last_name", "")),
        "ContactEmail":      _clean(data.get("contact_email", "")),
        "ContactPhone":      _clean(data.get("contact_phone", "")),
        "Address":           _clean(data.get("address", "")),
        "County":            "",
        "City":              _clean(data.get("city", "")),
        "State":             _clean(data.get("state", "")),
        "ZipCode":           _clean(data.get("zip_code", "")),
        "Country":           _clean(data.get("country", "USA")),
        "QuoteValidDate":    _clean(data.get("quote_valid_through", "")),
        "CustomerNumber":    _clean(data.get("customer_number", "")),
        "manufacturer_Name": "",
        "PDF":               pdf_name,
        "DemoQuote":         "",
    }
    rows = []
    for item in data.get("line_items", []):
        row = {
            **base,
            "item_id":    _clean(item.get("item_id", "")),
            "item_desc":  _clean(item.get("item_desc", "")),
            "Unit Price": item.get("unit_price", ""),
            "TotalSales": item.get("extension", ""),
        }
        rows.append({h: row.get(h, "") for h in HEADERS})
    return rows


def _parse_quote_pdf(pdf_bytes):
    text = _pdf_to_text(pdf_bytes)
    if not text.strip():
        raise ValueError("No text could be extracted from this PDF")
    data     = _extract_with_groq(text)
    q_num    = _clean(data.get("quote_number", "unknown"))
    pdf_name = _make_pdf_name(q_num)
    rows     = _build_rows(data, pdf_name)
    if not rows:
        raise ValueError(f"No line items found in quote {q_num}")
    return rows, pdf_name


# ── Public entry points ───────────────────────────────────────────────────────

def process_pdf_file(uploaded_file):
    pdf_bytes = uploaded_file.read()
    rows, pdf_name = _parse_quote_pdf(pdf_bytes)
    return {"rows": rows, "pdfs": [(pdf_name, pdf_bytes)]}


def process_msg_file(uploaded_file):
    if extract_msg is None:
        raise RuntimeError("extract-msg is not installed")
    temp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(temp_dir, "input.msg")
    msg = None
    try:
        with open(tmp_path, "wb") as f:
            f.write(uploaded_file.read())
        msg = extract_msg.Message(tmp_path)
        rows, pdfs = [], []
        for att in msg.attachments:
            filename = att.longFilename or att.shortFilename or ""
            if not filename.lower().endswith(".pdf"):
                continue
            pdf_bytes = att.data
            if _is_quote_pdf(filename):
                pdf_rows, pdf_name = _parse_quote_pdf(pdf_bytes)
                rows.extend(pdf_rows)
                pdfs.append((pdf_name, pdf_bytes))
            else:
                pdfs.append((filename, pdf_bytes))
        if not rows:
            raise ValueError("No main quote PDF found (expected 'Quote XXXXXX.pdf')")
        return {"rows": rows, "pdfs": pdfs}
    finally:
        try:
            if msg: msg.close()
        except Exception: pass
        try: os.remove(tmp_path)
        except Exception: pass
        try: shutil.rmtree(temp_dir)
        except Exception: pass
        gc.collect()


def process_uploaded_file(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith(".msg"):   return process_msg_file(uploaded_file)
    if name.endswith(".pdf"):   return process_pdf_file(uploaded_file)
    raise ValueError("Please upload only .msg or .pdf files")
