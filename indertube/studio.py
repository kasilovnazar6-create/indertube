"""Creator studio: upload, manage and edit own videos."""
import os

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)

from db import execute, query
from helpers import CATEGORIES, SHORT_MAX_DURATION, login_required
from media import (make_thumbnail, remove_files, save_thumbnail_data,
                   save_thumbnail_upload, save_video)

bp = Blueprint("studio", __name__)

VISIBILITIES = {"public", "unlisted", "private"}


def _format(request_form):
    return "short" if request_form.get("format") == "short" else "video"


def _own_video(video_id):
    video = query(
        """SELECT v.*, u.display_name, u.username
           FROM videos v JOIN users u ON u.id = v.user_id
           WHERE v.id = ?""",
        (video_id,), one=True,
    )
    if video is None:
        abort(404)
    if video["user_id"] != g.user["id"]:
        abort(403)
    return video


@bp.route("/upload", methods=("GET", "POST"))
@login_required
def upload():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        category = request.form.get("category") or "Другое"
        visibility = request.form.get("visibility") or "public"
        thumb_data = request.form.get("thumb_data") or ""
        duration = request.form.get("duration") or "0"
        is_short = 1 if _format(request.form) == "short" else 0
        aspect = "vertical" if is_short else "landscape"

        errors = []
        if not (3 <= len(title) <= 120):
            errors.append("Название должно быть от 3 до 120 символов.")
        if len(description) > 5000:
            errors.append("Описание — не длиннее 5000 символов.")
        if category not in CATEGORIES or category == "Все":
            category = "Другое"
        if visibility not in VISIBILITIES:
            visibility = "public"
        try:
            duration = max(0, min(int(float(duration)), 24 * 3600))
        except (TypeError, ValueError):
            duration = 0
        if is_short and duration > SHORT_MAX_DURATION:
            errors.append(f"Shorts должны быть короче {SHORT_MAX_DURATION} секунд.")

        video_file = request.files.get("video")
        if not video_file or not video_file.filename:
            errors.append("Выберите видеофайл.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("upload.html", categories=CATEGORIES[1:],
                                   form=request.form,
                                   current_format=_format(request.form)), 400

        try:
            filename = save_video(video_file)
        except (ValueError, OSError) as exc:
            flash(str(exc), "error")
            return render_template("upload.html", categories=CATEGORIES[1:],
                                   form=request.form,
                                   current_format=_format(request.form)), 400

        thumb_name = None
        try:
            if thumb_data.startswith("data:image/"):
                thumb_name = save_thumbnail_data(thumb_data, aspect=aspect)
            thumb_file = request.files.get("thumbnail")
            if not thumb_name and thumb_file and thumb_file.filename:
                thumb_name = save_thumbnail_upload(thumb_file, aspect=aspect)
        except (ValueError, OSError) as exc:
            flash(f"Не удалось обработать превью: {exc}", "error")
        if not thumb_name:
            thumb_name = make_thumbnail(title, g.user["display_name"], category, aspect=aspect)

        try:
            video_id = execute(
                """INSERT INTO videos (user_id, title, description, filename, thumbnail,
                                       duration, is_short, category, visibility)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (g.user["id"], title, description, filename, thumb_name,
                 duration, is_short, category, visibility),
            )
        except Exception:
            remove_files(filename, thumb_name)
            raise

        flash("Shorts опубликован!" if is_short else "Видео опубликовано!", "success")
        if is_short:
            return redirect(url_for("main.shorts", video_id=video_id))
        return redirect(url_for("main.watch", video_id=video_id))

    return render_template("upload.html", categories=CATEGORIES[1:], form={},
                           current_format="short" if request.args.get("format") == "short" else "video")


@bp.route("/studio")
@login_required
def index():
    videos = query(
        """SELECT v.*,
                  (SELECT COUNT(*) FROM likes l WHERE l.video_id = v.id AND l.value = 1) AS like_count,
                  (SELECT COUNT(*) FROM comments c WHERE c.video_id = v.id) AS comment_count
           FROM videos v WHERE v.user_id = ?
           ORDER BY v.created_at DESC""",
        (g.user["id"],),
    )
    totals = query(
        """SELECT COUNT(*) AS videos, COALESCE(SUM(views), 0) AS views
           FROM videos WHERE user_id = ?""",
        (g.user["id"],), one=True,
    )
    likes = query(
        "SELECT COUNT(*) AS n FROM likes l JOIN videos v ON v.id = l.video_id "
        "WHERE v.user_id = ? AND l.value = 1",
        (g.user["id"],), one=True,
    )
    comments = query(
        "SELECT COUNT(*) AS n FROM comments c JOIN videos v ON v.id = c.video_id "
        "WHERE v.user_id = ?",
        (g.user["id"],), one=True,
    )
    return render_template("studio.html", videos=videos, totals=totals,
                           likes=likes["n"], comments=comments["n"])


@bp.route("/studio/video/<int:video_id>", methods=("GET", "POST"))
@login_required
def edit(video_id):
    video = _own_video(video_id)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        category = request.form.get("category") or video["category"]
        visibility = request.form.get("visibility") or video["visibility"]

        errors = []
        if not (3 <= len(title) <= 120):
            errors.append("Название должно быть от 3 до 120 символов.")
        if len(description) > 5000:
            errors.append("Описание — не длиннее 5000 символов.")
        if category not in CATEGORIES or category == "Все":
            category = video["category"]
        if visibility not in VISIBILITIES:
            visibility = video["visibility"]

        thumb_name = video["thumbnail"]
        thumb_file = request.files.get("thumbnail")
        if thumb_file and thumb_file.filename:
            try:
                new_thumb = save_thumbnail_upload(
                    thumb_file, aspect="vertical" if video["is_short"] else "landscape"
                )
                if thumb_name:
                    remove_files(thumb_name)
                thumb_name = new_thumb
            except (ValueError, OSError) as exc:
                errors.append(str(exc))

        if errors:
            for error in errors:
                flash(error, "error")
        else:
            execute(
                """UPDATE videos SET title = ?, description = ?, category = ?,
                       visibility = ?, thumbnail = ? WHERE id = ?""",
                (title, description, category, visibility, thumb_name, video_id),
            )
            flash("Изменения сохранены.", "success")
            return redirect(url_for("studio.edit", video_id=video_id))

    return render_template("edit_video.html", video=video, categories=CATEGORIES[1:])


@bp.post("/studio/video/<int:video_id>/delete")
@login_required
def delete(video_id):
    video = _own_video(video_id)
    remove_files(video["filename"], video["thumbnail"])
    execute("DELETE FROM videos WHERE id = ?", (video_id,))
    flash("Удалено.", "success")
    return redirect(url_for("studio.index"))
