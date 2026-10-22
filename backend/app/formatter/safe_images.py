"""Cheap pixel-count checks before decoding user-supplied raster images."""
from __future__ import annotations

from io import BytesIO
import warnings

from PIL import Image

# Image size in bytes alone does not bound memory: a tiny compressed image can
# expand to hundreds of MB. 50 MP is already more than enough for document art.
MAX_SAFE_IMAGE_PIXELS = 50_000_000


def open_checked_image(payload: bytes) -> Image.Image:
    """Open image metadata safely; callers must close the returned image."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(payload))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(
            "Image dimensions are too large for safe processing. "
            "Resize the image to 50 megapixels or less before uploading."
        ) from exc

    width, height = image.size
    if width * height > MAX_SAFE_IMAGE_PIXELS:
        image.close()
        raise ValueError(
            "Image dimensions are too large for safe processing. "
            "Resize the image to 50 megapixels or less before uploading."
        )
    return image
