"""Public, metadata-only Canary API v1 client with an offline SQLite cache."""
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import random
import sqlite3
import threading
import time
from urllib.parse import quote

import requests

BASE_URL = "https://canary.best/api/v1"
SONG_FIELDS = ("id", "title", "artist", "era", "category", "duration_seconds", "duration_str", "page_url", "cover_url")
TRACKER_FIELDS = ("alt_names", "producers", "engineers", "record_date", "dates", "first_previewed", "additional_info")


class CanaryError(Exception):
    def __init__(self, message, status=None, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def public_song(song):
    result = {key: song[key] for key in SONG_FIELDS if key in song}
    tracker = song.get("tracker") or {}
    result["tracker"] = {key: tracker[key] for key in TRACKER_FIELDS if key in tracker} if isinstance(tracker, dict) else {}
    return result


class CanaryClient:
    def __init__(self, cache_path, session=None, interval=0.8, timeout=(5, 15)):
        self.cache_path = str(cache_path)
        Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "GRAILD/1.0 CanaryMetadataIntegration", "Accept": "application/json"})
        self.interval, self.timeout = interval, timeout
        self.lock = threading.RLock()
        self.last_request = 0.0
        self.cooldown = 0.0
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS api_cache (key TEXT PRIMARY KEY, saved REAL NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.cache_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _id(song_id):
        value = str(song_id).strip()
        if not value or value in (".", "..") or len(value) > 256 or any(char in value for char in "/?#\\"):
            raise CanaryError("Invalid Canary song identifier.", 400)
        return quote(value, safe="")

    def _read(self, key):
        with self._db() as db:
            row = db.execute("SELECT saved, payload FROM api_cache WHERE key = ?", (key,)).fetchone()
        if row:
            try:
                payload = json.loads(row[1])
                return (row[0], payload) if isinstance(payload, dict) else None
            except (ValueError, TypeError):
                pass
        return None

    def _write(self, key, payload):
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO api_cache VALUES (?, ?, ?)", (key, time.time(), json.dumps(payload)))

    def _get(self, path, params=None, image=False, ttl=3600):
        key = json.dumps([path, params or {}, image], sort_keys=True)
        with self.lock:
            cached = self._read(key)
            if cached and time.time() - cached[0] < ttl:
                return {**cached[1], "_cached": True, "_stale": False}
            try:
                if time.monotonic() < self.cooldown:
                    raise CanaryError("Canary is rate limited. Please wait before trying again.", 429,
                                      max(1, int(self.cooldown - time.monotonic())))
                delay = self.interval - (time.monotonic() - self.last_request)
                if delay > 0:
                    time.sleep(delay)
                self.last_request = time.monotonic()
                response = self.session.get(BASE_URL + path, params=params, timeout=self.timeout, stream=image)
                try:
                    status = response.status_code
                    if status != 200:
                        messages = {400: "Canary could not process those search filters.",
                                    404: "That song or artwork was not found on Canary.",
                                    429: "Canary is rate limited. Please wait before trying again.",
                                    503: "Canary's catalogue is temporarily unavailable."}
                        retry = 60
                        if status == 429:
                            try:
                                retry = max(1, min(600, int(response.headers.get("Retry-After", "60"))))
                            except ValueError:
                                pass
                            self.cooldown = time.monotonic() + retry
                        raise CanaryError(messages.get(status, "Canary is temporarily unavailable."), status, retry if status == 429 else 0)
                    if image:
                        mime = response.headers.get("Content-Type", "").split(";")[0].lower()
                        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"):
                            raise CanaryError("Canary did not return a supported cover image.")
                        content = bytearray()
                        for chunk in response.iter_content(65536):
                            content.extend(chunk)
                            if len(content) > 8 * 1024 * 1024:
                                raise CanaryError("This Canary artwork is too large to display.")
                        payload = {"image": "data:" + mime + ";base64," + base64.b64encode(content).decode("ascii")}
                    else:
                        payload = response.json()
                        if not isinstance(payload, dict):
                            raise CanaryError("Canary returned an unexpected metadata response.")
                    self._write(key, payload)
                    return {**payload, "_cached": False, "_stale": False}
                finally:
                    response.close()
            except (requests.Timeout, requests.ConnectionError) as error:
                failure = CanaryError("Canary is offline or the request timed out. Local playback is still available.")
            except requests.RequestException:
                failure = CanaryError("Could not contact Canary. Local playback is still available.")
            except ValueError:
                failure = CanaryError("Canary returned unreadable metadata. Please try again later.")
            except CanaryError as error:
                if error.status in (400, 404):
                    raise
                failure = error
            if cached:
                return {**cached[1], "_cached": True, "_stale": True,
                        "_notice": str(failure) + " Showing saved metadata."}
            raise failure

    def get_catalog(self):
        return self._get("/catalog", ttl=86400)

    def search_songs(self, query="", era="", category="", artist="", page=0, limit=20):
        if not isinstance(page, int) or not isinstance(limit, int) or page < 0 or not 1 <= limit <= 100:
            raise CanaryError("Invalid catalogue page or page size.", 400)
        params = {"page": page, "limit": limit}
        for key, value in (("q", query), ("era", era), ("category", category), ("artist", artist)):
            if str(value).strip():
                params[key] = str(value).strip()
        data = self._get("/songs", params)
        if not isinstance(data.get("songs"), list):
            raise CanaryError("Canary returned an unexpected song list.")
        return {**data, "songs": [public_song(song) for song in data["songs"] if isinstance(song, dict)]}

    def random_song(self, era=""):
        first = self.search_songs(era=era, limit=1)
        total = int(first.get("total", 0))
        if not total:
            raise CanaryError("Canary has no songs in the selected recording era.")
        page = random.randrange(total)
        result = first if page == 0 else self.search_songs(era=era, page=page, limit=1)
        if not result.get("songs"):
            raise CanaryError("Canary's catalogue changed. Please choose another random song.")
        song = result["songs"][0]
        return {"song": song, "_notice": result.get("_notice", "")}

    def cached_recording_eras(self):
        saved = self._read("recording-eras-v1")
        return saved[1].get("eras", []) if saved else []

    def get_song_index(self):
        saved = self._read("catalog-song-index-v1")
        if saved and time.time() - saved[0] < 86400:
            return {**saved[1], "_cached": True}
        records = {}
        notice = ""
        try:
            for page in range(100):
                result = self.search_songs(page=page, limit=100)
                notice = notice or result.get("_notice", "")
                for song in result["songs"]:
                    if song.get("id"):
                        records[song["id"]] = song
                total = int(result.get("total", 0))
                if (page + 1) * 100 >= total:
                    if len(records) < total:
                        raise CanaryError("Canary's catalogue is changing. Automatic era checks will retry later.")
                    break
                if not result["songs"]:
                    raise CanaryError("Canary returned an incomplete catalogue. Please retry later.")
            else:
                raise CanaryError("The catalogue is too large to check safely. Please retry later.")
        except CanaryError as error:
            if saved:
                return {**saved[1], "_cached": True, "_stale": True,
                        "_notice": str(error) + " Using the saved Canary catalogue."}
            raise
        data = {"songs": list(records.values()), "_notice": notice}
        self._write("catalog-song-index-v1", data)
        self._write("recording-eras-v1", {"eras": sorted({song["era"] for song in records.values() if song.get("era")}), "_notice": notice})
        return data

    def get_recording_eras(self):
        cached = self._read("recording-eras-v1")
        if cached and time.time() - cached[0] < 86400:
            return {**cached[1], "_cached": True}
        index = self.get_song_index()
        return {"eras": sorted({song["era"] for song in index["songs"] if song.get("era")}),
                "_notice": index.get("_notice", "")}

    def get_song(self, song_id):
        data = self._get("/songs/" + self._id(song_id), ttl=86400)
        if not data.get("id") or not data.get("title") or str(data["id"]) != str(song_id):
            raise CanaryError("Canary returned incomplete song details.")
        return {**public_song(data), **{key: value for key, value in data.items() if key.startswith("_")}}

    def get_cover(self, song_id):
        return self._get("/songs/" + self._id(song_id) + "/cover", image=True, ttl=7 * 86400)

    def get_covers(self, song_id):
        return self._get("/songs/" + self._id(song_id) + "/covers", ttl=86400)

    def search_covers(self, query, limit=10):
        if not str(query).strip():
            raise CanaryError("Enter a song title to search covers.", 400)
        return self._get("/covers/search", {"q": str(query).strip(), "limit": max(1, min(100, int(limit)))}, ttl=86400)
