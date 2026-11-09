import os
os.environ["SDL_AUDIODRIVER"] = "dummy"

import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import pygame
from random_snippets import RandomSnippet
from web_app import JuiceSpotApp


class GameModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.mixer.init(frequency=8000, size=-16, channels=1)

    @classmethod
    def tearDownClass(cls):
        pygame.mixer.quit()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(pygame.mixer.music.unload)
        playback = patch("web_app.get_playback_path", side_effect=lambda path: path)
        playback.start()
        self.addCleanup(playback.stop)
        self.folder = Path(temporary.name)
        self.song = self.folder / "song.wav"
        with wave.open(str(self.song), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"".join(struct.pack("<h", i % 30000) for i in range(30 * 8000)))

    def app(self):
        app = JuiceSpotApp.__new__(JuiceSpotApp)
        app.current_user_id = "player"
        app.current_track = "song.wav"
        app.tracks = ["song.wav", "other.wav"]
        app.game_mode = "classic"
        app.music_folder = str(self.folder)
        app.preview_durations = [1, 2, 4, 7, 11, 16]
        app.attempt = 0
        app.is_game_over = False
        app.audio_error = None
        app.random_snippet = None
        app.snippet_buffer = None
        app.stop_timer = None
        app.state = lambda: {"game_mode": app.game_mode}
        return app

    def test_random_clip_starts_at_chosen_offset_and_expands_from_same_point(self):
        with patch("random_snippets.random.randint", return_value=16000):
            snippet = RandomSnippet(str(self.song), 16)
        short, duration = snippet.clip(1)
        long, _ = snippet.clip(4)
        with wave.open(short) as audio:
            self.assertEqual(audio.getnframes(), 8000)
            short_pcm = audio.readframes(8000)
        with wave.open(long) as audio:
            self.assertEqual(audio.getnframes(), 32000)
            self.assertEqual(audio.readframes(8000), short_pcm)
        self.assertEqual(short_pcm, snippet.pcm[16000 * 2:24000 * 2])
        self.assertEqual(duration, 1)

    def test_short_song_clips_at_end_without_empty_audio(self):
        snippet = RandomSnippet(str(self.song), 60)
        buffer, duration = snippet.clip(60)
        self.assertEqual(duration, 30)
        self.assertEqual(snippet.start_frame, 0)
        with wave.open(buffer) as audio:
            self.assertEqual(audio.getnframes(), 30 * 8000)

    def test_saved_random_start_replays_identical_audio(self):
        original = RandomSnippet(str(self.song), 16, start_frame=16000)
        restored = RandomSnippet(str(self.song), 16, start_frame=original.start_frame)
        before, _ = original.clip(2)
        after, _ = restored.clip(2)
        self.assertEqual(before.read(), after.read())
        with self.assertRaises(ValueError):
            RandomSnippet(str(self.song), 16, start_frame=999999999)

    def test_mode_change_and_reroll_reset_random_start(self):
        app = self.app()
        app.random_snippet = object()
        self.assertEqual(app.set_game_mode("random")["game_mode"], "random")
        self.assertIsNone(app.random_snippet)
        app.random_snippet = object()
        previous = app.current_track
        app.reroll_song()
        self.assertIsNone(app.random_snippet)
        self.assertNotEqual(app.current_track, previous)

    def test_invalid_or_signed_out_mode_changes_are_ignored(self):
        app = self.app()
        app.set_game_mode("unknown")
        self.assertEqual(app.game_mode, "classic")
        app.current_user_id = None
        app.set_game_mode("random")
        self.assertEqual(app.game_mode, "classic")

    def test_random_mode_replay_reuses_decoded_audio_and_valid_wav_playback(self):
        app = self.app()
        app.game_mode = "random"
        with patch("web_app.threading.Timer") as timer:
            self.assertTrue(app.play_snippet()["ok"])
            decoded = app.random_snippet
            app.attempt = 1
            self.assertEqual(app.play_snippet()["duration"], 2)
            self.assertIs(app.random_snippet, decoded)
            self.assertEqual(timer.call_args.args[0], 2)
        pygame.mixer.music.stop()

    def test_classic_mode_keeps_intro_playback(self):
        app = self.app()
        with patch("web_app.RandomSnippet") as decoder, patch("web_app.threading.Timer"):
            self.assertTrue(app.play_snippet()["ok"])
            decoder.assert_not_called()
        pygame.mixer.music.stop()


if __name__ == "__main__":
    unittest.main()
