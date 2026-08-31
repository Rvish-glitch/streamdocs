import sys
import re
from pathlib import Path
import os
MAX_PARSE_PAGES = 100

def parse_pdf_text(storage_path: str, max_pages: int = MAX_PARSE_PAGES) -> str:
    if not os.path.exists(storage_path):
        return ""

    pages_out: list[str] = []
    
    # Try PyMuPDF (pymupdf) first for ultra-fast C-based extraction
    try:
        import pymupdf
        with pymupdf.open(storage_path) as doc:
            total_pages = doc.page_count
            pages_to_process = min(total_pages, max_pages) if max_pages > 0 else total_pages

            for idx in range(pages_to_process):
                page = doc.load_page(idx)
                page_text = page.get_text("text") or ""

                page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
                page_text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", page_text)
                page_text = re.sub(r"[\t\f\v]+", " ", page_text)
                page_text = re.sub(r"[ ]{2,}", " ", page_text)
                page_text = re.sub(r"\n{3,}", "\n\n", page_text).strip()

                if page_text:
                    pages_out.append(f"--- Page {idx + 1} ---\n{page_text}")

            if max_pages > 0 and total_pages > max_pages:
                pages_out.append(f"\n[Note: Extracted first {max_pages} of {total_pages} total pages]")

        return "\n\n".join(pages_out).strip()
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"PyMuPDF parse failed, attempting fallback: {e}\n")


    # Fallback to pdfplumber
    import pdfplumber
    with pdfplumber.open(storage_path) as pdf:
        total_pages = len(pdf.pages)
        pages_to_process = min(total_pages, max_pages) if max_pages > 0 else total_pages

        for idx in range(pages_to_process):
            page = pdf.pages[idx]
            page_text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            
            if not page_text.strip():
                try:
                    words = page.extract_words(x_tolerance=2, y_tolerance=3)
                    page_text = " ".join([w["text"] for w in words])
                except Exception:
                    page_text = ""

            page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
            page_text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", page_text)
            page_text = re.sub(r"[\t\f\v]+", " ", page_text)
            page_text = re.sub(r"[ ]{2,}", " ", page_text)
            page_text = re.sub(r"\n{3,}", "\n\n", page_text).strip()

            if page_text:
                pages_out.append(f"--- Page {idx + 1} ---\n{page_text}")

        if max_pages > 0 and total_pages > max_pages:
            pages_out.append(f"\n[Note: Extracted first {max_pages} of {total_pages} total pages]")

    return "\n\n".join(pages_out).strip()

def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("Usage: python -m app.pdf_parse_runner <input_pdf> <output_txt>\n")
        return 2
    try:
        text = parse_pdf_text(argv[1])
        Path(argv[2]).write_text(text, encoding="utf-8", errors="replace")
        return 0
    except Exception as e:
        sys.stderr.write(f"PDF parse failed: {type(e).__name__}: {e}\n")
        return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))