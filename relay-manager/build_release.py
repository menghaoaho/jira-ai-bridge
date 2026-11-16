#!/usr/bin/env python3
"""Build a self-contained Windows bundle from official, pinned upstream files."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / '.cache'
OUT = ROOT / 'release'
FRP = '0.70.1'
NGINX = '1.30.6'
WINDOWS_FILES = ('install.ps1', 'common.ps1', 'supervisor.ps1', 'manage.ps1', 'nginx.conf.template')
ASSETS = {
    'frp_windows.zip': ('https://github.com/fatedier/frp/releases/download/v0.70.1/frp_0.70.1_windows_amd64.zip',
                        '531f3cd3cc41c0b4f077b54fe6b7dd83c0ff727e7f0bf412a4c78fa279165de5'),
    'frp_linux.tar.gz': ('https://github.com/fatedier/frp/releases/download/v0.70.1/frp_0.70.1_linux_amd64.tar.gz',
                         '333da23d1b9009d7c01638e9ba38cf4600f7d37d393f854e96ee1396adefa9a6'),
    'frp_darwin.tar.gz': ('https://github.com/fatedier/frp/releases/download/v0.70.1/frp_0.70.1_darwin_arm64.tar.gz',
                          'cfa733b5a261c1647edee3c1fc4133d2542989b28f5602e81d47fc821d25c55f'),
    'nginx_windows.zip': ('https://nginx.org/download/nginx-1.30.6.zip', None),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def download(name, url, expected):
    CACHE.mkdir(exist_ok=True)
    file = CACHE / name
    if file.exists():
        data = file.read_bytes()
        if expected and sha(data) != expected:
            raise RuntimeError('cached artifact checksum mismatch: ' + name)
    else:
        partial = CACHE / (name + '.part')
        subprocess.run(['curl', '-fL', '--retry', '3', '--retry-delay', '1', '--connect-timeout', '15',
                        '--max-time', '240', '-o', str(partial), url], check=True, capture_output=True)
        data = partial.read_bytes()
        if expected and sha(data) != expected:
            raise RuntimeError('upstream artifact checksum mismatch: ' + name)
        file.write_bytes(data)
        partial.unlink()
    return data


def main():
    missing = [name for name in WINDOWS_FILES if not (ROOT / 'windows' / name).is_file()]
    if missing:
        raise RuntimeError('missing required Windows components: ' + ', '.join(missing))
    OUT.mkdir(exist_ok=True)
    provenance = {}
    data = {}
    for name, (url, expected) in ASSETS.items():
        print('Fetching official artifact:', name, flush=True)
        data[name] = download(name, url, expected)
        provenance[name] = {'url': url, 'sha256': sha(data[name]), 'upstream_digest_verified': bool(expected)}
    # Pin the vendor HTTPS download on first build. Future builds require the
    # same bytes; a version replacement requires explicit source review.
    lock_path = ROOT / 'vendor-lock.json'
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if lock != provenance:
            raise RuntimeError('vendor release changed; review vendor-lock.json before rebuilding')
    else:
        lock_path.write_text(json.dumps(provenance, indent=2) + '\n')
    files = {}
    for name in WINDOWS_FILES:
        file = ROOT / 'windows' / name
        if file.suffix == '.ps1':
            # Windows PowerShell 5.1 requires a BOM for Chinese UTF-8 text.
            source = file.read_text(encoding='utf-8-sig')
            file.write_text(source, encoding='utf-8-sig')
        files[name] = file.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data['frp_windows.zip'])) as archive:
        for name in ('frpc.exe', 'LICENSE'):
            match = [i for i in archive.namelist() if i.endswith('/' + name)]
            if len(match) != 1:
                raise RuntimeError('unexpected FRP archive layout')
            files['frpc.exe' if name == 'frpc.exe' else 'licenses/FRP-LICENSE.txt'] = archive.read(match[0])
    with zipfile.ZipFile(io.BytesIO(data['nginx_windows.zip'])) as archive:
        prefix = 'nginx-' + NGINX + '/'
        for item in archive.infolist():
            if item.is_dir() or not item.filename.startswith(prefix):
                continue
            relative = item.filename[len(prefix):]
            if relative.startswith(('conf/', 'html/')) or relative == 'nginx.exe':
                files['nginx/' + relative] = archive.read(item)
            elif relative.startswith('docs/') and ('LICENSE' in relative or relative.endswith('README.md')):
                files['licenses/nginx/' + relative[len('docs/'):]] = archive.read(item)
    if 'nginx/nginx.exe' not in files:
        raise RuntimeError('missing Windows nginx binary')
    files['nginx/logs/.keep'] = b''
    # Official nginx's distribution imports the OS MSVCRT, with no separately
    # installed VC++ redistributable. Keep this check tied to the pinned build.
    if any(x.lower() in files['nginx/nginx.exe'].lower() for x in (b'vcruntime140.dll', b'msvcp140.dll')):
        raise RuntimeError('new runtime dependency requires a reviewed redistributable')
    files['licenses/vendor-sources.json'] = json.dumps(provenance, indent=2).encode()
    manifest = {'version': '1.1.0', 'frp': FRP, 'nginx': NGINX,
                'files': {name: sha(content) for name, content in sorted(files.items())}}
    files['manifest.json'] = json.dumps(manifest, indent=2).encode()
    bundle = OUT / 'bundle.zip'
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (2026, 11, 16, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    for asset, target in [('frp_linux.tar.gz', OUT / 'frps'), ('frp_darwin.tar.gz', CACHE / 'frps'), ('frp_darwin.tar.gz', CACHE / 'frpc')]:
        with tarfile.open(fileobj=io.BytesIO(data[asset]), mode='r:gz') as archive:
            member = next(x for x in archive.getmembers() if x.name.endswith('/' + target.name))
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(0o755)
    result = {'version': '1.1.0', 'bundle_sha256': sha(bundle.read_bytes()),
              'bootstrap_sha256': sha((ROOT / 'windows' / 'install.ps1').read_bytes()),
              'frps_sha256': sha((OUT / 'frps').read_bytes()), 'bundle_bytes': bundle.stat().st_size}
    (OUT / 'release.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
