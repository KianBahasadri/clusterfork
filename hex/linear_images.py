"""Validate image attachments before passing them to OpenCode."""

import io
import warnings


MAX_IMAGES = 6
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_SIDE = 8192
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class ImageContextError(RuntimeError):
    """Only integration-owned messages, never URLs or remote exception strings."""

def validate_image(raw, mime):
    from PIL import Image

    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageContextError("oversized; maximum is 5 MiB per image")
    if mime not in EXTENSIONS:
        raise ImageContextError("unsupported media type; use PNG, JPEG, or non-animated WebP")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if FORMATS.get(image.format) != mime:
                    raise ImageContextError("image format does not match its media type")
                width, height = image.size
                if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
                    raise ImageContextError("oversized dimensions; maximum is 20 megapixels and 8192 pixels per side")
                if getattr(image, "is_animated", False):
                    raise ImageContextError("animated images are unsupported; upload a still image")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
    except ImageContextError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageContextError("oversized image dimensions") from None
    except Exception:
        raise ImageContextError("unsupported or corrupt image data") from None
