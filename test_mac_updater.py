"""Mac update signatures, archive safety, and native replacement/rollback."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import plistlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from app_version import APP_VERSION
from auto_update import UpdateManager
import mac_updater
from mac_update_security import archive_bundle_digest, bundle_digest, verify_bundle, macho_architectures, BUNDLE_ID
from update_security import UpdateError, atomic_json, canonical, verify_manifest


def app_zip(version='99.0.0', extras=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        entries = [('GRAILD.app/Contents/Info.plist', plistlib.dumps({
            'CFBundleIdentifier': BUNDLE_ID, 'CFBundleExecutable': 'GRAILD',
            'CFBundleShortVersionString': version}), stat.S_IFREG | 0o644),
            ('GRAILD.app/Contents/MacOS/GRAILD', b'#!/bin/sh\nexit 0\n', stat.S_IFREG | 0o755)]
        for name, data, mode in [*entries, *extras]:
            item = zipfile.ZipInfo(name)
            item.create_system = 3
            item.external_attr = mode << 16
            archive.writestr(item, data)
    return buffer.getvalue()


def signed_mac(path, arch='arm64'):
    private = Ed25519PrivateKey.generate()
    keys = {'test': base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()}
    now = datetime.now(timezone.utc)
    manifest = {'schema':1,'app':'GRAILD','repository':'example/graild','channel':'beta',
                'version':'99.0.0','tag':'v99.0.0','platform':'macos','architecture':arch,
                'asset':f'GRAILD-99.0.0-macOS-{arch}.zip','bundle_id':BUNDLE_ID,
                'bundle_sha256':archive_bundle_digest(path),'size':path.stat().st_size,
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'issued_at':now.isoformat(),'expires_at':(now+timedelta(days=30)).isoformat(),'notes':'Mac update'}
    envelope = {'manifest':manifest,'key_id':'test','signature':base64.b64encode(private.sign(canonical(manifest))).decode()}
    return envelope, keys


class MacArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = self.root / 'app.zip'
        self.archive.write_bytes(app_zip())

    def test_mac_manifest_is_bound_to_platform_architecture_and_bytes(self):
        envelope, keys = signed_mac(self.archive)
        verify_manifest(envelope, keys, 'example/graild', 'beta', APP_VERSION, platform='macos', architecture='arm64')
        with self.assertRaises(UpdateError):
            verify_manifest(envelope, keys, 'example/graild', 'beta', APP_VERSION, platform='macos', architecture='x86_64')
        with self.assertRaises(UpdateError):
            verify_manifest(envelope, keys, 'example/graild', 'beta', APP_VERSION)
        envelope['manifest']['bundle_sha256'] = '0' * 64
        with self.assertRaises(UpdateError):
            verify_manifest(envelope, keys, 'example/graild', 'beta', APP_VERSION, platform='macos', architecture='arm64')

    def test_architecture_checks_do_not_require_developer_tools(self):
        import struct
        binary=self.root/'GRAILD'
        binary.write_bytes(b'\xcf\xfa\xed\xfe'+struct.pack('<I',0x0100000c))
        self.assertEqual(macho_architectures(binary),{'arm64'})
        binary.write_bytes(b'\xcf\xfa\xed\xfe'+struct.pack('<I',0x01000007))
        self.assertEqual(macho_architectures(binary),{'x86_64'})
        binary.write_bytes(b'not a MachO executable')
        with self.assertRaises(UpdateError): macho_architectures(binary)

    def test_universal_macho_architectures_are_bounded(self):
        import struct
        binary=self.root/'GRAILD'
        binary.write_bytes(b'\xca\xfe\xba\xbe'+struct.pack('>I',2)+
                           struct.pack('>IIIII',0x01000007,0,0,0,0)+struct.pack('>IIIII',0x0100000c,0,0,0,0))
        self.assertEqual(macho_architectures(binary),{'arm64','x86_64'})
        binary.write_bytes(b'\xca\xfe\xba\xbe'+struct.pack('>I',100))
        with self.assertRaises(UpdateError): macho_architectures(binary)

    def test_traversal_external_links_special_files_and_writes_through_links_rejected(self):
        cases = [[('../escape', b'x', stat.S_IFREG | 0o644)],
                 [('GRAILD.app/../../escape', b'x', stat.S_IFREG | 0o644)],
                 [('GRAILD.app/Contents/link', b'/tmp/escape', stat.S_IFLNK | 0o777)],
                 [('GRAILD.app/Contents/link', b'../../../escape', stat.S_IFLNK | 0o777)],
                 [('GRAILD.app/Contents/socket', b'x', stat.S_IFIFO | 0o644)],
                 [('GRAILD.app/Contents/link', b'MacOS', stat.S_IFLNK | 0o777),
                  ('GRAILD.app/Contents/link/attack', b'x', stat.S_IFREG | 0o644)],
                 [('GRAILD.app/Contents//link', b'MacOS', stat.S_IFLNK | 0o777)],
                 [('GRAILD.app/Contents/deep/link', b'../../Contents/shallow/../../outside', stat.S_IFLNK | 0o777),
                  ('GRAILD.app/Contents/shallow', b'../Contents', stat.S_IFLNK | 0o777)],
                 [('GRAILD.app/Contents/link', b'MacOS', stat.S_IFLNK | 0o777),
                  ('GRAILD.app/Contents/LINK/attack', b'x', stat.S_IFREG | 0o644)]]
        for extras in cases:
            self.archive.write_bytes(app_zip(extras=extras))
            with self.subTest(extras=extras), self.assertRaises(UpdateError):
                archive_bundle_digest(self.archive)

    def test_duplicate_and_case_colliding_entries_rejected(self):
        self.archive.write_bytes(app_zip(extras=[('GRAILD.app/Contents/info.plist', b'x', stat.S_IFREG | 0o644)]))
        with self.assertRaises(UpdateError): archive_bundle_digest(self.archive)

    def test_mac_manager_selects_only_its_signed_architecture(self):
        envelope, keys = signed_mac(self.archive)
        session = Mock()
        with patch('auto_update.sys.platform','darwin'), patch('auto_update.platform.machine',return_value='arm64'):
            manager = UpdateManager(root=self.root/'updates',keys=keys,session=session)
        manager.config.update(repository='example/graild',channel='beta')
        url = 'https://github.com/example/graild/releases/download/v99.0.0/'
        release = {'tag_name':'v99.0.0','assets':[
            {'name':'graild-update.json','browser_download_url':url+'graild-update.json'},
            {'name':'graild-update-macos-arm64.json','browser_download_url':url+'graild-update-macos-arm64.json'},
            {'name':envelope['manifest']['asset'],'browser_download_url':url+envelope['manifest']['asset']}]}
        manager._json = Mock(side_effect=[[release],envelope])
        manager._check_worker()
        self.assertEqual(manager.snapshot()['status'],'available')
        self.assertTrue(manager._json.call_args.args[0].endswith('graild-update-macos-arm64.json'))
        self.assertTrue(manager.candidate['url'].endswith('-macOS-arm64.zip'))

    def test_unsigned_zip_never_becomes_installable(self):
        envelope, keys = signed_mac(self.archive)
        with patch('auto_update.sys.platform','darwin'), patch('auto_update.platform.machine',return_value='arm64'):
            manager = UpdateManager(root=self.root/'updates',keys=keys,session=Mock())
        manager.config.update(repository='example/graild',channel='beta')
        manager._response = Mock()
        manager._response.return_value.iter_content.return_value = [b'corrupted archive']
        manager._download_worker({'envelope':envelope,'repository':'example/graild','channel':'beta','url':'https://github.com/test'})
        self.assertEqual(manager.snapshot()['status'],'error')
        self.assertIsNone(manager.ready_job)

    def test_cancel_during_mac_bundle_verification_never_marks_download_ready(self):
        envelope, keys = signed_mac(self.archive)
        with patch('auto_update.sys.platform','darwin'), patch('auto_update.platform.machine',return_value='arm64'):
            manager = UpdateManager(root=self.root/'cancel-updates',keys=keys,session=Mock())
        manager._response = Mock()
        manager._response.return_value.iter_content.return_value = [self.archive.read_bytes()]
        def cancel(_path):
            manager.cancel.set()
            return envelope['manifest']['bundle_sha256']
        with patch('mac_update_security.archive_bundle_digest',side_effect=cancel):
            manager._download_worker({'envelope':envelope,'repository':'example/graild','channel':'beta','url':'https://github.com/test'})
        self.assertEqual(manager.snapshot()['status'],'cancelled')
        self.assertIsNone(manager.ready_job)

    def test_preparation_timeout_stops_helper_before_parent_can_close(self):
        envelope, keys = signed_mac(self.archive)
        with patch('auto_update.sys.platform','darwin'), patch('auto_update.platform.machine',return_value='arm64'):
            manager = UpdateManager(root=self.root/'timeout-updates',keys=keys,session=Mock())
        manager.config.update(repository='example/graild',channel='beta')
        job=manager.root/'jobs'/uuid.uuid4().hex; job.mkdir()
        (job/'payload.zip').write_bytes(self.archive.read_bytes()); atomic_json(job/'manifest.json',envelope)
        manager.ready_job=job; manager.candidate={'manifest':envelope['manifest']}; manager.state['status']='ready'
        target=self.root/'GRAILD.app'; target.mkdir(); (target/'original').write_bytes(b'old app')
        process=Mock(); process.poll.return_value=None
        with patch('update_security.update_root',return_value=manager.root), \
             patch('mac_updater.sys.platform','darwin'), patch('mac_updater.sys.frozen',True,create=True), \
             patch('mac_updater.app_bundle',return_value=target), patch('mac_updater.validate_target',return_value=target), \
             patch('mac_updater.bundle_digest',return_value='0'*64), patch('mac_updater.copy_bundle'), \
             patch('mac_updater.subprocess.run'), patch('mac_updater.launch',return_value=process), \
             patch('mac_updater.time.monotonic',side_effect=[0,61]), self.assertRaises(UpdateError):
            mac_updater.prepare_install(manager)
        process.terminate.assert_called_once()
        self.assertEqual((target/'original').read_bytes(),b'old app')


@unittest.skipUnless(sys.platform == 'darwin', 'Native ditto, file modes and bundle transactions require macOS')
class NativeMacTransactionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.updates = self.root/'cache'
        self.job = self.updates/'jobs'/uuid.uuid4().hex
        self.job.mkdir(parents=True)
        self.target = self.root/'Applications'/'GRAILD.app'
        (self.target/'Contents/MacOS').mkdir(parents=True)
        (self.target/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier':BUNDLE_ID,'CFBundleExecutable':'GRAILD','CFBundleShortVersionString':APP_VERSION}))
        binary=self.target/'Contents/MacOS/GRAILD'; binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o755)
        payload=self.job/'payload.zip'; payload.write_bytes(app_zip())
        self.envelope,self.keys=signed_mac(payload,platform.machine())
        atomic_json(self.job/'manifest.json',self.envelope)
        self.old=bundle_digest(self.target)
        self.request={'target':str(self.target),'parent_pid':123,'current_version':APP_VERSION,
                      'old_sha256':self.old,'health_token':'a'*64,'repository':'example/graild',
                      'channel':'beta','architecture':platform.machine()}
        atomic_json(self.job/'request.json',self.request)
        for attribute,value in [('update_security.update_root',lambda:self.updates),('mac_updater.update_root',lambda:self.updates),
                                ('mac_updater.user_data_directory',lambda:self.root/'profile')]:
            patcher=patch(attribute,value); patcher.start(); self.addCleanup(patcher.stop)
        self.parent=Mock()
        self.verifier=lambda bundle,manifest:verify_bundle(bundle,manifest,codesign=False)

    def install(self,startup):
        mac_updater.install_job(self.job,keys=self.keys,parent_factory=lambda *args:self.parent,
                               startup=startup,bundle_verifier=self.verifier)

    def test_native_process_identity_resolves_native_child(self):
        import subprocess, time
        process = subprocess.Popen(['/bin/sleep','10'])
        try:
            deadline = time.monotonic() + 5
            actual = None
            while not actual and time.monotonic() < deadline:
                actual = mac_updater.process_path(process.pid)
                time.sleep(0.02)
            self.assertTrue(actual and actual.samefile('/bin/sleep'))
        finally:
            process.terminate(); process.wait(timeout=10)

    def test_native_bundle_replacement_preserves_backup_and_file_modes(self):
        def startup(target,job,token,version):
            self.assertTrue((job/'ready.json').is_file())
            self.assertEqual(version,'99.0.0')
            self.assertEqual(bundle_digest(target),self.envelope['manifest']['bundle_sha256'])
        self.install(startup)
        self.parent.wait.assert_called_once()
        backup=self.target.parent/('.graild-backup-'+self.job.name+'.app')
        self.assertEqual(bundle_digest(backup),self.old)
        self.assertEqual(json.loads((self.job/'result.json').read_bytes())['status'],'installed')

    def test_failed_startup_rolls_back_and_relaunches_previous_app(self):
        with patch('mac_updater.launch') as launch, self.assertRaises(UpdateError):
            self.install(Mock(side_effect=UpdateError('failed startup')))
        self.assertEqual(bundle_digest(self.target),self.old)
        self.assertEqual(json.loads((self.job/'result.json').read_bytes())['status'],'rolled_back')
        launch.assert_called_once()

    def test_changed_target_is_never_replaced(self):
        (self.target/'Contents/extra').write_bytes(b'changed')
        with self.assertRaises(UpdateError): self.install(Mock())
        self.assertTrue((self.target/'Contents/extra').is_file())

    def test_interrupted_swap_recovers_old_bundle(self):
        backup=self.target.parent/('.graild-backup-'+self.job.name+'.app')
        self.target.rename(backup)
        atomic_json(self.job/'journal.json',{'phase':'prepared'})
        with patch('mac_updater.app_processes',return_value=[]):
            mac_updater.recover_job(self.job,keys=self.keys)
        self.assertEqual(bundle_digest(self.target),self.old)


if __name__ == '__main__': unittest.main()
