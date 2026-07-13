import asyncio
from app.db import init_db, close_db, insert_video_request
from app.scheduler import run_video_pipeline

async def main():
    await init_db()
    print("DB initialized")
    await insert_video_request("https://www.instagram.com/p/DaVL0LLikBF/?img_index=1&igsh=MW4xajUzdW15bzYyMA==", "China windstorm")
    print("Video inserted, running pipeline...")
    await run_video_pipeline()
    print("Pipeline finished")
    await close_db()

if __name__ == "__main__":
    asyncio.run(main())
