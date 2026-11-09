import os
os.environ["SDL_AUDIODRIVER"] = "dummy"

from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

import imageio_ffmpeg
import pygame
from audio_files import get_playback_path, is_audio_file
from web_app import JuiceSpotApp


class AudioFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.source = cls.folder / "source.wav"
        with wave.open(str(cls.source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b"".join(struct.pack("<h", (i % 100) * 200) for i in range(22050)))
        cls.formats = {"mp3": "libmp3lame", "m4a": "aac", "aac": "aac", "flac": "flac",
                       "opus": "libopus", "wma": "wmav2", "aiff": "pcm_s16be", "ogg": "libvorbis"}
        for extension, codec in cls.formats.items():
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error", "-y",
                            "-i", str(cls.source), "-c:a", codec, str(cls.folder / ("song." + extension))],
                           check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def setUp(self):
        self.cache = self.folder / self.id().split(".")[-1]

    def test_real_audio_formats_decode_to_playable_pcm(self):
        for extension in ["wav", *self.formats]:
            with self.subTest(extension=extension):
                source = self.source if extension == "wav" else self.folder / ("song." + extension)
                self.assertTrue(is_audio_file(source))
                normalized = get_playback_path(source, self.cache)
                with wave.open(normalized) as audio:
                    self.assertEqual(audio.getnchannels(), 2)
                    self.assertEqual(audio.getframerate(), 44100)
                    self.assertEqual(audio.getsampwidth(), 2)
                    self.assertGreater(audio.getnframes(), 0)

    def test_repeat_playback_uses_cache_and_source_changes_invalidate_it(self):
        source = self.folder / "change.wav"
        source.write_bytes(self.source.read_bytes())
        first = get_playback_path(source, self.cache)
        with patch("audio_files.subprocess.run") as decoder:
            self.assertEqual(get_playback_path(source, self.cache), first)
            decoder.assert_not_called()
        stamp = source.stat().st_mtime_ns
        os.utime(source, ns=(stamp + 1000000000, stamp + 1000000000))
        self.assertNotEqual(get_playback_path(source, self.cache), first)

    def test_invalid_files_fail_without_leaving_partial_cache(self):
        corrupt = self.folder / "broken.m4a"
        corrupt.write_text("not audio", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "could not be decoded"):
            get_playback_path(corrupt, self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertFalse(is_audio_file(self.folder))
        self.assertFalse(is_audio_file(self.folder / "notes.txt"))

    def test_known_audio_extensions_skip_mime_lookup(self):
        with patch("audio_files.mimetypes.guess_type", side_effect=AssertionError("MIME lookup")):
            self.assertTrue(is_audio_file(self.source))
            self.assertFalse(is_audio_file(self.folder / "missing.mp3"))
            self.assertFalse(is_audio_file(self.folder))

    def test_directory_scan_uses_entry_metadata_and_preserves_mime_fallback(self):
        with os.scandir(self.folder) as entries:
            entry = next(item for item in entries if item.name == self.source.name)
            with patch("audio_files.Path.is_file", side_effect=AssertionError("Extra stat")):
                self.assertTrue(is_audio_file(entry.path, entry=entry))
        unusual = self.folder / "song.customaudio"
        unusual.write_bytes(b"audio")
        with patch("audio_files.mimetypes.guess_type", return_value=("audio/custom", None)):
            self.assertTrue(is_audio_file(unusual))

    def test_m4a_plays_in_both_modes_and_answer_reveal(self):
        pygame.mixer.init(frequency=44100, size=-16, channels=2)
        self.addCleanup(pygame.mixer.quit)
        app = JuiceSpotApp.__new__(JuiceSpotApp)
        app.current_user_id = "player"
        app.current_track = "song.m4a"
        app.music_folder = str(self.folder)
        app.is_game_over = False
        app.audio_error = None
        app.preview_durations = [1, 2, 4]
        app.attempt = 0
        app.random_snippet = None
        app.snippet_buffer = None
        app.stop_timer = None
        with patch("web_app.get_playback_path", side_effect=lambda source: get_playback_path(source, self.cache)), patch("web_app.threading.Timer"):
            app.game_mode = "classic"
            self.assertTrue(app.play_snippet()["ok"])
            app.game_mode = "random"
            self.assertTrue(app.play_snippet()["ok"])
            self.assertIsNone(app.play_correct_answer())
        pygame.mixer.music.unload()


if __name__ == "__main__":
    unittest.main()
