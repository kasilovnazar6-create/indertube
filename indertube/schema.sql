-- InderTube schema (SQLite)

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT    NOT NULL,
    bio           TEXT    NOT NULL DEFAULT '',
    avatar_color  TEXT    NOT NULL DEFAULT '#ff4d4d',
    avatar_file   TEXT,
    banner_color  TEXT    NOT NULL DEFAULT '#1f1f1f',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    filename    TEXT    NOT NULL,
    thumbnail   TEXT    NOT NULL,
    duration    INTEGER NOT NULL DEFAULT 0,
    is_short    INTEGER NOT NULL DEFAULT 0,
    category    TEXT    NOT NULL DEFAULT 'Другое',
    visibility  TEXT    NOT NULL DEFAULT 'public'
                CHECK (visibility IN ('public', 'unlisted', 'private')),
    views       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS likes (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    value      INTEGER NOT NULL CHECK (value IN (-1, 1)),
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, video_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id  INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS comment_likes (
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (comment_id, user_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    subscriber_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (subscriber_id, channel_id),
    CHECK (subscriber_id <> channel_id)
);

CREATE TABLE IF NOT EXISTS history (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    watched_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, video_id)
);

CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    visibility  TEXT    NOT NULL DEFAULT 'public'
                CHECK (visibility IN ('public', 'unlisted', 'private')),
    kind        TEXT    NOT NULL DEFAULT 'custom',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (playlist_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_videos_user      ON videos(user_id);
CREATE INDEX IF NOT EXISTS idx_videos_created   ON videos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_category  ON videos(category);
CREATE INDEX IF NOT EXISTS idx_videos_short     ON videos(is_short, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_video   ON comments(video_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent  ON comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_subs_channel     ON subscriptions(channel_id);
CREATE INDEX IF NOT EXISTS idx_history_user     ON history(user_id, watched_at DESC);
CREATE INDEX IF NOT EXISTS idx_playlist_items   ON playlist_items(playlist_id, position);
