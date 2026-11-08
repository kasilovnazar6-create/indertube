"""SQLite access layer: one connection per request, tiny query helpers."""
import sqlite3

from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=20,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    lastrowid = cur.lastrowid
    cur.close()
    return lastrowid


def executescript(script):
    db = get_db()
    db.executescript(script)
    db.commit()


def init_db():
    with current_app.open_resource("schema.sql") as fh:
        executescript(fh.read().decode("utf-8"))


def init_app(app):
    app.teardown_appcontext(close_db)
