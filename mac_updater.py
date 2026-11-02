"""Network-free signed Mac app replacement, startup confirmation, and rollback."""
from contextlib import contextmanager
import ctypes
import hashlib
import hmac
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile

from app_version import APP_VERSION
from platform_support import user_data_directory
from update_security import (UpdateError, atomic_json, strict_json, validated_job,
                             verify_manifest, verify_file, update_root)
from update_trust import TRUSTED_PUBLIC_KEYS
from mac_update_security import BUNDLE_ID, archive_bundle_digest, bundle_digest, verify_bundle


def app_bundle(executable=None):
    executable = Path(executable or sys.executable).resolve()
    bundle = executable.parent.parent.parent
    if executable != bundle / 'Contents/MacOS/GRAILD' or bundle.suffix != '.app':
        raise UpdateError('Install updates from the packaged GRAILD.app, not a Python development session.')
    return bundle


def validate_target(target, *, require_bundle=True):
    raw = Path(target)
    target = raw.resolve()
    if not raw.is_absolute() or raw.is_symlink() or target.suffix != '.app':
        raise UpdateError('Invalid Mac installation target.')
    if target.is_relative_to(update_root().resolve()) or target.is_relative_to(user_data_directory().resolve()):
        raise UpdateError('An app installation cannot replace update storage or player data.')
    if 'AppTranslocation' in target.parts:
        raise UpdateError('Move GRAILD.app to Applications or a writable folder, then reopen it before updating.')
    if not require_bundle:
        return target
    try:
        info = plistlib.loads((target / 'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleExecutable') != 'GRAILD':
            raise UpdateError('The target is not a GRAIL\'D app.')
    except (OSError, ValueError) as error:
        raise UpdateError('Could not validate the installed Mac app.') from error
    return target


def copy_bundle(source, destination):
    if Path(destination).exists() or Path(destination).is_symlink():
        raise UpdateError('Update staging destination already exists.')
    subprocess.run(['/usr/bin/ditto', '--rsrc', '--extattr', '--acl', str(source), str(destination)],
                   check=True, capture_output=True, timeout=180)


def independent_environment():
    result = {key: value for key, value in os.environ.items() if not key.startswith('_PYI_')}
    result['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return result


def launch(command):
    return subprocess.Popen([str(arg) for arg in command], env=independent_environment(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, close_fds=True)


def process_path(pid):
    api = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    api.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    api.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    return Path(os.fsdecode(buffer.value)).resolve() if api.proc_pidpath(int(pid), buffer, len(buffer)) > 0 else None


def app_processes(target):
    executable = target / 'Contents/MacOS/GRAILD'
    output = subprocess.check_output(['/bin/ps', '-axo', 'pid='], text=True, timeout=20)
    return [int(pid) for pid in output.split() if pid.isdigit() and process_path(int(pid)) == executable]


class ParentProcess:
    def __init__(self, pid, target):
        self.pid, self.target = int(pid), target
        if self.pid <= 1 or process_path(self.pid) != target / 'Contents/MacOS/GRAILD':
            raise UpdateError('The updater parent does not match the installed Mac app.')
        if set(app_processes(target)) != {self.pid}:
            raise UpdateError('Close other GRAIL\'D instances before updating.')

    def wait(self):
        deadline = time.monotonic() + 180
        while process_path(self.pid) == self.target / 'Contents/MacOS/GRAILD':
            if time.monotonic() >= deadline:
                raise UpdateError('GRAIL\'D is still open. No update was installed.')
            time.sleep(0.2)
        if app_processes(self.target):
            raise UpdateError('Another GRAIL\'D instance is running. Close it and retry.')


@contextmanager
def install_lock(target):
    import fcntl
    name = '.graild-update-' + hashlib.sha256(str(target).encode()).hexdigest()[:24] + '.lock'
    descriptor = os.open(target.parent / name, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError('Another Mac update is already running.') from error
        yield
    finally:
        os.close(descriptor)


def read_request(job, keys, *, recovery=False):
    request = strict_json((job / 'request.json').read_bytes())
    if request.get('current_version') != APP_VERSION or request.get('architecture') not in ('arm64', 'x86_64'):
        raise UpdateError('The helper does not match the running Mac app.')
    for name in ('health_token', 'old_sha256'):
        if not isinstance(request.get(name), str) or not re.fullmatch(r'[0-9a-f]{64}', request[name]):
            raise UpdateError('Invalid Mac updater handoff.')
    manifest = verify_manifest(strict_json((job / 'manifest.json').read_bytes()), keys,
                               request['repository'], request['channel'], request['current_version'],
                               platform='macos', architecture=request['architecture'])
    target = validate_target(request['target'], require_bundle=not recovery)
    return request, manifest, target


def monitor_startup(target, job, token, version):
    process = launch([target / 'Contents/MacOS/GRAILD', '--post-mac-update', job, token])
    atomic_json(job / 'launched.json', {'pid': process.pid})
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            try:
                health = strict_json((job / 'health.json').read_bytes())
                if hmac.compare_digest(str(health.get('token', '')), token) and health.get('version') == version and process.poll() is None:
                    return
            except OSError:
                pass
            if process.poll() is not None:
                raise UpdateError('The new Mac app exited before confirming startup.')
            time.sleep(0.2)
        raise UpdateError('The new Mac app did not confirm startup in time.')
    except Exception:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        raise


def restore_backup(target, backup, old_digest, new_digest, job):
    if not backup.is_dir() or bundle_digest(backup) != old_digest:
        raise UpdateError('The Mac recovery backup did not verify. It was left untouched.')
    if target.exists():
        if bundle_digest(target) != new_digest:
            raise UpdateError('The installation changed independently; recovery will not overwrite it.')
        failed = target.parent / ('.graild-failed-' + job.name + '.app')
        if failed.exists():
            raise UpdateError('A previous failed app is already preserved; manual recovery is required.')
        os.rename(target, failed)
    os.rename(backup, target)
    if bundle_digest(target) != old_digest:
        raise UpdateError('Restored Mac app did not verify.')


def install_job(directory, keys=TRUSTED_PUBLIC_KEYS, parent_factory=ParentProcess,
                startup=monitor_startup, bundle_verifier=verify_bundle, copier=copy_bundle, locker=install_lock):
    job = validated_job(directory)
    request, manifest, target = read_request(job, keys)
    payload = job / 'payload.zip'
    verify_file(payload, manifest)
    if archive_bundle_digest(payload) != manifest['bundle_sha256']:
        raise UpdateError('Mac update archive checksum does not match the signed bundle.')
    incoming = target.parent / ('.graild-new-' + job.name + '.app')
    backup = target.parent / ('.graild-backup-' + job.name + '.app')
    moved_old = False
    with locker(target):
        try:
            if bundle_digest(target) != request['old_sha256'] or backup.exists():
                raise UpdateError('The installed app changed or a backup already exists. No files were replaced.')
            parent = parent_factory(request['parent_pid'], target)
            extract = job / 'extracted'
            extract.mkdir(mode=0o700)
            subprocess.run(['/usr/bin/ditto', '-x', '-k', str(payload), str(extract)],
                           check=True, capture_output=True, timeout=180)
            new_app = extract / 'GRAILD.app'
            bundle_verifier(new_app, manifest)
            copier(new_app, incoming)
            bundle_verifier(incoming, manifest)
            atomic_json(job / 'ready.json', {'ready': True})
            parent.wait()
            verify_file(payload, manifest)
            if bundle_digest(target) != request['old_sha256']:
                raise UpdateError('The installed app changed during shutdown.')
            bundle_verifier(incoming, manifest)
            atomic_json(job / 'journal.json', {'phase':'prepared','version':manifest['version']})
            os.rename(target, backup)
            moved_old = True
            os.rename(incoming, target)
            atomic_json(job / 'journal.json', {'phase':'installed','version':manifest['version']})
            bundle_verifier(target, manifest)
            startup(target, job, request['health_token'], manifest['version'])
            atomic_json(job / 'journal.json', {'phase':'healthy','version':manifest['version']})
            atomic_json(job / 'result.json', {'status':'installed','version':manifest['version'],
                                             'message':'Mac update installed and startup verified.'})
        except Exception as error:
            if moved_old:
                try:
                    restore_backup(target, backup, request['old_sha256'], manifest['bundle_sha256'], job)
                    atomic_json(job / 'journal.json', {'phase':'rolled_back'})
                    atomic_json(job / 'result.json', {'status':'rolled_back','message':str(error)+' The previous Mac app was restored.'})
                    launch([target / 'Contents/MacOS/GRAILD'])
                except Exception as recovery_error:
                    atomic_json(job / 'result.json', {'status':'recovery_required','message':str(recovery_error),'backup':str(backup)})
            else:
                atomic_json(job / 'result.json', {'status':'failed','message':str(error)})
            raise


def prepare_install(manager):
    if sys.platform != 'darwin' or not getattr(sys, 'frozen', False):
        raise UpdateError('Restart & Install is available in the packaged Mac app.')
    with manager.lock:
        if manager.state['status'] != 'ready' or not manager.ready_job or not manager.candidate:
            raise UpdateError('No verified Mac update is ready to install.')
        job = validated_job(manager.ready_job)
        manifest = manager._verify_manifest(strict_json((job / 'manifest.json').read_bytes()),
                                            manager.config['repository'], manager.config['channel'])
        verify_file(job / 'payload.zip', manifest)
        target = validate_target(app_bundle())
        probe = target.parent / ('.graild-write-test-' + secrets.token_hex(16))
        try:
            probe.write_bytes(b'test')
        except OSError as error:
            raise UpdateError('Move GRAILD.app to a writable Applications folder, reopen it, and retry. The current app was left open.') from error
        finally:
            if probe.exists(): probe.unlink()
        old_digest = bundle_digest(target)
        with zipfile.ZipFile(job / 'payload.zip') as archive:
            expanded = sum(item.file_size for item in archive.infolist())
        old_size = sum(path.stat().st_size for path in target.rglob('*') if path.is_file() and not path.is_symlink())
        margin = 64 * 1024**2
        required = old_size + expanded + margin
        if target.parent.stat().st_dev == job.stat().st_dev:
            required += expanded
        if shutil.disk_usage(job).free < required or shutil.disk_usage(target.parent).free < expanded + margin:
            raise UpdateError('Free disk space before updating. The current Mac app was left open.')
        runner = job / 'runner.app'
        copy_bundle(target, runner)
        if bundle_digest(runner) != old_digest:
            raise UpdateError('Independent Mac helper copy did not verify.')
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(runner)], check=True, capture_output=True, timeout=60)
        atomic_json(job / 'request.json', {'target':str(target),'parent_pid':os.getpid(),
                    'old_sha256':old_digest,'current_version':APP_VERSION,'architecture':manager.architecture,
                    'repository':manager.config['repository'],'channel':manager.config['channel'],
                    'health_token':secrets.token_hex(32)})
        manager._state('installing', 'Preparing the Mac updater. GRAIL\'D will restart shortly...')
    process = launch([runner / 'Contents/MacOS/GRAILD', '--mac-update-helper', '--job', job])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if (job / 'ready.json').is_file():
            return manager.snapshot()
        if (job / 'result.json').is_file():
            raise UpdateError(strict_json((job / 'result.json').read_bytes()).get('message', 'Mac updater preparation failed.'))
        if process.poll() is not None: break
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
    atomic_json(job / 'result.json', {'status':'failed','message':'Mac updater preparation timed out; the current app was left open.'})
    raise UpdateError('The Mac updater did not become ready. Your app was left open; retry the download.')


def acknowledge_startup(handoff):
    if sys.platform != 'darwin' or not getattr(sys, 'frozen', False):
        return
    target = app_bundle()
    directories = [handoff[0]] if handoff else list((update_root() / 'jobs').glob('*'))[-100:]
    for directory in directories:
        try:
            job = validated_job(directory)
            request, manifest, expected = read_request_for_new_app(job)
            if expected != target or manifest['version'] != APP_VERSION:
                continue
            if handoff and not hmac.compare_digest(request['health_token'], handoff[1]):
                continue
            if not handoff and strict_json((job / 'journal.json').read_bytes()).get('phase') not in ('prepared','installed'):
                continue
            verify_file(job / 'payload.zip', manifest)
            verify_bundle(target, manifest)
            atomic_json(job / 'health.json', {'token':request['health_token'],'version':APP_VERSION})
            if not handoff:
                atomic_json(job / 'journal.json', {'phase':'healthy','version':APP_VERSION})
                atomic_json(job / 'result.json', {'status':'installed','version':APP_VERSION,
                                                 'message':'Interrupted Mac update recovered; startup verified.'})
        except (OSError, UpdateError, KeyError, TypeError, ValueError):
            continue


def read_request_for_new_app(job):
    request = strict_json((job / 'request.json').read_bytes())
    if not re.fullmatch(r'[0-9a-f]{64}', str(request.get('health_token', ''))):
        raise UpdateError('Invalid Mac startup handoff.')
    manifest = verify_manifest(strict_json((job / 'manifest.json').read_bytes()), TRUSTED_PUBLIC_KEYS,
                               request['repository'], request['channel'], request['current_version'],
                               platform='macos', architecture=request['architecture'])
    return request, manifest, validate_target(request['target'])


def recover_job(directory, keys=TRUSTED_PUBLIC_KEYS, locker=install_lock):
    job = validated_job(directory)
    request, manifest, target = read_request(job, keys, recovery=True)
    backup = target.parent / ('.graild-backup-' + job.name + '.app')
    with locker(target):
        phase = strict_json((job / 'journal.json').read_bytes()).get('phase')
        current = bundle_digest(target) if target.exists() else None
        if phase == 'healthy':
            if current != manifest['bundle_sha256']:
                raise UpdateError('Healthy installation changed; recovery will not overwrite it.')
            return
        if current == request['old_sha256']:
            atomic_json(job / 'result.json', {'status':'rolled_back','message':'The previous Mac app is already installed.'})
            return
        if app_processes(target):
            raise UpdateError('Close GRAIL\'D before recovering an interrupted update.')
        restore_backup(target, backup, request['old_sha256'], manifest['bundle_sha256'], job)
        atomic_json(job / 'journal.json', {'phase':'rolled_back'})
        atomic_json(job / 'result.json', {'status':'rolled_back','message':'Interrupted Mac update recovered; the previous app was restored.'})


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('--recover', action='store_true')
    args = parser.parse_args(argv)
    try:
        recover_job(args.job) if args.recover else install_job(args.job)
        return 0
    except Exception as error:
        try:
            job = validated_job(args.job)
            if not (job / 'result.json').exists():
                atomic_json(job / 'result.json', {'status':'failed','message':str(error)})
        except Exception: pass
        return 1
