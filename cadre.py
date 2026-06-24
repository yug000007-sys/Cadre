"""
Cadre Wire Group — Quote Parser
Coordinate-based fitz parser + pdfminer fallback.
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


def _is_city_state_zip(line):
    """Check if a line looks like a city/state/zip."""
    return bool(re.search(r"\b[A-Z]{2}\s+\d{5}\b", line) or
                re.search(r"\bCANADA\b", line, re.I))


def _words_to_lines(words, x_min=0, x_max=9999, y_min=0, y_max=9999, tol=3):
    """Group fitz words into text lines, filtered by bounding box."""
    filtered = [w for w in words
                if x_min <= w[0] < x_max and y_min <= w[1] < y_max]
    if not filtered:
        return []
    filtered.sort(key=lambda w: (round(w[1] / tol), w[0]))
    lines, cur_bucket, cur = [], None, []
    for w in filtered:
        b = round(w[1] / tol)
        if cur_bucket is None or b == cur_bucket:
            cur.append((w[4], w[0], w[1]))
            cur_bucket = b
        else:
            lines.append((cur_bucket * tol, cur))
            cur, cur_bucket = [(w[4], w[0], w[1])], b
    if cur:
        lines.append((cur_bucket * tol, cur))
    return lines  # list of (y, [(text, x, y), ...])


# ── FITZ parser ───────────────────────────────────────────────────────────────

def _parse_with_fitz(pdf_bytes):
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Collect words from ALL pages with cumulative Y offset
    all_words = []
    y_offset  = 0.0
    for page in doc:
        for w in page.get_text("words"):
            all_words.append((w[0], w[1] + y_offset, w[2], w[3] + y_offset, w[4]))
        y_offset += page.rect.height + 20

    words = all_words

    # ── Scalar fields via regex on full text ──────────────────────────────────
    full = " ".join(w[4] for w in sorted(words, key=lambda w: (round(w[1]/3), w[0])))

    data = {
        "quote_number": "", "customer_number": "", "contact": "",
        "salesperson": "", "quote_date": "", "quote_valid": "",
        "company": "", "address": "", "city": "", "state": "",
        "zip_code": "", "country": "USA",
    }

    # Quote number — first 6-digit number
    for w in sorted(words, key=lambda w: w[1]):
        if re.fullmatch(r"\d{6}", w[4]):
            data["quote_number"] = w[4]
            break

    # Customer number — next 5-7 digit number after quote number
    found = False
    for w in sorted(words, key=lambda w: w[1]):
        if w[4] == data["quote_number"]: found = True; continue
        if found and re.fullmatch(r"\d{5,7}", w[4]):
            data["customer_number"] = w[4]; break

    # Quote date — first MM/DD/YYYY
    for w in sorted(words, key=lambda w: w[1]):
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", w[4]):
            data["quote_date"] = w[4]; break

    # Quote valid through — find "Through" word then next date
    for w in sorted(words, key=lambda w: w[1]):
        if w[4] == "Through":
            through_y = w[1]
            candidates = [ww[4] for ww in words
                          if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", ww[4])
                          and ww[1] >= through_y - 2]
            if candidates:
                data["quote_valid"] = candidates[0]
            break

    # Contact — words right of "Contact" label on same Y, x < 500
    for w in words:
        if w[4].lower().rstrip(":") == "contact":
            cy, cx1 = w[1], w[2]
            nearby = [ww[4] for ww in words
                      if abs(ww[1] - cy) < 4 and ww[0] > cx1 + 2 and ww[0] < 500
                      and ww[4].lower() not in {"contact","date","salesperson","customer","quote"}]
            if nearby:
                data["contact"] = " ".join(nearby); break

    # Salesperson
    for w in words:
        if w[4].lower() == "salesperson":
            sy, sx1 = w[1], w[2]
            nearby = [ww[4] for ww in words
                      if abs(ww[1] - sy) < 4 and ww[0] > sx1 + 2 and ww[0] < 500]
            if nearby:
                data["salesperson"] = " ".join(nearby); break

    # ── Quoted For block ──────────────────────────────────────────────────────
    # Find "Quoted" word (part of "Quoted For:" label)
    qf_word = None
    for w in sorted(words, key=lambda w: w[1]):
        if w[4] == "Quoted":
            qf_word = w; break

    if qf_word:
        qf_y  = qf_word[1]
        qf_x0 = qf_word[0]

        # LEFT side only (x < 300) — excludes Ship To block on the right
        # Y range: same line as "Quoted For:" down to ~80px below
        left_block = _words_to_lines(words, x_min=qf_x0 + 40, x_max=300,
                                     y_min=qf_y - 2, y_max=qf_y + 90)

        # Company is on the SAME Y line as "Quoted For:"
        # subsequent lines are address / optional contact name / city
        block_texts = []
        for y, word_list in left_block:
            text = _clean(" ".join(t for t, x, _ in word_list))
            if text and text not in ("Quoted", "For:", "For"):
                block_texts.append(text)

        if block_texts:
            data["company"] = block_texts[0]

        # Remaining lines: skip lines that look like a person's name (contact),
        # find address and city/state/zip
        remaining = block_texts[1:]
        for line in remaining:
            if _is_city_state_zip(line):
                _parse_city_line(line, data)
            elif not data["address"] and not _is_city_state_zip(line):
                # Could be contact name or address
                # If it looks like a name (no numbers, no street keywords), skip it
                looks_like_name = (
                    re.match(r"^[A-Z][a-z]+ [A-Z][A-Z]+$", line) or  # ALL-CAPS last name
                    re.match(r"^[A-Z]+ [A-Z]+$", line)                # all caps name
                )
                if looks_like_name:
                    continue  # skip — it's a contact name line
                data["address"] = line

    # ── Line items ────────────────────────────────────────────────────────────
    # Find Y of "Line Item Ordered Price Extension" header row
    line_header_y = None
    row_buckets   = {}
    for w in words:
        b = round(w[1] / 3)
        row_buckets.setdefault(b, []).append(w[4])
    for b, texts in sorted(row_buckets.items()):
        if "Line" in texts and "Item" in texts and "Ordered" in texts:
            line_header_y = b * 3; break

    if line_header_y is None:
        raise ValueError(f"Could not find item table in quote {data['quote_number']}")

    # Find Y of "Product" footer
    product_y = 999999
    for w in words:
        if w[4] == "Product" and w[1] > line_header_y + 10:
            product_y = w[1]; break

    item_area   = [w for w in words if w[1] > line_header_y + 4 and w[1] < product_y]
    left_words  = [w for w in item_area if w[0] < 370]
    right_words = [w for w in item_area if w[0] >= 370]

    left_lines = _words_to_lines(left_words, x_max=370, tol=3)

    # Each item: "1 HS.1635F1-C48" on one line, descriptions on following lines
    items = []
    current = None
    for y, word_list in left_lines:
        line_str = _clean(" ".join(t for t, x, _ in word_list))

        # New item: starts with number + item_id pattern
        m = re.match(r"^(\d{1,3})\s+([A-Z][A-Z0-9./_\-]{2,})(.*)?$", line_str)
        if m:
            if current:
                items.append(current)
            extra = _clean(m.group(3)) if m.group(3) else ""
            current = {"item_id": m.group(2), "desc_lines": [extra] if extra else [], "y_start": y}
        elif current is not None:
            desc = _clean(line_str)
            if desc:
                current["desc_lines"].append(desc)

    if current:
        items.append(current)

    rows = []
    for i, item in enumerate(items):
        next_y = items[i+1]["y_start"] if i+1 < len(items) else product_y
        right_row = [w for w in right_words if item["y_start"] - 2 <= w[1] < next_y - 2]
        right_row.sort(key=lambda w: w[0])
        nums = re.findall(r"[\d,]+\.\d+", " ".join(w[4] for w in right_row))
        price = float(nums[0].replace(",", ""))  if nums else 0.0
        ext   = float(nums[-1].replace(",", "")) if nums else 0.0
        desc  = _clean(" ".join(item["desc_lines"]))
        rows.append({"item_id": item["item_id"], "item_desc": desc,
                     "Unit Price": price, "TotalSales": ext})

    # Tax
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
        rows.append({"item_id": "Tax", "item_desc": "",
                     "Unit Price": tax_amount, "TotalSales": tax_amount})

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
        if re.fullmatch(r"\d{6}", line): data["quote_number"] = line; break
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
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", line): data["quote_date"] = line; break
    m = re.search(r"Quote Good Through\s*\n+\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if not m: m = re.search(r"Quote Good Through\s+(\d{1,2}/\d{1,2}/\d{4})", text)
    if m: data["quote_valid"] = m.group(1)

    # Quoted For block — handle optional contact name line
    m2 = re.search(r"Quoted For:\n+(.+?)\n+(.*?)\n+(.*?)\n+(.*?)(?:\n|$)", text)
    if m2:
        data["company"] = _clean(m2.group(1))
        lines_after = [_clean(m2.group(i)) for i in range(2, 5)]
        for line in lines_after:
            if not line: continue
            if _is_city_state_zip(line):
                _parse_city_line(line, data); break
            elif not data["address"]:
                # Skip if looks like a person name
                if not re.match(r"^[A-Z ]+$", line) or len(line.split()) > 4:
                    data["address"] = line

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
            "Unit Price": price, "TotalSales": ext,
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
        "ReferralManager": "", "ReferralEmail": _referral_email(data["salesperson"]),
        "Brand": "Cadre Wire Group", "QuoteNumber": q_num, "QuoteDate": data["quote_date"],
        "Company": data["company"], "FirstName": first, "LastName": last,
        "ContactEmail": "", "ContactPhone": "", "Address": data["address"],
        "County": "", "City": data["city"], "State": data["state"],
        "ZipCode": data["zip_code"], "Country": data["country"],
        "QuoteValidDate": data["quote_valid"], "CustomerNumber": data["customer_number"],
        "manufacturer_Name": "", "PDF": pdf_name, "DemoQuote": "",
    }

    rows = []
    for item in line_items:
        rows.append({h: {**base, **item}.get(h, "") for h in HEADERS})
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
