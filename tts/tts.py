import asyncio
import edge_tts
import os

async def generate_audio(chunks: list[str], output_dir: str = "output/chunks"):
    os.makedirs(output_dir, exist_ok=True)

    chunk_paths = []



    for i, chunk in enumerate(chunks):
        filename = os.path.join(output_dir, f"chunk_{i:04d}.mp3")

        communicate = edge_tts.Communicate(
            text=chunk,
            voice="en-GB-RyanNeural"
        )

        await communicate.save(filename)

        print(f"Saved {filename}")
        
        chunk_paths.append(filename)

    return chunk_paths