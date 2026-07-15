import os
import urllib.request
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")
FONT_URL = "https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Bold.ttf"
FONT_PATH = os.path.join(FONTS_DIR, "Roboto-Bold.ttf")

def download_font():
    os.makedirs(FONTS_DIR, exist_ok=True)
    if not os.path.exists(FONT_PATH):
        logger.info(f"Downloading Roboto-Bold.ttf to {FONT_PATH}...")
        try:
            req = urllib.request.Request(FONT_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(FONT_PATH, 'wb') as out_file:
                data = response.read()
                out_file.write(data)
            logger.info("Download complete.")
        except Exception as e:
            logger.error(f"Failed to download font: {e}")
    else:
        logger.info(f"Font already exists at {FONT_PATH}.")

if __name__ == "__main__":
    download_font()
