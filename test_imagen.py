import vertexai
from vertexai.preview.vision_models import ImageGenerationModel
from PIL import Image
import os

try:
    vertexai.init()
    model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
    print("Methods available on ImageGenerationModel:")
    print(dir(model))
except Exception as e:
    print(f"Error: {e}")
