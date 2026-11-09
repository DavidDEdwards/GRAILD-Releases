"""Validate signed Mac app archives and deterministic bundle contents."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import plistlib
import posixpath
import stat
import struct
import subprocess
import zipfile

from update_security import UpdateError, canonical

BUNDLE_ID = 'com.graild.desktop'
MAX_UNPACKED = 2 * 1024**3


def _digest(records):
    return hashlib.sha256(canonical(sorted(records))).hexdigest()


def archive_bundle_digest(filename):
    records, links, paths = [], {}, set()
    unpacked = 0
    with zipfile.ZipFile(filename) as archive:
        if len(archive.infolist()) > 50000:
            raise UpdateError('Mac update has too many archive entries.')
        for item in archive.infolist():
            name = item.filename
            path = PurePosixPath(name)
            if '\\' in name or '\x00' in name or path.is_absolute() or str(path) != name.rstrip('/') or any(part in ('..', '.') for part in path.parts) or name.startswith('/'):
                raise UpdateError('Unsafe path in Mac update archive.')
            if not path.parts or path.parts[0] not in ('GRAILD.app', '__MACOSX'):
                raise UpdateError('The Mac update must contain only GRAILD.app and its archive metadata.')
            normalized = name.rstrip('/').casefold()
            if normalized in paths:
                raise UpdateError('Duplicate paths in Mac update archive.')
            paths.add(normalized)
            unpacked += item.file_size
            if unpacked > MAX_UNPACKED:
                raise UpdateError('Mac update exceeds the unpacked size limit.')
            mode = item.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                raise UpdateError('Unsupported special file in Mac update archive.')
            if path.parts[0] == '__MACOSX':
                if kind == stat.S_IFLNK:
                    raise UpdateError('Unsafe archive metadata link.')
                continue
            if item.is_dir():
                continue
            if len(path.parts) < 2:
                raise UpdateError('Invalid application bundle root.')
            relative = '/'.join(path.parts[1:])
            if kind == stat.S_IFLNK:
                if item.file_size > 4096:
                    raise UpdateError('Invalid Mac bundle link.')
                target = archive.read(item).decode('utf-8')
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                if target.startswith('/') or '\\' in target or '\x00' in target or not resolved.startswith('GRAILD.app/'):
                    raise UpdateError('Mac update links outside its app bundle.')
                links[name.rstrip('/')] = target
                records.append((relative, 'link', target))
            else:
                digest = hashlib.sha256()
                with archive.open(item) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                records.append((relative, 'file', bool(mode & 0o111), digest.hexdigest()))
        link_targets = {name.casefold(): target for name, target in links.items()}

        def resolve_parts(parts, seen=frozenset()):
            resolved = []
            for part in parts:
                if part in ('', '.'):
                    continue
                if part == '..':
                    if len(resolved) <= 1:
                        raise UpdateError('Mac bundle link chain escapes its app.')
                    resolved.pop()
                    continue
                resolved.append(part)
                key = '/'.join(resolved).casefold()
                if key in link_targets:
                    if key in seen or len(seen) > 64:
                        raise UpdateError('Cyclic Mac bundle link chain.')
                    resolved = resolve_parts(resolved[:-1] + link_targets[key].split('/'), seen | {key})
            if not resolved or resolved[0] != 'GRAILD.app':
                raise UpdateError('Mac bundle link chain escapes its app.')
            return resolved

        for name in links:
            resolve_parts(name.split('/'))
        # Extraction must never write through a symbolic-link directory.
        for item in archive.infolist():
            for parent in PurePosixPath(item.filename).parents:
                if str(parent).casefold() in link_targets:
                    raise UpdateError('Mac archive writes through a symbolic link.')
    names = {record[0] for record in records}
    if not {'Contents/Info.plist', 'Contents/MacOS/GRAILD'} <= names:
        raise UpdateError('Mac update is missing its app metadata or executable.')
    return _digest(records)


def bundle_digest(bundle):
    bundle = Path(bundle)
    if bundle.is_symlink() or not bundle.is_dir():
        raise UpdateError('Invalid Mac app bundle.')
    root = bundle.resolve()
    records = []
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in sorted(folders + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                try:
                    if not path.resolve().is_relative_to(root):
                        raise UpdateError('Mac app has a link outside its bundle.')
                except (OSError, RuntimeError) as error:
                    raise UpdateError('Invalid Mac bundle link.') from error
                records.append((relative, 'link', os.readlink(path)))
            elif stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with path.open('rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                records.append((relative, 'file', bool(mode & 0o111), digest.hexdigest()))
            elif not stat.S_ISDIR(mode):
                raise UpdateError('Unsupported file in Mac app bundle.')
    return _digest(records)


def verify_bundle(bundle, manifest, *, codesign=True):
    bundle = Path(bundle)
    if bundle_digest(bundle) != manifest['bundle_sha256']:
        raise UpdateError('The Mac app does not match the signed bundle checksum.')
    try:
        info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
        executable = bundle / 'Contents/MacOS/GRAILD'
        if info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleShortVersionString') != manifest['version'] or info.get('CFBundleExecutable') != 'GRAILD' or not executable.is_file() or executable.is_symlink() or not executable.stat().st_mode & 0o111:
            raise UpdateError('The Mac update has invalid application metadata.')
        if codesign:
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True, capture_output=True, timeout=60)
            if manifest['architecture'] not in macho_architectures(executable):
                raise UpdateError('The Mac update executable has the wrong architecture.')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise UpdateError('The Mac app signature or executable could not be verified.') from error


def macho_architectures(filename):
    """Read CPU headers directly; updating must not require Xcode or lipo."""
    with Path(filename).open('rb') as source:
        header = source.read(8)
        if len(header) != 8:
            raise UpdateError('Invalid Mac executable header.')
        magic = header[:4]
        cpus = []
        if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xfe\xed\xfa\xce'):
            endian = '<' if magic[0] in (0xcf, 0xce) else '>'
            cpus.append(struct.unpack(endian+'I', header[4:])[0])
        elif magic in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf', b'\xbe\xba\xfe\xca', b'\xbf\xba\xfe\xca'):
            endian = '>' if magic[0] == 0xca else '<'
            count = struct.unpack(endian+'I', header[4:])[0]
            if not 1 <= count <= 16:
                raise UpdateError('Invalid universal Mac executable header.')
            size = 32 if magic in (b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca') else 20
            for _ in range(count):
                entry = source.read(size)
                if len(entry) != size:
                    raise UpdateError('Truncated universal Mac executable.')
                cpus.append(struct.unpack(endian+'I', entry[:4])[0])
        else:
            raise UpdateError('The update is not a Mac executable.')
    return {name for cpu, name in ((0x01000007,'x86_64'),(0x0100000c,'arm64')) if cpu in cpus}
