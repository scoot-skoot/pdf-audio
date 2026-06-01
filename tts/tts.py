import asyncio
import edge_tts
import os

async def generate_chunk(chunk: str, filename: str, semaphore: asyncio.Semaphore):
    async with semaphore:
        communicate = edge_tts.Communicate(text=chunk, voice="en-GB-RyanNeural")

        await communicate.save(filename)
        
        print(f"Saved {filename}")

async def generate_audio(chunks: list[str], output_dir: str = "output/chunks"):

    os.makedirs(output_dir, exist_ok=True) 
    semaphore = asyncio.Semaphore(5)

    tasks = []
    chunk_paths = []

    for i, chunk in enumerate(chunks):
        filename = os.path.join(output_dir, f"chunk_{i:04d}.mp3")

        chunk_paths.append(filename)

        tasks.append(generate_chunk(chunk, filename, semaphore))

       

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            print("Error:", result)

    return chunk_paths