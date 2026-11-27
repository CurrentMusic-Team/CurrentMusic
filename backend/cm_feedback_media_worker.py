"""Resource-limited subprocess for uploaded media validation and processing."""
import errno
import os
import resource
import sys


def screenshot(source, target, content_type, edge, pixels):
    from PIL import Image, ImageOps, UnidentifiedImageError

    expected = {"image/jpeg": "JPEG", "image/png": "PNG"}[content_type]
    with open(source, "rb") as handle:
        prefix = handle.read(8)
    if not prefix.startswith(b"\xff\xd8\xff" if expected == "JPEG" else b"\x89PNG\r\n\x1a\n"):
        return 15
    try:
        if expected == "PNG":
            with Image.open(source, formats=[expected]) as image:
                image.verify()
        with Image.open(source, formats=[expected]) as image:
            width, height = image.size
            if max(width, height) > edge or width * height > pixels or getattr(image, "n_frames", 1) != 1:
                return 22
            oriented = ImageOps.exif_transpose(image)
            mode = "RGBA" if expected == "PNG" and ("A" in oriented.getbands() or "transparency" in oriented.info) else "RGB"
            # Copy pixels into a fresh image so no source metadata reaches the encoder.
            clean = Image.new(mode, oriented.size)
            clean.paste(oriented.convert(mode))
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, UnidentifiedImageError):
        return 22
    try:
        clean.save(target, format=expected, **({"quality": 90} if expected == "JPEG" else {}))
    except OSError as exc:
        return 13 if exc.errno == 27 else 3
    return 0


def banner(source, target, image_format, left, top, width, height):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return 3
    from cm_image import MAX_PIXELS, SAFE_FORMATS

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(source, formats=[image_format] if image_format else list(SAFE_FORMATS)) as image:
            if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                return 22
            oriented = ImageOps.exif_transpose(image)
            right, bottom = left + width, top + height
            if right > oriented.width or bottom > oriented.height:
                return 22
            mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
            clean = Image.new(mode, (width, height))
            clean.paste(oriented.crop((left, top, right, bottom)).convert(mode))
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return 22
    try:
        clean.save(target, format="PNG", compress_level=3)
    except OSError as exc:
        return 13 if exc.errno == errno.EFBIG else 3
    return 0


def main():
    limit, operation, *args = sys.argv[1:]
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 ** 2, 768 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_CPU, (25, 26))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(limit), int(limit)))
    if operation == "screenshot":
        source, target, kind, edge, pixels = args
        return screenshot(source, target, kind, int(edge), int(pixels))
    if operation == "banner":
        source, target, image_format, *crop = args
        return banner(source, target, image_format, *map(int, crop))
    os.execv(args[0], args)


if __name__ == "__main__":
    sys.exit(main())
