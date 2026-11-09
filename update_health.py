"""Acknowledge a verified new build only after its native window loads."""
import os
from pathlib import Path
import sys

from app_version import APP_VERSION
from update_trust import TRUSTED_PUBLIC_KEYS
from update_security import (UpdateError, atomic_json, strict_json, validated_job,
                             verify_manifest, verify_file, update_root)

_HANDOFF = None


def set_handoff(directory, token):
    global _HANDOFF
    _HANDOFF = (str(directory), str(token))


def acknowledge_startup():
    if sys.platform == 'darwin':
        from mac_updater import acknowledge_startup as mac_acknowledge
        return mac_acknowledge(_HANDOFF)
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    target = Path(sys.executable).resolve()
    directories = [_HANDOFF[0]] if _HANDOFF else list((update_root() / "jobs").glob("*"))[-100:]
    for directory in directories:
        try:
            job = validated_job(directory)
            request = strict_json((job / "request.json").read_bytes())
            if Path(request["target"]).resolve() != target:
                continue
            manifest = verify_manifest(strict_json((job / "manifest.json").read_bytes()), TRUSTED_PUBLIC_KEYS,
                                       request["repository"], request["channel"], request["current_version"])
            if manifest["version"] != APP_VERSION:
                continue
            verify_file(target, manifest)
            token = request["health_token"]
            if _HANDOFF and token != _HANDOFF[1]:
                continue
            if not _HANDOFF:
                journal = strict_json((job / "journal.json").read_bytes())
                if journal.get("phase") not in ("prepared", "installed"):
                    continue
            atomic_json(job / "health.json", {"token": token, "version": APP_VERSION})
            if not _HANDOFF:
                atomic_json(job / "journal.json", {"phase": "healthy", "version": APP_VERSION})
                atomic_json(job / "result.json", {"status": "installed", "version": APP_VERSION,
                            "message": "Interrupted installation recovered; this build started successfully."})
        except (OSError, UpdateError, KeyError, TypeError):
            continue
