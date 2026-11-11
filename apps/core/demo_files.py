"""Синтетические файлы для демонстрации: PDF, DOCX и XLSX без внешних библиотек.

Всё содержимое вымышлено (компания «Nordlicht Robotics» не существует). Текст PDF только латиницей:
используются стандартные шрифты PDF без встраивания.
"""
import io
import zipfile

COMPANY = "Nordlicht Robotics (fictional)"
FOOTER = "Synthetic demo data. Not a real company or real figures."


def _esc(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _page_stream(title, lines, bars=None, page_no=1, total=1):
    out = ["BT /F2 22 Tf 56 760 Td (%s) Tj ET" % _esc(title)]
    out.append("0.14 0.32 0.27 rg 56 748 120 3 re f 0 g")
    y = 716
    for line in lines:
        out.append("BT /F1 11 Tf 56 %d Td (%s) Tj ET" % (y, _esc(line)))
        y -= 18
    if bars:
        base, x = 330, 70
        peak = max(v for _, v in bars) or 1
        for label, value in bars:
            height = int(180 * value / peak)
            out.append("0.14 0.32 0.27 rg %d %d 44 %d re f 0 g" % (x, base, height))
            out.append("BT /F1 9 Tf %d %d Td (%s) Tj ET" % (x, base - 14, _esc(label)))
            out.append("BT /F1 9 Tf %d %d Td (%s) Tj ET" % (x, base + height + 4, _esc(str(value))))
            x += 70
    out.append("BT /F1 8 Tf 56 36 Td (%s   |   %s   |   %d / %d) Tj ET" % (_esc(COMPANY), _esc(FOOTER), page_no, total))
    return "\n".join(out).encode("latin-1")


def make_pdf(title, pages):
    """pages: список словарей {"lines": [...], "bars": [(метка, значение), ...] | None}. Возвращает байты PDF."""
    total = len(pages)
    objects = [None, None]  # 1: каталог, 2: страницы
    font1 = 3 + 2 * total
    font2 = font1 + 1
    kids = []
    for i, page in enumerate(pages, start=1):
        page_id = 3 + 2 * (i - 1)
        stream = _page_stream(title if i == 1 else f"{title} ({i})", page.get("lines", []), page.get("bars"), i, total)
        kids.append(f"{page_id} 0 R")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {page_id + 1} 0 R "
            f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {total} >>".encode()

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def make_docx(paragraphs):
    from xml.sax.saxutils import escape

    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        f"{body}</w:body></w:document>"
    )
    return _zip({
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
        ),
        "word/document.xml": document,
    })


def make_xlsx(rows):
    from xml.sax.saxutils import escape

    def cell(ref, value):
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    data = ""
    for r, row in enumerate(rows, start=1):
        data += f'<row r="{r}">' + "".join(cell(f"{chr(65 + c)}{r}", v) for c, v in enumerate(row)) + "</row>"
    return _zip({
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Model" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{data}</sheetData></worksheet>"
        ),
    })


def _zip(parts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return buf.getvalue()
