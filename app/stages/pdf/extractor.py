from pypdf import PdfReader

def extract_text(pdf_path: str) -> str: #Function type hints
    reader = PdfReader(pdf_path)

    text = ""

    for page in reader.pages:
        page_text = page.extract_text()

        if page_text:
            text += page_text + " "

    return text