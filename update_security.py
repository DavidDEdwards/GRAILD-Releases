"""Shared fail-closed signature, semantic-version and filesystem checks."""
import base64
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from functools import total_ordering
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import uuid

from platform_support import update_directory

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class UpdateError(Exception):
    pass


@total_ordering
@dataclass(frozen=True, eq=False)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple = ()

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str):
            raise UpdateError("Invalid semantic version.")
        match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?", value)
        if not match:
            raise UpdateError("Invalid semantic version: " + value[:80])
        pre = tuple(match[4].split(".")) if match[4] else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in pre):
            raise UpdateError("Numeric prerelease identifiers cannot have leading zeroes.")
        return cls(int(match[1]), int(match[2]), int(match[3]), pre)

    def __eq__(self, other):
        return isinstance(other, SemVer) and (self.major, self.minor, self.patch, self.prerelease) == (other.major, other.minor, other.patch, other.prerelease)

    def __lt__(self, other):
        if not isinstance(other, SemVer):
            return NotImplemented
        left, right = (self.major, self.minor, self.patch), (other.major, other.minor, other.patch)
        if left != right:
            return left < right
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for a, b in zip(self.prerelease, other.prerelease):
            if a == b:
                continue
            if a.isdigit() and b.isdigit():
                return int(a) < int(b)
            if a.isdigit() != b.isdigit():
                return a.isdigit()
            return a < b
        return len(self.prerelease) < len(other.prerelease)


def strict_json(data):
    if isinstance(data, bytes):
        if len(data) > 131072:
            raise UpdateError("Update metadata is too large.")
        data = data.decode("utf-8")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise UpdateError("Duplicate update metadata fields.")
            result[key] = value
        return result
    def invalid(value):
        raise UpdateError("Non-finite metadata number.")
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError) as error:
        raise UpdateError("Unreadable update metadata.") from error


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def repository(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value) or value.split("/")[1] in (".", ".."):
        raise UpdateError("Enter a GitHub repository as owner/repository.")
    return value


def verify_manifest(envelope, keys, repo, channel, current_version, expected_tag=None, *, platform="windows", architecture=None):
    try:
        if not isinstance(envelope, dict) or set(envelope) != {"manifest", "signature", "key_id"}:
            raise UpdateError("Invalid signed release manifest.")
        key = keys.get(envelope["key_id"])
        if not key:
            raise UpdateError("Release was not signed by a trusted publisher key.")
        manifest = envelope["manifest"]
        if not isinstance(manifest, dict):
            raise UpdateError("Invalid release manifest.")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True)).verify(
            base64.b64decode(envelope["signature"], validate=True), canonical(manifest))
        if type(manifest.get("schema")) is not int or manifest.get("schema") != 1 or manifest.get("app") != "GRAILD":
            raise UpdateError("Release is not a supported GRAIL'D update.")
        if repository(manifest.get("repository")).casefold() != repository(repo).casefold():
            raise UpdateError("Release repository does not match your configuration.")
        version = SemVer.parse(manifest.get("version"))
        if version <= SemVer.parse(current_version):
            raise UpdateError("Release is not newer than this build.")
        if channel not in ("stable", "beta") or manifest.get("channel") not in ("stable", "beta"):
            raise UpdateError("Invalid release channel.")
        if channel == "stable" and (version.prerelease or manifest["channel"] != "stable"):
            raise UpdateError("This release is not on the stable channel.")
        tag = manifest.get("tag")
        if not isinstance(tag, str) or tag not in (manifest["version"], "v" + manifest["version"]):
            raise UpdateError("Release tag and version do not match.")
        if expected_tag and tag != expected_tag:
            raise UpdateError("Manifest does not belong to this release tag.")
        if platform == 'macos':
            if architecture not in ('arm64', 'x86_64') or manifest.get('platform') != 'macos' or manifest.get('architecture') != architecture:
                raise UpdateError('This Mac update is for a different platform or architecture.')
            if manifest.get('asset') != f"GRAILD-{manifest['version']}-macOS-{architecture}.zip" or manifest.get('bundle_id') != 'com.graild.desktop':
                raise UpdateError('Invalid Mac application asset.')
            if not re.fullmatch(r'[0-9a-f]{64}', manifest.get('bundle_sha256', '')):
                raise UpdateError('Missing signed Mac app bundle checksum.')
        elif platform != 'windows' or manifest.get('platform', 'windows') != 'windows' or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,120}\.exe", manifest.get("asset", "")):
            raise UpdateError("Invalid executable asset name.")
        if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("sha256", "")):
            raise UpdateError("Invalid signed checksum.")
        size = manifest.get("size")
        if type(size) is not int or not 1 <= size <= 2 * 1024**3:
            raise UpdateError("Invalid signed executable size.")
        if not isinstance(manifest.get("issued_at"), str) or not isinstance(manifest.get("expires_at"), str):
            raise UpdateError("Invalid manifest validity dates.")
        issued = datetime.fromisoformat(manifest["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(manifest["expires_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if issued.tzinfo is None or expires.tzinfo is None or issued > now + timedelta(minutes=5) or expires <= now or not timedelta(0) < expires - issued <= timedelta(days=180):
            raise UpdateError("The signed update manifest has expired or has invalid dates.")
        if not isinstance(manifest.get("notes", ""), str) or len(manifest.get("notes", "")) > 50000:
            raise UpdateError("Invalid signed release notes.")
        return manifest
    except InvalidSignature as error:
        raise UpdateError("Release signature verification failed. Nothing will be installed.") from error
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise UpdateError("Invalid signed release manifest.") from error


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path, manifest):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size != manifest["size"] or not hmac.compare_digest(sha256_file(path), manifest["sha256"]):
        raise UpdateError("Executable verification failed. Nothing will be installed.")


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "xb") as file:
            file.write(canonical(data))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def update_root():
    return update_directory()


def validated_job(path):
    path = Path(path).resolve()
    root = (update_root() / "jobs").resolve()
    if path.parent != root or not re.fullmatch(r"[0-9a-f]{32}", path.name):
        raise UpdateError("Invalid updater staging directory.")
    return path
