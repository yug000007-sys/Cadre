"""
Cadre Wire Group — Quote Parser
Pure regex + pdfminer/pymupdf. No AI/API required.
"""

import re
import os
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


def _clean(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()


def _referral_email(salesperson):
    return SALESPERSON_MAP.get(_clean(salesperson).lower(), "rdeavers@cadrewire.com")


def _make_pdf_name(quote_number):
    s = _clean(quote_number)
    return f"Cadre Wire Group_{s}.pdf" if s else "Cadre Wire Group_unknown.pdf"


def _is_quote_pdf(filename):
    return bool(re.match(r"quote\s*\d+", filename.lower().strip()))


# ── Header parsing ────────────────────────────────────────────────────────────

def _parse_header(text):
    lines = [_clean(l) for l in text.splitlines() if _clean(l)]

    data = {
        "quote_number": "", "customer_number": "", "contact": "",
        "salesperson": "", "quote_date": "", "quote_valid": "",
        "company": "", "address": "", "city": "", "state": "",
        "zip_code": "", "country": "USA",
    }

    # Quote number — first standalone 6-digit number
    for line in lines[:25]:
        if re.fullmatch(r"\d{6}", line):
            data["quote_number"] = line
            break

    # Customer number — next standalone 5-7 digit number after quote number
    found = False
    for line in lines[:25]:
        if line == data["quote_number"]:
            found = True
            continue
        if found and re.fullmatch(r"\d{5,7}", line):
            data["customer_number"] = line
            break

    # Contact + Salesperson — two lines after customer number
    for i, line in enumerate(lines[:25]):
        if line == data["customer_number"] and data["customer_number"]:
            if i + 1 < len(lines): data["contact"]     = lines[i + 1]
            if i + 2 < len(lines): data["salesperson"] = lines[i + 2]
            break

    # Quote date
    for line in lines[:35]:
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", line):
            data["quote_date"] = line
            break

    # Quote valid through
    m = re.search(r"Quote Good Through\s*\n+\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if not m:
        m = re.search(r"Quote Good Through\s+(\d{1,2}/\d{1,2}/\d{4})", text)
    if m:
        data["quote_valid"] = m.group(1)

    # Company + address from "Quoted For:" block
    m2 = re.search(r"Quoted For:\n+(.+?)\n+(.+?)\n+(.+?)(?:\n|$)", text)
    if m2:
        data["company"] = _clean(m2.group(1))
        data["address"] = _clean(m2.group(2))
        _parse_city_line(_clean(m2.group(3)), data)

    return data


def _parse_city_line(line, data):
    if line in ("United States of America", "United States"):
        data["country"] = "USA"
        return
    # "Houston, TX 77040" or "Little Rock, AR 72209"
    m = re.match(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$", line)
    if m:
        data["city"]     = m.group(1).strip()
        data["state"]    = m.group(2)
        data["zip_code"] = m.group(3)
        data["country"]  = "USA"
        return
    # Canada
    m2 = re.match(r"^(.+?)\s+([A-Z]{2})\s+CANADA\s+([A-Z]\d[A-Z]\s?\d[A-Z]\d)$", line, re.I)
    if m2:
        data["city"]     = m2.group(1).strip()
        data["state"]    = m2.group(2)
        data["zip_code"] = m2.group(3)
        data["country"]  = "CANADA"


# ── Line item parsing ─────────────────────────────────────────────────────────

def _parse_line_items(text):
    # Items: line# \n\n item_id \n description (double newlines between number and id)
    # Normalize: collapse 3+ newlines to 2, so pattern works with both fitz and pdfminer
    text_norm = re.sub(r"\n{3,}", "\n\n", text)

    item_matches = list(re.finditer(
        r"(?<!\d)(\d{1,3})\n+([A-Z][A-Z0-9./_\-]{2,})\n(.*?)(?=\n+\d{1,3}\n+[A-Z][A-Z0-9./_\-]{2,}\n|\n+Ordered\n|\n+Product\n|\n+Page\n|\Z)",
        text_norm, re.DOTALL
    ))
    if not item_matches:
        # Last resort: try on single-newline normalized text
        text_single = re.sub(r"\n+", "\n", text)
        item_matches = list(re.finditer(
            r"(?<!\d)(\d{1,3})\n([A-Z][A-Z0-9./_\-]{2,})\n(.*?)(?=\n\d{1,3}\n[A-Z][A-Z0-9./_\-]{2,}\n|\nOrdered\n|\nProduct\n|\nPage\n|\Z)",
            text_single, re.DOTALL
        ))

    # Qty/price/extension triplets: "4 FT\n\n0.80000\n\nFT\n\n3.20"
    qty_matches = re.findall(
        r"(\d[\d,]*)\s+(FT|EAC|MFT|LOT|EA|PR)\s+([\d,]+\.\d+)\s+(?:FT|EAC|MFT|LOT|EA|PR)\s+([\d,]+\.\d{2})",
        text
    )

    # Tax — pattern: "Product\nTax\n\nTotal\n\n{product}\n{tax}\n{grand}"
    tax_amount = 0.0
    m_tax = re.search(r"Product\nTax\n+Total\n+([\d,]+\.\d{2})\n+([\d,]+\.\d{2})", text)
    if m_tax:
        tax_amount = float(m_tax.group(2).replace(",", ""))

    rows = []
    for i, m in enumerate(item_matches):
        item_id   = _clean(m.group(2))
        item_desc = _clean(re.sub(r"\s+", " ", m.group(3)))

        if i < len(qty_matches):
            _, _, price_raw, ext_raw = qty_matches[i]
            price = float(price_raw.replace(",", ""))
            ext   = float(ext_raw.replace(",", ""))
        else:
            price, ext = 0.0, 0.0

        rows.append({
            "item_id":    item_id,
            "item_desc":  item_desc,
            "Unit Price": price,
            "TotalSales": ext,
        })

    if tax_amount > 0:
        rows.append({
            "item_id":    "Tax",
            "item_desc":  "",
            "Unit Price": tax_amount,
            "TotalSales": tax_amount,
        })

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def _parse_quote_pdf(pdf_bytes):
    text       = _pdf_to_text(pdf_bytes)
    header     = _parse_header(text)
    line_items = _parse_line_items(text)

    if not line_items:
        raise ValueError(f"No line items found in quote {header.get('quote_number', '?')}")

    q_num    = header["quote_number"]
    pdf_name = _make_pdf_name(q_num)

    parts = header["contact"].split()
    first = parts[0] if parts else ""
    last  = " ".join(parts[1:]) if len(parts) > 1 else ""

    base = {
        "ReferralManager":   "",
        "ReferralEmail":     _referral_email(header["salesperson"]),
        "Brand":             "Cadre Wire Group",
        "QuoteNumber":       q_num,
        "QuoteDate":         header["quote_date"],
        "Company":           header["company"],
        "FirstName":         first,
        "LastName":          last,
        "ContactEmail":      "",
        "ContactPhone":      "",
        "Address":           header["address"],
        "County":            "",
        "City":              header["city"],
        "State":             header["state"],
        "ZipCode":           header["zip_code"],
        "Country":           header["country"],
        "QuoteValidDate":    header["quote_valid"],
        "CustomerNumber":    header["customer_number"],
        "manufacturer_Name": "",
        "PDF":               pdf_name,
        "DemoQuote":         "",
    }

    rows = []
    for item in line_items:
        row = {**base, **item}
        rows.append({h: row.get(h, "") for h in HEADERS})

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
