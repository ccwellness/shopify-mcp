"""One-shot helper to dump docs/design_requirements.docx to plain text."""
import sys, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
DOCX = ROOT / "docs" / "design_requirements.docx"
OUT = ROOT / "docs" / "design_extracted.txt"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

def extract() -> str:
    with zipfile.ZipFile(DOCX) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for p in root.iter("{%s}p" % NS["w"]):
        parts: list[str] = []
        for t in p.iter("{%s}t" % NS["w"]):
            if t.text:
                parts.append(t.text)
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)

if __name__ == "__main__":
    text = extract()
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {len(text)} chars to {OUT}", file=sys.stderr)
