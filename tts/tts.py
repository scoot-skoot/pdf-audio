import asyncio
import edge_tts
import os

async def generate_audio(chunks: list[str], outpur_dur: str = "output/chunks"):
    os.makedirs(output_dir, exist_ok=True)

    for i, chunk in enumerate(chunks):
        filename = os.path.join(output_dir, f"chunk_{i:04d}.mp3")

        communicate = edge.tts.Communicate(
            text=chunk,
            voice="en-GB-RyanNueral"
        )

        await communicate.save(filename)

        print(f"Saved {filename}")
        