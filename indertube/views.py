"""Public pages: home, watch, shorts, search, channels, feeds, playlists."""
from flask import (Blueprint, abort, g, redirect, render_template, request,
                   url_for)

from db import query
from helpers import (CATEGORIES, get_or_create_watch_later, is_subscribed,
                     subscriber_count)

bp = Blueprint("main", __name__)

VIDEO_FIELDS = """
    v.id, v.user_id, v.title, v.description, v.filename, v.thumbnail, v.duration,
    v.is_short, v.category, v.visibility, v.views, v.created_at,
    u.username, u.display_name, u.avatar_color, u.avatar_file,
    (SELECT COUNT(*) FROM likes l WHERE l.video_id = v.id AND l.value = 1) AS like_count,
    (SELECT COUNT(*) FROM comments c WHERE c.video_id = v.id) AS comment_count
"""

VIDEO_FROM = "FROM videos v JOIN users u ON u.id = v.user_id"


# --------------------------------------------------------------------------
# home / feeds
# --------------------------------------------------------------------------

@bp.route("/")
def index():
    category = request.args.get("category") or "Все"
    if category not in CATEGORIES:
        category = "Все"

    params = []
    where = "v.visibility = 'public' AND v.is_short = 0"
    if category != "Все":
        where += " AND v.category = ?"
        params.append(category)

    videos = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} WHERE {where} "
        "ORDER BY v.created_at DESC LIMIT 60",
        params,
    )
    shorts = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
        "WHERE v.visibility = 'public' AND v.is_short = 1 "
        "ORDER BY v.views DESC, v.created_at DESC LIMIT 10"
    )
    return render_template("index.html", videos=videos, shorts=shorts,
                           active_category=category)


@bp.route("/feed/trending")
def trending():
    videos = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
        "WHERE v.visibility = 'public' AND v.is_short = 0 "
        "ORDER BY v.views DESC, v.created_at DESC LIMIT 40"
    )
    return render_template("trending.html", videos=videos)


@bp.route("/feed/subscriptions")
def subscriptions():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))

    channels = query(
        """SELECT u.id, u.username, u.display_name, u.avatar_color, u.avatar_file,
                  (SELECT COUNT(*) FROM subscriptions s2 WHERE s2.channel_id = u.id) AS subs
           FROM subscriptions s JOIN users u ON u.id = s.channel_id
           WHERE s.subscriber_id = ?
           ORDER BY u.display_name COLLATE NOCASE""",
        (g.user["id"],),
    )
    videos = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
        "WHERE v.visibility = 'public' AND v.is_short = 0 AND v.user_id IN "
        "(SELECT channel_id FROM subscriptions WHERE subscriber_id = ?) "
        "ORDER BY v.created_at DESC LIMIT 60",
        (g.user["id"],),
    )
    suggestions = []
    if not channels:
        suggestions = query(
            f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
            "WHERE v.visibility = 'public' AND v.is_short = 0 "
            "ORDER BY v.views DESC LIMIT 8"
        )
    return render_template("subscriptions.html", videos=videos, channels=channels,
                           suggestions=suggestions)


@bp.route("/feed/history")
def history():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    videos = query(
        f"""SELECT {VIDEO_FIELDS}, h.watched_at
            FROM history h
            JOIN videos v ON v.id = h.video_id
            JOIN users u ON u.id = v.user_id
            WHERE h.user_id = ?
            ORDER BY h.watched_at DESC LIMIT 200""",
        (g.user["id"],),
    )
    return render_template("history.html", videos=videos)


@bp.route("/feed/liked")
def liked():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    videos = query(
        f"SELECT {VIDEO_FIELDS}, l.created_at AS liked_at "
        f"{VIDEO_FROM} JOIN likes l ON l.video_id = v.id "
        "WHERE l.user_id = ? AND l.value = 1 "
        "ORDER BY l.created_at DESC LIMIT 200",
        (g.user["id"],),
    )
    return render_template("liked.html", videos=videos)


# --------------------------------------------------------------------------
# watch / shorts
# --------------------------------------------------------------------------

@bp.route("/watch/<int:video_id>")
def watch(video_id):
    video = query(
        f"SELECT {VIDEO_FIELDS}, u.bio AS channel_bio {VIDEO_FROM} WHERE v.id = ?",
        (video_id,), one=True,
    )
    if video is None:
        abort(404)
    if video["visibility"] == "private" and (not g.user or g.user["id"] != video["user_id"]):
        abort(404)
    if video["is_short"]:
        return redirect(url_for("main.shorts", video_id=video_id))

    uid = g.user["id"] if g.user else None
    user_like = 0
    if uid:
        row = query("SELECT value FROM likes WHERE user_id = ? AND video_id = ?",
                    (uid, video_id), one=True)
        user_like = row["value"] if row else 0

    comments = query(
        """SELECT c.*, u.username, u.display_name, u.avatar_color, u.avatar_file,
                  (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id = c.id) AS likes,
                  EXISTS(SELECT 1 FROM comment_likes cl
                         WHERE cl.comment_id = c.id AND cl.user_id = ?) AS liked
           FROM comments c JOIN users u ON u.id = c.user_id
           WHERE c.video_id = ? AND c.parent_id IS NULL
           ORDER BY c.created_at DESC""",
        (uid, video_id),
    )
    replies = query(
        """SELECT c.*, u.username, u.display_name, u.avatar_color, u.avatar_file,
                  (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id = c.id) AS likes,
                  EXISTS(SELECT 1 FROM comment_likes cl
                         WHERE cl.comment_id = c.id AND cl.user_id = ?) AS liked
           FROM comments c JOIN users u ON u.id = c.user_id
           WHERE c.video_id = ? AND c.parent_id IS NOT NULL
           ORDER BY c.created_at ASC""",
        (uid, video_id),
    )
    replies_map = {}
    for reply in replies:
        replies_map.setdefault(reply["parent_id"], []).append(reply)

    related = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
        "WHERE v.visibility = 'public' AND v.is_short = 0 AND v.id <> ? "
        "ORDER BY (v.category = ?) DESC, v.views DESC, v.created_at DESC LIMIT 14",
        (video_id, video["category"]),
    )

    return render_template(
        "watch.html",
        video=video,
        related=related,
        comments=comments,
        replies_map=replies_map,
        user_like=user_like,
        channel_subs=subscriber_count(video["user_id"]),
        subscribed=is_subscribed(video["user_id"]),
        is_owner=bool(g.user and g.user["id"] == video["user_id"]),
    )


@bp.route("/shorts")
@bp.route("/shorts/<int:video_id>")
def shorts(video_id=None):
    rows = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
        "WHERE v.visibility = 'public' AND v.is_short = 1 "
        "ORDER BY v.views DESC, v.created_at DESC LIMIT 60"
    )
    if video_id is not None:
        current = query(
            f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} WHERE v.id = ? AND v.is_short = 1",
            (video_id,), one=True,
        )
        if current is None:
            abort(404)
        if current["visibility"] == "private" and (not g.user or g.user["id"] != current["user_id"]):
            abort(404)
        if all(row["id"] != current["id"] for row in rows):
            rows.insert(0, current)

    subs_by_channel = {}
    for row in rows:
        channel_id = row["user_id"]
        if channel_id not in subs_by_channel:
            subs_by_channel[channel_id] = {
                "subscribed": is_subscribed(channel_id),
                "count": subscriber_count(channel_id),
            }

    liked_ids = set()
    if g.user and rows:
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in ids)
        liked_ids = {
            row["video_id"] for row in query(
                "SELECT video_id FROM likes WHERE user_id = ? AND value = 1 "
                f"AND video_id IN ({placeholders})",
                [g.user["id"], *ids],
            )
        }

    return render_template("shorts.html", shorts=rows, active_id=video_id,
                           subs_by_channel=subs_by_channel, liked_ids=liked_ids)


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

@bp.route("/results")
def results():
    term = (request.args.get("search_query") or "").strip()
    sort = request.args.get("sort") or "relevance"
    if sort not in ("relevance", "date", "views"):
        sort = "relevance"

    videos, total = [], 0
    if term:
        like = f"%{term}%"
        order = {
            "relevance": "v.views DESC, v.created_at DESC",
            "date": "v.created_at DESC",
            "views": "v.views DESC",
        }[sort]
        videos = query(
            f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
            "WHERE v.visibility = 'public' AND (v.title LIKE ? OR v.description LIKE ? "
            "OR u.display_name LIKE ? OR u.username LIKE ?) "
            f"ORDER BY {order} LIMIT 60",
            (like, like, like, like),
        )
        total = len(videos)

    return render_template("results.html", videos=videos, term=term, sort=sort, total=total)


# --------------------------------------------------------------------------
# channel
# --------------------------------------------------------------------------

@bp.route("/channel/<username>")
def channel(username):
    user = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
    if user is None:
        abort(404)

    is_owner = bool(g.user and g.user["id"] == user["id"])
    tab = request.args.get("tab") or "videos"
    if tab not in ("videos", "shorts", "about"):
        tab = "videos"

    visibility = "" if is_owner else " AND v.visibility = 'public'"
    videos = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
        f"WHERE v.user_id = ? AND v.is_short = 0{visibility} "
        "ORDER BY v.created_at DESC",
        (user["id"],),
    )
    shorts = query(
        f"SELECT {VIDEO_FIELDS} {VIDEO_FROM} "
        f"WHERE v.user_id = ? AND v.is_short = 1{visibility} "
        "ORDER BY v.created_at DESC",
        (user["id"],),
    )
    stats = query(
        """SELECT COUNT(*) AS videos, COALESCE(SUM(views), 0) AS views
           FROM videos WHERE user_id = ? AND visibility = 'public'""",
        (user["id"],), one=True,
    )
    return render_template(
        "channel.html",
        channel=user,
        videos=videos,
        shorts=shorts,
        stats=stats,
        tab=tab,
        subs=subscriber_count(user["id"]),
        subscribed=is_subscribed(user["id"]),
        is_owner=is_owner,
    )


# --------------------------------------------------------------------------
# playlists
# --------------------------------------------------------------------------

@bp.route("/feed/playlists")
def playlists():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    get_or_create_watch_later(g.user["id"])
    rows = query(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM playlist_items pi WHERE pi.playlist_id = p.id) AS item_count,
                  (SELECT v.thumbnail FROM playlist_items pi JOIN videos v ON v.id = pi.video_id
                   WHERE pi.playlist_id = p.id ORDER BY pi.position, pi.added_at LIMIT 1) AS cover
           FROM playlists p
           WHERE p.user_id = ?
           ORDER BY (p.kind = 'watch_later') DESC, p.created_at DESC""",
        (g.user["id"],),
    )
    return render_template("playlists.html", playlists=rows)


@bp.route("/playlist/watch-later")
def watch_later():
    if not g.user:
        return redirect(url_for("auth.login", next=request.path))
    playlist = get_or_create_watch_later(g.user["id"])
    return redirect(url_for("main.playlist", playlist_id=playlist["id"]))


@bp.route("/playlist/<int:playlist_id>")
def playlist(playlist_id):
    row = query("SELECT * FROM playlists WHERE id = ?", (playlist_id,), one=True)
    if row is None:
        abort(404)
    is_owner = bool(g.user and g.user["id"] == row["user_id"])
    if row["visibility"] == "private" and not is_owner:
        abort(404)

    items = query(
        f"""SELECT {VIDEO_FIELDS}, pi.position, pi.added_at
            FROM playlist_items pi
            JOIN videos v ON v.id = pi.video_id
            JOIN users u ON u.id = v.user_id
            WHERE pi.playlist_id = ?
            ORDER BY pi.position, pi.added_at""",
        (playlist_id,),
    )
    total_duration = sum(int(item["duration"] or 0) for item in items)
    owner = query("SELECT * FROM users WHERE id = ?", (row["user_id"],), one=True)
    return render_template("playlist.html", playlist=row, items=items,
                           total_duration=total_duration, owner=owner, is_owner=is_owner)
