"""
Cadre Wire Group — Quote Parser
Uses fitz word-position extraction (coordinate-based, two-column layout).
Pdfminer fallback for local use.
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


# ── Group fitz words into lines by Y coordinate ───────────────────────────────

def _group_into_lines(words, x_min=0, x_max=9999, y_min=0, y_max=9999, tol=3):
    """
    words: list of (x0, y0, x1, y1, text, ...)
    Returns list of (y, [text, text, ...]) sorted by y.
    """
    filtered = [w for w in words
                if x_min <= w[0] < x_max and y_min <= w[1] < y_max]
    if not filtered:
        return []
    filtered.sort(key=lambda w: (round(w[1] / tol), w[0]))
    lines = []
    cur_y_bucket, cur_texts = None, []
    for w in filtered:
        bucket = round(w[1] / tol)
        if cur_y_bucket is None or bucket == cur_y_bucket:
            cur_texts.append(w[4])
            cur_y_bucket = bucket
        else:
            lines.append((cur_y_bucket * tol, cur_texts))
            cur_texts, cur_y_bucket = [w[4]], bucket
    if cur_texts:
        lines.append((cur_y_bucket * tol, cur_texts))
    return lines


# ── FITZ parser ───────────────────────────────────────────────────────────────

def _parse_with_fitz(pdf_bytes):
    import fitz

    all_rows, all_pdfs_info = [], []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Collect all words across all pages
    all_words = []
    page_breaks = []
    y_offset = 0
    for page in doc:
        pw = page.get_text("words")
        for w in pw:
            all_words.append((w[0], w[1] + y_offset, w[2], w[3] + y_offset, w[4]))
        page_breaks.append(y_offset)
        y_offset += page.rect.height + 20  # small gap between pages

    words = all_words

    # Full text for regex searches
    full_text = " ".join(w[4] for w in sorted(words, key=lambda w: (round(w[1]/3), w[0])))

    # ── Header fields ─────────────────────────────────────────────────────────
    data = {
        "quote_number": "", "customer_number": "", "contact": "",
        "salesperson": "", "quote_date": "", "quote_valid": "",
        "company": "", "address": "", "city": "", "state": "",
        "zip_code": "", "country": "USA",
    }

    # Quote number — first 6-digit standalone number
    for w in sorted(words, key=lambda w: w[1]):
        if re.fullmatch(r"\d{6}", w[4]):
            data["quote_number"] = w[4]
            break

    # Customer number — 5-7 digit number near quote number
    found_quote = False
    for w in sorted(words, key=lambda w: w[1]):
        if w[4] == data["quote_number"]:
            found_quote = True
            continue
        if found_quote and re.fullmatch(r"\d{5,7}", w[4]):
            data["customer_number"] = w[4]
            break

    # Quote date
    for w in sorted(words, key=lambda w: w[1]):
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", w[4]):
            data["quote_date"] = w[4]
            break

    # Quote valid through — find "Through" word, then next date
    through_y = None
    for w in words:
        if w[4] == "Through":
            through_y = w[1]
            break
    if through_y is not None:
        dates_after = [w[4] for w in words
                       if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", w[4])
                       and w[1] >= through_y - 2]
        if dates_after:
            data["quote_valid"] = dates_after[0]

    # Contact — words on same Y as "Contact" label, to its right, x < 500
    for w in words:
        if w[4].lower().rstrip(":") == "contact":
            cy, cx1 = w[1], w[2]
            nearby = [ww[4] for ww in words
                      if abs(ww[1] - cy) < 4 and ww[0] > cx1 + 2 and ww[0] < 500
                      and ww[4].lower() not in {"contact","date","salesperson","customer","quote"}]
            if nearby:
                data["contact"] = " ".join(nearby)
                break

    # Salesperson — same approach
    for w in words:
        if w[4].lower() == "salesperson":
            sy, sx1 = w[1], w[2]
            nearby = [ww[4] for ww in words
                      if abs(ww[1] - sy) < 4 and ww[0] > sx1 + 2 and ww[0] < 500]
            if nearby:
                data["salesperson"] = " ".join(nearby)
                break

    # Company + address — left column below "Quoted For:"
    qf_y = None
    for i, w in enumerate(sorted(words, key=lambda w: w[1])):
        if w[4] == "For:" or (w[4] == "For" and i > 0):
            qf_y = w[1]
            break
        if "Quoted" in w[4]:
            qf_y = w[1]
            break

    if qf_y is not None:
        below_left = [(w[4], w[0], w[1]) for w in words
                      if w[1] > qf_y + 2 and w[1] < qf_y + 90 and w[0] < 310]
        below_left.sort(key=lambda b: (round(b[2]/3), b[1]))
        block_lines = []
        cur_y, cur = None, []
        for txt, x, y in below_left:
            bucket = round(y / 3)
            if cur_y is None or bucket == cur_y:
                cur.append(txt)
                cur_y = bucket
            else:
                block_lines.append(" ".join(cur))
                cur, cur_y = [txt], bucket
        if cur:
            block_lines.append(" ".join(cur))

        if block_lines:     data["company"] = block_lines[0]
        if len(block_lines) > 1: data["address"] = block_lines[1]
        if len(block_lines) > 2: _parse_city_line(block_lines[2], data)

    # ── Line items ────────────────────────────────────────────────────────────
    # Find Y of "Line" + "Item" header (same Y row)
    line_header_y = None
    line_words_on_row = {}
    for w in words:
        bucket = round(w[1] / 3)
        line_words_on_row.setdefault(bucket, []).append(w[4])

    for bucket, texts in sorted(line_words_on_row.items()):
        if "Line" in texts and "Item" in texts and "Ordered" in texts:
            line_header_y = bucket * 3
            break

    if line_header_y is None:
        raise ValueError(f"Could not find item table header in quote {data['quote_number']}")

    # Find Y of "Product" footer
    product_y = 999999
    for w in words:
        if w[4] == "Product" and w[1] > line_header_y + 10:
            product_y = w[1]
            break

    # Separate left (x < 370) and right (x >= 370) columns in item area
    item_area = [w for w in words if w[1] > line_header_y + 4 and w[1] < product_y]
    left_words  = [w for w in item_area if w[0] < 370]
    right_words = [w for w in item_area if w[0] >= 370]

    # Group left words into lines
    left_lines = _group_into_lines(left_words, x_min=0, x_max=370, tol=3)

    # Each item line: "1 HS.1635F1-C48"  (line_num + item_id on SAME Y row)
    # Description lines follow on subsequent rows (indented, x > 30)
    items = []
    current = None

    for y, texts in left_lines:
        line_str = " ".join(texts)

        # Check if line starts with a number followed by an item_id
        m = re.match(r"^(\d{1,3})\s+([A-Z][A-Z0-9./_\-]{2,})(.*)?$", line_str)
        if m:
            if current:
                items.append(current)
            item_id   = m.group(2)
            extra     = _clean(m.group(3)) if m.group(3) else ""
            current   = {"item_id": item_id, "desc_lines": [extra] if extra else [], "y_start": y}
        elif current is not None:
            # Description continuation line
            desc_line = _clean(line_str)
            if desc_line:
                current["desc_lines"].append(desc_line)

    if current:
        items.append(current)

    # Match each item to its right-column price/extension
    rows = []
    for i, item in enumerate(items):
        next_y = items[i + 1]["y_start"] if i + 1 < len(items) else product_y

        # Right column words for this item's Y range
        right_for_item = [w for w in right_words
                          if item["y_start"] - 2 <= w[1] < next_y - 2]
        right_for_item.sort(key=lambda w: w[0])

        nums = re.findall(r"[\d,]+\.\d+", " ".join(w[4] for w in right_for_item))
        price = float(nums[0].replace(",", ""))  if nums else 0.0
        ext   = float(nums[-1].replace(",", "")) if nums else 0.0

        desc = _clean(" ".join(item["desc_lines"]))
        rows.append({
            "item_id":    item["item_id"],
            "item_desc":  desc,
            "Unit Price": price,
            "TotalSales": ext,
        })

    # Tax row
    tax_amount = 0.0
    for w in words:
        if w[4] == "Tax" and w[1] > line_header_y:
            tax_nums = [ww[4] for ww in words
                        if abs(ww[1] - w[1]) < 4 and ww[0] > w[0]]
            for tn in tax_nums:
                if re.match(r"[\d,]+\.\d{2}$", tn):
                    val = float(tn.replace(",", ""))
                    if val > 0:
                        tax_amount = val
                    break
            break

    if tax_amount > 0:
        rows.append({"item_id": "Tax", "item_desc": "", "Unit Price": tax_amount, "TotalSales": tax_amount})

    if not rows:
        raise ValueError(f"No line items found in quote {data['quote_number']}")

    return data, rows


# ── PDFMINER fallback ─────────────────────────────────────────────────────────

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
            data["quote_number"] = line; break
    found = False
    for line in lines[:25]:
        if line == data["quote_number"]: found = True; continue
        if found and re.fullmatch(r"\d{5,7}", line): data["customer_number"] = line; break
    for i, line in enumerate(lines[:25]):
        if line == data["customer_number"] and data["customer_number"]:
            if i+1 < len(lines): data["contact"]     = lines[i+1]
            if i+2 < len(lines): data["salesperson"] = lines[i+2]
            break
    for line in lines[:35]:
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", line):
            data["quote_date"] = line; break
    m = re.search(r"Quote Good Through\s*\n+\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if not m: m = re.search(r"Quote Good Through\s+(\d{1,2}/\d{1,2}/\d{4})", text)
    if m: data["quote_valid"] = m.group(1)
    m2 = re.search(r"Quoted For:\n+(.+?)\n+(.+?)\n+(.+?)(?:\n|$)", text)
    if m2:
        data["company"] = _clean(m2.group(1))
        data["address"] = _clean(m2.group(2))
        _parse_city_line(_clean(m2.group(3)), data)

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
        try:
            data, line_items = _parse_with_fitz(pdf_bytes)
        except Exception:
            # fitz parsing failed — try pdfminer if available
            try:
                data, line_items = _parse_with_pdfminer(pdf_bytes)
            except ImportError:
                raise
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
