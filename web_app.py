import ctypes
from ctypes import wintypes
import base64
import hashlib
import hmac
import http.server
import json
import mimetypes
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone

import pygame
import webview

from achievements import account_achievements, PROFILE_TAGS
from random_snippets import RandomSnippet
from audio_files import is_audio_file, get_playback_path
from canary_integration import CanaryIntegration
from auto_update import UpdateManager, UnavailableUpdateManager
from update_security import UpdateError
from app_version import APP_VERSION
from platform_support import user_data_directory, mac_session_data
from tls_support import verified_ssl_context
from oauth_config import google_client_id as resolve_google_client_id

try:
  import requests
except ImportError:
  requests = None

try:
  from mutagen.mp3 import MP3
  from mutagen.id3 import APIC
except ImportError:
  MP3 = None
  APIC = None


CHALLENGE_MODES = ("sudden_death", "three_lives", "time_attack", "sprint")

DIFFICULTIES = {
    "Easy": [1, 2, 4, 7, 11, 16],
    "Medium": [1, 3, 6, 10],
    "Hard": [1, 3, 6],
}
PASSWORD_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,24}$")
AVATARS = ("🧃", "💿", "🌙", "⭐", "🧊", "🕊️", "🦋", "🎧", "💎", "🖤")
DEFAULT_DISCORD_BROKER_URL = "https://graild-discord-broker.onrender.com"
VERSION_SUFFIX = re.compile(
    r"\s*(?:\(|\[)?\s*(?:session(?:\s+edit|s)?|normal(?:\s+version)?|"
    r"original(?:\s+version)?|standard(?:\s+version)?|studio(?:\s+version)?|"
    r"album(?:\s+version)?|alternate(?:\s+version)?|alt(?:\s+version)?|"
    r"demo|version\s*\d+|v\d+)\s*(?:\)|\])?\s*$",
    re.IGNORECASE,
)


def normalize_song_title(title):
  normalized = re.sub(r"\s+", " ", str(title).replace("_", " ")).strip().casefold()
  previous = None
  while normalized != previous:
    previous = normalized
    normalized = VERSION_SUFFIX.sub("", normalized).strip(" -_()[]")
  return normalized


def resource_path(filename):
  base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
  return os.path.join(base_path, filename)


class DataBlob(ctypes.Structure):
  _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class JuiceSpotApp:
  def __init__(self):
    self.volume = 0.7
    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
    self.audio_error = None
    try:
      pygame.mixer.init()
      pygame.mixer.music.set_volume(self.volume)
    except pygame.error as error:
      self.audio_error = str(error)

    config_directory = str(user_data_directory())
    os.makedirs(config_directory, exist_ok=True)
    self.config_file = os.path.join(config_directory, "juicespot_config.json")
    self.song_stats_file = os.path.join(config_directory, "song_stats.json")
    self.account_db_path = os.path.join(config_directory, "accounts.sqlite3")
    self.session_file = os.path.join(config_directory, "session.dat")
    try:
      self._updates = UpdateManager()
    except OSError as error:
      self._updates = UnavailableUpdateManager(error)
    self._canary = CanaryIntegration(config_directory)
    self.recording_era = ""
    self.library_source = "local"
    self.song_stats = self.load_song_stats()
    self.initialize_account_store()
    self.current_user_id = None
    self.social_flow = None
    self.social_flow_lock = threading.Lock()
    self.pending_social_identity = None
    self.legacy_config_files = self.get_legacy_config_files()
    self.music_folder = self.load_config_folder()
    self.tracks = []
    self.library_error = None
    self.current_track = None
    self.game_mode = "classic"
    self.random_snippet = None
    self.snippet_buffer = None
    self.current_difficulty = "Easy"
    self.preview_durations = DIFFICULTIES[self.current_difficulty]
    self.attempt = 0
    self.score = 0
    self.streak = 0
    self.correct_guesses = 0
    self.wrong_guesses = 0
    self.is_game_over = False
    self._window = None
    self.stop_timer = None
    self.load_tracks()
    self.restore_persistent_session()

  @contextmanager
  def account_connection(self):
    connection = sqlite3.connect(self.account_db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
      yield connection
      connection.commit()
    except Exception:
      connection.rollback()
      raise
    finally:
      connection.close()

  def initialize_account_store(self):
    with self.account_connection() as connection:
      connection.executescript(
          """
          CREATE TABLE IF NOT EXISTS accounts (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            username_key TEXT NOT NULL UNIQUE,
            password_salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            password_iterations INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            avatar TEXT NOT NULL,
            created_at TEXT NOT NULL,
            total_correct INTEGER NOT NULL DEFAULT 0,
            total_wrong INTEGER NOT NULL DEFAULT 0,
            current_streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            total_points INTEGER NOT NULL DEFAULT 0,
            songs_played INTEGER NOT NULL DEFAULT 0,
            is_creator INTEGER NOT NULL DEFAULT 0
          );
          CREATE TABLE IF NOT EXISTS song_stats (
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            track_key TEXT NOT NULL,
            title TEXT NOT NULL,
            correct INTEGER NOT NULL DEFAULT 0,
            wrong INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, track_key)
          );
          CREATE TABLE IF NOT EXISTS grails (
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            track_key TEXT NOT NULL,
            title TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY (user_id, track_key)
          );
          CREATE TABLE IF NOT EXISTS external_identities (
            provider TEXT NOT NULL,
            subject TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            email TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (provider, subject)
          );
          CREATE TABLE IF NOT EXISTS app_session (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS achievement_counters (
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            metric TEXT NOT NULL,
            value INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, metric)
          );
          CREATE TABLE IF NOT EXISTS account_achievements (
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            unlocked_at TEXT NOT NULL,
            PRIMARY KEY (user_id, achievement_id)
          );
          CREATE TABLE IF NOT EXISTS account_tags (
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL,
            awarded_at TEXT NOT NULL,
            assigned_by TEXT REFERENCES accounts(user_id) ON DELETE SET NULL,
            PRIMARY KEY (user_id, tag_id)
          );
          CREATE TABLE IF NOT EXISTS game_preferences (
            user_id TEXT PRIMARY KEY REFERENCES accounts(user_id) ON DELETE CASCADE,
            recording_era TEXT NOT NULL DEFAULT '',
            library_source TEXT NOT NULL DEFAULT 'local'
          );
          CREATE TABLE IF NOT EXISTS saved_rounds (
            user_id TEXT PRIMARY KEY REFERENCES accounts(user_id) ON DELETE CASCADE,
            folder TEXT NOT NULL,
            track TEXT NOT NULL,
            mode TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            game_over INTEGER NOT NULL,
            random_start INTEGER
          );
          CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS challenge_bests (
            user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
            mode TEXT NOT NULL,
            best_score INTEGER NOT NULL DEFAULT 0,
            best_songs INTEGER NOT NULL DEFAULT 0,
            runs INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, mode)
          );
          """
      )
      preference_columns = {row["name"] for row in connection.execute("PRAGMA table_info(game_preferences)")}
      if "library_source" not in preference_columns:
        connection.execute("ALTER TABLE game_preferences ADD COLUMN library_source TEXT NOT NULL DEFAULT 'local'")
      account_columns = {
          row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
      }
      if "is_creator" not in account_columns:
          connection.execute(
              "ALTER TABLE accounts ADD COLUMN is_creator INTEGER NOT NULL DEFAULT 0"
          )
      creator_migration = connection.execute(
          "SELECT value FROM app_meta WHERE key = 'creator_assigned'"
      ).fetchone()
      if not creator_migration:
        first_account = connection.execute(
            "SELECT user_id FROM accounts ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
        if first_account:
          connection.execute(
              "UPDATE accounts SET is_creator = 1 WHERE user_id = ?",
              (first_account["user_id"],),
          )
          connection.execute(
              "INSERT INTO app_meta (key, value) VALUES ('creator_assigned', '1')"
          )

  @staticmethod
  def protect_session_data(data, decrypt=False):
    if sys.platform == "darwin":
      return mac_session_data(data, decrypt=decrypt)
    if os.name != "nt":
      raise OSError("Persistent sign-in requires Windows DPAPI or macOS Keychain.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(
        len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output = DataBlob()
    if decrypt:
      transform = crypt32.CryptUnprotectData
      transform.argtypes = [
          ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
      ]
      succeeded = transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
      transform = crypt32.CryptProtectData
      transform.argtypes = [
          ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
      ]
      succeeded = transform(
          ctypes.byref(source), "GRAIL'D persistent session", None, None, None, 1,
          ctypes.byref(output),
      )
    transform.restype = wintypes.BOOL
    if not succeeded:
      raise ctypes.WinError(ctypes.get_last_error())
    try:
      return ctypes.string_at(output.pbData, output.cbData)
    finally:
      kernel32.LocalFree.argtypes = [ctypes.c_void_p]
      kernel32.LocalFree.restype = ctypes.c_void_p
      kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))

  def restore_persistent_session(self):
    try:
      with open(self.session_file, "rb") as session_file:
        token = self.protect_session_data(session_file.read(), decrypt=True).decode("ascii")
      token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
      with self.account_connection() as connection:
        session = connection.execute(
            "SELECT user_id FROM app_session WHERE singleton_id = 1 AND token_hash = ?",
            (token_hash,),
        ).fetchone()
      if not session:
        self.clear_saved_session()
        return False
      self.current_user_id = session["user_id"]
      self.load_account_stats()
      return True
    except (OSError, UnicodeError, sqlite3.Error):
      self.clear_saved_session()
      return False

  def clear_saved_session(self):
    database_cleared = False
    file_cleared = False
    try:
      with self.account_connection() as connection:
        connection.execute("DELETE FROM app_session WHERE singleton_id = 1")
    except sqlite3.Error:
      return False
    database_cleared = True
    try:
      os.remove(self.session_file)
    except FileNotFoundError:
      file_cleared = True
    except OSError:
      return False
    else:
      file_cleared = True
    return database_cleared and file_cleared

  def create_persistent_session(self, user_id):
    token = secrets.token_urlsafe(32)
    protected_token = self.protect_session_data(token.encode("ascii"))
    temp_path = f"{self.session_file}.{uuid.uuid4().hex}.tmp"
    try:
      with open(temp_path, "wb") as session_file:
        session_file.write(protected_token)
        session_file.flush()
        os.fsync(session_file.fileno())
      os.replace(temp_path, self.session_file)
      with self.account_connection() as connection:
        connection.execute("DELETE FROM app_session WHERE singleton_id = 1")
        connection.execute(
            "INSERT INTO app_session (singleton_id, user_id, token_hash, created_at) "
            "VALUES (1, ?, ?, ?)",
            (user_id, hashlib.sha256(token.encode("ascii")).hexdigest(), self.utc_now()),
        )
    except (OSError, sqlite3.Error):
      for path in (temp_path, self.session_file):
        try:
          os.remove(path)
        except OSError:
          pass
      raise

  @staticmethod
  def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

  def load_account_stats(self):
    with self.account_connection() as connection:
      account = connection.execute(
          "SELECT total_correct, total_wrong, total_points, current_streak "
          "FROM accounts WHERE user_id = ?",
          (self.current_user_id,),
      ).fetchone()
    if not account:
      self.current_user_id = None
      self.correct_guesses = self.wrong_guesses = self.score = self.streak = 0
      return
    self.load_game_preferences()
    self.correct_guesses = account["total_correct"]
    self.wrong_guesses = account["total_wrong"]
    self.score = account["total_points"]
    self.streak = account["current_streak"]
    self.random_snippet = None
    self._random_start_frame = None
    self.snippet_buffer = None
    self.game_mode = "classic"
    self.current_difficulty = "Easy"
    self.preview_durations = DIFFICULTIES[self.current_difficulty]
    self.attempt = 0
    self.is_game_over = False
    if not self._restore_round():
      self.next_round()

  def _save_round(self):
    if not self.current_user_id or not self.current_track or not getattr(self, "account_db_path", None):
      return
    try:
      with self.account_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO saved_rounds "
            "(user_id, folder, track, mode, difficulty, attempt, game_over, random_start) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.current_user_id, os.path.normcase(os.path.abspath(self.music_folder)), self.current_track,
             self.game_mode, self.current_difficulty, self.attempt, int(self.is_game_over),
             getattr(self, "_random_start_frame", None)))
      self.round_save_error = None
    except (OSError, sqlite3.Error) as error:
      self.round_save_error = str(error)

  def _restore_round(self):
    if not self.current_user_id or not getattr(self, "account_db_path", None):
      return False
    try:
      with self.account_connection() as connection:
        saved = connection.execute("SELECT * FROM saved_rounds WHERE user_id = ?", (self.current_user_id,)).fetchone()
      if not saved or saved["folder"] != os.path.normcase(os.path.abspath(self.music_folder)):
        return False
      if saved["track"] not in self.get_eligible_tracks() or saved["mode"] not in ("classic", "random"):
        return False
      if saved["difficulty"] not in DIFFICULTIES or saved["game_over"] not in (0, 1):
        return False
      durations = DIFFICULTIES[saved["difficulty"]]
      if type(saved["attempt"]) is not int or not 0 <= saved["attempt"] <= len(durations) - (not saved["game_over"]):
        return False
      start = saved["random_start"]
      if start is not None and (type(start) is not int or start < 0):
        return False
      self.current_track = saved["track"]
      self.game_mode = saved["mode"]
      self.current_difficulty = saved["difficulty"]
      self.preview_durations = durations
      self.attempt = saved["attempt"]
      self.is_game_over = bool(saved["game_over"])
      self.random_snippet = None
      self.snippet_buffer = None
      self._random_start_frame = start if self.game_mode == "random" else None
      self.round_save_error = None
      return True
    except (OSError, sqlite3.Error):
      return False

  def get_account_state(self):
    if not self.current_user_id:
      return {"authenticated": False}
    with self.account_connection() as connection:
      account = connection.execute(
          "SELECT * FROM accounts WHERE user_id = ?", (self.current_user_id,)
      ).fetchone()
      if not account:
        self.current_user_id = None
        return {"authenticated": False}
      grails_collected = connection.execute(
          "SELECT COUNT(*) FROM grails WHERE user_id = ?", (self.current_user_id,)
      ).fetchone()[0]
      linked_providers = [
          row["provider"]
          for row in connection.execute(
              "SELECT provider FROM external_identities WHERE user_id = ? ORDER BY provider",
              (self.current_user_id,),
          )
      ]
      achievements = account_achievements(connection, self.current_user_id, self.utc_now())
      global_tags = self._cached_global_tag_state(connection, self.current_user_id)
    attempts = account["total_correct"] + account["total_wrong"]
    return {
        "authenticated": True,
        "achievements": achievements,
        "profile": {
            "username": account["username"],
            "display_name": account["display_name"],
            "avatar": account["avatar"],
            "created_at": account["created_at"],
            "total_correct": account["total_correct"],
            "total_wrong": account["total_wrong"],
            "accuracy": round(account["total_correct"] * 100 / attempts, 1) if attempts else 0,
            "current_streak": account["current_streak"],
            "best_streak": account["best_streak"],
            "is_creator": bool(global_tags.get("can_manage_tags")),
            "tags": global_tags.get("tags", []),
            "global_player_id": global_tags.get("player_id", ""),
            "total_points": account["total_points"],
            "songs_played": account["songs_played"],
            "grails_collected": grails_collected,
            "linked_providers": linked_providers,
        },
    }

  def get_achievements(self):
    if not self.current_user_id:
      return {"entries": [], "unlocked": 0, "total": 50}
    with self.account_connection() as connection:
      return account_achievements(connection, self.current_user_id, self.utc_now())

  def get_tag_accounts(self):
    result, _ = self._request_global_tags('/account/tag-players')
    if not isinstance(result.get('accounts'), list):
      result['accounts'] = []
    return result

  def set_account_tag(self, player_id, tag_id, assigned):
    if not self._assignable_tag_id(tag_id) or type(assigned) is not bool:
      return {'success': False, 'message': 'Choose Beta Tester or a custom tag. Earned and owner tags cannot be assigned manually.'}
    if not isinstance(player_id, str) or not re.fullmatch(r'[a-f0-9]{64}', player_id):
      return {'success': False, 'message': 'Choose a verified global account.'}
    # The broker checks the verified Discord owner identity on every mutation.
    result, _ = self._request_global_tags('/account/tags/assign', {
        'player_id': player_id, 'tag_id': tag_id, 'assigned': 'true' if assigned else 'false'})
    return result

  def set_discord_account_tag(self, discord_id, tag_id, assigned):
    match = re.fullmatch(r'(?:([0-9]{15,22})|<@!?([0-9]{15,22})>)', str(discord_id or '').strip())
    if not match or not self._assignable_tag_id(tag_id) or type(assigned) is not bool:
      return {'success': False, 'message': 'Enter a Discord ID or mention and choose an assignable tag.'}
    result, _ = self._request_global_tags('/account/tags/discord', {
        'discord_id': match.group(1) or match.group(2), 'tag_id': tag_id,
        'assigned': 'true' if assigned else 'false'})
    return result

  @staticmethod
  def _assignable_tag_id(tag_id):
    return isinstance(tag_id, str) and (tag_id == 'beta_tester' or re.fullmatch(r'custom_[a-f0-9]{24}', tag_id) is not None)

  def get_tag_catalog(self):
    result, _ = self._request_global_tags('/account/tags/catalog')
    return result

  def create_custom_tag(self, name, description):
    if not isinstance(name, str) or not isinstance(description, str) or not 1 <= len(name.strip()) <= 32 or not 1 <= len(description.strip()) <= 240:
      return {'success': False, 'message': 'Enter a tag name (1–32 characters) and hover description (1–240 characters).'}
    result, _ = self._request_global_tags('/account/tags/create', {'name': name.strip(), 'description': description.strip()})
    return result

  def _cached_global_tag_state(self, connection, user_id):
    try:
      saved = connection.execute("SELECT value FROM app_meta WHERE key = ?", ('global_tags:' + user_id,)).fetchone()
      credential = connection.execute("SELECT value FROM app_meta WHERE key = ?", ('global_identity:' + user_id,)).fetchone()
      if not saved or not credential:
        return {}
      cache, session = json.loads(saved['value']), json.loads(credential['value'])
      broker = self.get_discord_broker_url()
      if cache['broker'] != broker or session['broker'] != broker or cache['binding'] != hashlib.sha256(session['token'].encode()).hexdigest():
        return {}
      return cache['state'] if isinstance(cache['state'], dict) else {}
    except (ValueError, KeyError, TypeError, AttributeError):
      return {}

  def _request_global_tags(self, endpoint, payload=None, *, user_id=None):
    user_id = self.current_user_id if user_id is None else user_id
    try:
      if user_id != self.current_user_id:
        raise ValueError('Account changed. Refresh your profile.')
      if not user_id:
        raise ValueError('Sign in and connect Discord or Google to use global tags.')
      broker = self.get_discord_broker_url()
      with self.account_connection() as connection:
        row = connection.execute("SELECT value FROM app_meta WHERE key = ?", ('global_identity:' + user_id,)).fetchone()
      if not row:
        raise ValueError('Connect Discord or Google to recognize your tags globally.')
      saved = json.loads(row['value'])
      if saved['broker'] != broker:
        raise ValueError('Reconnect Discord or Google to this server.')
      token = self.protect_session_data(base64.b64decode(saved['token'], validate=True), decrypt=True).decode('utf-8')
      result = self.oauth_json_request(broker + endpoint, payload, headers={'Authorization': 'Bearer ' + token})
      if not isinstance(result, dict) or result.get('success') is not True:
        raise ValueError('The tag server returned an invalid response.')
      if user_id != self.current_user_id:
        raise ValueError('Account changed. Refresh your profile.')
      return result, hashlib.sha256(saved['token'].encode()).hexdigest()
    except urllib.error.HTTPError as error:
      message = {401: 'Reconnect Discord or Google to refresh global tags.',
                 400: 'Check the tag details and choose an assignable tag.',
                 409: 'A tag with that name already exists. Choose it from the tag list.',
                 403: 'Only the verified Discord owner can manage global tags.',
                 404: 'The sign-in server needs the global tags update.'}.get(error.code, 'Global tags are unavailable. Please retry.')
      error.close()
    except (OSError, sqlite3.Error):
      message = 'Global tags are offline. Your last synced tags remain available.'
    except (ValueError, KeyError, TypeError) as error:
      message = str(error) if isinstance(error, ValueError) else 'Reconnect Discord or Google to refresh global tags.'
    return {'success': False, 'message': message}, None

  def sync_global_tags(self):
    user_id = self.current_user_id
    if not user_id:
      return {'success': False, 'message': 'Sign in to recognize your tags globally.'}
    with self.account_connection() as connection:
      achievements = account_achievements(connection, user_id, self.utc_now())
    result, binding = self._request_global_tags('/account/tags/sync', {
        'achievement_ids': json.dumps([item['id'] for item in achievements['entries'] if item['unlocked']])}, user_id=user_id)
    if not result.get('success'):
      return result
    if not isinstance(result.get('player_id'), str) or not re.fullmatch(r'[a-f0-9]{64}', result['player_id']) or not isinstance(result.get('tags'), list):
      return {'success': False, 'message': 'The tag server returned an invalid profile.'}
    tags = []
    for tag in result['tags']:
      if isinstance(tag, dict) and isinstance(tag.get('id'), str) and tag['id'] in PROFILE_TAGS:
        tags.append({**PROFILE_TAGS[tag['id']], 'awarded_at': tag.get('awarded_at')})
      elif (isinstance(tag, dict) and isinstance(tag.get('id'), str)
            and re.fullmatch(r'custom_[a-f0-9]{24}', tag['id']) and tag.get('kind') == 'assigned'
            and isinstance(tag.get('name'), str) and 1 <= len(tag['name']) <= 32
            and isinstance(tag.get('description'), str) and 1 <= len(tag['description']) <= 240):
        tags.append({key: tag.get(key) for key in ('id', 'name', 'description', 'kind', 'awarded_at')})
    state = {'player_id': result['player_id'], 'tags': tags,
             'can_manage_tags': result.get('can_manage_tags') is True}
    with self.account_connection() as connection:
      # Cache server grants for offline display; local tags/owner flags are never uploaded.
      connection.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                         ('global_tags:' + user_id, json.dumps({'broker': self.get_discord_broker_url(),
                                                               'binding': binding, 'state': state})))
    return {'success': True, 'message': 'Tags synced to your verified ' + str(result.get('provider', '')) + ' account.', **state}

  def register_account(self, username, password):
    if self.current_user_id:
      return {"success": False, "message": "Sign out before creating or switching accounts."}
    username = str(username or "").strip()
    password = str(password or "")
    if not USERNAME_PATTERN.fullmatch(username):
      return {"success": False, "message": "Username must be 3–24 letters, numbers, or underscores."}
    if not 10 <= len(password) <= 128:
      return {"success": False, "message": "Password must be between 10 and 128 characters."}
    user_id = uuid.uuid4().hex
    pending_identity = self.pending_social_identity
    if pending_identity and pending_identity["expires_at"] < time.time():
      pending_identity = None
      self.pending_social_identity = None
    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    try:
      with self.account_connection() as connection:
        account_count = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        connection.execute(
            "INSERT INTO accounts (user_id, username, username_key, password_salt, "
            "password_hash, password_iterations, display_name, avatar, created_at, is_creator) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, username.casefold(), salt, password_hash,
             PASSWORD_ITERATIONS, username, AVATARS[0], self.utc_now(), int(account_count == 0)),
        )
        if account_count == 0:
          connection.execute(
              "INSERT OR IGNORE INTO app_meta (key, value) VALUES ('creator_assigned', '1')"
          )
        if account_count == 0:
          self.import_legacy_song_stats(connection, user_id)
        if pending_identity:
          connection.execute(
              "INSERT INTO external_identities (provider, subject, user_id, email, created_at) "
              "VALUES (?, ?, ?, ?, ?)",
              (pending_identity["provider"], pending_identity["subject"], user_id,
               pending_identity.get("email"), self.utc_now()),
          )
      self.create_persistent_session(user_id)
    except sqlite3.IntegrityError:
      message = (
          "That username is taken, or this provider account is already linked."
          if pending_identity
          else "That username is already taken."
      )
      return {"success": False, "message": message}
    except (OSError, sqlite3.Error) as error:
      return {"success": False, "message": f"Could not create account: {error}"}
    self.current_user_id = user_id
    self.pending_social_identity = None
    if pending_identity:
      self.save_global_identity(user_id, pending_identity)
    self.load_account_stats()
    return {"success": True, "message": "Account created and signed in.", **self.get_account_state()}

  def start_social_auth(self, provider):
    provider = str(provider or "").strip().casefold()
    if provider not in ("google", "discord"):
      return {"success": False, "message": "Choose Google or Discord."}
    google_client_id = resolve_google_client_id()
    if provider == "google" and not google_client_id:
      return {"success": False, "message": "Set GRAILD_GOOGLE_CLIENT_ID to your Google Desktop OAuth client ID."}
    if provider == "discord":
      try:
        broker_url = self.get_discord_broker_url()
      except ValueError as error:
        return {
            "success": False,
            "message": f"{error} The Discord client secret must stay on the broker server.",
        }
    else:
      broker_url = None

    with self.social_flow_lock:
      if self.social_flow and self.social_flow["status"] == "pending":
        return {"success": False, "message": "A provider sign-in is already in progress."}
      state = secrets.token_urlsafe(32)
      verifier = secrets.token_urlsafe(64)
      flow = {
          "flow_id": uuid.uuid4().hex,
          "provider": provider,
          "broker_url": broker_url,
          "client_id": google_client_id,
          "state": state,
          "verifier": verifier,
          "callback": None,
          "status": "pending",
          "result": None,
          "started_at": time.time(),
          "callback_path": "/",
      }
      try:
        server = http.server.HTTPServer(
            ("127.0.0.1", 0), self.oauth_callback_handler(flow)
        )
      except OSError as error:
        return {"success": False, "message": f"Could not open the local OAuth callback: {error}"}
      server.timeout = 1
      flow["redirect_uri"] = f"http://127.0.0.1:{server.server_port}{flow['callback_path']}"
      self.social_flow = flow

    threading.Thread(
        target=self.run_social_auth_flow,
        args=(flow, server),
        daemon=True,
        name=f"graild-{provider}-oauth",
    ).start()
    return {
        "success": True,
        "flow_id": flow["flow_id"],
        "message": f"Continue in your browser to connect {provider.title()}.",
    }

  @staticmethod
  def get_discord_broker_url():
    configured_url = os.environ.get("GRAILD_DISCORD_BROKER_URL", "").strip()
    broker_url = (configured_url or DEFAULT_DISCORD_BROKER_URL).rstrip("/")
    parsed = urllib.parse.urlsplit(broker_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
      raise ValueError("GRAILD_DISCORD_BROKER_URL must be an HTTPS origin.")
    return broker_url

  @staticmethod
  def oauth_callback_handler(flow):
    class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
      def do_GET(handler):
        parsed = urllib.parse.urlsplit(handler.path)
        values = urllib.parse.parse_qs(parsed.query)
        callback = {key: items[0] for key, items in values.items() if items}
        if parsed.path != flow["callback_path"]:
          callback = {"error": "invalid_callback"}
        elif not hmac.compare_digest(callback.get("state", ""), flow["state"]):
          callback = {"error": "state_mismatch"}
        elif flow["provider"] == "discord" and callback.get("iss") != "https://discord.com":
          callback = {"error": "issuer_mismatch"}
        flow["callback"] = callback
        body = (
            b"<!doctype html><meta charset=utf-8><title>GRAIL'D</title>"
            b"<p>Account connection complete. Return to GRAIL'D.</p>"
        )
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

      def log_message(self, _format, *args):
        return

    return OAuthCallbackHandler

  @staticmethod
  def oauth_json_request(url, payload=None, headers=None):
    request_headers = {"Accept": "application/json"}
    request_headers.update(headers or {})
    body = None
    if payload is not None:
      body = urllib.parse.urlencode(payload).encode("utf-8")
      request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=request_headers)
    with urllib.request.urlopen(request, timeout=15, context=verified_ssl_context()) as response:
      return json.loads(response.read().decode("utf-8"))

  def build_social_authorization_url(self, flow):
    if flow["provider"] == "google":
      challenge = base64.urlsafe_b64encode(
          hashlib.sha256(flow["verifier"].encode("ascii")).digest()
      ).rstrip(b"=").decode("ascii")
      query = urllib.parse.urlencode({
          "client_id": flow["client_id"],
          "redirect_uri": flow["redirect_uri"],
          "response_type": "code",
          "scope": "openid email profile",
          "state": flow["state"],
          "code_challenge": challenge,
          "code_challenge_method": "S256",
          "prompt": "select_account",
      })
      return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    broker_url = flow.get("broker_url") or self.get_discord_broker_url()
    response = self.oauth_json_request(
        f"{broker_url}/oauth/start",
        {
            "provider": "discord",
            "state": flow["state"],
            "redirect_uri": flow["redirect_uri"],
        },
    )
    authorization_url = response.get("authorization_url", "")
    parsed = urllib.parse.urlsplit(authorization_url)
    if parsed.scheme != "https" or parsed.hostname != "discord.com":
      raise ValueError("Discord broker returned an invalid authorization URL.")
    return authorization_url

  def exchange_social_callback(self, flow, callback):
    if callback.get("error"):
      raise ValueError("Provider authorization was cancelled or failed validation.")
    if not callback.get("code") and flow["provider"] == "google":
      raise ValueError("Google did not return an authorization code.")

    if flow["provider"] == "google":
      token_response = self.oauth_json_request(
          "https://oauth2.googleapis.com/token",
          {
              "client_id": flow["client_id"],
              "code": callback["code"],
              "code_verifier": flow["verifier"],
              "grant_type": "authorization_code",
              "redirect_uri": flow["redirect_uri"],
          },
      )
      id_token = token_response.get("id_token")
      if not id_token:
        raise ValueError("Google token exchange did not return an ID token.")
      return self.oauth_json_request(
          f"{self.get_discord_broker_url()}/oauth/google/verify",
          {"id_token": id_token},
      )

    broker_url = flow.get("broker_url") or self.get_discord_broker_url()
    identity = self.oauth_json_request(
        f"{broker_url}/oauth/complete",
        {
            "provider": "discord",
            "state": flow["state"],
            "ticket": callback.get("ticket", ""),
            "redirect_uri": flow["redirect_uri"],
        },
    )
    return {
        "global_token": identity.get("global_token"),
        "provider": "discord",
        "subject": str(identity.get("subject") or ""),
        "email": identity.get("email"),
        "display_name": identity.get("display_name") or identity.get("username", ""),
    }

  def finish_social_identity(self, identity):
    provider = str(identity.get("provider") or "")
    subject = str(identity.get("subject") or "").strip()
    if provider not in ("google", "discord"):
      return {"success": False, "message": "The provider identity is invalid."}
    if not subject or len(subject) > 256:
      return {"success": False, "message": "The provider returned an invalid account identifier."}
    linked_user_id = None
    linking_to_current = bool(self.current_user_id)
    try:
      with self.account_connection() as connection:
        linked = connection.execute(
            "SELECT user_id FROM external_identities WHERE provider = ? AND subject = ?",
            (provider, subject),
        ).fetchone()
        if self.current_user_id:
          if linked and linked["user_id"] != self.current_user_id:
            return {"success": False, "message": "That provider account is connected to another GRAIL'D account."}
          if not linked:
            connection.execute(
                "INSERT INTO external_identities (provider, subject, user_id, email, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (provider, subject, self.current_user_id, identity.get("email"), self.utc_now()),
            )
        elif linked:
          linked_user_id = linked["user_id"]
    except sqlite3.IntegrityError:
      return {"success": False, "message": "That provider account is already linked."}
    except (OSError, sqlite3.Error) as error:
      return {"success": False, "message": f"Could not link provider account: {error}"}

    if linking_to_current or linked_user_id:
      self.save_global_identity(self.current_user_id or linked_user_id, identity)
    if linking_to_current:
      return {
          "success": True,
          "message": f"{provider.title()} connected to your account.",
          **self.get_account_state(),
      }
    if linked_user_id:
      try:
        self.create_persistent_session(linked_user_id)
      except (OSError, sqlite3.Error) as error:
        return {"success": False, "message": f"Could not save your sign-in: {error}"}
      self.current_user_id = linked_user_id
      self.pending_social_identity = None
      self.load_account_stats()
      return {
          "success": True,
          "message": f"Signed in with {provider.title()}.",
          **self.get_account_state(),
      }

    self.pending_social_identity = {
        **identity,
        "expires_at": time.time() + 900,
    }
    return {
        "success": True,
        "registration_required": True,
        "provider": provider,
        "email": identity.get("email") or "",
        "display_name": identity.get("display_name") or "",
        "message": f"{provider.title()} verified. Choose your GRAIL'D username and password to finish.",
    }

  def run_social_auth_flow(self, flow, server):
    try:
      authorization_url = self.build_social_authorization_url(flow)
      if not webbrowser.open(authorization_url):
        raise OSError("Could not open the system browser.")
      deadline = time.monotonic() + 180
      while flow["callback"] is None and time.monotonic() < deadline:
        server.handle_request()
      if flow["callback"] is None:
        raise TimeoutError("Provider sign-in timed out. Start again to retry.")
      identity = self.exchange_social_callback(flow, flow["callback"])
      result = self.finish_social_identity(identity)
    except Exception as error:
      result = {"success": False, "message": str(error)}
    finally:
      server.server_close()
    with self.social_flow_lock:
      flow["result"] = result
      flow["status"] = "complete"

  def get_social_auth_status(self):
    with self.social_flow_lock:
      flow = self.social_flow
      if not flow:
        return {"status": "idle"}
      return {
          "status": flow["status"],
          **(flow["result"] or {"message": "Waiting for provider authorization..."}),
      }

  def link_pending_social_identity(self):
    identity = self.pending_social_identity
    if not identity or identity["expires_at"] < time.time():
      self.pending_social_identity = None
      return None
    return identity

  def sign_in(self, username, password):
    if self.current_user_id:
      return {"success": False, "message": "Sign out before switching accounts."}
    username_key = str(username or "").strip().casefold()
    password = str(password or "")
    try:
      with self.account_connection() as connection:
        account = connection.execute(
            "SELECT user_id, password_salt, password_hash, password_iterations "
            "FROM accounts WHERE username_key = ?",
            (username_key,),
        ).fetchone()
      if not account or len(password) > 128:
        return {"success": False, "message": "Incorrect username or password."}
      candidate = hashlib.pbkdf2_hmac(
          "sha256", password.encode("utf-8"), account["password_salt"],
          account["password_iterations"],
      )
      if not hmac.compare_digest(candidate, account["password_hash"]):
        return {"success": False, "message": "Incorrect username or password."}
      self.create_persistent_session(account["user_id"])
    except (OSError, sqlite3.Error) as error:
      return {"success": False, "message": f"Could not sign in securely: {error}"}
    self.current_user_id = account["user_id"]
    self.pending_social_identity = None
    self.load_account_stats()
    return {"success": True, "message": "Signed in successfully.", **self.get_account_state()}

  def sign_out(self):
    if not self.current_user_id:
      cleared = self.clear_saved_session()
      return {
          "success": cleared,
          "message": "You are signed out." if cleared else "Could not clear the saved sign-in session.",
          "authenticated": False,
      }
    cleared = self.clear_saved_session()
    self.stop_audio()
    self.current_user_id = None
    self.pending_social_identity = None
    self.recording_era = ""
    self.library_source = "local"
    self.random_snippet = None
    self.snippet_buffer = None
    self.correct_guesses = self.wrong_guesses = self.score = self.streak = 0
    self.current_track = None
    self.attempt = 0
    self.is_game_over = False
    return {
      "success": cleared,
      "message": "Signed out." if cleared else "Signed out for now, but could not clear the saved sign-in session.",
      "authenticated": False,
    }

  def update_profile(self, display_name, avatar):
    if not self.current_user_id:
      return {"success": False, "message": "Sign in to edit your profile."}
    display_name = str(display_name or "").strip()
    avatar = str(avatar or "")
    if not 1 <= len(display_name) <= 32:
      return {"success": False, "message": "Display name must be 1–32 characters."}
    if avatar not in AVATARS:
      return {"success": False, "message": "Choose one of the available avatars."}
    with self.account_connection() as connection:
      connection.execute(
          "UPDATE accounts SET display_name = ?, avatar = ? WHERE user_id = ?",
          (display_name, avatar, self.current_user_id),
      )
    return {"success": True, "message": "Profile updated.", **self.get_account_state()}

  def import_legacy_song_stats(self, connection, user_id):
    imported = connection.execute(
        "SELECT value FROM app_meta WHERE key = 'legacy_stats_imported'"
    ).fetchone()
    if imported:
      return
    total_correct = 0
    total_wrong = 0
    songs_played = 0
    for track_key, record in self.song_stats.items():
      correct = record["correct"]
      wrong = record["wrong"]
      title = record["title"]
      connection.execute(
          "INSERT OR IGNORE INTO song_stats (user_id, track_key, title, correct, wrong) "
          "VALUES (?, ?, ?, ?, ?)",
          (user_id, track_key, title, correct, wrong),
      )
      total_correct += correct
      total_wrong += wrong
      songs_played += int(correct + wrong > 0)
      if correct:
        connection.execute(
            "INSERT OR IGNORE INTO grails (user_id, track_key, title, collected_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, track_key, title, self.utc_now()),
        )
    connection.execute(
        "UPDATE accounts SET total_correct = ?, total_wrong = ?, songs_played = ? "
        "WHERE user_id = ?",
        (total_correct, total_wrong, songs_played, user_id),
    )
    connection.execute(
        "INSERT INTO app_meta (key, value) VALUES ('legacy_stats_imported', '1')"
    )

  @staticmethod
  def get_legacy_config_files():
    app_directory = (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__))
    )
    return list(dict.fromkeys((
        os.path.join(app_directory, "juicespot_config.json"),
        os.path.join(os.getcwd(), "juicespot_config.json"),
    )))

  def load_config_folder(self):
    config_files = [self.config_file, *self.legacy_config_files]
    for config_path in config_files:
      try:
        with open(config_path, "r", encoding="utf-8") as config_file:
          path = json.load(config_file).get("music_folder")
        if isinstance(path, str) and path.strip():
          path = os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip())))
          if os.path.isdir(path):
            if config_path != self.config_file:
              self.music_folder = path
              self.save_config_folder()
            return path
      except (OSError, ValueError, TypeError):
        continue
    return os.path.join(os.path.expanduser("~"), "Music")

  def save_config_folder(self):
    try:
      with open(self.config_file, "w", encoding="utf-8") as config_file:
        json.dump({"music_folder": self.music_folder}, config_file)
      return None
    except OSError as error:
      return str(error)

  def load_song_stats(self):
    try:
      with open(self.song_stats_file, "r", encoding="utf-8") as stats_file:
        saved_stats = json.load(stats_file)
    except (OSError, ValueError):
      return {}
    if not isinstance(saved_stats, dict):
      return {}

    stats = {}
    for key, record in saved_stats.items():
      if not isinstance(key, str) or not isinstance(record, dict):
        continue
      try:
        correct = max(0, int(record.get("correct", 0)))
        wrong = max(0, int(record.get("wrong", 0)))
      except (TypeError, ValueError):
        continue
      title = record.get("title")
      if isinstance(title, str) and title.strip():
        stats[key] = {"title": title, "correct": correct, "wrong": wrong}
    return stats

  def save_song_stats(self):
    temp_path = f"{self.song_stats_file}.tmp"
    try:
      with open(temp_path, "w", encoding="utf-8") as stats_file:
        json.dump(self.song_stats, stats_file, ensure_ascii=False, indent=2)
      os.replace(temp_path, self.song_stats_file)
      return None
    except OSError as error:
      try:
        os.remove(temp_path)
      except OSError:
        pass
      return str(error)

  def record_song_guess(self, correct, points_awarded=0):
    if not self.current_user_id:
      return "Sign in to save game statistics."
    filename = self.current_track
    key = filename.casefold()
    title = os.path.splitext(filename)[0]
    count_type = "correct" if correct else "wrong"
    try:
      with self.account_connection() as connection:
        connection.execute(
            "INSERT INTO song_stats (user_id, track_key, title, correct, wrong) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, track_key) DO UPDATE SET "
            f"title = excluded.title, {count_type} = song_stats.{count_type} + 1",
            (self.current_user_id, key, title, int(correct), int(not correct)),
        )
        if correct:
          connection.execute(
              "UPDATE accounts SET total_correct = total_correct + ?, "
              "total_points = total_points + ?, current_streak = ?, "
              "best_streak = MAX(best_streak, ?), songs_played = songs_played + 1 "
              "WHERE user_id = ?",
              (1, points_awarded, self.streak, self.streak, self.current_user_id),
          )
          connection.execute(
              "INSERT OR IGNORE INTO grails (user_id, track_key, title, collected_at) "
              "VALUES (?, ?, ?, ?)",
              (self.current_user_id, key, title, self.utc_now()),
          )
          for metric, qualifies in (("first_try", self.attempt == 0),
                                    ("hard", self.current_difficulty == "Hard")):
            if qualifies:
              connection.execute(
                  "INSERT INTO achievement_counters (user_id, metric, value) VALUES (?, ?, 1) "
                  "ON CONFLICT(user_id, metric) DO UPDATE SET value = value + 1",
                  (self.current_user_id, metric),
              )
        else:
          connection.execute(
              "UPDATE accounts SET total_wrong = total_wrong + 1 WHERE user_id = ?",
              (self.current_user_id,),
          )
      return None
    except sqlite3.Error as error:
      return str(error)

  def save_global_identity(self, user_id, identity):
    token = identity.get("global_token")
    if not token:
      return
    # Credentials stay encrypted at rest and never cross the webview bridge.
    encrypted = self.protect_session_data(token.encode("utf-8"))
    record = json.dumps({"token": base64.b64encode(encrypted).decode("ascii"),
                         "broker": self.get_discord_broker_url()})
    with self.account_connection() as connection:
      connection.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                         ("global_identity:" + user_id, record))

  def get_global_leaderboard(self):
    message = "Connect Google or Discord in Account to publish your score."
    synced = False
    try:
      broker = self.get_discord_broker_url()
      with self.account_connection() as connection:
        account = connection.execute("SELECT * FROM accounts WHERE user_id = ?",
                                     (self.current_user_id,)).fetchone()
        credential = connection.execute("SELECT value FROM app_meta WHERE key = ?",
                                        ("global_identity:" + str(self.current_user_id),)).fetchone()
      if account and credential:
        try:
          saved = json.loads(credential["value"])
          if not isinstance(saved, dict) or saved.get("broker") != broker:
            raise ValueError("Saved leaderboard credentials do not match the broker.")
          token = self.protect_session_data(
              base64.b64decode(saved["token"], validate=True), decrypt=True
          ).decode("utf-8")
          self.oauth_json_request(broker + "/leaderboard/update", {
              "display_name": account["display_name"], "points": account["total_points"],
              "correct": account["total_correct"], "wrong": account["total_wrong"],
          }, headers={"Authorization": "Bearer " + token})
          synced = True
          message = "Your score is synced. Top 50 players by lifetime points."
        except urllib.error.HTTPError as error:
          if error.code == 401:
            message = "Reconnect Google or Discord in Account to renew score syncing."
          elif error.code == 404:
            message = "The server needs the global leaderboard update."
          else:
            message = "Score sync unavailable. Your progress is saved locally; refresh to retry."
          error.close()
        except (ValueError, KeyError, TypeError):
          message = "Reconnect Google or Discord in Account to renew score syncing."
        except OSError:
          message = "Score sync unavailable. Your progress is saved locally; refresh to retry."
      board = self.oauth_json_request(broker + "/leaderboard")
      if not isinstance(board, dict) or not isinstance(board.get("entries"), list):
        raise ValueError("Invalid leaderboard response.")
      return {"success": True, "entries": board["entries"], "synced": synced,
              "message": message}
    except urllib.error.HTTPError as error:
      missing = error.code == 404
      error.close()
      return {"success": False, "entries": [], "synced": synced,
              "message": ("The server needs the global leaderboard update. Please deploy the updated broker."
                          if missing else "Global leaderboard unavailable. Refresh to retry.")}
    except (OSError, ValueError, sqlite3.Error):
      return {"success": False, "entries": [], "synced": synced,
              "message": "Global leaderboard unavailable. Your progress is saved locally; refresh to retry."}

  def get_song_leaderboard(self):
    saved_stats = {}
    if self.current_user_id:
      with self.account_connection() as connection:
        saved_stats = {
            row["track_key"]: dict(row)
            for row in connection.execute(
                "SELECT track_key, title, correct, wrong FROM song_stats WHERE user_id = ?",
                (self.current_user_id,),
            )
        }
    return sorted(
        [
            {
          "title": os.path.splitext(filename)[0],
          "correct": saved_stats.get(filename.casefold(), {}).get("correct", 0),
          "wrong": saved_stats.get(filename.casefold(), {}).get("wrong", 0),
            }
        for filename in self.tracks
        ],
      key=lambda record: record["title"].casefold(),
    )

  def load_tracks(self):
    self.library_error = None
    if not os.path.isdir(self.music_folder):
      self.tracks = []
      self.current_track = None
      self.library_error = f"Music folder not found: {self.music_folder}"
      return
    try:
      with os.scandir(self.music_folder) as entries:
        self.tracks = sorted(
            entry.name for entry in entries
            if is_audio_file(entry.path, entry=entry)
        )
    except OSError as error:
      self.tracks = []
      self.current_track = None
      self.library_error = f"Could not read music folder: {error}"
      return
    if not self.tracks:
      self.library_error = "No supported audio files were found in this folder."
    self._canary.start_era_scan(self.music_folder, self.tracks)
    if not self._restore_round():
      self.next_round()

  def state(self):
    return {
        "folder": self.music_folder,
      "authenticated": bool(self.current_user_id),
        "track_count": len(self.tracks),
        "eligible_track_count": len(self.get_eligible_tracks()),
        "recording_era": getattr(self, "recording_era", ""),
        "library_source": getattr(self, "library_source", "local"),
        "recording_eras": self.get_recording_eras(),
        "error": None if getattr(self, "library_source", "local") == "canary" else self.library_error or ("No Canary-checked local songs match this era yet. Check era progress in Catalog or choose All eras." if getattr(self, "recording_era", "") and not self.get_eligible_tracks() else None),
        "volume": round(self.volume * 100),
        "has_track": self.current_track is not None,
        "difficulty": self.current_difficulty,
        "game_mode": self.game_mode,
        "durations": self.preview_durations,
        "attempt": self.attempt,
        "score": self.score,
        "streak": self.streak,
        "correct_guesses": self.correct_guesses,
        "wrong_guesses": self.wrong_guesses,
        "game_over": self.is_game_over,
        "resume_error": getattr(self, "round_save_error", None),
        "title": os.path.splitext(self.current_track)[0]
        if self.current_track and self.is_game_over
        else "",
    }

  def get_state(self):
    return self.state()

  def set_volume(self, volume):
    try:
      self.volume = max(0.0, min(1.0, float(volume)))
    except (TypeError, ValueError):
      return {"error": "Volume must be a number between 0 and 1."}
    if pygame.mixer.get_init():
      try:
        pygame.mixer.music.set_volume(self.volume)
      except pygame.error as error:
        return {"error": f"Could not change volume: {error}"}
    return {"volume": round(self.volume * 100)}

  def get_catalog(self):
    matches = self._canary.library_matches(self.music_folder, self.tracks)
    era_checks = self._canary.library_era_checks(self.music_folder, self.tracks)
    return [
        {"filename": filename, "title": os.path.splitext(filename)[0],
         "format": os.path.splitext(filename)[1][1:].upper(),
         "canary_era": era_checks.get(filename, {}).get("era", ""),
         "era_status": era_checks.get(filename, {}).get("status", "pending"),
         "canary": {key: matches[filename].get(key) for key in ("id", "title", "artist", "era", "category")} if filename in matches else None}
        for filename in self.tracks
    ]

  def load_game_preferences(self):
    self.recording_era = ""
    self.library_source = "local"
    if not self.current_user_id:
      return
    with self.account_connection() as connection:
      preference = connection.execute(
          "SELECT recording_era, library_source FROM game_preferences WHERE user_id = ?", (self.current_user_id,)
      ).fetchone()
    if preference:
      self.recording_era = preference["recording_era"]
      self.library_source = preference["library_source"] if preference["library_source"] in ("local", "canary") else "local"

  def get_recording_eras(self):
    if not getattr(self, "_canary", None):
      return []
    checks = self._canary.library_era_checks(self.music_folder, self.tracks)
    return sorted({check["era"] for check in checks.values() if check.get("era")} | set(self._canary.client.cached_recording_eras()))

  def get_eligible_tracks(self):
    if getattr(self, "library_source", "local") == "canary":
      return []
    era = getattr(self, "recording_era", "")
    if not era:
      return list(self.tracks)
    if not getattr(self, "_canary", None):
      return []
    checks = self._canary.library_era_checks(self.music_folder, self.tracks)
    return [filename for filename in self.tracks if checks.get(filename, {}).get("era") == era]

  def start_canary_era_scan(self):
    return self._canary.start_era_scan(self.music_folder, self.tracks, force=True)

  def get_canary_era_scan(self):
    status = self._canary.era_scan_status(self.music_folder)
    if status["status"] == "complete" and self.current_user_id and getattr(self, "library_source", "local") == "local":
      eligible = self.get_eligible_tracks()
      if (self.current_track is None and eligible) or (self.current_track and getattr(self, "recording_era", "") and self.current_track not in eligible):
        self.next_round()
    return status

  def set_recording_era(self, era):
    if not self.current_user_id:
      return {"success": False, "message": "Sign in to save your era preference.", "state": self.state()}
    era = str(era or "").strip()
    if era and era not in self.get_recording_eras():
      return {"success": False, "message": "Refresh the Canary era list or confirm a song in that era first. Eras are never read from filenames.", "state": self.state()}
    try:
      with self.account_connection() as connection:
        connection.execute(
            "INSERT INTO game_preferences (user_id, recording_era) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET recording_era = excluded.recording_era",
            (self.current_user_id, era),
        )
    except sqlite3.Error:
      return {"success": False, "message": "Could not save your era preference. Please retry.", "state": self.state()}
    self.recording_era = era
    return {"success": True, "message": "Your era preference is saved. A new round has started.", "state": self.next_round()}

  def set_library_source(self, source):
    if not self.current_user_id or source not in ("local", "canary"):
      return {"success": False, "message": "Sign in and choose a valid playback option.", "state": self.state()}
    try:
      with self.account_connection() as connection:
        connection.execute(
            "INSERT INTO game_preferences (user_id, recording_era, library_source) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET library_source = excluded.library_source",
            (self.current_user_id, getattr(self, "recording_era", ""), source),
        )
    except sqlite3.Error:
      return {"success": False, "message": "Could not save your playback option.", "state": self.state()}
    self.library_source = source
    return {"success": True, "state": self.next_round(), "message": "Playback option saved."}

  def start_canary_request(self, operation, options=None):
    options = options if isinstance(options, dict) else {}
    if operation in ("local", "confirm"):
      filename = options.get("filename")
      if filename not in self.tracks:
        return {"status": "failed", "message": "This local song is no longer in your library."}
      arguments = {"filename": os.path.join(self.music_folder, filename)}
      if operation == "local" and "query" in options:
        arguments["query"] = str(options["query"])[:200]
      if operation == "confirm":
        arguments["song_id"] = str(options.get("song_id", ""))[:256]
    elif operation == "search":
      try:
        arguments = {"query": str(options.get("query", ""))[:200],
                     "era": str(options.get("era", ""))[:60],
                     "category": str(options.get("category", ""))[:60],
                     "page": int(options.get("page", 0)), "limit": 20}
      except (ValueError, TypeError):
        return {"status": "failed", "message": "Invalid catalogue page."}
    elif operation == "info":
      arguments = {"song_id": str(options.get("song_id", ""))[:256]}
    elif operation == "random":
      arguments = {"era": str(options.get("era", ""))[:60]}
    elif operation in ("catalog", "eras"):
      arguments = {}
    else:
      return {"status": "failed", "message": "Unknown Canary metadata request."}
    return self._canary.start(operation, **arguments)

  def get_canary_request(self, job_id):
    return self._canary.poll(job_id)

  def forget_canary_match(self, filename):
    if filename not in self.tracks:
      return {"success": False, "message": "This local file is no longer available."}
    self._canary.forget(os.path.join(self.music_folder, filename))
    if getattr(self, "recording_era", ""):
      self.next_round()
    return {"success": True}

  def open_canary_page(self, url):
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != "https" or parsed.netloc != "canary.best" or not re.fullmatch(r"/song/[A-Za-z0-9_%.-]+", parsed.path):
      return {"success": False}
    return {"success": bool(webbrowser.open(str(url)))}

  def select_music_folder(self):
    if not self._window:
      return {"state": self.state(), "message": "The app window is not ready."}
    try:
      selected = self._window.create_file_dialog(
          webview.FileDialog.FOLDER,
          directory=self.music_folder if os.path.isdir(self.music_folder) else "",
      )
    except Exception as error:
      return {"state": self.state(), "message": f"Could not open folder picker: {error}"}
    if not selected:
      return {"state": self.state(), "message": ""}
    selected_path = os.path.abspath(os.path.expanduser(os.fspath(selected[0])))
    if not os.path.isdir(selected_path):
      return {
          "state": self.state(),
          "message": "The selected folder is no longer available. Please choose another folder.",
      }
    self.music_folder = selected_path
    save_error = self.save_config_folder()
    self.load_tracks()
    message = self.library_error or f"Loaded {len(self.tracks)} audio files."
    if save_error:
      message += f" Folder setting could not be saved: {save_error}"
    return {"state": self.state(), "message": message}

  def set_game_mode(self, mode):
    if not self.current_user_id or mode not in ("classic", "random"):
      return self.state()
    if mode == self.game_mode:
      return self.state()
    previous_track = self.current_track
    self.game_mode = mode
    return self.next_round(exclude_track=previous_track)

  def get_challenge_bests(self):
    if not self.current_user_id:
      return {}
    try:
      with self.account_connection() as connection:
        rows = connection.execute(
            "SELECT mode, best_score, best_songs, runs FROM challenge_bests WHERE user_id = ?",
            (self.current_user_id,),
        ).fetchall()
    except sqlite3.Error:
      return {}
    return {row["mode"]: {"best_score": row["best_score"], "best_songs": row["best_songs"], "runs": row["runs"]}
            for row in rows if row["mode"] in CHALLENGE_MODES}

  def save_challenge_result(self, mode, score, songs):
    if not self.current_user_id:
      return {"success": False, "message": "Sign in to save challenge results."}
    if mode not in CHALLENGE_MODES:
      return {"success": False, "message": "Unknown challenge."}
    try:
      score, songs = int(score), int(songs)
    except (TypeError, ValueError):
      return {"success": False, "message": "Challenge results must be whole numbers."}
    if not (0 <= score <= 1_000_000 and 0 <= songs <= 10_000):
      return {"success": False, "message": "Challenge result is out of range."}
    previous = self.get_challenge_bests().get(mode)
    try:
      with self.account_connection() as connection:
        connection.execute(
            "INSERT INTO challenge_bests (user_id, mode, best_score, best_songs, runs, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(user_id, mode) DO UPDATE SET "
            "best_score = MAX(best_score, excluded.best_score), best_songs = MAX(best_songs, excluded.best_songs), "
            "runs = runs + 1, updated_at = excluded.updated_at",
            (self.current_user_id, mode, score, songs, self.utc_now()),
        )
    except sqlite3.Error:
      return {"success": False, "message": "Could not save this challenge result."}
    old_best = previous["best_score"] if previous else 0
    return {"success": True, "new_best": score > old_best, "bests": self.get_challenge_bests()}

  def set_difficulty(self, difficulty):
    if not self.current_user_id or difficulty not in DIFFICULTIES:
      return self.state()
    self.current_difficulty = difficulty
    self.preview_durations = DIFFICULTIES[difficulty]
    self.next_round()
    return self.state()

  def next_round(self, exclude_track=None):
    self.stop_audio()
    self.random_snippet = None
    self.snippet_buffer = None
    self._random_start_frame = None
    if not self.current_user_id:
      self.current_track = None
      self.attempt = 0
      self.is_game_over = False
      return self.state()
    self.random_snippet = None
    self.attempt = 0
    self.is_game_over = False
    eligible = self.get_eligible_tracks()
    if exclude_track is not None:
      eligible = [track for track in eligible if track != exclude_track] or eligible
    self.current_track = random.choice(eligible) if eligible else None
    self._save_round()
    return self.state()

  def reroll_song(self):
    if not self.current_user_id:
      return {"state": self.state(), "message": "Sign in to play.", "changed": False}
    if not self.current_track or self.is_game_over:
      return {"state": self.state(), "message": "Start a round before rerolling.", "changed": False}
    alternatives = [track for track in self.get_eligible_tracks() if track != self.current_track]
    if not alternatives:
      return {"state": self.state(), "message": "There are no other songs to reroll to.", "changed": False}
    self.stop_audio()
    self.current_track = random.choice(alternatives)
    self.random_snippet = None
    self.snippet_buffer = None
    self._random_start_frame = None
    self.attempt = 0
    self._save_round()
    return {
      "state": self.state(),
      "message": "Song changed. Your score and stats are unchanged.",
      "changed": True,
    }

  def play_snippet(self):
    if not self.current_user_id:
      return {"ok": False, "message": "Sign in to play and save your statistics."}
    if not self.current_track or self.is_game_over:
      return {"ok": False, "message": "Choose a folder containing audio files first."}
    if self.audio_error:
      return {"ok": False, "message": f"Audio is unavailable: {self.audio_error}"}
    self.stop_audio()
    track_path = os.path.join(self.music_folder, self.current_track)
    try:
      track_path = get_playback_path(track_path)
      duration = self.preview_durations[self.attempt]
      if self.game_mode == "random":
        if self.random_snippet is None:
          start = getattr(self, "_random_start_frame", None)
          self.random_snippet = (RandomSnippet(track_path, max(self.preview_durations), start_frame=start)
                                 if start is not None else RandomSnippet(track_path, max(self.preview_durations)))
          self._random_start_frame = self.random_snippet.start_frame
          self._save_round()
        self.snippet_buffer, duration = self.random_snippet.clip(duration)
        pygame.mixer.music.load(self.snippet_buffer, "wav")
      else:
        pygame.mixer.music.load(track_path)
      pygame.mixer.music.play()
      self.stop_timer = threading.Timer(duration, pygame.mixer.music.stop)
      self.stop_timer.daemon = True
      self.stop_timer.start()
      return {"ok": True, "duration": round(duration, 2)}
    except (OSError, ValueError, pygame.error) as error:
      failed_track = self.current_track
      self.tracks = [track for track in self.tracks if track != failed_track]
      state = self.next_round()
      if self.current_track:
        message = f"Couldn't play '{failed_track}': {error}. Skipped it and selected another song."
      else:
        message = f"Couldn't play '{failed_track}': {error}. No other songs are available."
      return {"ok": False, "message": message, "state": state}

  def stop_audio(self):
    if self.stop_timer:
      self.stop_timer.cancel()
      self.stop_timer = None
    if pygame.mixer.get_init():
      pygame.mixer.music.stop()

  def play_correct_answer(self):
    if self.audio_error:
      return self.audio_error
    self.stop_audio()
    try:
      track_path = os.path.join(self.music_folder, self.current_track)
      pygame.mixer.music.load(get_playback_path(track_path))
      pygame.mixer.music.play()
      self.stop_timer = threading.Timer(10, pygame.mixer.music.stop)
      self.stop_timer.daemon = True
      self.stop_timer.start()
      return None
    except (OSError, ValueError, pygame.error) as error:
      return str(error)

  def submit_guess(self, guess):
    if not self.current_user_id:
      return {"state": self.state(), "message": "Sign in to play and save your statistics."}
    if not self.current_track or self.is_game_over:
      return {"state": self.state(), "message": "Choose a music folder to start."}
    actual_title = os.path.splitext(self.current_track)[0]
    if normalize_song_title(guess) == normalize_song_title(actual_title):
      points_awarded = (len(self.preview_durations) - self.attempt) * 10
      self.correct_guesses += 1
      self.score += points_awarded
      self.streak += 1
      stats_error = self.record_song_guess(correct=True, points_awarded=points_awarded)
      self.is_game_over = True
      self._save_round()
      audio_error = self.play_correct_answer()
      return {
          "state": self.state(),
          "message": f"Correct! It was '{actual_title}'."
          if not stats_error
          else f"Correct! It was '{actual_title}', but the song stats could not be saved: {stats_error}",
          "success": True,
          "cover": self.get_cover_data(),
          "audio_error": audio_error,
      }
    self.wrong_guesses += 1
    stats_error = self.record_song_guess(correct=False)
    result = self.skip_attempt()
    if result["state"]["game_over"]:
      result["message"] = f"Incorrect guess. The track was '{actual_title}'."
    else:
      result["message"] = "Incorrect guess. Revealing a longer snippet..."
    if stats_error:
      result["message"] += f" Song stats could not be saved: {stats_error}"
    return result

  def skip_attempt(self):
    if not self.current_user_id:
      return {"state": self.state(), "message": "Sign in to play and save your statistics."}
    if not self.current_track or self.is_game_over:
      return {"state": self.state(), "message": "Choose a music folder to start."}
    self.attempt += 1
    if self.attempt >= len(self.preview_durations):
      self.streak = 0
      self.is_game_over = True
      try:
        with self.account_connection() as connection:
          connection.execute(
              "UPDATE accounts SET songs_played = songs_played + 1, current_streak = 0 "
              "WHERE user_id = ?",
              (self.current_user_id,),
          )
      except sqlite3.Error:
        pass
      self._save_round()
      return {
          "state": self.state(),
          "message": f"Round over. The track was '{os.path.splitext(self.current_track)[0]}'.",
          "success": False,
          "cover": self.get_cover_data(),
      }
    self._save_round()
    return {"state": self.state(), "message": ""}

  def get_cover_data(self):
    if not self.current_track:
      return ""
    if getattr(self, "_canary", None):
      matched = self._canary.local_match(os.path.join(self.music_folder, self.current_track))
      if matched and matched.get("artwork"):
        return matched["artwork"]
    base_name = os.path.splitext(self.current_track)[0]
    image_path = None
    try:
      for filename in os.listdir(self.music_folder):
        if (
            os.path.splitext(filename)[0].strip().casefold() == base_name.strip().casefold()
            and filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ):
          image_path = os.path.join(self.music_folder, filename)
          break
    except OSError:
      pass

    if image_path:
      try:
        with open(image_path, "rb") as image_file:
          return self.image_data_url(image_file.read(), image_path)
      except OSError:
        pass

    urls_file = os.path.join(self.music_folder, "imgur_covers.json")
    if requests:
      try:
        with open(urls_file, "r", encoding="utf-8") as cover_file:
          url_map = json.load(cover_file)
        image_url = url_map.get(self.current_track) or url_map.get(base_name)
        if image_url:
          response = requests.get(image_url, timeout=5)
          response.raise_for_status()
          return self.image_data_url(response.content, response.headers.get("Content-Type", ""))
      except (OSError, ValueError, requests.RequestException):
        pass

    if MP3 and APIC and self.current_track.lower().endswith(".mp3"):
      try:
        audio_file = MP3(os.path.join(self.music_folder, self.current_track))
        for tag in audio_file.tags.values():
          if isinstance(tag, APIC):
            return self.image_data_url(tag.data, tag.mime)
      except (OSError, AttributeError, TypeError):
        pass
    return ""

  @staticmethod
  def image_data_url(image_bytes, content_type):
    mime_type = content_type if content_type.startswith("image/") else ""
    mime_type = mime_type or mimetypes.guess_type(str(content_type))[0] or "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"

  def get_update_state(self):
    state = self._updates.snapshot()
    state["automatic_checks_supported"] = sys.platform != "darwin"
    return state

  def save_update_settings(self, repository, channel, automatic=True):
    try:
      if sys.platform == "darwin":
        automatic = False
      return {"success": True, "automatic_checks_supported": sys.platform != "darwin",
              **self._updates.save_settings(repository, channel, automatic)}
    except UpdateError as error:
      return {"success": False, "message": str(error)}

  def check_for_updates(self):
    state = self._updates.check()
    state["automatic_checks_supported"] = sys.platform != "darwin"
    return state

  def download_update(self):
    try:
      return {"success": True, "automatic_checks_supported": sys.platform != "darwin",
              **self._updates.download()}
    except UpdateError as error:
      return {"success": False, "message": str(error)}

  def cancel_update_download(self):
    state = self._updates.cancel_download()
    state["automatic_checks_supported"] = sys.platform != "darwin"
    return state

  def install_update(self):
    try:
      self._updates.install(resource_path("GRAILDUpdater.exe"))
      self._window.destroy()
      return {"success": True}
    except (OSError, UpdateError) as error:
      self._updates._state("error", str(error))
      return {"success": False, "message": str(error)}

  def _on_window_loaded(self):
    from update_health import acknowledge_startup
    try:
      healthy_ui = self._window.evaluate_js("!!document.getElementById('tab-hub') && typeof renderState === 'function' && typeof canaryRequest === 'function' && typeof renderUpdateState === 'function'")
      if not healthy_ui:
        return
      acknowledge_startup()
      if sys.platform != 'darwin':
        self._updates.check(automatic=True)
    except Exception:
      # An updated build must not acknowledge startup if its native UI failed.
      return

  def run(self):
    if sys.platform == 'win32':
      try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GRAILD.Desktop')
      except (AttributeError, OSError):
        pass
    self._window = webview.create_window(
        "GRAIL'D - Song Guessing Game",
        resource_path("index.html"),
        js_api=self,
        min_size=(760, 650),
    )
    self._window.events.loaded += self._on_window_loaded
    self._window.events.closing += lambda: self._updates.cancel_download()
    webview.start(icon=resource_path('assets/graild.icns' if sys.platform == 'darwin' else 'assets/graild.ico'))


def main():
  JuiceSpotApp().run()
