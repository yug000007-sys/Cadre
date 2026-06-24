"""
Cadre Wire Group — Quote Parser
Uses fitz word-position extraction to handle the two-column PDF layout.
Falls back to pdfminer line-based parsing if fitz unavailable.
"""

import re
import os
import tempfile
import shutil
import gc

try:
    import extract_msg
except Exception:
    extract_msg = None

# ─────────────────────────────────────────────────────────────────────────────
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


def _make_pdf_name(q):
    s = _clean(q)
    return f"Cadre Wire Group_{s}.pdf" if s else "Cadre Wire Group_unknown.pdf"


def _is_quote_pdf(filename):
    return bool(re.match(r"quote\s*\d+", filename.lower().strip()))


def _parse_city_line(line, data):
    line = _clean(line)
    if line in ("United States of America", "United States"):
        data["country"] = "USA"
        return
    m = re.match(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$", line)
    if m:
        data["city"]     = m.group(1).strip()
        data["state"]    = m.group(2)
        data["zip_code"] = m.group(3)
        data["country"]  = "USA"
        return
    m2 = re.match(r"^(.+?)\s+([A-Z]{2})\s+CANADA\s+([A-Z]\d[A-Z]\s?\d[A-Z]\d)$", line, re.I)
    if m2:
        data["city"]     = m2.group(1).strip()
        data["state"]    = m2.group(2)
        data["zip_code"] = m2.group(3)
        data["country"]  = "CANADA"


# ── FITZ (pymupdf) parser — coordinate-based ──────────────────────────────────

def _parse_with_fitz(pdf_bytes):
    import fitz

    doc   = fitz.open(stream=pdf_bytes, filetype="pdf")
    page0 = doc[0]

    # ── words: (x0, y0, x1, y1, text, block, line, word) ────────────────────
    words = page0.get_text("words")

    def words_in_band(x_min, x_max, y_min=0, y_max=99999):
        return [(w[4], w[1]) for w in words
                if x_min <= w[0] <= x_max and y_min <= w[1] <= y_max]

    def line_text(ws, y_tol=3):
        """Group words into lines by Y coordinate."""
        if not ws:
            return []
        ws = sorted(ws, key=lambda w: (round(w[1] / y_tol), w[0]))
        lines, cur_y, cur = [], None, []
        for txt, y in ws:
            bucket = round(y / y_tol)
            if cur_y is None or bucket == cur_y:
                cur.append(txt)
                cur_y = bucket
            else:
                lines.append(" ".join(cur))
                cur, cur_y = [txt], bucket
        if cur:
            lines.append(" ".join(cur))
        return lines

    # Header block — right side of page (x > 350)
    right_words = [(w[4], w[0], w[1]) for w in words if w[0] > 350]
    right_words.sort(key=lambda w: (round(w[2] / 3), w[1]))

    data = {
        "quote_number": "", "customer_number": "", "contact": "",
        "salesperson": "", "quote_date": "", "quote_valid": "",
        "company": "", "address": "", "city": "", "state": "",
        "zip_code": "", "country": "USA",
    }

    # Full page words for regex searches
    all_text = "\n".join(w[4] for w in sorted(words, key=lambda w: (round(w[1]/3), w[0])))
    full_text = page0.get_text("text")

    # Quote number
    m = re.search(r"\b(\d{6})\b", full_text)
    if m:
        data["quote_number"] = m.group(1)

    # Customer number — 5-7 digit number that appears near Quote number
    nums = re.findall(r"\b(\d{5,7})\b", full_text)
    seen_quote = False
    for n in nums:
        if n == data["quote_number"]:
            seen_quote = True
            continue
        if seen_quote and n != data["quote_number"]:
            data["customer_number"] = n
            break

    # Quote date
    m2 = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", full_text)
    if m2:
        data["quote_date"] = m2.group(1)

    # Quote valid through
    m3 = re.search(r"Quote\s+Good\s+Through\s+(\d{1,2}/\d{1,2}/\d{4})", full_text)
    if m3:
        data["quote_valid"] = m3.group(1)

    # Contact — words on same Y line as "Contact" label, to its right but left of center
    for w in words:
        if w[4].lower().rstrip(":") == "contact":
            contact_y  = w[1]
            contact_x1 = w[2]
            nearby = [ww[4] for ww in words
                      if abs(ww[1] - contact_y) < 4
                      and ww[0] > contact_x1 + 2
                      and ww[0] < 500
                      and ww[4].lower() not in {"contact", "date", "salesperson", "customer", "quote"}]
            data["contact"] = " ".join(nearby)
            break

    # Salesperson — same approach
    for w in words:
        if w[4].lower() == "salesperson":
            sp_y  = w[1]
            sp_x1 = w[2]
            nearby = [ww[4] for ww in words
                      if abs(ww[1] - sp_y) < 4
                      and ww[0] > sp_x1 + 2
                      and ww[0] < 500]
            data["salesperson"] = " ".join(nearby)
            break

    # Company + address — left column below "Quoted For:"
    for i, w in enumerate(words):
        if "Quoted" in w[4] and i + 1 < len(words) and words[i+1][4] == "For:":
            qf_y = w[1]
            # Get lines just below Quoted For label, left side only (x < 300)
            below = [(ww[4], ww[0], ww[1]) for ww in words
                     if ww[1] > qf_y + 2 and ww[1] < qf_y + 80 and ww[0] < 310]
            below.sort(key=lambda b: (round(b[2]/3), b[1]))

            # Group into lines
            block_lines = []
            cur_y, cur = None, []
            for txt, x, y in below:
                bucket = round(y / 3)
                if cur_y is None or bucket == cur_y:
                    cur.append(txt)
                    cur_y = bucket
                else:
                    block_lines.append(" ".join(cur))
                    cur, cur_y = [txt], bucket
            if cur:
                block_lines.append(" ".join(cur))

            if block_lines:
                data["company"] = block_lines[0]
            if len(block_lines) > 1:
                data["address"] = block_lines[1]
            if len(block_lines) > 2:
                _parse_city_line(block_lines[2], data)
            break

    # ── Line items ────────────────────────────────────────────────────────────
    # Find Y of "Line  Item" header row
    line_header_y = None
    for w in words:
        if w[4] == "Line":
            # Check if "Item" is nearby on same row
            for w2 in words:
                if w2[4] == "Item" and abs(w2[1] - w[1]) < 4 and w2[0] > w[0]:
                    line_header_y = w[1]
                    break
            if line_header_y:
                break

    # Find Y of "Product" (end of items)
    product_y = 99999
    for w in words:
        if w[4] == "Product" and w[1] > (line_header_y or 0):
            product_y = w[1]
            break

    if line_header_y is None:
        raise ValueError("Could not find line item table in PDF")

    # Words in item area
    item_words = [w for w in words
                  if w[1] > line_header_y + 5 and w[1] < product_y]

    # LEFT column: x < 350 → line numbers, item_ids, descriptions
    # RIGHT column: x > 350 → qty, unit, price, unit, extension

    left  = [(w[4], w[0], w[1]) for w in item_words if w[0] < 350]
    right = [(w[4], w[0], w[1]) for w in item_words if w[0] >= 350]

    left.sort(key=lambda w: (round(w[2]/3), w[1]))
    right.sort(key=lambda w: (round(w[2]/3), w[1]))

    # Group left into lines
    def group_lines(ws, tol=3):
        if not ws:
            return []
        ws = sorted(ws, key=lambda w: (round(w[2]/tol), w[1]))
        lines, cur_y, cur = [], None, []
        for txt, x, y in ws:
            bucket = round(y / tol)
            if cur_y is None or bucket == cur_y:
                cur.append((txt, x))
                cur_y = bucket
            else:
                lines.append((cur, cur_y * tol))
                cur, cur_y = [(txt, x)], bucket
        if cur:
            lines.append((cur, cur_y * tol))
        return lines

    left_lines  = group_lines(left)
    right_lines = group_lines(right)

    # Parse item blocks from left column
    # A new item starts with a line containing only a number
    items = []
    current = None
    for words_in_line, y in left_lines:
        line_text_str = " ".join(t for t, x in words_in_line)
        is_line_num = bool(re.fullmatch(r"\d{1,3}", line_text_str.strip()))
        looks_like_item_id = bool(re.match(r"[A-Z][A-Z0-9./_\-]{2,}", words_in_line[0][0])) if words_in_line else False

        if is_line_num:
            if current:
                items.append(current)
            current = {"line_num": int(line_text_str.strip()), "item_id": "", "desc_lines": [], "y": y}
        elif current is not None:
            if not current["item_id"] and looks_like_item_id:
                current["item_id"] = line_text_str.strip()
            else:
                current["desc_lines"].append(line_text_str)

    if current:
        items.append(current)

    # Parse price/qty/extension from right column
    # Pattern per item row: QTY UNIT  PRICE UNIT  EXTENSION
    # Right column words grouped by Y proximity to each item
    def find_right_for_item(item_y, next_y):
        row_words = [(t, x, y) for t, x, y in right if item_y - 2 <= y <= next_y - 2]
        row_words.sort(key=lambda w: w[1])  # left to right
        return [t for t, x, y in row_words]

    rows = []
    for i, item in enumerate(items):
        next_y = items[i+1]["y"] if i + 1 < len(items) else product_y
        right_row = find_right_for_item(item["y"], next_y)

        # Extract price and extension from right_row
        # Pattern: QTY UNIT PRICE UNIT EXTENSION
        price, ext = 0.0, 0.0
        nums_found = re.findall(r"[\d,]+\.\d+", " ".join(right_row))
        if len(nums_found) >= 2:
            price = float(nums_found[0].replace(",", ""))
            ext   = float(nums_found[-1].replace(",", ""))
        elif len(nums_found) == 1:
            price = ext = float(nums_found[0].replace(",", ""))

        desc = _clean(" ".join(item["desc_lines"]))
        rows.append({
            "item_id":    item["item_id"],
            "item_desc":  desc,
            "Unit Price": price,
            "TotalSales": ext,
        })

    # Tax
    tax_amount = 0.0
    for i, w in enumerate(words):
        if w[4] == "Tax" and w[1] > line_header_y:
            # Find number to the right on same line
            tax_nums = [ww[4] for ww in words
                        if abs(ww[1] - w[1]) < 4 and ww[0] > w[0]]
            for tn in tax_nums:
                m_t = re.match(r"[\d,]+\.\d{2}", tn)
                if m_t:
                    tax_amount = float(m_t.group().replace(",", ""))
                    break
            break

    if tax_amount > 0:
        rows.append({"item_id": "Tax", "item_desc": "", "Unit Price": tax_amount, "TotalSales": tax_amount})

    if not rows:
        raise ValueError(f"No line items found in quote {data['quote_number']}")

    return data, rows


# ── PDFMINER fallback parser ──────────────────────────────────────────────────

def _parse_with_pdfminer(pdf_bytes):
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    from io import BytesIO

    out = BytesIO()
    extract_text_to_fp(BytesIO(pdf_bytes), out, laparams=LAParams(), output_type="text")
    text = out.getvalue().decode("utf-8", errors="ignore")
    lines = [_clean(l) for l in text.splitlines() if _clean(l)]

    data = {
        "quote_number": "", "customer_number": "", "contact": "",
        "salesperson": "", "quote_date": "", "quote_valid": "",
        "company": "", "address": "", "city": "", "state": "",
        "zip_code": "", "country": "USA",
    }

    for line in lines[:25]:
        if re.fullmatch(r"\d{6}", line):
            data["quote_number"] = line
            break
    found = False
    for line in lines[:25]:
        if line == data["quote_number"]:
            found = True; continue
        if found and re.fullmatch(r"\d{5,7}", line):
            data["customer_number"] = line; break
    for i, line in enumerate(lines[:25]):
        if line == data["customer_number"] and data["customer_number"]:
            if i+1 < len(lines): data["contact"]     = lines[i+1]
            if i+2 < len(lines): data["salesperson"] = lines[i+2]
            break
    for line in lines[:35]:
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", line):
            data["quote_date"] = line; break
    m = re.search(r"Quote Good Through\s*\n+\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if not m:
        m = re.search(r"Quote Good Through\s+(\d{1,2}/\d{1,2}/\d{4})", text)
    if m:
        data["quote_valid"] = m.group(1)
    m2 = re.search(r"Quoted For:\n+(.+?)\n+(.+?)\n+(.+?)(?:\n|$)", text)
    if m2:
        data["company"] = _clean(m2.group(1))
        data["address"] = _clean(m2.group(2))
        _parse_city_line(_clean(m2.group(3)), data)

    # Normalize newlines for item pattern
    text_norm = re.sub(r"\n{3,}", "\n\n", text)
    item_matches = list(re.finditer(
        r"(?<!\d)(\d{1,3})\n+([A-Z][A-Z0-9./_\-]{2,})\n(.*?)(?=\n+\d{1,3}\n+[A-Z][A-Z0-9./_\-]{2,}\n|\n+Ordered\n|\n+Product\n|\n+Page\n|\Z)",
        text_norm, re.DOTALL
    ))
    qty_matches = re.findall(
        r"(\d[\d,]*)\s+(FT|EAC|MFT|LOT|EA|PR)\s+([\d,]+\.\d+)\s+(?:FT|EAC|MFT|LOT|EA|PR)\s+([\d,]+\.\d{2})",
        text
    )
    rows = []
    for i, m3 in enumerate(item_matches):
        price, ext = 0.0, 0.0
        if i < len(qty_matches):
            price = float(qty_matches[i][2].replace(",", ""))
            ext   = float(qty_matches[i][3].replace(",", ""))
        rows.append({
            "item_id":    _clean(m3.group(2)),
            "item_desc":  _clean(re.sub(r"\s+", " ", m3.group(3))),
            "Unit Price": price,
            "TotalSales": ext,
        })
    m_tax = re.search(r"Product\nTax\n+Total\n+([\d,]+\.\d{2})\n+([\d,]+\.\d{2})", text)
    if m_tax:
        tax = float(m_tax.group(2).replace(",", ""))
        if tax > 0:
            rows.append({"item_id": "Tax", "item_desc": "", "Unit Price": tax, "TotalSales": tax})

    if not rows:
        raise ValueError(f"No line items found in quote {data['quote_number']}")
    return data, rows


# ── Main parse ────────────────────────────────────────────────────────────────

def _parse_quote_pdf(pdf_bytes):
    try:
        import fitz  # noqa
        data, line_items = _parse_with_fitz(pdf_bytes)
    except ImportError:
        data, line_items = _parse_with_pdfminer(pdf_bytes)

    q_num    = data["quote_number"]
    pdf_name = _make_pdf_name(q_num)
    parts    = data["contact"].split()
    first    = parts[0] if parts else ""
    last     = " ".join(parts[1:]) if len(parts) > 1 else ""

    base = {
        "ReferralManager":   "",
        "ReferralEmail":     _referral_email(data["salesperson"]),
        "Brand":             "Cadre Wire Group",
        "QuoteNumber":       q_num,
        "QuoteDate":         data["quote_date"],
        "Company":           data["company"],
        "FirstName":         first,
        "LastName":          last,
        "ContactEmail":      "",
        "ContactPhone":      "",
        "Address":           data["address"],
        "County":            "",
        "City":              data["city"],
        "State":             data["state"],
        "ZipCode":           data["zip_code"],
        "Country":           data["country"],
        "QuoteValidDate":    data["quote_valid"],
        "CustomerNumber":    data["customer_number"],
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
