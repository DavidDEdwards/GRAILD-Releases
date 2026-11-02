"""Recognize audio files and normalize them with the bundled FFmpeg decoder."""
import hashlib
import mimetypes
import os
from pathlib import Path
import subprocess
import tempfile
import threading

import imageio_ffmpeg

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".wave", ".ogg", ".oga", ".opus", ".flac", ".m4a",
    ".aac", ".wma", ".aif", ".aiff", ".aifc", ".alac", ".ape", ".wv",
    ".amr", ".awb", ".au", ".snd", ".caf", ".mka", ".m4b", ".m4r",
    ".mp2", ".mp1", ".ac3", ".eac3", ".dts", ".tta", ".tak", ".spx",
    ".dsf", ".dff", ".webm", ".weba", ".mp4", ".3gp", ".3g2",
}
_DECODE_LOCK = threading.Lock()


def is_audio_file(filename, *, entry=None):
    path = Path(filename)
    # Known audio extensions do not need the Windows MIME registry. DirEntry
    # reuses directory metadata instead of issuing a stat for every song.
    if not (entry.is_file() if entry is not None else path.is_file()):
        return False
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return True
    mime, _ = mimetypes.guess_type(str(path))
    return bool(mime and mime.startswith("audio/"))


def get_playback_path(filename, cache_dir=None):
    source = Path(filename).resolve()
    stat = source.stat()
    fingerprint = f"{source}:{stat.st_size}:{stat.st_mtime_ns}:pcm-v1"
    key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    cache = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "JuiceSpot-audio-cache"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (key + ".wav")
    with _DECODE_LOCK:
        if target.is_file() and target.stat().st_size > 44:
            return str(target)
        handle, temporary_name = tempfile.mkstemp(suffix=".wav", dir=cache)
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            command = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                       "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0", "-vn",
                       "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(temporary)]
            try:
                result = subprocess.run(command, capture_output=True, timeout=120,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                raise ValueError("Audio decoding timed out. Try a shorter file.") from None
            if result.returncode or temporary.stat().st_size <= 44:
                raise ValueError("This file could not be decoded into playable audio.")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return str(target)
