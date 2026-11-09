"""Local-file matching and bounded background jobs for Canary metadata."""
from contextlib import contextmanager
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import uuid

from canary_client import CanaryClient, CanaryError


def normalized_title(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold().replace("_", " ")
    value = re.sub(r"^juice\s*wrld\s*[-:]+\s*", "", value)
    value = re.sub(r"^\d{1,3}[. _-]+", "", value)
    return " ".join(re.findall(r"[^\W_]+", value, re.UNICODE))


def search_title(filename):
    value = Path(filename).stem.replace("_", " ")
    value = re.sub(r"^juice\s*wrld\s*[-:]+\s*", "", value, flags=re.I)
    value = re.sub(r"^\d{1,3}[. _-]+", "", value)
    value = re.sub(r"\s*[-(\[]?\s*(?:session(?:s| edit)?|alternate(?: version)?|alt(?: version)?|demo|original(?: version)?|normal(?: version)?|studio(?: version)?|album version|v\d+|version \d+)[)\]]?\s*$", "", value, flags=re.I)
    return value.strip(" -_()[]")


def rank_candidates(filename, songs):
    local = normalized_title(Path(filename).stem)
    base = normalized_title(search_title(filename))
    candidates = []
    for song in songs:
        title = normalized_title(song.get("title", ""))
        aliases = (song.get("tracker") or {}).get("alt_names") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [normalized_title(alias) for alias in aliases]
        if local == title:
            score, reason = 100, "Exact title. Confirm this is the same recording."
        elif local in aliases:
            score, reason = 95, "Exact alternate name. Confirm the recording version."
        elif base == title or base in aliases:
            score, reason = 75, "Related title; the local filename indicates a different version or session edit."
        else:
            score = int(SequenceMatcher(None, base, title).ratio() * 60)
            reason = "Possible title match. Verify the era, category, and recording before linking."
        candidates.append({**song, "match_score": score, "match_reason": reason})
    return sorted(candidates, key=lambda song: (-song["match_score"], song.get("title", "")))


def era_title_key(value):
    # These are catalogue naming variants of a session, not a licence to strip
    # arbitrary demo/master/version markers or link recordings automatically.
    value = unicodedata.normalize("NFKC", str(value)).casefold().replace("_", " ")
    value = re.sub(r"^juice\s*wrld\s*[-:]+\s*", "", value)
    # Bare leading numbers can be part of the real title (for example 10 Feet).
    # Strip only explicit track numbering such as "01 - " or "01. ".
    value = re.sub(r"^\d{1,3}(?:\s+-\s+|\.\s+)", "", value)
    value = " ".join(re.findall(r"[^\W_]+", value, re.UNICODE))
    return re.sub(r"\b(?:sessions|session edits?)$", "session", value)


def automatic_era(filename, title_index):
    # Version markers remain part of the key; similarity alone is never enough.
    title = era_title_key(Path(filename).stem)
    candidates = title_index.get(title, [])
    if not candidates:
        return {"era": "", "status": "not_found"}
    eras = {song.get("era", "") for song in candidates}
    if len(eras) == 1 and "" not in eras:
        return {"era": next(iter(eras)), "status": "checked"}
    return {"era": "", "status": "needs_review"}


def build_title_index(songs):
    index = {}
    for song in songs:
        aliases = (song.get("tracker") or {}).get("alt_names") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        for name in [song.get("title", ""), *aliases]:
            key = era_title_key(name)
            if key:
                index.setdefault(key, []).append(song)
    return index


class CanaryIntegration:
    def __init__(self, directory, client=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.client = client or CanaryClient(directory / "canary_api.sqlite3")
        self.path = str(directory / "canary_matches.sqlite3")
        self.jobs = {}
        self.lock = threading.Lock()
        self._scan_generation = 0
        self._scan_pending = None
        self._scan_thread = None
        self._scan_state = {"status": "idle", "total": 0, "processed": 0, "resolved": 0, "review": 0}
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS local_matches (path TEXT PRIMARY KEY, size INTEGER, modified INTEGER, song_id TEXT, metadata TEXT, artwork TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS era_checks (path TEXT PRIMARY KEY, size INTEGER NOT NULL, modified INTEGER NOT NULL, era TEXT NOT NULL, status TEXT NOT NULL, checked REAL NOT NULL)")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def identity(filename):
        path = os.path.normcase(os.path.abspath(filename))
        stat = os.stat(path)
        return path, stat.st_size, stat.st_mtime_ns

    def local_match(self, filename):
        try:
            path, size, modified = self.identity(filename)
            with self.db() as db:
                row = db.execute("SELECT * FROM local_matches WHERE path = ? AND size = ? AND modified = ?", (path, size, modified)).fetchone()
            if row:
                return {"song": json.loads(row["metadata"]), "artwork": row["artwork"] or ""}
        except (OSError, ValueError):
            pass
        return None

    def library_matches(self, directory, filenames):
        with self.db() as db:
            rows = {row["path"]: row for row in db.execute("SELECT path, size, modified, metadata FROM local_matches")}
        matches = {}
        for filename in filenames:
            try:
                path, size, modified = self.identity(os.path.join(directory, filename))
                row = rows.get(path)
                if row and row["size"] == size and row["modified"] == modified:
                    matches[filename] = json.loads(row["metadata"])
            except (OSError, ValueError):
                continue
        return matches

    def library_era_checks(self, directory, filenames):
        with self.db() as db:
            rows = {row["path"]: row for row in db.execute("SELECT * FROM era_checks")}
        result = {}
        for filename in filenames:
            try:
                path, size, modified = self.identity(os.path.join(directory, filename))
                row = rows.get(path)
                if row and row["size"] == size and row["modified"] == modified:
                    result[filename] = {"era": row["era"], "status": row["status"], "checked": row["checked"]}
            except OSError:
                pass
        # An explicit recording confirmation always takes precedence.
        for filename, song in self.library_matches(directory, filenames).items():
            result[filename] = {"era": song.get("era", ""), "status": "confirmed", "checked": time.time()}
        return result

    def start_era_scan(self, directory, filenames, force=False):
        directory = os.path.abspath(directory)
        with self.lock:
            self._scan_generation += 1
            self._scan_pending = (directory, list(filenames), bool(force), self._scan_generation)
            self._scan_state = {"status": "queued", "directory": directory, "total": len(filenames),
                                "processed": 0, "resolved": 0, "review": 0}
            if self._scan_thread is None or not self._scan_thread.is_alive():
                self._scan_thread = threading.Thread(target=self._era_scan_worker, name="Canary-era-scan", daemon=True)
                self._scan_thread.start()
        return self.era_scan_status(directory)

    def era_scan_status(self, directory):
        with self.lock:
            if self._scan_state.get("directory") != os.path.abspath(directory):
                return {"status": "idle", "total": 0, "processed": 0, "resolved": 0, "review": 0}
            return {key: value for key, value in self._scan_state.items() if key != "directory"}

    def _era_scan_worker(self):
        while True:
            with self.lock:
                if self._scan_pending is None:
                    self._scan_thread = None
                    return
                directory, filenames, force, generation = self._scan_pending
                self._scan_pending = None
                self._scan_state["status"] = "running"
            try:
                existing = self.library_era_checks(directory, filenames)
                pending = [name for name in filenames if name not in existing or
                           (existing[name]["status"] != "confirmed" and (force or time.time() - existing[name]["checked"] >= 86400))]
                title_index = {}
                notice = ""
                if pending:
                    snapshot = self.client.get_song_index()
                    title_index = build_title_index(snapshot["songs"])
                    notice = snapshot.get("_notice", "")
                for filename in pending:
                    with self.lock:
                        if generation != self._scan_generation:
                            break
                    full_path = os.path.join(directory, filename)
                    try:
                        identity = self.identity(full_path)
                        outcome = automatic_era(filename, title_index)
                        if self.identity(full_path) != identity:
                            continue
                        with self.db() as db:
                            db.execute("INSERT OR REPLACE INTO era_checks VALUES (?, ?, ?, ?, ?, ?)",
                                       (*identity, outcome["era"], outcome["status"], time.time()))
                        existing[filename] = outcome
                    except OSError:
                        continue
                    with self.lock:
                        if generation == self._scan_generation:
                            self._scan_state["processed"] = len(existing)
                with self.lock:
                    if generation == self._scan_generation:
                        self._scan_state.update({"status": "complete", "processed": len(filenames),
                            "resolved": sum(bool(value.get("era")) for value in existing.values()),
                            "review": sum(not value.get("era") for value in existing.values()), "notice": notice})
            except Exception as error:
                with self.lock:
                    if generation == self._scan_generation:
                        self._scan_state.update({"status": "failed", "message": str(error) if isinstance(error, CanaryError) else
                                                "Automatic Canary era check failed. Local playback is still available."})

    def forget(self, filename):
        path = os.path.normcase(os.path.abspath(filename))
        with self.db() as db:
            db.execute("DELETE FROM local_matches WHERE path = ?", (path,))

    def confirm(self, filename, song_id):
        identity = self.identity(filename)
        info = self.song_info(song_id)
        if self.identity(filename) != identity:
            raise CanaryError("The local file changed. Please search for its metadata again.")
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO local_matches VALUES (?, ?, ?, ?, ?, ?)",
                       (*identity, info["song"]["id"], json.dumps(info["song"]), info["artwork"]))
        return {**info, "confirmed": True, "filename": Path(filename).name}

    def song_info(self, song_id):
        song = self.client.get_song(song_id)
        notice = song.get("_notice", "")
        artwork = ""
        try:
            cover = self.client.get_cover(song_id)
            artwork = cover.get("image", "")
            notice = notice or cover.get("_notice", "")
        except CanaryError as error:
            notice = (notice + " " + str(error)).strip()
        return {"song": song, "artwork": artwork, "notice": notice}

    def local_info(self, filename, query=None):
        saved = self.local_match(filename)
        if saved and query is None:
            try:
                info = self.confirm(filename, saved["song"]["id"])
                return info
            except CanaryError as error:
                return {**saved, "confirmed": True, "filename": Path(filename).name,
                        "notice": str(error) + " Showing confirmed local metadata."}
        query = search_title(filename) if query is None else str(query).strip()
        if not query:
            raise CanaryError("Enter a title to find a Canary recording.", 400)
        result = self.client.search_songs(query, limit=20)
        return {"confirmed": False, "filename": Path(filename).name, "query": query,
                "candidates": rank_candidates(filename, result["songs"]),
                "notice": result.get("_notice", ""),
                "message": "Choose a recording explicitly. Similar titles and session edits are never merged automatically."}

    def start(self, task, **arguments):
        with self.lock:
            # Completed results expire; active work is bounded to protect the API.
            expired = [key for key, job in self.jobs.items() if job["status"] != "running" and time.monotonic() - job["created"] > 300]
            for key in expired:
                del self.jobs[key]
            if sum(job["status"] == "running" for job in self.jobs.values()) >= 4:
                return {"status": "failed", "message": "Please wait for the current Canary requests to finish."}
            if len(self.jobs) >= 40:
                finished = next((key for key, job in self.jobs.items() if job["status"] != "running"), None)
                if finished:
                    del self.jobs[finished]
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"status": "running", "created": time.monotonic()}
        def work():
            try:
                if task == "search":
                    result = self.client.search_songs(**arguments)
                elif task == "catalog":
                    result = self.client.get_catalog()
                elif task == "random":
                    result = self.client.random_song(**arguments)
                elif task == "eras":
                    result = self.client.get_recording_eras()
                elif task == "info":
                    result = self.song_info(**arguments)
                elif task == "local":
                    result = self.local_info(**arguments)
                elif task == "confirm":
                    result = self.confirm(**arguments)
                else:
                    raise CanaryError("Unknown Canary operation.")
                outcome = {"status": "complete", "result": result}
            except CanaryError as error:
                outcome = {"status": "failed", "message": str(error), "http_status": error.status, "retry_after": error.retry_after}
            except Exception:
                outcome = {"status": "failed", "message": "Canary metadata could not be loaded. Your local audio and game progress are unchanged."}
            with self.lock:
                self.jobs[job_id].update(outcome)
        threading.Thread(target=work, name="Canary-metadata", daemon=True).start()
        return {"job_id": job_id, "status": "running"}

    def poll(self, job_id):
        with self.lock:
            job = self.jobs.get(str(job_id))
            return {key: value for key, value in job.items() if key != "created"} if job else {"status": "failed", "message": "This metadata request expired. Please retry."}
