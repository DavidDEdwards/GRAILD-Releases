"""Publisher configuration compiled into both the app and its updater.

Add only public Ed25519 keys here. Keep private release-signing keys offline.
Never load trust keys from a downloaded manifest or user-editable settings.
"""
DEFAULT_REPOSITORY = "DavidDEdwards/GRAILD-Releases"
DEFAULT_CHANNEL = "beta"
TRUSTED_PUBLIC_KEYS = {'graild-beta-2026': 'K6XHKB3+iANUKw8KRTrC0L3eT3Z4Eed2OcciGpy0F0o='}
