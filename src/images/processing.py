"""Проверка и нормализация изображений. Декодирование работает в воркере."""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps
from pillow_heif import get_file_mimetype, register_heif_opener

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 18 * 1024 * 1024
MAX_PIXELS = 20_000_000
FORMATS = ("JPEG", "PNG", "WEBP", "HEIF")
HEIF_CONTENT_TYPES = {
    "image/heic",
    "image/heif",
    "image/heic-sequence",
    "image/heif-sequence",
}

register_heif_opener(thumbnails=False)


class InvalidImage(ValueError):
    """Причину можно показывать пользователю."""


def sniff_content_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        content_type = get_file_mimetype(data)
        if content_type in HEIF_CONTENT_TYPES:
            return content_type
    raise InvalidImage("Допустимы JPEG, PNG, WebP, HEIC и HEIF")


@dataclass(frozen=True)
class ProcessedImage:
    full: bytes
    preview: bytes
    avatar: bytes | None


def normalized_image(data: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(data), formats=FORMATS) as source:
            width, height = source.size
            if width * height > MAX_PIXELS:
                raise InvalidImage("Изображение больше 20 мегапикселей")
            # PNG/WebP открываются на первом доступном кадре, APNG может начать
            # со статичной обложки. HEIF-плагин сразу выбирает основное фото.
            source.load()
            oriented = ImageOps.exif_transpose(source)
            alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            rgb = oriented.convert("RGBA" if alpha else "RGB")
            rgb.info.clear()
            return rgb
    except Image.DecompressionBombError as error:
        raise InvalidImage("Изображение больше 20 мегапикселей") from error
    except (OSError, SyntaxError, ValueError) as error:
        if isinstance(error, InvalidImage):
            raise
        raise InvalidImage("Не удалось прочитать изображение") from error


def webp(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="WEBP", quality=85, method=4)
    return output.getvalue()


def square(image: Image.Image) -> bytes:
    size = min(*image.size, 512)
    return webp(ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS))


def process_upload(data: bytes, make_avatar: bool) -> ProcessedImage:
    with normalized_image(data) as image:
        image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        full = webp(image)
        avatar = square(image) if make_avatar else None
        image.thumbnail((400, 400), Image.Resampling.LANCZOS)
        return ProcessedImage(full, webp(image), avatar)


def crop_avatar(data: bytes) -> bytes:
    with normalized_image(data) as image:
        return square(image)
