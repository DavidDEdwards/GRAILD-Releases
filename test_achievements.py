import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from achievements import ACHIEVEMENTS
from web_app import JuiceSpotApp


class AchievementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = JuiceSpotApp.__new__(JuiceSpotApp)
        self.app.account_db_path = str(Path(temporary.name) / "accounts.sqlite3")
        self.app.initialize_account_store()
        self.app.current_user_id = "a"
        self.app.current_track = "Test Song.mp3"
        self.app.current_difficulty = "Hard"
        self.app.attempt = 0
        self.app.streak = 1
        for user in ("a", "b"):
            with self.app.account_connection() as db:
                db.execute("INSERT INTO accounts (user_id, username, username_key, password_salt, password_hash, password_iterations, display_name, avatar, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (user, user, user, b"salt", b"hash", 1, user, "*", "2026-10-30T00:00:00+00:00"))

    def entries(self):
        return {item["id"]: item for item in self.app.get_achievements()["entries"]}

    def test_catalog_has_fifty_unique_achievements(self):
        self.assertEqual(len(ACHIEVEMENTS), 50)
        self.assertEqual(len({item["id"] for item in ACHIEVEMENTS}), 50)
        self.assertTrue(all(item["description"] and item["target"] > 0 for item in ACHIEVEMENTS))

    def test_first_hard_win_unlocks_correct_collection_and_special_badges(self):
        self.assertIsNone(self.app.record_song_guess(True, 30))
        entries = self.entries()
        for key in ("correct_1", "grails_1", "first_try_1", "hard_1"):
            self.assertTrue(entries[key]["unlocked"], key)
        self.assertFalse(entries["points_100"]["unlocked"])
        self.assertEqual(entries["points_100"]["progress"], 30)

    def test_wrong_guess_and_later_attempt_do_not_count_as_first_try(self):
        self.app.record_song_guess(False)
        self.app.attempt = 1
        self.app.record_song_guess(True, 20)
        self.assertFalse(self.entries()["first_try_1"]["unlocked"])
        self.assertTrue(self.entries()["hard_1"]["unlocked"])

    def test_easy_win_does_not_count_as_hard_and_grails_are_unique(self):
        self.app.current_difficulty = "Easy"
        self.app.record_song_guess(True, 60)
        self.app.record_song_guess(True, 60)
        entries = self.entries()
        self.assertFalse(entries["hard_1"]["unlocked"])
        self.assertEqual(entries["grails_5"]["progress"], 1)
        self.assertEqual(entries["first_try_10"]["progress"], 2)

    def test_existing_stats_unlock_and_dates_are_permanent(self):
        with self.app.account_connection() as db:
            db.execute("UPDATE accounts SET total_correct = 100, total_points = 1000, best_streak = 10, songs_played = 100 WHERE user_id = ?", ("a",))
        with patch.object(self.app, "utc_now", return_value="2026-10-30T12:00:00+00:00"):
            before = self.entries()
        self.assertTrue(before["correct_100"]["unlocked"])
        self.assertTrue(before["streak_10"]["unlocked"])
        self.assertFalse(before["first_try_1"]["unlocked"])
        with self.app.account_connection() as db:
            db.execute("UPDATE accounts SET best_streak = 0 WHERE user_id = ?", ("a",))
        after = self.entries()
        self.assertTrue(after["streak_10"]["unlocked"])
        self.assertEqual(after["streak_10"]["unlocked_at"], before["streak_10"]["unlocked_at"])
        self.assertEqual(after["streak_10"]["progress"], 10)

    def test_accounts_and_signed_out_progress_are_isolated(self):
        self.app.record_song_guess(True, 30)
        self.assertGreater(self.app.get_achievements()["unlocked"], 0)
        self.app.current_user_id = "b"
        self.assertEqual(self.app.get_achievements()["unlocked"], 0)
        self.app.current_user_id = None
        self.assertEqual(self.app.get_achievements()["entries"], [])

    def test_provider_badges_require_linked_identity(self):
        with self.app.account_connection() as db:
            db.execute("INSERT INTO external_identities VALUES (?, ?, ?, ?, ?)",
                       ("discord", "123", "a", None, "2026-10-30"))
        entries = self.entries()
        self.assertTrue(entries["linked_discord"]["unlocked"])
        self.assertFalse(entries["linked_google"]["unlocked"])

    def test_schema_upgrade_preserves_existing_progress(self):
        self.app.record_song_guess(True, 30)
        self.app.initialize_account_store()
        self.assertTrue(self.entries()["first_try_1"]["unlocked"])
        with self.app.account_connection() as db:
            self.assertEqual(db.execute("SELECT total_points FROM accounts WHERE user_id = 'a'").fetchone()[0], 30)


if __name__ == "__main__":
    unittest.main()
