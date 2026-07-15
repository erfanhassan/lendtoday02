import os
import traceback
import logging
from app.image_generator import generate_graphic
from app.video_processor import apply_video_template
from app.db import insert_article, update_article_ai_decision, store_publishing_post_ids

logger = logging.getLogger(__name__)

async def run_simulation():
    """
    Run end-to-end simulation of the pipeline safely on Replit
    without external API dependencies (except maybe downloading an image).
    """
    results = {}
    test_article_id = None
    mock_graphic_path = None
    mock_video_path = None
    dummy_input_video_path = "static/videos/dummy_test.mp4"

    try:
        # -----------------------------------------------------
        # Phase E: Database Verification (do this first to get an ID)
        # -----------------------------------------------------
        logger.info("Simulation: Testing Database Insertion...")
        import uuid
        test_url = f"https://test-simulation.local/article/{uuid.uuid4()}"
        
        # Inserting mock record
        test_article_id = await insert_article(
            source="SimulationTest",
            url=test_url,
            title="Simulation Test News Title"
        )
        
        # Update dummy decisions
        await update_article_ai_decision(
            article_id=test_article_id,
            status="SIMULATION",
            category="Simulation",
            slot=1,
            headline="Simulation Mock Headline",
            source_text="Simulation Source Text",
            search_query="simulation search",
            social_media_caption="Simulation Caption",
            engagement_question="Simulation Question",
            hashtags="#Simulation #Test",
            article_image_url="https://upload.wikimedia.org/wikipedia/commons/thumb/1/15/Cat_August_2010-4.jpg/1200px-Cat_August_2010-4.jpg"
        )
        await store_publishing_post_ids(test_article_id, ig_post_id="TEST_IG", fb_post_id="TEST_FB")
        results["database"] = "PASS"

        # -----------------------------------------------------
        # Phase A & B: Mocking Scraper and AI Data
        # -----------------------------------------------------
        # We simulate the AI's deterministc output via a dict
        logger.info("Simulation: Generating Mock AI Decision...")
        decision = {
            "headline": "Simulation Mock Headline",
            "social_media_caption": "This is a caption generated during simulation.",
            "engagement_question": "What do you think about simulations?",
            "hashtags": "#simulation #test",
            "search_query": "technology simulation",
            "source_text": "Simulation Source",
            "article_image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/15/Cat_August_2010-4.jpg/1200px-Cat_August_2010-4.jpg"
        }
        results["scraper_and_ai_mocking"] = "PASS"

        # -----------------------------------------------------
        # Phase C: Image Processing Test
        # -----------------------------------------------------
        logger.info("Simulation: Testing Pillow Image Generation...")
        mock_graphic_path = generate_graphic(test_article_id, decision)
        if mock_graphic_path and os.path.exists(mock_graphic_path):
            results["pillow_rendering"] = "PASS"
        else:
            raise Exception(f"Image not found at expected path: {mock_graphic_path}")

        # -----------------------------------------------------
        # Phase D: Video Processing Test
        # -----------------------------------------------------
        logger.info("Simulation: Testing FFmpeg Video Processing...")
        # Create a tiny, low-FPS dummy video using moviepy
        from moviepy import ColorClip
        
        # Create a simple red 3-second video
        clip = ColorClip(size=(640, 360), color=(255, 0, 0), duration=3)
        clip = clip.with_fps(10)
        clip.write_videofile(dummy_input_video_path, codec="libx264", audio=False, preset="ultrafast", logger=None)
        
        # Make sure directory exists for video output
        os.makedirs("static/videos", exist_ok=True)
        
        mock_video_path = apply_video_template(
            video_path=dummy_input_video_path,
            template_path="assets/video_template_vertical.png", # May not exist, we just check if it fails or succeeds gracefully
            title="Simulation Title",
            credit_text="Simulation Credit",
            output_id=test_article_id
        )
        
        if mock_video_path and os.path.exists(mock_video_path):
             results["ffmpeg_video_processing"] = "PASS"
        else:
             raise Exception(f"Video not found at expected path: {mock_video_path}")

        # -----------------------------------------------------
        # Phase F: Storage Cleanup
        # -----------------------------------------------------
        logger.info("Simulation: Testing Cleanup...")
        if mock_graphic_path and os.path.exists(mock_graphic_path):
            os.remove(mock_graphic_path)
            
        if mock_video_path and os.path.exists(mock_video_path):
            os.remove(mock_video_path)
            
        if os.path.exists(dummy_input_video_path):
            os.remove(dummy_input_video_path)
            
        results["cleanup"] = "PASS"

    except Exception as e:
        logger.error(f"Simulation failed: {traceback.format_exc()}")
        results["failed_phase_error"] = str(e)
        results["traceback"] = traceback.format_exc()

    return results
