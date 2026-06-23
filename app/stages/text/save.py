import os

def save_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path,"w", encoding="utf-8") as f:
        f.write(content)

