def chunk_text(text, max_len = 2000):
    sentences = re.split(r'(?<=[.!?]) +', text)

    chunks = []
    current = ""

    for s in sentences:
        if len(current) + len(s) <= max_len:
            current += s + " "
        else:
            chunks.append(current.strip())
            current = s + " "

    if current:
        chunks.appent(current.strip())


    return chunks

def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text) # Regular expression cleaning of whitespace
    return text.strip()