"""InderTube — a YouTube-style video platform built with Flask + SQLite.

Run:  python app.py  (then open http://127.0.0.1:5000)
"""
import argparse
import os
import secrets

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge  # noqa: F401  (kept for clarity)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _load_secret_key():
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "secret_key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
            if key:
                return key
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_load_secret_key(),
        DATABASE=os.environ.get("INDERTUBE_DB") or os.path.join(DATA_DIR, "indertube.db"),
        UPLOAD_VIDEO=os.environ.get("INDERTUBE_VIDEO_DIR") or os.path.join(DATA_DIR, "videos"),
        UPLOAD_THUMB=os.environ.get("INDERTUBE_THUMB_DIR") or os.path.join(DATA_DIR, "thumbnails"),
        UPLOAD_AVATAR=os.environ.get("INDERTUBE_AVATAR_DIR") or os.path.join(DATA_DIR, "avatars"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024 * 1024,  # 2 GB
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TEMPLATES_AUTO_RELOAD=True,
    )
    for key in ("UPLOAD_VIDEO", "UPLOAD_THUMB", "UPLOAD_AVATAR"):
        os.makedirs(app.config[key], exist_ok=True)

    from db import init_app as init_db_app
    from db import init_db
    from helpers import init_app as init_helpers

    init_db_app(app)
    if not os.path.exists(app.config["DATABASE"]):
        with app.app_context():
            init_db()
    init_helpers(app)

    from api import bp as api_bp
    from auth import bp as auth_bp
    from media import bp as media_bp
    from studio import bp as studio_bp
    from views import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(studio_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(media_bp)

    from helpers import validate_csrf

    @app.before_request
    def csrf_guard():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            return validate_csrf()
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="not_found"), 404
        return render_template("error.html", code=404, message="Страница не найдена"), 404

    @app.errorhandler(403)
    def forbidden(error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="forbidden"), 403
        return render_template("error.html", code=403, message="Доступ запрещён"), 403

    @app.errorhandler(413)
    def too_large(error):
        flash("Файл слишком большой. Максимальный размер — 2 ГБ.", "error")
        return redirect(url_for("studio.upload"))

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled server error")
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="server_error"), 500
        return render_template("error.html", code=500, message="Что-то пошло не так"), 500

    @app.template_filter("nl2br")
    def nl2br(value):
        from markupsafe import Markup, escape
        return Markup("<br>".join(escape(value or "").split("\n")))

    return app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InderTube server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
