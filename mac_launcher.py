"""Native macOS entry point; avoids the unused legacy Tkinter frontend."""
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--mac-update-helper':
        from mac_updater import main as update_main
        raise SystemExit(update_main(sys.argv[2:]))
    if len(sys.argv) == 3 and sys.argv[1] == '--tls-smoke-test':
        from tls_support import write_diagnostic_report
        raise SystemExit(write_diagnostic_report(sys.argv[2]))
    if len(sys.argv) == 4 and sys.argv[1] == '--post-mac-update':
        from update_health import set_handoff
        set_handoff(sys.argv[2], sys.argv[3])
    from web_app import main
    main()
