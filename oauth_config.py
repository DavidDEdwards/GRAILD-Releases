"""Public desktop OAuth configuration, included by PyInstaller on all platforms.

The production ID comes from the existing Windows development configuration.
Only the public client ID belongs here; credentials and tokens must never be added.
"""
import os


DEFAULT_GOOGLE_CLIENT_ID = (
    "784665885847-mhnaeujlqde91c3p4qpbjls087fqc82v.apps.googleusercontent.com"
)


def google_client_id():
    """Resolve at login time so development overrides take effect immediately.

    Empty or whitespace-only overrides use the production configuration. A Python
    module avoids depending on the launch environment or bundled resource paths.
    """
    return os.environ.get("GRAILD_GOOGLE_CLIENT_ID", "").strip() or DEFAULT_GOOGLE_CLIENT_ID
