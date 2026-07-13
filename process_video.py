import asyncio
import os
import sys
from dotenv import load_dotenv

import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

load_dotenv()

from app.video_scraper import download_video_and_metadata
from app.ai import generate_video_text
from app.video_processor import apply_video_template
from app.publisher import publish_video_to_facebook

async def main():
    url = "https://www.instagram.com/p/DaVL0LLikBF/?img_index=1&igsh=MW4xajUzdW15bzYyMA=="
    context = "China windstorm"
    output_id = 999
    
    print("1. Downloading video...")
    video_meta = download_video_and_metadata(url, output_id)
    if not video_meta:
        print("Failed to download video")
        return
    
    video_path = video_meta['video_path']
    original_caption = video_meta.get('description', '')
    
    print(f"2. Generating AI text for video. Original caption: {original_caption[:100]}...")
    ai_text = await generate_video_text(original_caption, context)
    if not ai_text:
        print("Failed to generate AI text")
        return
        
    short_title = ai_text.get('short_title', 'Viral Video')
    caption = ai_text.get('social_media_caption', '')
    hashtags = ai_text.get('hashtags', '')
    
    if isinstance(hashtags, list):
        hashtags = " ".join([h if h.startswith('#') else f"#{h}" for h in hashtags])
        
    full_caption = f"{caption}\n\n{hashtags}"
    
    print(f"3. Applying template. Title: {short_title}")
    template_path = "static/video_template.png"
    final_video = apply_video_template(video_path, template_path, short_title, "", output_id)
    
    if not final_video:
        print("Failed to apply video template")
        return
        
    print(f"4. Publishing to Facebook... Video: {final_video}")
    success = await publish_video_to_facebook(final_video, full_caption, short_title)
    if success:
        print("Successfully published video to Facebook!")
    else:
        print("Failed to publish video to Facebook.")

if __name__ == "__main__":
    asyncio.run(main())
