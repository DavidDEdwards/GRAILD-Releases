import sys
import tempfile
import types
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
import platform_support


class MacSupportTests(unittest.TestCase):
    def test_windows_paths_remain_unchanged(self):
        with patch('platform_support.sys.platform', 'win32'), patch.dict('os.environ', {'APPDATA':'C:/Roaming', 'LOCALAPPDATA':'C:/Local'}):
            self.assertEqual(platform_support.user_data_directory(), Path('C:/Roaming/JuiceSpot'))
            self.assertEqual(platform_support.update_directory(), Path('C:/Local/GRAILD/updates'))

    def test_mac_uses_library_directories(self):
        with patch('platform_support.sys.platform', 'darwin'), patch('platform_support.Path.home', return_value=Path('/Users/test')):
            self.assertEqual(platform_support.user_data_directory(), Path('/Users/test/Library/Application Support/GRAILD'))
            self.assertEqual(platform_support.update_directory(), Path('/Users/test/Library/Caches/GRAILD/updates'))

    def test_mac_encryption_roundtrip_and_tampering(self):
        values = {}
        backend = Mock()
        backend.get_password.side_effect=lambda service, account: values.get((service, account))
        backend.set_password.side_effect=lambda service, account, value: values.__setitem__((service, account),value)
        module = types.ModuleType('keyring.backends.macOS');module.Keyring=lambda:backend
        with patch.dict(sys.modules, {'keyring.backends.macOS':module}):
            encrypted=platform_support.mac_session_data(b'session-token')
            self.assertNotIn(b'session-token', encrypted)
            self.assertEqual(platform_support.mac_session_data(encrypted, decrypt=True), b'session-token')
            with self.assertRaises(OSError):platform_support.mac_session_data(encrypted[:-3]+b'bad', decrypt=True)
            backend.set_password.assert_called_once()

    def test_missing_key_does_not_create_replacement_on_decrypt(self):
        backend=Mock();backend.get_password.return_value=None
        module=types.ModuleType('keyring.backends.macOS');module.Keyring=lambda:backend
        with patch.dict(sys.modules, {'keyring.backends.macOS':module}):
            with self.assertRaises(OSError):platform_support.mac_session_data(b'old-ciphertext', decrypt=True)
        backend.set_password.assert_not_called()

    def test_mac_uses_native_update_platform(self):
        from auto_update import UpdateManager
        with tempfile.TemporaryDirectory() as temp:
            session=Mock()
            with patch('auto_update.sys.platform','darwin'), patch('auto_update.platform.machine',return_value='arm64'):
                manager=UpdateManager(root=temp,session=session)
            self.assertEqual(manager.platform,'macos')
            self.assertEqual(manager.architecture,'arm64')


if __name__=='__main__':unittest.main()
