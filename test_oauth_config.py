"""Google public configuration works without a developer launch environment."""
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import oauth_config
from prepare_mac_release import FILES
from web_app import JuiceSpotApp


class GoogleOAuthConfigurationTests(unittest.TestCase):
    def test_development_without_environment_uses_production_id(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(oauth_config.google_client_id(), oauth_config.DEFAULT_GOOGLE_CLIENT_ID)
        self.assertRegex(oauth_config.DEFAULT_GOOGLE_CLIENT_ID,
                         r'^\d+-[a-z0-9]+\.apps\.googleusercontent\.com$')

    def test_development_override_is_trimmed_and_resolved_at_call_time(self):
        with patch.dict(os.environ, {'GRAILD_GOOGLE_CLIENT_ID': '  development-id  '}):
            self.assertEqual(oauth_config.google_client_id(), 'development-id')
            os.environ['GRAILD_GOOGLE_CLIENT_ID'] = 'another-id'
            self.assertEqual(oauth_config.google_client_id(), 'another-id')

    def test_blank_overrides_use_production_id(self):
        for value in ('', '   '):
            with self.subTest(value=value), patch.dict(os.environ, {'GRAILD_GOOGLE_CLIENT_ID': value}):
                self.assertEqual(oauth_config.google_client_id(), oauth_config.DEFAULT_GOOGLE_CLIENT_ID)

    def test_packaged_login_without_environment_or_resource_files(self):
        for platform, arch in (('win32', 'AMD64'), ('darwin', 'x86_64'), ('darwin', 'arm64')):
            with self.subTest(platform=platform, arch=arch), \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, '_MEIPASS', '/nonexistent/bundle', create=True), \
                    patch.object(sys, 'platform', platform), \
                    patch('platform.machine', return_value=arch):
                self.assert_login_client_id(oauth_config.DEFAULT_GOOGLE_CLIENT_ID)

    def test_packaged_login_honors_environment_override(self):
        with patch.object(sys, 'frozen', True, create=True), \
                patch.dict(os.environ, {'GRAILD_GOOGLE_CLIENT_ID': ' override-id '}):
            self.assert_login_client_id('override-id')

    def assert_login_client_id(self, expected):
        app = JuiceSpotApp.__new__(JuiceSpotApp)
        app.social_flow_lock = threading.Lock()
        app.social_flow = None
        server = Mock(server_port=43210)
        with patch('web_app.http.server.HTTPServer', return_value=server) as listener, \
                patch('web_app.threading.Thread'):
            result = app.start_social_auth('google')
        self.assertTrue(result['success'], result)
        self.assertEqual(app.social_flow['client_id'], expected)
        self.assertEqual(app.social_flow['redirect_uri'], 'http://127.0.0.1:43210/')
        self.assertTrue(app.social_flow['state'])
        self.assertTrue(app.social_flow['verifier'])
        self.assertEqual(listener.call_args.args[0], ('127.0.0.1', 0))

    def test_mac_source_handoffs_include_configuration_and_regressions(self):
        for name in ('oauth_config.py', 'test_oauth_config.py'):
            self.assertIn(name, FILES)
            self.assertTrue(Path(__file__).with_name(name).is_file())


if __name__ == '__main__':
    unittest.main()
