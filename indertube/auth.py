"""Registration, login, logout and channel settings."""
import re

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import execute, query
from helpers import AVATAR_COLORS, login_required, safe_next
from media import remove_files, save_avatar

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD = 8


def _login_user(user_id):
    session.clear()
    session["user_id"] = user_id
    session.permanent = True


def _darken(hex_color, factor=0.32):
    value = hex_color.lstrip("#")
    rgb = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{int(channel * factor):02x}" for channel in rgb)


@bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("main.index"))

    form = {"username": "", "display_name": "", "email": ""}
    if request.method == "POST":
        form["username"] = (request.form.get("username") or "").strip().lower()
        form["display_name"] = (request.form.get("display_name") or "").strip()
        form["email"] = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""

        errors = []
        if not USERNAME_RE.match(form["username"]):
            errors.append("Имя канала: 3–30 символов, латиница, цифры, точка или подчёркивание.")
        if not form["display_name"]:
            form["display_name"] = form["username"]
        if len(form["display_name"]) > 60:
            errors.append("Название канала — не длиннее 60 символов.")
        if not EMAIL_RE.match(form["email"]):
            errors.append("Укажите корректный e-mail.")
        if len(password) < MIN_PASSWORD:
            errors.append(f"Пароль должен содержать минимум {MIN_PASSWORD} символов.")
        if password != password2:
            errors.append("Пароли не совпадают.")
        if query("SELECT 1 FROM users WHERE username = ?", (form["username"],), one=True):
            errors.append("Такой канал уже существует.")
        if query("SELECT 1 FROM users WHERE email = ?", (form["email"],), one=True):
            errors.append("На этот e-mail уже зарегистрирован аккаунт.")

        if errors:
            for error in errors:
                flash(error, "error")
        else:
            import random
            color = random.choice(AVATAR_COLORS)
            user_id = execute(
                """INSERT INTO users (username, email, password_hash, display_name,
                                      avatar_color, banner_color)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (form["username"], form["email"], generate_password_hash(password),
                 form["display_name"], color, _darken(color)),
            )
            _login_user(user_id)
            flash("Добро пожаловать на InderTube! Канал создан.", "success")
            return redirect(url_for("main.index"))

    return render_template("register.html", form=form)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("main.index"))

    form = {"identifier": ""}
    if request.method == "POST":
        form["identifier"] = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        user = None
        if form["identifier"]:
            user = query(
                "SELECT * FROM users WHERE email = ? OR username = ?",
                (form["identifier"], form["identifier"]), one=True,
            )
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Неверный логин или пароль.", "error")
        else:
            _login_user(user["id"])
            flash(f"С возвращением, {user['display_name']}!", "success")
            target = safe_next(request.args.get("next") or request.form.get("next"))
            return redirect(target or url_for("main.index"))

    return render_template("login.html", form=form, next_url=request.args.get("next", ""))


@bp.post("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("main.index"))


@bp.route("/settings", methods=("GET", "POST"))
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "profile":
            display_name = (request.form.get("display_name") or "").strip()
            username = (request.form.get("username") or "").strip().lower()
            bio = (request.form.get("bio") or "").strip()
            avatar_color = request.form.get("avatar_color") or g.user["avatar_color"]
            banner_color = request.form.get("banner_color") or g.user["banner_color"]

            errors = []
            if not display_name or len(display_name) > 60:
                errors.append("Название канала: от 1 до 60 символов.")
            if not USERNAME_RE.match(username):
                errors.append("Ссылка на канал: 3–30 символов, латиница, цифры, точка или подчёркивание.")
            if len(bio) > 1000:
                errors.append("Описание — не длиннее 1000 символов.")
            other = query("SELECT id FROM users WHERE username = ? AND id <> ?",
                          (username, g.user["id"]), one=True)
            if other:
                errors.append("Эта ссылка на канал уже занята.")
            if avatar_color not in AVATAR_COLORS:
                avatar_color = g.user["avatar_color"]
            if not re.match(r"^#[0-9a-fA-F]{6}$", banner_color or ""):
                banner_color = g.user["banner_color"]

            avatar_file = g.user["avatar_file"]
            upload = request.files.get("avatar")
            if upload and upload.filename:
                try:
                    new_avatar = save_avatar(upload)
                    if avatar_file:
                        remove_files(avatar_file)
                    avatar_file = new_avatar
                except (ValueError, OSError) as exc:
                    errors.append(str(exc))

            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                execute(
                    """UPDATE users SET display_name = ?, username = ?, bio = ?,
                           avatar_color = ?, banner_color = ?, avatar_file = ?
                       WHERE id = ?""",
                    (display_name, username, bio, avatar_color, banner_color,
                     avatar_file, g.user["id"]),
                )
                flash("Настройки канала сохранены.", "success")
                return redirect(url_for("auth.settings"))

        elif action == "password":
            current = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            new2 = request.form.get("new_password2") or ""
            if not check_password_hash(g.user["password_hash"], current):
                flash("Текущий пароль неверен.", "error")
            elif len(new) < MIN_PASSWORD:
                flash(f"Новый пароль должен содержать минимум {MIN_PASSWORD} символов.", "error")
            elif new != new2:
                flash("Новые пароли не совпадают.", "error")
            else:
                execute("UPDATE users SET password_hash = ? WHERE id = ?",
                        (generate_password_hash(new), g.user["id"]))
                flash("Пароль обновлён.", "success")
                return redirect(url_for("auth.settings"))

    stats = query(
        """SELECT COUNT(*) AS videos, COALESCE(SUM(views), 0) AS views
           FROM videos WHERE user_id = ? AND visibility <> 'private'""",
        (g.user["id"],), one=True,
    )
    return render_template("settings.html", stats=stats)
