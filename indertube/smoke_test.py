"""End-to-end smoke test for InderTube (isolated temp database, no server needed).

Run:  python smoke_test.py
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="indertube-test-")
os.environ["INDERTUBE_DB"] = os.path.join(TMP, "test.db")
os.environ["INDERTUBE_VIDEO_DIR"] = os.path.join(TMP, "videos")
os.environ["INDERTUBE_THUMB_DIR"] = os.path.join(TMP, "thumbs")
os.environ["INDERTUBE_AVATAR_DIR"] = os.path.join(TMP, "avatars")

from app import app  # noqa: E402
from PIL import Image  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')
passed = 0
VIDEO = os.path.join(TMP, "landscape.mp4")
SHORT = os.path.join(TMP, "vertical.mp4")


def check(name, condition, extra=""):
    global passed
    if condition:
        passed += 1
        print(f"  OK  {name}")
    else:
        print(f"  FAIL {name} {extra}")
        cleanup()
        raise SystemExit(1)


def cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


def token_from(client, path="/"):
    response = client.get(path)
    match = CSRF_RE.search(response.get_data(as_text=True))
    return (match.group(1) if match else ""), response


def make_clip(path, size, seconds=3):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-pix_fmt", "yuv420p", path,
    ]
    return subprocess.run(command, capture_output=True).returncode == 0


def register(client, username, display_name):
    token, _ = token_from(client, "/register")
    return client.post("/register", data={
        "_csrf": token,
        "username": username,
        "display_name": display_name,
        "email": f"{username}@example.com",
        "password": "password123",
        "password2": "password123",
    })


def upload(client, path, title, fmt="video", duration="3"):
    token, _ = token_from(client, "/upload")
    with open(path, "rb") as fh:
        return client.post("/upload", data={
            "_csrf": token,
            "title": title,
            "description": f"Описание для «{title}».",
            "category": "Технологии",
            "visibility": "public",
            "duration": duration,
            "format": fmt,
            "video": (fh, os.path.basename(path)),
        }, content_type="multipart/form-data")


def main():
    has_media = make_clip(VIDEO, "1280x720") and make_clip(SHORT, "720x1280")

    print("Публичные страницы")
    guest = app.test_client()
    page = guest.get("/")
    check("главная 200", page.status_code == 200)
    check("приветствие", "Добро пожаловать в InderTube" in page.get_data(as_text=True))
    check("shorts 200", guest.get("/shorts").status_code == 200)
    check("тренды", guest.get("/feed/trending").status_code == 200)
    check("поиск", guest.get("/results?search_query=test").status_code == 200)
    check("логин", guest.get("/login").status_code == 200)
    check("регистрация", guest.get("/register").status_code == 200)
    check("404", guest.get("/nope").status_code == 404)
    check("переключатель темы в шапке", guest.get("/").get_data(as_text=True).count("data-theme-toggle") >= 1)

    print("CSRF и регистрация")
    bad = guest.post("/login", data={"identifier": "x", "password": "y"})
    check("POST без токена отклонён", bad.status_code == 302)
    check("регистрация автора", register(guest, "creator", "Создатель").status_code == 302)
    check("кабинет автора", guest.get("/studio").status_code == 200)
    check("загрузка", guest.get("/upload").status_code == 200)

    print("Загрузка видео и Shorts")
    if not has_media:
        print("  SKIP  ffmpeg не найден — тесты загрузки пропущены")
    else:
        long_upload = upload(guest, VIDEO, "Моё первое видео")
        check("видео загружено", long_upload.status_code == 302, long_upload.status_code)
        video_url = long_upload.headers.get("Location", "")
        check("редирект на watch", "/watch/" in video_url, video_url)
        video_id = video_url.rstrip("/").split("/")[-1]
        watch = guest.get(video_url)
        check("страница видео", watch.status_code == 200 and "Моё первое видео" in watch.get_data(as_text=True))

        short_upload = upload(guest, SHORT, "Мой первый Shorts", fmt="short")
        check("Shorts загружен", short_upload.status_code == 302, short_upload.status_code)
        short_url = short_upload.headers.get("Location", "")
        check("редирект на Shorts", "/shorts/" in short_url, short_url)
        short_id = short_url.rstrip("/").split("/")[-1]
        shorts_page = guest.get("/shorts")
        check("Shorts в ленте", short_id in shorts_page.get_data(as_text=True))
        check("watch перенаправляет на Shorts", guest.get(f"/watch/{short_id}").status_code == 302)

        with sqlite3.connect(app.config["DATABASE"]) as conn:
            thumb = conn.execute("SELECT thumbnail FROM videos WHERE id = ?", (short_id,)).fetchone()[0]
            path = os.path.join(app.config["UPLOAD_THUMB"], thumb)
            with Image.open(path) as image:
                check("вертикальное превью 9:16", image.size == (720, 1280), image.size)

        too_long = upload(guest, SHORT, "Слишком длинный Shorts", fmt="short", duration="61")
        check("Shorts длиннее 60 секунд отклонён", too_long.status_code == 400)
        check("текст ошибки", "60 секунд" in too_long.get_data(as_text=True))

        media = guest.get(f"/media/video/{os.listdir(app.config['UPLOAD_VIDEO'])[0]}",
                          headers={"Range": "bytes=0-1023"})
        check("Range 206", media.status_code == 206, media.status_code)
        check("размер чанка", len(media.data) == 1024)

        print("Лайки, комментарии, история")
        token, _ = token_from(guest, "/shorts")
        like = guest.post(f"/api/videos/{short_id}/like", json={"value": 1},
                          headers={"X-CSRF-Token": token})
        check("лайк Shorts", like.status_code == 200 and like.get_json()["user_like"] == 1)
        comment = guest.post(f"/api/videos/{short_id}/comments",
                             json={"text": "Тестовый комментарий"},
                             headers={"X-CSRF-Token": token})
        payload = comment.get_json()
        check("комментарий создан", comment.status_code == 200 and "Тестовый комментарий" in payload["html"])
        listing = guest.get(f"/api/videos/{short_id}/comments")
        check("список комментариев", listing.status_code == 200 and "Тестовый комментарий" in listing.get_json()["html"])
        check("удаление комментария", guest.delete(f"/api/comments/{payload['comment_id']}",
                                                   headers={"X-CSRF-Token": token}).status_code == 200)
        guest.post(f"/api/videos/{short_id}/view", headers={"X-CSRF-Token": token})
        check("история", "Мой первый Shorts" in guest.get("/feed/history").get_data(as_text=True))

        print("Поиск и канал")
        check("поиск находит Shorts", "Мой первый Shorts" in guest.get("/results?search_query=Shorts").get_data(as_text=True))
        channel_page = guest.get("/channel/creator?tab=shorts").get_data(as_text=True)
        check("канал: вкладка Shorts", "Shorts" in channel_page)
        check("канал: Shorts в сетке", "/shorts/" + short_id in channel_page)
        check("студия показывает Shorts", "Мой первый Shorts" in guest.get("/studio").get_data(as_text=True))

        print("Плейлисты и второй пользователь")
        playlist = guest.post("/api/playlists", json={"title": "Тестовый плейлист"},
                              headers={"X-CSRF-Token": token}).get_json()
        playlist_id = playlist["playlist"]["id"]
        added = guest.post(f"/api/playlists/{playlist_id}/add", json={"video_id": int(short_id)},
                           headers={"X-CSRF-Token": token})
        check("Shorts добавлен в плейлист", added.get_json()["added"] is True)

        second = app.test_client()
        check("регистрация зрителя", register(second, "viewer", "Зритель").status_code == 302)
        with sqlite3.connect(app.config["DATABASE"]) as conn:
            creator_id = conn.execute("SELECT id FROM users WHERE username = 'creator'").fetchone()[0]
        second_token, _ = token_from(second)
        subscribed = second.post(f"/api/channels/{creator_id}/subscribe",
                                 headers={"X-CSRF-Token": second_token})
        check("подписка на канал", subscribed.get_json()["subscribed"] is True)

        print("Удаление")
        delete = guest.post(f"/studio/video/{short_id}/delete", data={"_csrf": token})
        check("Shorts удалён", delete.status_code == 302)
        check("удалённый Shorts 404", guest.get(f"/shorts/{short_id}").status_code == 404)
        check("видео удалено", guest.post(f"/studio/video/{video_id}/delete",
                                          data={"_csrf": token}).status_code == 302)

    print("Гость и API")
    guest2 = app.test_client()
    guest_token, _ = token_from(guest2, "/shorts")
    check("401 для гостя", guest2.post("/api/videos/1/like", json={"value": 1},
                                       headers={"X-CSRF-Token": guest_token}).status_code == 401)

    print(f"\nВсе проверки пройдены: {passed}")
    cleanup()


if __name__ == "__main__":
    main()
