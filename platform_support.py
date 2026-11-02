"""OS-specific storage paths and macOS Keychain-backed session encryption."""
import os
from pathlib import Path
import sys
import threading

_MAC_SESSION_LOCK = threading.RLock()


def user_data_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'GRAILD'
    return Path(os.environ.get('APPDATA', os.path.expanduser('~'))) / 'JuiceSpot'


def update_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Caches' / 'GRAILD' / 'updates'
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'GRAILD' / 'updates'


def mac_session_data(data, decrypt=False):
    with _MAC_SESSION_LOCK:
        return _mac_session_data(data, decrypt=decrypt)


def _mac_session_data(data, decrypt=False):
    # Instantiate Apple's backend explicitly: never select a plaintext fallback.
    try:
        from keyring.backends.macOS import Keyring
        from cryptography.fernet import Fernet
        backend = Keyring()
        service, account = 'com.graild.desktop', 'session-encryption-v1'
        key = backend.get_password(service, account)
        if key is None:
            if decrypt:
                raise OSError('The saved session key is missing from macOS Keychain. Sign in again.')
            key = Fernet.generate_key().decode('ascii')
            backend.set_password(service, account, key)
            # Use the value committed to Keychain, not an unpersisted key.
            key = backend.get_password(service, account)
            if not key:
                raise OSError('Could not save the session key in macOS Keychain.')
        cipher = Fernet(key.encode('ascii'))
        return cipher.decrypt(data) if decrypt else cipher.encrypt(data)
    except OSError:
        raise
    except Exception:
        # Do not expose key material or backend error details to the UI/logs.
        raise OSError('macOS Keychain could not protect this session. Unlock your login Keychain and try signing in again.') from None
