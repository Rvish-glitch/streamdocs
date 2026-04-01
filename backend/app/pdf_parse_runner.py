import sys
from pathlib import Path

import pdfplumber


def parse_pdf_text(storage_path: str) -> str:
    import re

    pages_out: list[str] = []
    with pdfplumber.open(storage_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text(layout=True) or page.extract_text() or ""
            page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
            page_text = re.sub(r"-\n(?=\w)", "", page_text)
            page_text = re.sub(r"[\t\f\v]+", " ", page_text)
            page_text = re.sub(r"[ ]{2,}", " ", page_text)
            page_text = re.sub(r"\n{3,}", "\n\n", page_text).strip()

            if page_text:
                pages_out.append(f"--- Page {idx} ---\n{page_text}")

    return "\n\n".join(pages_out).strip()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("Usage: python -m app.pdf_parse_runner <input_pdf> <output_txt>\n")
        return 2

    input_path = argv[1]
    output_path = argv[2]

    try:
        text = parse_pdf_text(input_path)
        Path(output_path).write_text(text, encoding="utf-8", errors="replace")
        return 0
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"PDF parse failed: {type(e).__name__}: {e}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
