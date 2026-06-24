"""
Cadre Wire Group — Quote Parser
Mirrors Sheffer pattern: extract-msg + pymupdf + Groq AI
"""

import re
import os
import json
import tempfile
import shutil
import gc
from datetime import datetime

import fitz

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


# ── Salesperson email map ─────────────────────────────────────────────────────
SALESPERSON_MAP = {
    "regina deavers":   "rdeavers@cadrewire.com",
    "dara august":      "daugust@cadrewire.com",
    "andrew smith":     "Asmith@cadrewire.com",
    "industrial sales": "rdeavers@cadrewire.com",
}

# ── Column headers (exact match to SampleCadre.xlsx) ─────────────────────────
HEADERS = [
    "ReferralManager",
    "ReferralEmail",
    "Brand",
    "QuoteNumber",
    "QuoteDate",
    "Company",
    "FirstName",
    "LastName",
    "ContactEmail",
    "ContactPhone",
    "Address",
    "County",
    "City",
    "State",
    "ZipCode",
    "Country",
    "item_id",
    "item_desc",
    "Unit Price",
    "TotalSales",
    "QuoteValidDate",
    "CustomerNumber",
    "manufacturer_Name",
    "PDF",
    "DemoQuote",
]

# ── Groq extraction prompt ────────────────────────────────────────────────────
PROMPT = """You are a data extraction agent for Cadre Wire Group.
Extract ALL fields from this sales quote PDF text. Return ONLY valid JSON, no markdown.

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
  "address":             "string",
  "city":                "string",
  "state":               "string",
  "zip_code":            "string",
  "country":             "string",
  "salesperson":         "string",
  "line_items": [
    {
      "item_id":    "string",
      "item_desc":  "string",
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
- Return ONLY raw JSON, no fences

PDF TEXT:
"""


def _clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def _pdf_to_text(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text("text") for page in doc)


def _make_pdf_name(quote_number):
    safe = _clean(quote_number)
    return f"Cadre Wire Group_{safe}.pdf" if safe else "Cadre Wire Group_unknown.pdf"


def _referral_email(salesperson):
    return SALESPERSON_MAP.get(_clean(salesperson).lower(), "rdeavers@cadrewire.com")


def _extract_with_groq(pdf_text):
    from groq import Groq
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


def _build_rows(data, pdf_name, received_datetime=""):
    salesperson = _clean(data.get("salesperson", ""))
    referral_email = _referral_email(salesperson)

    base = {
        "ReferralManager":   "",
        "ReferralEmail":     referral_email,
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


def _process_pdf_bytes(pdf_bytes, received_datetime=""):
    text = _pdf_to_text(pdf_bytes)
    if not text.strip():
        raise ValueError("No text could be extracted from this PDF (may be a scanned image)")

    data = _extract_with_groq(text)
    q_num = _clean(data.get("quote_number", "unknown"))
    pdf_name = _make_pdf_name(q_num)
    rows = _build_rows(data, pdf_name, received_datetime)

    if not rows:
        raise ValueError(f"No line items found in quote {q_num}")

    return rows, pdf_name


# ── Public entry points (same interface as sheffer_d.py) ─────────────────────

def process_pdf_file(uploaded_file):
    pdf_bytes = uploaded_file.read()
    rows, pdf_name = _process_pdf_bytes(pdf_bytes)
    return {"rows": rows, "pdfs": [(pdf_name, pdf_bytes)]}


def process_msg_file(uploaded_file):
    if extract_msg is None:
        raise RuntimeError("extract-msg is not installed — add it to requirements.txt")

    temp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(temp_dir, "input.msg")
    msg = None

    try:
        with open(tmp_path, "wb") as f:
            f.write(uploaded_file.read())

        msg = extract_msg.Message(tmp_path)

        # Get received date from MSG
        received_datetime = ""
        try:
            if msg.date:
                received_datetime = str(msg.date)
        except Exception:
            pass

        rows = []
        pdfs = []

        for att in msg.attachments:
            filename = att.longFilename or att.shortFilename or ""
            if filename.lower().endswith(".pdf"):
                pdf_bytes = att.data
                pdf_rows, pdf_name = _process_pdf_bytes(pdf_bytes, received_datetime)
                rows.extend(pdf_rows)
                pdfs.append((pdf_name, pdf_bytes))

        if not rows:
            raise ValueError("No PDF attachments found in this MSG file")

        return {"rows": rows, "pdfs": pdfs}

    finally:
        try:
            if msg is not None:
                msg.close()
        except Exception:
            pass
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass
        gc.collect()


def process_uploaded_file(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith(".msg"):
        return process_msg_file(uploaded_file)
    if name.endswith(".pdf"):
        return process_pdf_file(uploaded_file)
    raise ValueError("Please upload only .msg or .pdf files")
