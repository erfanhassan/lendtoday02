import os
import logging
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

def _create_text_overlay(width: int, height: int, title: str, credit_text: str) -> str:
    """
    Creates a transparent PNG with the title at the top center and credit at the bottom right.
    Returns the path to the temporary PNG file.
    """
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Try to load a font, fallback to default
    try:
        title_font = ImageFont.truetype("Arial.ttf", 60)
        credit_font = ImageFont.truetype("Arial.ttf", 40)
    except Exception:
        title_font = ImageFont.load_default()
        credit_font = ImageFont.load_default()

    # Draw Title (Top Center)
    if title:
        # Simple text wrapping could be added here if needed
        bbox = draw.textbbox((0, 0), title, font=title_font)
        text_w = bbox[2] - bbox[0]
        x_pos = (width - text_w) / 2
        y_pos = height * 0.1 # 10% from top
        
        # Draw outline
        outline_color = "black"
        for adj_x in [-2, 2]:
            for adj_y in [-2, 2]:
                draw.text((x_pos + adj_x, y_pos + adj_y), title, font=title_font, fill=outline_color)
        # Draw text
        draw.text((x_pos, y_pos), title, font=title_font, fill="yellow")

    # Draw Credit (Bottom Right)
    if credit_text:
        bbox = draw.textbbox((0, 0), credit_text, font=credit_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x_pos = width - text_w - 40 # 40px margin
        y_pos = height - text_h - 40
        
        for adj_x in [-2, 2]:
            for adj_y in [-2, 2]:
                draw.text((x_pos + adj_x, y_pos + adj_y), credit_text, font=credit_font, fill="black")
        draw.text((x_pos, y_pos), credit_text, font=credit_font, fill="white")

    overlay_path = "static/videos/temp_text_overlay.png"
    img.save(overlay_path)
    return overlay_path


def apply_video_template(video_path: str, template_path: str, title: str, credit_text: str, output_id: int) -> str | None:
    """
    Applies a static image template and text overlay to a video.
    Returns the path to the final edited video.
    """
    try:
        logger.info(f"Processing video {video_path}...")
        
        # Load video
        video_clip = VideoFileClip(video_path)
        w, h = video_clip.size
        
        clips = [video_clip]
        
        # Add template overlay if it exists
        if os.path.exists(template_path):
            template_clip = ImageClip(template_path).set_duration(video_clip.duration)
            # Resize template to match video dimensions if needed
            if template_clip.size != (w, h):
                template_clip = template_clip.resize(newsize=(w, h))
            clips.append(template_clip)
        else:
            logger.warning(f"Template not found at {template_path}, skipping template overlay.")

        # Create and add text overlay
        text_overlay_path = _create_text_overlay(w, h, title, credit_text)
        text_clip = ImageClip(text_overlay_path).set_duration(video_clip.duration)
        clips.append(text_clip)

        # Composite everything
        final_clip = CompositeVideoClip(clips)
        
        output_path = f"static/videos/final_{output_id}.mp4"
        
        # Write result
        final_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile="static/videos/temp-audio.m4a",
            remove_temp=True,
            fps=30,
            preset="fast"
        )
        
        # Clean up
        video_clip.close()
        final_clip.close()
        if os.path.exists(text_overlay_path):
            os.remove(text_overlay_path)
            
        return output_path
        
    except Exception as e:
        logger.error(f"Error processing video {video_path}: {e}")
        return None
