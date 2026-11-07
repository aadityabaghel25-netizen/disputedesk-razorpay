"""
A tiny text-only PDF writer (standard library only).

Razorpay takes contest evidence as files (PDF, PNG or JPG), not as text. So the reply the seller approved,
and the records it cites, are written into plain PDFs by code. No AI touches this step.
The built-in Helvetica font covers Latin text only, so anything else (Devanagari, for example) is replaced.
"""

import textwrap

PAGE_W, PAGE_H, MARGIN, SIZE, LEADING = 595, 842, 50, 10, 14   # A4 in points
WRAP = 95
LINES_PER_PAGE = (PAGE_H - 2 * MARGIN) // LEADING

REPLACE = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "₹": "Rs. "}


def _latin(text: str) -> str:
    text = "".join(REPLACE.get(c, c) for c in text)
    return text.encode("latin-1", "replace").decode("latin-1")


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_pdf(title: str, body: str) -> bytes:
    lines = [title.upper(), ""]
    for para in _latin(body).splitlines():
        lines += textwrap.wrap(para, WRAP) or [""]
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]

    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for page in pages:
        ops = [f"BT /F1 {SIZE} Tf {LEADING} TL {MARGIN} {PAGE_H - MARGIN} Td"] + [f"({_escape(_latin(t))}) Tj T*" for t in page] + ["ET"]
        stream = "\n".join(ops).encode("latin-1")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
        content_ref = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_ref} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"

    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + (obj if isinstance(obj, bytes) else obj.encode("latin-1")) + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
