"""Media handling: uploads, thumbnails, avatars and file serving blueprint."""
import base64
import io
import os
import re
import uuid

from flask import Blueprint, abort, current_app, send_from_directory
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

bp = Blueprint("media", __name__, url_prefix="/media")

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024

PALETTES = {
    "Технологии": ("#101f3c", "#1f5f8b"),
    "Музыка": ("#3b0a45", "#b3125b"),
    "Игры": ("#1b0b3b", "#3f2b96"),
    "Наука": ("#052b2b", "#0f6b5c"),
    "Путешествия": ("#5c1c00", "#e07b1a"),
    "Еда": ("#4a1200", "#c2410c"),
    "Влоги": ("#4a0d2c", "#c2185b"),
    "Образование": ("#0d1b4c", "#2563eb"),
    "Спорт": ("#0c2a12", "#2e7d32"),
    "Кино": ("#15181d", "#46505e"),
}
FALLBACK_PALETTES = [
    ("#111827", "#334155"), ("#1e1b4b", "#4338ca"), ("#3f1d1d", "#b91c1c"),
    ("#052e2b", "#0f766e"), ("#3b0764", "#7e22ce"),
]


# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------

def _font(size, bold=False):
    candidates = []
    if bold:
        candidates += [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates += [
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _wrap(draw, text, font, max_width, max_lines):
    words = str(text or "").split()
    if not words:
        return [""] if max_lines else []
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    # trim the last line if it overflows visually
    if lines and draw.textlength(lines[-1], font=font) > max_width:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


# --------------------------------------------------------------------------
# thumbnail generation
# --------------------------------------------------------------------------

def _palette(category, seed_text):
    if category in PALETTES:
        return PALETTES[category]
    index = sum(ord(ch) for ch in str(seed_text)) % len(FALLBACK_PALETTES)
    return FALLBACK_PALETTES[index]


def make_thumbnail(title, channel, category="Другое", aspect="landscape", filename=None):
    """Render a stylised preview card (16:9 or 9:16) with Pillow."""
    vertical = aspect == "vertical"
    if vertical:
        width, height = 720, 1280
        margin = 48
        cat_size, cat_height = 24, 44
        brand_size = 24
        title_sizes = (54, 48, 42, 36, 30)
        channel_size = 26
        channel_h = 46
        bottom_offset = 92
        center = 0.54
    else:
        width, height = 1280, 720
        margin = 64
        cat_size, cat_height = 26, 52
        brand_size = 28
        title_sizes = (66, 60, 54, 48, 42, 36)
        channel_size = 30
        channel_h = 52
        bottom_offset = 104
        center = 0.52

    c1, c2 = _palette(category, title)
    rgb1, rgb2 = _hex(c1), _hex(c2)

    strip = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        strip.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(rgb1, rgb2)))
    img = strip.resize((width, height), Image.BILINEAR).convert("RGBA")

    # soft glowing orbs
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((width * 0.58, -height * 0.35, width * 1.25, height * 0.75), fill=(255, 255, 255, 26))
    gd.ellipse((-width * 0.2, height * 0.45, width * 0.32, height * 1.15), fill=(255, 255, 255, 18))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.alpha_composite(img, glow)

    # bottom darkening for text contrast
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(height):
        alpha = int(150 * (y / height) ** 2)
        sd.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, shade)

    draw = ImageDraw.Draw(img)

    # category capsule
    cat_font = _font(cat_size, bold=True)
    cat_text = str(category or "Видео").upper()
    cat_w = draw.textlength(cat_text, font=cat_font)
    draw.rounded_rectangle((margin, 52, margin + cat_w + 40, 52 + cat_height),
                           radius=cat_height // 2, fill=(0, 0, 0, 95))
    draw.text((margin + 20, 52 + (cat_height - cat_size) // 2 - 3), cat_text,
              font=cat_font, fill=(255, 255, 255, 235))

    # brand mark (top right)
    brand_font = _font(brand_size, bold=True)
    draw.text((width - margin - draw.textlength("InderTube", font=brand_font), 58),
              "InderTube", font=brand_font, fill=(255, 255, 255, 175))

    # title (shrinking until it fits 3 lines)
    max_width = width - margin * 2
    title_font, lines = None, []
    for size in title_sizes:
        font = _font(size, bold=True)
        lines = _wrap(draw, title, font, max_width, 3)
        if len(lines) <= 3 and all(draw.textlength(line, font=font) <= max_width for line in lines):
            title_font = font
            break
    if title_font is None:
        title_font = _font(title_sizes[-1], bold=True)
        lines = lines[:3]

    line_height = int(title_font.size * 1.18) if hasattr(title_font, "size") else 44
    block_height = line_height * len(lines)
    start_y = int(height * center - block_height / 2)
    shadow = (0, 0, 0, 170)
    for i, line in enumerate(lines):
        y = start_y + i * line_height
        draw.text((margin + 2, y + 3), line, font=title_font, fill=shadow)
        draw.text((margin, y), line, font=title_font, fill=(255, 255, 255, 255))

    # channel row
    ch_font = _font(channel_size, bold=False)
    ch_y = height - bottom_offset
    draw.ellipse((margin, ch_y, margin + channel_h, ch_y + channel_h), fill=(255, 255, 255, 235))
    initial = (str(channel or "?")[:1] or "?").upper()
    initial_font = _font(int(channel_size * 0.9), bold=True)
    iw = draw.textlength(initial, font=initial_font)
    draw.text((margin + channel_h / 2 - iw / 2, ch_y + 9), initial,
              font=initial_font, fill=_hex(c1))
    draw.text((margin + channel_h + 18, ch_y + 8), str(channel or ""),
              font=ch_font, fill=(255, 255, 255, 225))

    name = filename or f"{uuid.uuid4().hex}.jpg"
    path = os.path.join(current_app.config["UPLOAD_THUMB"], name)
    img.convert("RGB").save(path, "JPEG", quality=88, optimize=True)
    return name


# --------------------------------------------------------------------------
# uploads
# --------------------------------------------------------------------------

def _video_dir():
    return current_app.config["UPLOAD_VIDEO"]


def _thumb_dir():
    return current_app.config["UPLOAD_THUMB"]


def _avatar_dir():
    return current_app.config["UPLOAD_AVATAR"]


def save_video(file_storage):
    filename = getattr(file_storage, "filename", "") or ""
    if not filename:
        raise ValueError("Файл не выбран")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise ValueError("Поддерживаются форматы MP4, WebM, MOV")
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(_video_dir(), stored)
    file_storage.save(path)
    if os.path.getsize(path) == 0:
        os.remove(path)
        raise ValueError("Файл пустой")
    return stored


def _save_jpg(image, directory, size=None, quality=88):
    if size:
        image = ImageOps.fit(image.convert("RGB"), size, Image.LANCZOS)
    stored = f"{uuid.uuid4().hex}.jpg"
    image.convert("RGB").save(os.path.join(directory, stored), "JPEG", quality=quality, optimize=True)
    return stored


def _thumb_size(aspect):
    return (720, 1280) if aspect == "vertical" else (1280, 720)


def save_thumbnail_upload(file_storage, aspect="landscape"):
    filename = getattr(file_storage, "filename", "") or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError("Превью должно быть PNG, JPG или WebP")
    image = Image.open(file_storage.stream)
    image.load()
    return _save_jpg(image, _thumb_dir(), _thumb_size(aspect), quality=88)


def save_thumbnail_data(data_url, aspect="landscape"):
    if not data_url or not data_url.startswith("data:image/"):
        return None
    match = re.match(r"data:image/(?:jpeg|jpg|png|webp);base64,(.+)", data_url, re.S)
    if not match:
        return None
    raw = base64.b64decode(match.group(1), validate=False)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Превью слишком большое")
    image = Image.open(io.BytesIO(raw))
    image.load()
    return _save_jpg(image, _thumb_dir(), _thumb_size(aspect), quality=88)


def save_avatar(file_storage):
    filename = getattr(file_storage, "filename", "") or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError("Аватар должен быть PNG, JPG или WebP")
    image = Image.open(file_storage.stream)
    image.load()
    return _save_jpg(image, _avatar_dir(), (256, 256), quality=90)


def remove_files(*names):
    """Delete stored media files if they exist (never raises)."""
    mapping = [
        (_video_dir(), lambda n: n),
        (_thumb_dir(), lambda n: n),
        (_avatar_dir(), lambda n: n),
    ]
    for name in names:
        if not name:
            continue
        for directory, _ in mapping:
            path = os.path.join(directory, os.path.basename(str(name)))
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@bp.route("/video/<path:filename>")
def video(filename):
    return send_from_directory(_video_dir(), filename, conditional=True)


@bp.route("/thumb/<path:filename>")
def thumb(filename):
    return send_from_directory(_thumb_dir(), filename, conditional=True)


@bp.route("/avatar/<path:filename>")
def avatar(filename):
    return send_from_directory(_avatar_dir(), filename, conditional=True)
