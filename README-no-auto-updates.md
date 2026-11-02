# GRAIL'D 0.9.2 macOS build source — automatic checks disabled

This source branch builds native Apple Silicon and Intel Mac apps. On macOS, GRAIL'D does not check for updates at startup. Users can still check manually in Settings and install a publisher-signed update. Windows update behavior is unchanged.

The macOS workflow runs on GitHub-hosted Mac runners for this branch. Builds are ad-hoc signed and not notarized; complete interactive Mac testing and Developer ID notarization before public distribution.