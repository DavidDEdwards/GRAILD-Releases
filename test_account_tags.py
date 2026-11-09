import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock
from achievements import ACHIEVEMENTS
from web_app import JuiceSpotApp


class AccountTagTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = JuiceSpotApp.__new__(JuiceSpotApp)
        self.app.account_db_path = str(Path(temporary.name) / 'accounts.sqlite3')
        self.app.initialize_account_store()
        self.app.current_user_id = 'local-owner'
        self.app.get_discord_broker_url = lambda: 'https://broker.example.test'
        self.app.protect_session_data = lambda data, decrypt=False: data[::-1]
        with self.app.account_connection() as db:
            for user in ('local-owner', 'other'):
                db.execute('INSERT INTO accounts (user_id, username, username_key, password_salt, password_hash, '
                           'password_iterations, display_name, avatar, created_at, is_creator) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (user, user, user, b'salt', b'hash', 1, user, '*', '2026-11-02', 1))
        self.response = {'success': True, 'provider': 'discord', 'player_id': 'a' * 64,
                         'tags': [{'id': 'beta_tester', 'awarded_at': 123}], 'can_manage_tags': False}
        self.app.oauth_json_request = Mock(side_effect=lambda *args, **kwargs: dict(self.response))

    def connect(self, user='local-owner'):
        self.app.save_global_identity(user, {'global_token': 'verified-session'})

    def profile(self):
        return self.app.get_account_state()['profile']

    def test_local_owner_and_local_tags_are_not_global_grants(self):
        with self.app.account_connection() as db:
            db.execute("INSERT INTO account_tags (user_id, tag_id, awarded_at) VALUES ('local-owner', 'beta_tester', 'old')")
        self.assertFalse(self.profile()['is_creator'])
        self.assertEqual(self.profile()['tags'], [])
        self.assertFalse(self.app.get_tag_accounts()['success'])
        self.assertFalse(self.app.set_account_tag('a' * 64, 'beta_tester', True)['success'])
        self.app.oauth_json_request.assert_not_called()

    def test_verified_tags_cache_for_offline_display_without_tokens(self):
        self.connect()
        result = self.app.sync_global_tags()
        self.assertTrue(result['success'])
        self.assertEqual(result['tags'][0]['id'], 'beta_tester')
        self.assertIn('beta test', result['tags'][0]['description'])
        self.assertNotIn('verified-session', json.dumps(result))
        self.assertEqual(self.app.oauth_json_request.call_args.kwargs['headers']['Authorization'], 'Bearer verified-session')
        self.app.oauth_json_request.side_effect = OSError('offline')
        self.assertFalse(self.app.sync_global_tags()['success'])
        self.assertEqual(self.profile()['tags'][0]['id'], 'beta_tester')

    def test_cache_is_bound_to_local_account_broker_and_provider_session(self):
        self.connect()
        self.app.sync_global_tags()
        self.app.current_user_id = 'other'
        self.assertEqual(self.profile()['tags'], [])
        self.app.current_user_id = 'local-owner'
        self.app.save_global_identity('local-owner', {'global_token': 'different-provider-session'})
        self.assertEqual(self.profile()['tags'], [])
        self.app.sync_global_tags()
        self.app.get_discord_broker_url = lambda: 'https://different.example.test'
        self.assertEqual(self.profile()['tags'], [])
        self.assertFalse(self.app.sync_global_tags()['success'])

    def test_only_server_owner_permissions_enable_management(self):
        self.connect()
        self.response.update(can_manage_tags=True, tags=[{'id': 'owner'}])
        self.app.sync_global_tags()
        self.assertTrue(self.profile()['is_creator'])
        self.app.oauth_json_request.side_effect = urllib.error.HTTPError('https://broker.example.test', 403, 'denied', {}, None)
        self.assertFalse(self.app.set_account_tag('b' * 64, 'beta_tester', True)['success'])
        self.assertFalse(self.app.get_tag_accounts()['success'])

    def test_assignment_uses_global_id_and_validates_inputs(self):
        self.connect()
        self.assertTrue(self.app.set_account_tag('b' * 64, 'beta_tester', True)['success'])
        self.assertEqual(self.app.oauth_json_request.call_args.args[1],
                         {'player_id': 'b' * 64, 'tag_id': 'beta_tester', 'assigned': 'true'})
        for tag in ('owner', 'top_earner', 'unknown', []):
            self.assertFalse(self.app.set_account_tag('b' * 64, tag, True)['success'])
        self.assertFalse(self.app.set_account_tag('local-username', 'beta_tester', True)['success'])

    def test_discord_assignment_accepts_numeric_id_and_mentions(self):
        self.connect()
        for value in ('456789012345678901', '<@456789012345678901>', '<@!456789012345678901>'):
            self.assertTrue(self.app.set_discord_account_tag(value, 'beta_tester', True)['success'])
            self.assertEqual(self.app.oauth_json_request.call_args.args[1], {
                'discord_id': '456789012345678901', 'tag_id': 'beta_tester', 'assigned': 'true'})
        for value in ('username', '123', '<@456789012345678901', None):
            self.assertFalse(self.app.set_discord_account_tag(value, 'beta_tester', True)['success'])
        self.assertFalse(self.app.set_discord_account_tag('456789012345678901', 'top_earner', True)['success'])

    def test_custom_tags_are_created_assigned_and_cached_with_hover_descriptions(self):
        self.connect()
        tag_id = 'custom_' + 'a' * 24
        self.assertTrue(self.app.create_custom_tag('Contributor', 'Helped build the game')['success'])
        self.assertTrue(self.app.set_account_tag('b' * 64, tag_id, True)['success'])
        self.assertTrue(self.app.set_discord_account_tag('456789012345678901', tag_id, True)['success'])
        self.response['tags'] = [{'id':tag_id,'kind':'assigned','name':'Contributor',
                                  'description':'Helped build the game','awarded_at':123}]
        self.app.sync_global_tags()
        self.assertEqual(self.profile()['tags'][0]['description'], 'Helped build the game')
        self.assertFalse(self.app.create_custom_tag('', 'description')['success'])
        self.assertFalse(self.app.create_custom_tag('x'*33, 'description')['success'])

    def test_earned_sync_uses_real_unlocked_ids(self):
        self.connect()
        with self.app.account_connection() as db:
            for item in ACHIEVEMENTS:
                db.execute('INSERT INTO account_achievements VALUES (?, ?, ?)', ('local-owner', item['id'], 'earlier'))
        self.app.sync_global_tags()
        ids = json.loads(self.app.oauth_json_request.call_args.args[1]['achievement_ids'])
        self.assertEqual(set(ids), {item['id'] for item in ACHIEVEMENTS})
        self.assertEqual(len(ids), 50)

    def test_switch_during_sync_does_not_apply_another_accounts_tags(self):
        self.connect()
        def switch(*args, **kwargs):
            self.app.current_user_id = 'other'
            return self.response
        self.app.oauth_json_request.side_effect = switch
        self.assertFalse(self.app.sync_global_tags()['success'])
        self.assertEqual(self.profile()['tags'], [])


if __name__ == '__main__':
    unittest.main()
