"""
Quote data extraction using Groq API (llama-3.3-70b-versatile).
Parses PDF text → structured JSON → Excel rows.
"""

import re
import json
import logging
from io import BytesIO

from groq import Groq
from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams

log = logging.getLogger("cadre.extractor")

# ── Column schema (matches SampleCadre.xlsx exactly) ─────────────────────────
COLUMNS = [
    "ReferralManager", "ReferralEmail", "Brand", "QuoteNumber", "QuoteDate",
    "Company", "FirstName", "LastName", "ContactEmail", "ContactPhone",
    "Address", "County", "City", "State", "ZipCode", "Country",
    "item_id", "item_desc", "Unit Price", "TotalSales",
    "QuoteValidDate", "CustomerNumber", "manufacturer_Name", "PDF", "DemoQuote",
]

EXTRACTION_PROMPT = """You are a data extraction agent for Cadre Wire Group.
Extract ALL fields from this sales quote PDF text. Return ONLY valid JSON, no markdown.

Return a JSON object with:
- quote_number        (string)
- quote_date          (string MM/DD/YYYY)
- quote_valid_through (string MM/DD/YYYY)
- customer_number     (string, account number e.g. 100447)
- company             (string, customer company name)
- contact_first_name  (string)
- contact_last_name   (string)
- contact_email       (string or null)
- contact_phone       (string or null)
- address             (string, street only)
- city                (string)
- state               (string, 2-letter)
- zip_code            (string)
- country             (string, default "USA")
- salesperson         (string, full name)
- quote_description   (string or null)
- line_items (array):
    - item_id    (string, part number)
    - item_desc  (string, full description + stock notes)
    - ordered    (number)
    - unit_price (number, keep MFT rate as-is for cable)
    - extension  (number, line total)
- product_total (number)
- tax           (number)
- grand_total   (number)

Rules:
- Include Tax as a line item: item_id="Tax", item_desc="", ordered=1, unit_price=tax_amount, extension=tax_amount
- Keep MFT prices as-is (e.g. 4190.0 for cable priced per thousand feet)
- Return ONLY the JSON object

PDF TEXT:
"""

SALESPERSON_EMAIL = {
    "regina deavers":   "rdeavers@cadrewire.com",
    "dara august":      "daugust@cadrewire.com",
    "andrew smith":     "Asmith@cadrewire.com",
    "industrial sales": None,
}


def pdf_bytes_to_text(pdf_bytes: bytes) -> str:
    output = BytesIO()
    extract_text_to_fp(BytesIO(pdf_bytes), output, laparams=LAParams(), output_type="text")
    return output.getvalue().decode("utf-8", errors="ignore")


def extract_pdf_from_msg(msg_bytes: bytes) -> bytes | None:
    pdf_start = msg_bytes.find(b"%PDF-")
    if pdf_start == -1:
        return None
    eof_pos = msg_bytes.rfind(b"%%EOF", pdf_start)
    return msg_bytes[pdf_start: eof_pos + 5] if eof_pos > 0 else msg_bytes[pdf_start:]


def extract_sender_email(msg_bytes: bytes) -> str | None:
    hits = re.findall(rb"From:\s.*?<([a-zA-Z0-9._%+\-]+@cadrewire\.com)>", msg_bytes)
    if hits:
        return hits[0].decode()
    hits = re.findall(rb"([a-zA-Z0-9._%+\-]+@cadrewire\.com)", msg_bytes)
    return hits[0].decode() if hits else None


def extract_quote_with_groq(pdf_text: str, api_key: str, model: str = "llama-3.3-70b-versatile") -> dict:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + pdf_text}],
        temperature=0.0,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def _excel_date(date_str: str):
    from datetime import datetime
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date()
    except Exception:
        return date_str


def build_rows(data: dict, sender_email: str | None, quote_number: str) -> list[dict]:
    salesperson_name = (data.get("salesperson") or "").strip().lower()
    referral_email = sender_email or SALESPERSON_EMAIL.get(salesperson_name, "")

    base = {
        "ReferralManager":   None,
        "ReferralEmail":     referral_email,
        "Brand":             "Cadre Wire Group",
        "QuoteNumber":       data.get("quote_number"),
        "QuoteDate":         _excel_date(data.get("quote_date", "")),
        "Company":           data.get("company"),
        "FirstName":         data.get("contact_first_name"),
        "LastName":          data.get("contact_last_name"),
        "ContactEmail":      data.get("contact_email"),
        "ContactPhone":      data.get("contact_phone"),
        "Address":           data.get("address"),
        "County":            None,
        "City":              data.get("city"),
        "State":             data.get("state"),
        "ZipCode":           data.get("zip_code"),
        "Country":           data.get("country", "USA"),
        "QuoteValidDate":    _excel_date(data.get("quote_valid_through", "")),
        "CustomerNumber":    data.get("customer_number"),
        "manufacturer_Name": None,
        "PDF":               f"Cadre Wire Group_{quote_number}.pdf",
        "DemoQuote":         None,
    }

    rows = []
    for item in data.get("line_items", []):
        row = {**base,
               "item_id":    item.get("item_id"),
               "item_desc":  item.get("item_desc"),
               "Unit Price": item.get("unit_price"),
               "TotalSales": item.get("extension")}
        rows.append(row)
    return rows


def validate_rows(data: dict, rows: list[dict]) -> list[str]:
    issues = []
    if not data.get("quote_number"):
        issues.append("Missing quote number")
    product_rows = [r for r in rows if r.get("item_id") != "Tax"]
    if not product_rows:
        issues.append("No line items found")
    extracted_total = sum(r.get("TotalSales") or 0 for r in product_rows)
    pdf_total = data.get("product_total") or 0
    if pdf_total and abs(extracted_total - pdf_total) > 0.10:
        issues.append(f"Total mismatch: extracted ${extracted_total:.2f} vs PDF ${pdf_total:.2f}")
    return issues


def process_msg_bytes(
    msg_bytes: bytes,
    api_key: str,
    model: str = "llama-3.3-70b-versatile",
) -> dict:
    """
    Full pipeline: msg bytes → extracted rows + metadata.
    Returns dict with keys: rows, data, issues, sender_email, pdf_text
    """
    sender_email = extract_sender_email(msg_bytes)
    pdf_bytes = extract_pdf_from_msg(msg_bytes)

    if not pdf_bytes:
        return {"rows": [], "data": {}, "issues": ["No PDF found in .msg file"], "sender_email": sender_email, "pdf_text": ""}

    pdf_text = pdf_bytes_to_text(pdf_bytes)

    try:
        data = extract_quote_with_groq(pdf_text, api_key, model)
    except Exception as e:
        return {"rows": [], "data": {}, "issues": [f"AI extraction failed: {e}"], "sender_email": sender_email, "pdf_text": pdf_text}

    q_num = data.get("quote_number", "UNKNOWN")
    rows = build_rows(data, sender_email, q_num)
    issues = validate_rows(data, rows)

    return {
        "rows":         rows,
        "data":         data,
        "issues":       issues,
        "sender_email": sender_email,
        "pdf_text":     pdf_text,
    }
