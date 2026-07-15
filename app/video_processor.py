import os
import logging
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

os.makedirs("static/videos", exist_ok=True)

def _create_text_overlay(width: int, height: int, title: str, credit_text: str) -> str:
    """
    Creates a transparent PNG with the title at the top center and credit at the bottom right.
    Returns the path to the temporary PNG file.
    """
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Try to load a font from system paths, fallback to default
    FONT_PATHS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
    ]
    title_font = ImageFont.load_default()
    credit_font = ImageFont.load_default()
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                title_font = ImageFont.truetype(path, 60)
                credit_font = ImageFont.truetype(path, 40)
                break
            except Exception:
                continue

    # Draw Title (Bottom Center)
    if title:
        bbox = draw.textbbox((0, 0), title, font=title_font)
        text_w = bbox[2] - bbox[0]
        x_pos = (width - text_w) / 2
        y_pos = height * 0.85  # 85% from top
        
        outline_color = "black"
        for adj_x in [-2, 2]:
            for adj_y in [-2, 2]:
                draw.text((x_pos + adj_x, y_pos + adj_y), title, font=title_font, fill=outline_color)
        draw.text((x_pos, y_pos), title, font=title_font, fill="yellow")

    # Draw Credit (Top Right)
    if credit_text:
        bbox = draw.textbbox((0, 0), credit_text, font=credit_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x_pos = width - text_w - 40 # 40px margin right
        y_pos = 250 # 250px margin top (moved further down)
        
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
        target_w, target_h = 1080, 1920
        
        # Calculate aspect ratios
        video_ratio = video_clip.w / video_clip.h
        target_ratio = target_w / target_h
        
        # Zoom to fill: Scale up while preserving aspect ratio
        if video_ratio > target_ratio:
            video_clip = video_clip.resized(height=target_h)
        else:
            video_clip = video_clip.resized(width=target_w)
            
        # Crop center to strictly fit 1080x1920
        video_clip = video_clip.cropped(
            x_center=video_clip.w / 2, 
            y_center=video_clip.h / 2, 
            width=target_w, 
            height=target_h
        )
        
        # Place video in center
        video_clip = video_clip.with_position("center")
        
        clips = [video_clip]
        
        # Add template overlay if it exists
        if os.path.exists(template_path):
            template_clip = ImageClip(template_path).with_duration(video_clip.duration)
            if template_clip.size != (target_w, target_h):
                template_clip = template_clip.resized(new_size=(target_w, target_h))
            template_clip = template_clip.with_position("center")
            clips.append(template_clip)
        else:
            logger.warning(f"Template not found at {template_path}, skipping template overlay.")

        # Create and add text overlay
        text_overlay_path = _create_text_overlay(target_w, target_h, title, credit_text)
        text_clip = ImageClip(text_overlay_path).with_duration(video_clip.duration).with_position("center")
        clips.append(text_clip)

        # Composite everything on a 1080x1920 canvas
        final_clip = CompositeVideoClip(clips, size=(target_w, target_h))
        
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
