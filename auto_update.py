"""Background GitHub beta updates; only signed release payloads are eligible."""
import json
import os
import platform
from pathlib import Path
import secrets
import shutil
import sys
import threading
import time
from urllib.parse import urlsplit, unquote
import uuid

import requests

from app_version import APP_VERSION
from update_trust import DEFAULT_REPOSITORY, DEFAULT_CHANNEL, TRUSTED_PUBLIC_KEYS
from update_security import (UpdateError, SemVer, strict_json, repository, verify_manifest,
                             verify_file, sha256_file, atomic_json, update_root)


class Cancelled(UpdateError):
    pass


def validate_https(url, hosts):
    parsed = urlsplit(str(url))
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise UpdateError("Untrusted update download location.")
    return str(url)


def validate_asset_url(url, repo, tag, asset):
    validate_https(url, {"github.com"})
    actual = unquote(urlsplit(url).path)
    prefix = "/" + repo + "/releases/download/"
    if not actual[:len(prefix)].casefold() == prefix.casefold() or actual[len(prefix):] != tag + "/" + asset or urlsplit(url).query:
        raise UpdateError("Update asset does not belong to this GitHub release.")
    return url


class UpdateManager:
    def __init__(self, root=None, keys=None, session=None):
        self.platform = 'macos' if sys.platform == 'darwin' else 'windows'
        self.architecture = platform.machine() if self.platform == 'macos' else None
        self.root = Path(root) if root else update_root()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "jobs").mkdir(exist_ok=True)
        self.keys = TRUSTED_PUBLIC_KEYS if keys is None else keys
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "GRAILD/" + APP_VERSION, "Accept": "application/vnd.github+json"})
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.candidate = None
        self.ready_job = None
        self.config = {"repository": DEFAULT_REPOSITORY, "channel": DEFAULT_CHANNEL, "automatic": True}
        try:
            saved = strict_json((self.root / "settings.json").read_bytes())
            repo = saved.get("repository") or DEFAULT_REPOSITORY
            if repo:
                repository(repo)
            if saved.get("channel", "beta") not in ("stable", "beta"):
                raise UpdateError("Invalid saved update channel.")
            self.config.update({"repository": repo, "channel": saved.get("channel", "beta"), "automatic": saved.get("automatic", True) is True})
        except (OSError, UpdateError, AttributeError):
            pass
        self.last_result = None
        results = sorted((self.root / "jobs").glob("*/result.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if results:
            try:
                self.last_result = strict_json(results[0].read_bytes())
            except (OSError, UpdateError):
                pass
        self.state = {"status": "idle", "message": "Ready to check for updates.", "version": APP_VERSION, "downloaded": 0, "total": 0}
        # Interrupted downloads are never considered installable or resumed blindly.
        for job in list((self.root / "jobs").iterdir())[:200]:
            if job.is_dir() and len(job.name) == 32:
                partial = job / "download.part"
                if partial.is_file() and not partial.is_symlink() and partial.resolve().is_relative_to(self.root.resolve()):
                    try:
                        partial.unlink()
                    except OSError:
                        pass

    def snapshot(self):
        with self.lock:
            return {**self.state, "current_version": APP_VERSION, "repository": self.config["repository"],
                    "channel": self.config["channel"], "automatic": self.config["automatic"], "signing_configured": bool(self.keys), "last_result": self.last_result, "can_download": self.candidate is not None}

    def _state(self, status, message, **values):
        with self.lock:
            self.state.update({"status": status, "message": message, **values})

    def save_settings(self, repo, channel, automatic=True):
        with self.lock:
            if self.state["status"] in ("checking", "downloading", "verifying", "installing"):
                raise UpdateError("Wait for the current update operation or cancel its download first.")
            repo = str(repo or "").strip()
            if repo:
                repository(repo)
            if channel not in ("stable", "beta"):
                raise UpdateError("Choose the beta or stable update channel.")
            self.config = {"repository": repo, "channel": channel, "automatic": bool(automatic)}
            atomic_json(self.root / "settings.json", self.config)
            self.candidate = None
            self.ready_job = None
            self.state = {"status": "idle", "message": "Update settings saved.", "downloaded": 0, "total": 0}
        return self.snapshot()

    def _response(self, url, stream=False):
        hosts = {"api.github.com"} if urlsplit(url).hostname == "api.github.com" else {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
        for _ in range(6):
            validate_https(url, hosts)
            response = self.session.get(url, timeout=(5, 20), stream=stream, allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                redirect = response.headers.get("Location", "")
                response.close()
                validate_https(redirect, hosts)
                url = redirect
                continue
            if response.status_code != 200:
                code = response.status_code
                response.close()
                messages = {403: "GitHub denied the request or its API rate limit was reached.",
                            404: "The GitHub repository or release was not found. Use a public repository.",
                            429: "GitHub is rate limiting update checks. Try again later.",
                            503: "GitHub updates are temporarily unavailable."}
                raise UpdateError(messages.get(code, "The update server returned HTTP " + str(code) + "."))
            return response
        raise UpdateError("Too many update download redirects.")

    def _json(self, url, limit=8 * 1024 * 1024):
        response = self._response(url, stream=True)
        try:
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > limit:
                    raise UpdateError("Update metadata exceeded the size limit.")
            # GitHub release lists can be larger than a small signed manifest.
            return json.loads(bytes(content)) if limit > 131072 else strict_json(bytes(content))
        finally:
            response.close()

    def check(self, automatic=False):
        if self.platform == 'macos' and self.architecture not in ('arm64', 'x86_64'):
            self._state('unsupported', 'This Mac architecture is not supported for automatic updates.')
            return self.snapshot()
        with self.lock:
            if automatic and not self.config["automatic"]:
                return self.snapshot()
            if self.state["status"] in ("checking", "downloading", "verifying", "installing", "ready"):
                return self.snapshot()
            if not self.config["repository"]:
                self._state("unconfigured", "Set a GitHub repository in Settings to enable updates.")
                return self.snapshot()
            if not self.keys:
                self._state("unconfigured", "This build has no trusted release-signing key. The publisher must configure one before distributing updates.")
                return self.snapshot()
            self._state("checking", "Checking GitHub for signed updates...")
        threading.Thread(target=self._check_worker, name="GRAILD-update-check", daemon=True).start()
        return self.snapshot()

    def _check_worker(self):
        with self.lock:
            self.candidate = None
        try:
            repo, channel = self.config["repository"], self.config["channel"]
            releases = []
            for page in range(1, 4):
                batch = self._json("https://api.github.com/repos/" + repo + "/releases?per_page=100&page=" + str(page))
                if not isinstance(batch, list):
                    raise UpdateError("GitHub returned an invalid release list.")
                releases.extend(batch)
                if len(batch) < 100:
                    break
            candidates = []
            for release in releases:
                try:
                    if release.get("draft"):
                        continue
                    tag = release["tag_name"]
                    version = SemVer.parse(tag[1:] if tag.startswith("v") else tag)
                    if version <= SemVer.parse(APP_VERSION) or (channel == "stable" and (release.get("prerelease") or version.prerelease)):
                        continue
                    candidates.append((version, release))
                except (UpdateError, KeyError, TypeError, AttributeError):
                    continue
            candidates.sort(key=lambda item: item[0], reverse=True)
            rejected = False
            for _, release in candidates[:20]:
                try:
                    assets = {asset["name"]: asset for asset in release.get("assets", [])}
                    metadata_name = f'graild-update-macos-{self.architecture}.json' if self.platform == 'macos' else 'graild-update.json'
                    if metadata_name not in assets:
                        continue
                    metadata_asset = assets[metadata_name]
                    metadata_url = validate_asset_url(metadata_asset["browser_download_url"], repo, release["tag_name"], metadata_name)
                    envelope = self._json(metadata_url, limit=131072)
                    manifest = self._verify_manifest(envelope, repo, channel, release['tag_name'])
                    asset = assets[manifest["asset"]]
                    url = validate_asset_url(asset["browser_download_url"], repo, manifest["tag"], manifest["asset"])
                    with self.lock:
                        self.candidate = {"envelope": envelope, "manifest": manifest, "url": url, "repository": repo, "channel": channel}
                    self._state("available", "A verified update is available.", version=manifest["version"], notes=manifest.get("notes", ""), downloaded=0, total=manifest["size"])
                    return
                except (UpdateError, KeyError, ValueError, TypeError):
                    rejected = True
            if rejected:
                raise UpdateError("Newer releases were found, but none had a valid trusted signed manifest. Nothing was downloaded or installed.")
            self._state("up_to_date", "You are up to date on the " + channel + " channel.")
        except Exception as error:
            message = str(error) if isinstance(error, UpdateError) else "Could not check for updates. Check your connection and try again."
            self._state("error", message)

    def download(self):
        with self.lock:
            if self.state["status"] in ("downloading", "verifying", "ready", "installing"):
                return self.snapshot()
            if not self.candidate:
                raise UpdateError("Check for a verified update first.")
            candidate = dict(self.candidate)
            self.cancel.clear()
            self._state("downloading", "Downloading verified release...", downloaded=0, total=candidate["manifest"]["size"])
        threading.Thread(target=self._download_worker, args=(candidate,), name="GRAILD-update-download", daemon=True).start()
        return self.snapshot()

    def _verify_manifest(self, envelope, repo, channel, tag=None):
        return verify_manifest(envelope, self.keys, repo, channel, APP_VERSION, tag,
                               platform=self.platform, architecture=self.architecture)

    def _download_worker(self, candidate):
        job = self.root / "jobs" / uuid.uuid4().hex
        partial = job / "download.part"
        response = None
        try:
            job.mkdir(mode=0o700)
            manifest = self._verify_manifest(candidate['envelope'], candidate['repository'], candidate['channel'])
            atomic_json(job / "manifest.json", candidate["envelope"])
            response = self._response(candidate["url"], stream=True)
            received = 0
            with open(partial, "xb") as file:
                for chunk in response.iter_content(256 * 1024):
                    if self.cancel.is_set():
                        raise Cancelled("Update download cancelled. Your current app is unchanged.")
                    received += len(chunk)
                    if received > manifest["size"]:
                        raise UpdateError("Downloaded update exceeded its signed size.")
                    file.write(chunk)
                    self._state("downloading", "Downloading update...", downloaded=received)
                file.flush()
                os.fsync(file.fileno())
            self._state("verifying", "Verifying the downloaded update...")
            verify_file(partial, manifest)
            if self.cancel.is_set():
                raise Cancelled("Update download cancelled. Your current app is unchanged.")
            if self.platform == 'macos':
                from mac_update_security import archive_bundle_digest
                if archive_bundle_digest(partial) != manifest['bundle_sha256']:
                    raise UpdateError('The Mac archive does not match its signed bundle checksum.')
            if self.cancel.is_set():
                raise Cancelled('Update download cancelled. Your current app is unchanged.')
            os.replace(partial, job / ('payload.zip' if self.platform == 'macos' else 'payload.exe'))
            with self.lock:
                self.ready_job = job
            self._state("ready", "Download verified. Ready to restart and install.", downloaded=received)
        except Exception as error:
            message = str(error) if isinstance(error, UpdateError) else "Download interrupted. Your current app is unchanged; retry when connected."
            self._state("cancelled" if isinstance(error, Cancelled) else "error", message)
        finally:
            if response:
                response.close()
            if partial.exists():
                partial.unlink()

    def cancel_download(self):
        self.cancel.set()
        return self.snapshot()

    def install(self, helper_source, target=None):
        if self.platform == 'macos':
            from mac_updater import prepare_install
            try:
                return prepare_install(self)
            except Exception as error:
                message = str(error) if isinstance(error, UpdateError) else 'The Mac updater could not prepare safely. The current app was left open.'
                self._state('error', message)
                raise UpdateError(message) from error
        from graild_updater import launch
        if os.name != "nt" or not getattr(sys, "frozen", False):
            raise UpdateError("Installation is available in the packaged Windows app. Development mode can check and verify downloads.")
        with self.lock:
            if self.state["status"] != "ready" or not self.ready_job or not self.candidate:
                raise UpdateError("No verified update is ready to install.")
            job = self.ready_job
            manifest = verify_manifest(strict_json((job / "manifest.json").read_bytes()), self.keys,
                                       self.config["repository"], self.config["channel"], APP_VERSION)
            verify_file(job / "payload.exe", manifest)
            target = Path(target or sys.executable).resolve()
            # Fail before closing the app if its installation directory is not writable.
            probe = target.parent / (".graild-write-test-" + uuid.uuid4().hex)
            try:
                probe.write_bytes(b"test")
            finally:
                if probe.exists():
                    probe.unlink()
            helper = job / "updater.exe"
            shutil.copyfile(helper_source, helper)
            if sha256_file(helper) != sha256_file(helper_source):
                raise UpdateError("Bundled updater copy failed verification.")
            atomic_json(job / "request.json", {"target": str(target), "parent_pid": os.getpid(),
                        "old_sha256": sha256_file(target), "repository": self.config["repository"],
                        "channel": self.config["channel"], "current_version": APP_VERSION,
                        "health_token": secrets.token_hex(32)})
            self._state("installing", "Preparing the updater. GRAIL'D will restart shortly...")
        process = launch([str(helper), "--job", str(job)])
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if (job / "ready.json").is_file():
                return self.snapshot()
            result = job / "result.json"
            if result.is_file():
                self._state("error", strict_json(result.read_bytes()).get("message", "Updater preparation failed."))
                raise UpdateError(self.snapshot()["message"])
            if process.poll() is not None:
                break
            time.sleep(0.1)
        self._state("error", "The updater did not become ready. Your app was left open; please retry.")
        raise UpdateError(self.snapshot()["message"])


class UnavailableUpdateManager:
    """Keep gameplay available when Windows update storage is inaccessible."""
    def __init__(self, reason):
        self.message = "Update storage is unavailable. GRAIL'D can still be used. " + str(reason)

    def snapshot(self):
        return {"status": "error", "message": self.message, "current_version": APP_VERSION,
                "version": APP_VERSION, "repository": DEFAULT_REPOSITORY, "channel": DEFAULT_CHANNEL,
                "automatic": False, "signing_configured": bool(TRUSTED_PUBLIC_KEYS),
                "can_download": False, "downloaded": 0, "total": 0, "last_result": None}

    def check(self, automatic=False):
        return self.snapshot()

    def cancel_download(self):
        return self.snapshot()

    def save_settings(self, *args, **kwargs):
        raise UpdateError(self.message)

    def download(self):
        raise UpdateError(self.message)

    def install(self, *args, **kwargs):
        raise UpdateError(self.message)

    def _state(self, status, message, **values):
        self.message = message
