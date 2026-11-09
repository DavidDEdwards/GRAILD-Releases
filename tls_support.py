"""Explicit CA trust for urllib in source and frozen desktop builds."""
from pathlib import Path
import ssl
import sys

import certifi


def ca_bundle_path():
    candidates = []
    if getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None):
        root = Path(sys._MEIPASS)
        candidates.extend((root / 'certifi/cacert.pem', root.parent / 'Resources/certifi/cacert.pem'))
    candidates.append(Path(certifi.where()))
    for path in candidates:
        if path.is_file():
            return str(path)
    raise FileNotFoundError('The trusted CA certificate bundle is missing. Reinstall the complete GRAIL\'D app.')


def verified_ssl_context():
    # Keep system/administrator roots (including Windows trust), then add the
    # bundled Mozilla roots. Never alter global SSL defaults or disable checks.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=ca_bundle_path())
    return context


def write_diagnostic_report(destination):
    """Opt-in packaged TLS check; never opens a browser or touches accounts."""
    import json
    import urllib.error
    import urllib.request
    from web_app import JuiceSpotApp
    report = {'passed':False,'frozen':bool(getattr(sys,'frozen',False)),
              'default_verify_paths':ssl.get_default_verify_paths()._asdict()}
    try:
        report['ca_bundle'] = ca_bundle_path()
        context = verified_ssl_context()
        report.update(check_hostname=context.check_hostname, verify_mode=int(context.verify_mode),
                      trusted_roots=len(context.get_ca_certs()))
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        broker = JuiceSpotApp.get_discord_broker_url()
        # A context with no roots must reject this otherwise-valid server.
        empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            urllib.request.urlopen(broker+'/healthz', context=empty, timeout=15)
            raise AssertionError('An empty trust store unexpectedly accepted the broker.')
        except urllib.error.URLError as error:
            assert isinstance(error.reason, ssl.SSLCertVerificationError), str(error)
            report['empty_trust_store_rejected'] = True
        statuses = {}
        for endpoint in ('/healthz','/oauth/start','/oauth/complete'):
            try:
                payload = None if endpoint == '/healthz' else {'provider':'discord','state':'tls-check-invalid','redirect_uri':'http://127.0.0.1:49123/'}
                JuiceSpotApp.oauth_json_request(broker+endpoint,payload)
                statuses[endpoint] = 200
            except urllib.error.HTTPError as error:
                statuses[endpoint] = error.code
                assert error.code == 400, str(error)
                error.close()
        report['verified_https_statuses'] = statuses
        assert statuses['/healthz'] == 200
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
    Path(destination).write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1
