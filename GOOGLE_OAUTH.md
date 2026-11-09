# Google desktop login configuration

GRAIL'D 0.9.1 bundles the public Google Desktop OAuth client ID in
`oauth_config.py`. The value was recovered from the existing Windows
development process's `GRAILD_GOOGLE_CLIENT_ID` configuration. No client secret
or user credentials are included.

`web_app.py` imports the resolver directly, so PyInstaller includes the module
in Windows onefile builds and native Intel and Apple Silicon Mac bundles.
The Mac source handoff allowlist includes the module and its regression tests;
the GitHub source handoff uses that same allowlist. No environment variable,
external configuration file, or resource directory is needed at runtime.

For development, set `GRAILD_GOOGLE_CLIENT_ID` to a Google **Desktop app** OAuth
client ID before starting the app. Nonempty values override the bundled ID;
unset, empty, and whitespace-only values use the bundled production ID.
Build-time overrides are not captured in the application.

Previously, Windows inherited the ID from its launching environment, while Mac
builds had neither a bundled default nor an equivalent runtime environment.
Setting a variable on a build runner does not transfer it into a packaged app.

Validation: run `python -m unittest test_oauth_config test_tls_support -v`.
The frozen-runtime cases simulate Windows, Intel Mac, and Apple Silicon Mac
without a client ID environment variable or resource files. Native testing is
still required: build each Mac architecture from the updated sources, launch
from Finder without the variable, complete Google sign-in in the browser, and
confirm the account session survives an app restart. Existing build kits and
binaries must be regenerated from these sources to acquire the fix.
