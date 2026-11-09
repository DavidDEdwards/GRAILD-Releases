import json
import os
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import pygame

from web_app import DIFFICULTIES, JuiceSpotApp


class RoundResumeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(pygame.mixer.quit)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.music = self.root / 'Music'
        self.music.mkdir()
        for name in ('First.mp3', 'Second.mp3'):
            (self.music / name).write_bytes(b'audio fixture')
        (self.root / 'juicespot_config.json').write_text(json.dumps({'music_folder': str(self.music)}))
        setup = JuiceSpotApp.__new__(JuiceSpotApp)
        setup.account_db_path = str(self.root / 'accounts.sqlite3')
        setup.initialize_account_store()
        with setup.account_connection() as db:
            for user in ('a', 'b'):
                db.execute('INSERT INTO accounts (user_id, username, username_key, password_salt, password_hash, '
                           'password_iterations, display_name, avatar, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (user, user, user, b'salt', b'hash', 1, user, '*', '2026-11-02'))

    def open_app(self, user='a'):
        canary = Mock()
        canary.client.cached_recording_eras.return_value = []
        canary.library_era_checks.return_value = {}
        def restore(app):
            app.current_user_id = user
            if user:
                app.load_account_stats()
        with patch('web_app.user_data_directory', return_value=self.root), \
             patch('web_app.UpdateManager'), patch('web_app.CanaryIntegration', return_value=canary), \
             patch.object(JuiceSpotApp, 'get_legacy_config_files', return_value=[]), \
             patch.object(JuiceSpotApp, 'restore_persistent_session', autospec=True, side_effect=restore):
            app = JuiceSpotApp()
        return app

    def test_restart_restores_song_attempt_mode_difficulty_and_random_start(self):
        app = self.open_app()
        app.current_track = 'Second.mp3'
        app.set_game_mode('random')
        app.set_difficulty('Hard')
        track = app.current_track
        app._random_start_frame = 12345
        app.skip_attempt()
        reopened = self.open_app()
        self.assertEqual(reopened.current_track, track)
        self.assertEqual(reopened.game_mode, 'random')
        self.assertEqual(reopened.current_difficulty, 'Hard')
        self.assertEqual(reopened.preview_durations, DIFFICULTIES['Hard'])
        self.assertEqual(reopened.attempt, 1)
        self.assertEqual(reopened._random_start_frame, 12345)
        self.assertIsNone(reopened.random_snippet)
        self.assertFalse(reopened.is_game_over)

    def test_wrong_guess_is_restored_without_resetting_attempts_or_stats(self):
        app = self.open_app()
        app.submit_guess('not the title')
        reopened = self.open_app()
        self.assertEqual(reopened.attempt, 1)
        self.assertEqual(reopened.wrong_guesses, 1)
        self.assertEqual(reopened.current_track, app.current_track)

    def test_completed_round_stays_completed_and_cannot_score_again(self):
        app = self.open_app()
        with patch.object(app, 'play_correct_answer', return_value=None), patch.object(app, 'get_cover_data', return_value=''):
            self.assertTrue(app.submit_guess(Path(app.current_track).stem)['success'])
        reopened = self.open_app()
        self.assertTrue(reopened.is_game_over)
        score = reopened.score
        reopened.submit_guess(Path(reopened.current_track).stem)
        self.assertEqual(reopened.score, score)
        self.assertEqual(reopened.correct_guesses, 1)
        reopened.next_round()
        self.assertFalse(self.open_app().is_game_over)

    def test_last_skip_restores_finished_round(self):
        app = self.open_app()
        with patch.object(app, 'get_cover_data', return_value=''):
            for _ in app.preview_durations:
                app.skip_attempt()
        reopened = self.open_app()
        self.assertTrue(reopened.is_game_over)
        self.assertEqual(reopened.attempt, len(app.preview_durations))
        self.assertEqual(reopened.get_account_state()['profile']['songs_played'], 1)

    def test_saves_are_isolated_and_survive_sign_out(self):
        app = self.open_app()
        app.skip_attempt()
        track = app.current_track
        app.sign_out()
        other = self.open_app('b')
        self.assertEqual(other.attempt, 0)
        other.set_game_mode('random')
        reopened = self.open_app('a')
        self.assertEqual(reopened.attempt, 1)
        self.assertEqual(reopened.current_track, track)
        self.assertEqual(reopened.game_mode, 'classic')

    def test_missing_song_or_invalid_save_starts_valid_round(self):
        app = self.open_app()
        missing = app.current_track
        (self.music / missing).unlink()
        reopened = self.open_app()
        self.assertNotEqual(reopened.current_track, missing)
        self.assertEqual(reopened.attempt, 0)
        with reopened.account_connection() as db:
            db.execute("UPDATE saved_rounds SET attempt = 999 WHERE user_id = 'a'")
        self.assertEqual(self.open_app().attempt, 0)

    def test_reroll_and_mode_change_replace_saved_round(self):
        app = self.open_app()
        app.skip_attempt()
        previous = app.current_track
        app.reroll_song()
        reopened = self.open_app()
        self.assertNotEqual(reopened.current_track, previous)
        self.assertEqual(reopened.attempt, 0)
        reopened.set_game_mode('random')
        self.assertEqual(self.open_app().game_mode, 'random')

    def test_mode_switch_discards_previous_round_and_overwrites_single_save(self):
        app = self.open_app()
        original_track = app.current_track
        app.skip_attempt()
        app.set_game_mode('random')
        self.assertNotEqual(app.current_track, original_track)
        self.assertEqual(app.attempt, 0)
        app._random_start_frame = 12345
        app.skip_attempt()
        app.set_game_mode('classic')
        self.assertEqual(app.attempt, 0)
        self.assertIsNone(app._random_start_frame)
        reopened = self.open_app()
        self.assertEqual(reopened.game_mode, 'classic')
        self.assertEqual(reopened.attempt, 0)
        with app.account_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM saved_rounds WHERE user_id = 'a'").fetchone()[0], 1)

    def test_selecting_current_mode_keeps_round(self):
        app = self.open_app()
        app.skip_attempt()
        track = app.current_track
        app.set_game_mode('classic')
        self.assertEqual(app.current_track, track)
        self.assertEqual(app.attempt, 1)


if __name__ == '__main__':
    unittest.main()
