import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch

import certifi
import tls_support
from web_app import JuiceSpotApp


class CertificateBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_development_uses_certifi(self):
        with patch('tls_support.sys.frozen',False,create=True):
            self.assertEqual(Path(tls_support.ca_bundle_path()),Path(certifi.where()))

    def test_frozen_onefile_or_onedir_bundle_takes_precedence(self):
        bundle = self.root/'certifi/cacert.pem'; bundle.parent.mkdir(); bundle.write_bytes(b'bundled CA')
        with patch('tls_support.sys.frozen',True,create=True), patch('tls_support.sys._MEIPASS',str(self.root),create=True), patch('tls_support.certifi.where',return_value=str(self.root/'missing.pem')):
            self.assertEqual(Path(tls_support.ca_bundle_path()),bundle)

    def test_mac_resources_layout_is_resolved(self):
        contents=self.root/'GRAILD.app/Contents'
        bundle=contents/'Resources/certifi/cacert.pem'; bundle.parent.mkdir(parents=True); bundle.write_bytes(b'bundled CA')
        with patch('tls_support.sys.frozen',True,create=True), patch('tls_support.sys._MEIPASS',str(contents/'Frameworks'),create=True), patch('tls_support.certifi.where',return_value=str(self.root/'missing.pem')):
            self.assertEqual(Path(tls_support.ca_bundle_path()),bundle)

    def test_frozen_certifi_resource_fallback(self):
        with patch('tls_support.sys.frozen',True,create=True), patch('tls_support.sys._MEIPASS',str(self.root),create=True):
            self.assertEqual(Path(tls_support.ca_bundle_path()),Path(certifi.where()))

    def test_missing_bundle_fails_closed(self):
        with patch('tls_support.sys.frozen',True,create=True), patch('tls_support.sys._MEIPASS',str(self.root),create=True), patch('tls_support.certifi.where',return_value=str(self.root/'missing.pem')):
            with self.assertRaises(FileNotFoundError): tls_support.verified_ssl_context()

    def test_context_keeps_verification_and_adds_certifi_to_system_roots(self):
        context=tls_support.verified_ssl_context()
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(len(context.get_ca_certs()),0)
        default=Mock()
        with patch('tls_support.ssl.create_default_context',return_value=default):
            self.assertIs(tls_support.verified_ssl_context(),default)
        default.load_verify_locations.assert_called_once_with(cafile=certifi.where())

    def test_bad_pem_does_not_fall_back_to_unverified_tls(self):
        invalid=self.root/'invalid.pem'; invalid.write_text('not a CA certificate')
        with patch('tls_support.ca_bundle_path',return_value=str(invalid)):
            with self.assertRaises(ssl.SSLError): tls_support.verified_ssl_context()

    def test_oauth_client_passes_verified_context_without_changing_request(self):
        context=tls_support.verified_ssl_context()
        response=Mock(); response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"authorization_url":"https://discord.com/oauth2/authorize"}'
        with patch('web_app.verified_ssl_context',return_value=context), patch('web_app.urllib.request.urlopen',return_value=response) as urlopen:
            JuiceSpotApp.oauth_json_request('https://broker.example/oauth/start',{'provider':'discord','state':'state-token'},headers={'X-Test':'unchanged'})
        request=urlopen.call_args.args[0]
        self.assertEqual(request.get_method(),'POST')
        self.assertIn(b'state=state-token',request.data)
        self.assertEqual(request.get_header('X-test'),'unchanged')
        self.assertIs(urlopen.call_args.kwargs['context'],context)
        self.assertEqual(urlopen.call_args.kwargs['timeout'],15)


if __name__ == '__main__': unittest.main()
