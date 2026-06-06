import asyncio
import edge_tts
import os

DEFAULT_VOICE = "en-GB-RyanNeural"


async def generate_chunk(text: str, voice: str, filename: str, semaphore: asyncio.Semaphore):
    async with semaphore:
        communicate = edge_tts.Communicate(text=text, voice=voice or DEFAULT_VOICE)

        await communicate.save(filename)

        print(f"Saved {filename} ({voice or DEFAULT_VOICE})")


async def generate_audio(items: list[dict], output_dir: str = "output/chunks"):
    """Render each item to an MP3 in its own voice.

    items: list of {"text": str, "voice": str}. Output files are index-named
    chunk_NNNN.mp3 (order preserved); a failed chunk is reported, not raised.
    """
    os.makedirs(output_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(5)

    tasks = []
    chunk_paths = []

    for i, item in enumerate(items):
        filename = os.path.join(output_dir, f"chunk_{i:04d}.mp3")
        chunk_paths.append(filename)
        tasks.append(generate_chunk(item["text"], item.get("voice", DEFAULT_VOICE), filename, semaphore))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            print("Error:", result)

    return chunk_paths
