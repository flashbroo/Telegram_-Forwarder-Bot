# branding.py

from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import logging

logger = logging.getLogger(__name__)

DEFAULT_WATERMARK = "Powered by Bot"
MAX_IMAGE_SIZE = (1920, 1920)   # prevent huge memory usage
DEFAULT_FONT_SIZE = 24
DEFAULT_OPACITY = 180


# -----------------------------
# FONT LOADER
# -----------------------------

def load_font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


# -----------------------------
# WATERMARK ENGINE
# -----------------------------

def watermark_bytes(file_bytes: bytes, text: str = DEFAULT_WATERMARK) -> BytesIO:
    """
    Adds watermark text to image bytes.
    Returns BytesIO object ready to send.
    """

    try:
        im = Image.open(BytesIO(file_bytes)).convert("RGBA")
    except Exception as e:
        logger.error(f"Invalid image file: {e}")
        raise ValueError("Invalid image file")

    # Resize large images safely
    im.thumbnail(MAX_IMAGE_SIZE, Image.LANCZOS)

    width, height = im.size

    # Create transparent layer
    overlay = Image.new("RGBA", im.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(int(width / 30), DEFAULT_FONT_SIZE)
    font = load_font(font_size)

    text_width, text_height = draw.textbbox((0, 0), text, font=font)[2:]

    margin = 20
    x = width - text_width - margin
    y = height - text_height - margin

    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, DEFAULT_OPACITY)
    )

    # Merge watermark
    watermarked = Image.alpha_composite(im, overlay)

    # Export
    out = BytesIO()

    # Preserve PNG if input was PNG
    if im.format == "PNG":
        watermarked.save(out, format="PNG", optimize=True)
    else:
        watermarked.convert("RGB").save(out, format="JPEG", quality=85, optimize=True)

    out.seek(0)
    return out
