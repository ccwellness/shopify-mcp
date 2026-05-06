"""One-shot helper to dump docs/design_requirements.docx to plain text."""

import sys
import zipfile
from pathlib import Path

# Vendored design doc, not untrusted XML — bandit S405/S314 are noise here.
from xml.etree import ElementTree as ET  # noqa: S405

ROOT = Path(__file__).resolve().parent.parent
DOCX = ROOT / "docs" / "design_requirements.docx"
OUT = ROOT / "docs" / "design_extracted.txt"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W_NS = NS["w"]


def extract() -> str:
    with zipfile.ZipFile(DOCX) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    # Vendored design doc, not untrusted XML.
    root = ET.fromstring(xml)  # noqa: S314
    paragraphs: list[str] = []
    for p in root.iter(f"{{{W_NS}}}p"):
        parts: list[str] = []
        for t in p.iter(f"{{{W_NS}}}t"):
            if t.text:
                parts.append(t.text)
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


if __name__ == "__main__":
    text = extract()
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {len(text)} chars to {OUT}", file=sys.stderr)
