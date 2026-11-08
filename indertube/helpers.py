"""Shared helpers: auth guards, CSRF, Russian formatting, sidebar data."""
import hmac
import secrets
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import flash, g, jsonify, redirect, request, session, url_for

from db import query
from icons import ICONS

CATEGORIES = [
    "Все", "Технологии", "Музыка", "Игры", "Наука", "Путешествия",
    "Еда", "Влоги", "Образование", "Спорт", "Кино",
]

AVATAR_COLORS = [
    "#e53935", "#d81b60", "#8e24aa", "#5e35b1", "#3949ab", "#1e88e5",
    "#00897b", "#43a047", "#f4511e", "#fb8c00", "#6d4c41", "#546e7a",
]

SHORT_MAX_DURATION = 60

MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


# --------------------------------------------------------------------------
# auth / csrf
# --------------------------------------------------------------------------

def load_logged_in_user():
    uid = session.get("user_id")
    g.user = None
    if uid is not None:
        user = query("SELECT * FROM users WHERE id = ?", (uid,), one=True)
        if user is None:
            session.pop("user_id", None)
        g.user = user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="auth_required"), 401
            target = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login", next=target))
        return view(*args, **kwargs)

    return wrapped


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def validate_csrf():
    """Return an error response when the CSRF token is missing/invalid."""
    token = session.get("_csrf")
    sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
    if not sent:
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            sent = data.get("_csrf")
    if token and sent and hmac.compare_digest(token, sent):
        return None
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="csrf"), 400
    flash("Сессия устарела. Попробуйте ещё раз.", "error")
    return redirect(request.referrer or url_for("main.index"))


def safe_next(target):
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


# --------------------------------------------------------------------------
# formatting (ru)
# --------------------------------------------------------------------------

def plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def human_num(n):
    n = int(n or 0)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        value = round(n / 1000, 1)
        text = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"{text.replace('.', ',')} тыс."
    value = round(n / 1_000_000, 1)
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} млн"


def _count_text(n, one, few, many):
    """«тыс.»/«млн» are abbreviations of plural nouns, so the many-form follows."""
    n = int(n or 0)
    word = plural(n, one, few, many) if n < 1000 else many
    return f"{human_num(n)} {word}"


def views_text(n):
    return _count_text(n, "просмотр", "просмотра", "просмотров")


def subs_text(n):
    return _count_text(n, "подписчик", "подписчика", "подписчиков")


def comments_text(n):
    return _count_text(n, "комментарий", "комментария", "комментариев")


def duration(value):
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return "0:00"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_dt(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def timeago(value):
    then = parse_dt(value)
    seconds = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    if seconds < 60:
        return "только что"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')} назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')} назад"
    days = hours // 24
    if days < 7:
        return f"{days} {plural(days, 'день', 'дня', 'дней')} назад"
    weeks = days // 7
    if days < 30:
        return f"{weeks} {plural(weeks, 'неделю', 'недели', 'недель')} назад"
    months = days // 30
    if days < 365:
        return f"{months} {plural(months, 'месяц', 'месяца', 'месяцев')} назад"
    years = days // 365
    return f"{years} {plural(years, 'год', 'года', 'лет')} назад"


def ru_date(value):
    dt = parse_dt(value)
    return f"{dt.day} {MONTHS_GEN[dt.month - 1]} {dt.year} г."


def initials(name):
    parts = [p for p in str(name or "?").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if parts:
        return parts[0][:2].upper()
    return "?"


# --------------------------------------------------------------------------
# channel helpers
# --------------------------------------------------------------------------

def subscriber_count(channel_id):
    row = query("SELECT COUNT(*) AS n FROM subscriptions WHERE channel_id = ?", (channel_id,), one=True)
    return row["n"] if row else 0


def is_subscribed(channel_id):
    user = g.get("user")
    if not user:
        return False
    row = query(
        "SELECT 1 FROM subscriptions WHERE subscriber_id = ? AND channel_id = ?",
        (user["id"], channel_id), one=True,
    )
    return row is not None


def get_or_create_watch_later(user_id):
    row = query(
        "SELECT * FROM playlists WHERE user_id = ? AND kind = 'watch_later'", (user_id,), one=True
    )
    if row:
        return row
    from db import execute
    execute("INSERT INTO playlists (user_id, title, kind, visibility) VALUES (?, 'Смотреть позже', 'watch_later', 'private')", (user_id,))
    return query(
        "SELECT * FROM playlists WHERE user_id = ? AND kind = 'watch_later'", (user_id,), one=True
    )


def sidebar_subs():
    user = g.get("user")
    if not user:
        return []
    return query(
        """SELECT u.id, u.username, u.display_name, u.avatar_color, u.avatar_file,
                  (SELECT COUNT(*) FROM videos v WHERE v.user_id = u.id AND v.visibility = 'public') AS video_count
           FROM subscriptions s JOIN users u ON u.id = s.channel_id
           WHERE s.subscriber_id = ?
           ORDER BY u.display_name COLLATE NOCASE
           LIMIT 20""",
        (user["id"],),
    )


# --------------------------------------------------------------------------
# app wiring
# --------------------------------------------------------------------------

def init_app(app):
    app.before_request(load_logged_in_user)

    app.jinja_env.globals.update(ICONS=ICONS, csrf_token=csrf_token)

    app.jinja_env.filters.update({
        "human_num": human_num,
        "views_text": views_text,
        "subs_text": subs_text,
        "comments_text": comments_text,
        "duration": duration,
        "timeago": timeago,
        "ru_date": ru_date,
        "initials": initials,
        "plural": plural,
    })

    @app.context_processor
    def inject_globals():
        return {
            "current_user": g.get("user"),
            "CATEGORIES": CATEGORIES,
            "AVATAR_COLORS": AVATAR_COLORS,
            "ICONS": ICONS,
            "csrf_token": csrf_token,
            "subs_sidebar": sidebar_subs(),
        }
