"""JSON API used by the front-end (likes, comments, subscriptions, playlists)."""
from flask import (Blueprint, abort, g, jsonify, render_template, request,
                   session)

from db import execute, query
from helpers import (get_or_create_watch_later, human_num, login_required,
                     subscriber_count, subs_text, views_text)

bp = Blueprint("api", __name__, url_prefix="/api")


def _json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _comment_row(comment_id):
    return query(
        """SELECT c.*, u.username, u.display_name, u.avatar_color, u.avatar_file,
                  (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id = c.id) AS likes,
                  EXISTS(SELECT 1 FROM comment_likes cl
                         WHERE cl.comment_id = c.id AND cl.user_id = ?) AS liked
           FROM comments c JOIN users u ON u.id = c.user_id
           WHERE c.id = ?""",
        (g.user["id"], comment_id), one=True,
    )


def _comment_count(video_id):
    row = query("SELECT COUNT(*) AS n FROM comments WHERE video_id = ?", (video_id,), one=True)
    return row["n"] if row else 0


# --------------------------------------------------------------------------
# views & history
# --------------------------------------------------------------------------

@bp.post("/videos/<int:video_id>/view")
def record_view(video_id):
    video = query("SELECT id, views, visibility FROM videos WHERE id = ?", (video_id,), one=True)
    if video is None:
        abort(404)
    if video["visibility"] == "private" and (not g.user or g.user["id"] != video["user_id"]):
        abort(404)

    viewed = session.get("viewed", [])
    if video_id not in viewed:
        execute("UPDATE videos SET views = views + 1 WHERE id = ?", (video_id,))
        viewed.append(video_id)
        session["viewed"] = viewed[-400:]

    if g.user:
        execute(
            """INSERT INTO history (user_id, video_id, watched_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(user_id, video_id) DO UPDATE SET watched_at = excluded.watched_at""",
            (g.user["id"], video_id),
        )

    row = query("SELECT views FROM videos WHERE id = ?", (video_id,), one=True)
    return jsonify(ok=True, views=row["views"], views_text=views_text(row["views"]))


@bp.post("/history/clear")
@login_required
def clear_history():
    execute("DELETE FROM history WHERE user_id = ?", (g.user["id"],))
    return jsonify(ok=True)


@bp.delete("/history/<int:video_id>")
@login_required
def remove_history(video_id):
    execute("DELETE FROM history WHERE user_id = ? AND video_id = ?",
            (g.user["id"], video_id))
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# likes / subscriptions
# --------------------------------------------------------------------------

@bp.post("/videos/<int:video_id>/like")
@login_required
def like_video(video_id):
    if query("SELECT 1 FROM videos WHERE id = ?", (video_id,), one=True) is None:
        abort(404)
    try:
        value = int(_json().get("value", 1))
    except (TypeError, ValueError):
        value = 0
    if value not in (-1, 1):
        return jsonify(ok=False, error="bad_value"), 400

    existing = query("SELECT value FROM likes WHERE user_id = ? AND video_id = ?",
                     (g.user["id"], video_id), one=True)
    if existing and existing["value"] == value:
        execute("DELETE FROM likes WHERE user_id = ? AND video_id = ?",
                (g.user["id"], video_id))
        user_like = 0
    elif existing:
        execute("UPDATE likes SET value = ? WHERE user_id = ? AND video_id = ?",
                (value, g.user["id"], video_id))
        user_like = value
    else:
        execute("INSERT INTO likes (user_id, video_id, value) VALUES (?, ?, ?)",
                (g.user["id"], video_id, value))
        user_like = value

    counts = query(
        """SELECT COALESCE(SUM(value = 1), 0) AS likes,
                  COALESCE(SUM(value = -1), 0) AS dislikes
           FROM likes WHERE video_id = ?""",
        (video_id,), one=True,
    )
    return jsonify(ok=True, likes=counts["likes"], dislikes=counts["dislikes"],
                   likes_text=human_num(counts["likes"]), user_like=user_like)


@bp.post("/channels/<int:channel_id>/subscribe")
@login_required
def subscribe(channel_id):
    if channel_id == g.user["id"]:
        return jsonify(ok=False, error="self_subscribe"), 400
    if query("SELECT 1 FROM users WHERE id = ?", (channel_id,), one=True) is None:
        abort(404)

    existing = query("SELECT 1 FROM subscriptions WHERE subscriber_id = ? AND channel_id = ?",
                     (g.user["id"], channel_id), one=True)
    if existing:
        execute("DELETE FROM subscriptions WHERE subscriber_id = ? AND channel_id = ?",
                (g.user["id"], channel_id))
        subscribed = False
    else:
        execute("INSERT INTO subscriptions (subscriber_id, channel_id) VALUES (?, ?)",
                (g.user["id"], channel_id))
        subscribed = True

    count = subscriber_count(channel_id)
    return jsonify(ok=True, subscribed=subscribed, subscribers=count, subs_text=subs_text(count))


# --------------------------------------------------------------------------
# comments
# --------------------------------------------------------------------------

@bp.post("/videos/<int:video_id>/comments")
@login_required
def add_comment(video_id):
    video = query("SELECT id, user_id, visibility FROM videos WHERE id = ?",
                  (video_id,), one=True)
    if video is None:
        abort(404)
    if video["visibility"] == "private" and video["user_id"] != g.user["id"]:
        abort(404)

    data = _json()
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify(ok=False, error="empty"), 400
    if len(text) > 2000:
        return jsonify(ok=False, error="too_long"), 400

    parent_id = data.get("parent_id")
    parent_id = int(parent_id) if str(parent_id or "").isdigit() else None
    if parent_id:
        parent = query("SELECT id, parent_id FROM comments WHERE id = ? AND video_id = ?",
                       (parent_id, video_id), one=True)
        if parent is None or parent["parent_id"] is not None:
            return jsonify(ok=False, error="bad_parent"), 400

    comment_id = execute(
        "INSERT INTO comments (video_id, user_id, parent_id, text) VALUES (?, ?, ?, ?)",
        (video_id, g.user["id"], parent_id, text),
    )
    comment = _comment_row(comment_id)
    html = render_template("partials/comment.html", c=comment, c_replies=[], video=video)
    return jsonify(ok=True, html=html, comment_id=comment_id,
                   count=_comment_count(video_id), comment=comment["display_name"])


@bp.get("/videos/<int:video_id>/comments")
def list_comments(video_id):
    video = query("SELECT id, user_id, visibility FROM videos WHERE id = ?",
                  (video_id,), one=True)
    if video is None:
        abort(404)
    if video["visibility"] == "private" and (not g.user or g.user["id"] != video["user_id"]):
        abort(404)

    uid = g.user["id"] if g.user else None
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

    html = "".join(
        render_template("partials/comment.html", c=comment,
                        c_replies=replies_map.get(comment["id"], []), video=video)
        for comment in comments
    )
    return jsonify(ok=True, html=html, count=_comment_count(video_id))


@bp.post("/comments/<int:comment_id>/like")
@login_required
def like_comment(comment_id):
    if query("SELECT 1 FROM comments WHERE id = ?", (comment_id,), one=True) is None:
        abort(404)
    existing = query("SELECT 1 FROM comment_likes WHERE comment_id = ? AND user_id = ?",
                     (comment_id, g.user["id"]), one=True)
    if existing:
        execute("DELETE FROM comment_likes WHERE comment_id = ? AND user_id = ?",
                (comment_id, g.user["id"]))
        liked = False
    else:
        execute("INSERT INTO comment_likes (comment_id, user_id) VALUES (?, ?)",
                (comment_id, g.user["id"]))
        liked = True
    row = query("SELECT COUNT(*) AS n FROM comment_likes WHERE comment_id = ?",
                (comment_id,), one=True)
    return jsonify(ok=True, liked=liked, likes=row["n"], likes_text=human_num(row["n"]))


@bp.delete("/comments/<int:comment_id>")
@login_required
def delete_comment(comment_id):
    comment = query(
        """SELECT c.*, v.user_id AS channel_id FROM comments c
           JOIN videos v ON v.id = c.video_id WHERE c.id = ?""",
        (comment_id,), one=True,
    )
    if comment is None:
        abort(404)
    if g.user["id"] not in (comment["user_id"], comment["channel_id"]):
        return jsonify(ok=False, error="forbidden"), 403
    execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return jsonify(ok=True, count=_comment_count(comment["video_id"]))


# --------------------------------------------------------------------------
# playlists
# --------------------------------------------------------------------------

@bp.get("/playlists")
@login_required
def list_playlists():
    video_id = request.args.get("video_id", type=int)
    rows = query(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM playlist_items pi WHERE pi.playlist_id = p.id) AS item_count,
                  EXISTS(SELECT 1 FROM playlist_items pi
                         WHERE pi.playlist_id = p.id AND pi.video_id = ?) AS contains
           FROM playlists p WHERE p.user_id = ?
           ORDER BY (p.kind = 'watch_later') DESC, p.created_at DESC""",
        (video_id or -1, g.user["id"]),
    )
    return jsonify(ok=True, playlists=[dict(row) for row in rows])


@bp.post("/playlists")
@login_required
def create_playlist():
    title = str(_json().get("title") or "").strip()
    if not (1 <= len(title) <= 80):
        return jsonify(ok=False, error="bad_title"), 400
    playlist_id = execute(
        "INSERT INTO playlists (user_id, title, visibility) VALUES (?, ?, 'private')",
        (g.user["id"], title),
    )
    row = query("SELECT * FROM playlists WHERE id = ?", (playlist_id,), one=True)
    return jsonify(ok=True, playlist=dict(row), item_count=0)


@bp.post("/playlists/<int:playlist_id>/add")
@login_required
def toggle_playlist_item(playlist_id):
    playlist = query("SELECT * FROM playlists WHERE id = ? AND user_id = ?",
                     (playlist_id, g.user["id"]), one=True)
    if playlist is None:
        abort(404)
    try:
        video_id = int(_json().get("video_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="bad_video"), 400
    if query("SELECT 1 FROM videos WHERE id = ?", (video_id,), one=True) is None:
        abort(404)

    existing = query("SELECT 1 FROM playlist_items WHERE playlist_id = ? AND video_id = ?",
                     (playlist_id, video_id), one=True)
    if existing:
        execute("DELETE FROM playlist_items WHERE playlist_id = ? AND video_id = ?",
                (playlist_id, video_id))
        added = False
    else:
        row = query("SELECT COALESCE(MAX(position), 0) + 1 AS next FROM playlist_items WHERE playlist_id = ?",
                    (playlist_id,), one=True)
        execute(
            "INSERT INTO playlist_items (playlist_id, video_id, position) VALUES (?, ?, ?)",
            (playlist_id, video_id, row["next"]),
        )
        added = True

    count = query("SELECT COUNT(*) AS n FROM playlist_items WHERE playlist_id = ?",
                  (playlist_id,), one=True)["n"]
    return jsonify(ok=True, added=added, count=count)


@bp.delete("/playlists/<int:playlist_id>")
@login_required
def delete_playlist(playlist_id):
    playlist = query("SELECT * FROM playlists WHERE id = ? AND user_id = ?",
                     (playlist_id, g.user["id"]), one=True)
    if playlist is None:
        abort(404)
    if playlist["kind"] == "watch_later":
        return jsonify(ok=False, error="system_playlist"), 400
    execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    return jsonify(ok=True)


@bp.post("/watch-later/<int:video_id>")
@login_required
def toggle_watch_later(video_id):
    if query("SELECT 1 FROM videos WHERE id = ?", (video_id,), one=True) is None:
        abort(404)
    playlist = get_or_create_watch_later(g.user["id"])
    existing = query("SELECT 1 FROM playlist_items WHERE playlist_id = ? AND video_id = ?",
                     (playlist["id"], video_id), one=True)
    if existing:
        execute("DELETE FROM playlist_items WHERE playlist_id = ? AND video_id = ?",
                (playlist["id"], video_id))
        added = False
    else:
        row = query("SELECT COALESCE(MAX(position), 0) + 1 AS next FROM playlist_items WHERE playlist_id = ?",
                    (playlist["id"],), one=True)
        execute("INSERT INTO playlist_items (playlist_id, video_id, position) VALUES (?, ?, ?)",
                (playlist["id"], video_id, row["next"]))
        added = True
    return jsonify(ok=True, added=added)


@bp.get("/suggest")
def suggest():
    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify(ok=True, items=[])
    like = f"%{term}%"
    videos = query(
        "SELECT title FROM videos WHERE visibility = 'public' AND title LIKE ? "
        "ORDER BY views DESC LIMIT 6", (like,),
    )
    channels = query(
        "SELECT username, display_name, avatar_color FROM users "
        "WHERE username LIKE ? OR display_name LIKE ? LIMIT 3",
        (like, like),
    )
    items = [{"type": "video", "text": row["title"], "query": row["title"]} for row in videos]
    items += [{"type": "channel", "text": row["display_name"], "query": row["username"],
               "color": row["avatar_color"], "handle": row["username"]} for row in channels]
    return jsonify(ok=True, items=items)
