"""Platform image for officer applications (PNG or JPG only).

The file is opened with Pillow, checked, shrunk and saved again under a random
name, so a renamed virus or hidden photo data (location etc.) never gets stored.
"""
from pathlib import Path
from uuid import uuid4

from flask import current_app

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_SIDE = 2400
ALLOWED = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg")}


def directory() -> Path:
    path = Path(current_app.instance_path) / "platform_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve(filename):
    """Full path of a stored file, or None if the name is not a real stored file."""
    if not filename:
        return None
    root = directory().resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


def mimetype_for(filename) -> str:
    return "image/png" if str(filename).lower().endswith(".png") else "image/jpeg"


def delete(filename) -> None:
    path = resolve(filename)
    if path:
        try:
            path.unlink()
        except OSError:
            current_app.logger.warning("Could not delete platform image %s", filename)


def save(upload):
    """Validate and store an uploaded image. Returns (filename, error_message)."""
    if upload is None or not upload.filename:
        return None, None
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        current_app.logger.exception("Pillow is required to process platform images")
        return None, "Image upload is unavailable right now. Please contact the election committee."

    try:
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
    except OSError:
        return None, "The selected image could not be read."
    if size > MAX_BYTES:
        return None, "Choose an image smaller than 5 MB."

    try:
        image = Image.open(upload.stream)
        fmt = image.format
        if fmt not in ALLOWED:
            return None, "Use a PNG or JPG image."
        if image.width * image.height > MAX_PIXELS:
            return None, "The image is too large to process."
        image.verify()
        upload.stream.seek(0)
        image = Image.open(upload.stream)
        image.load()
        image = ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None, "The selected file is not a valid PNG or JPG image."

    image.thumbnail((MAX_SIDE, MAX_SIDE))
    ext = ALLOWED[fmt][0]
    filename = f"{uuid4().hex}.{ext}"
    destination = directory() / filename
    try:
        if fmt == "PNG":
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.save(destination, format="PNG", optimize=True)
        else:
            image.convert("RGB").save(destination, format="JPEG", quality=90, optimize=True)
    except OSError:
        current_app.logger.exception("Could not save platform image")
        return None, "The image could not be saved. Please try again."
    return filename, None
