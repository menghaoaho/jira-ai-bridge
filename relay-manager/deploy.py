#!/usr/bin/env python3
"""Install with reviewed config hashes, root-only backups, and rollback."""
import argparse
import datetime as dt
import hashlib
import json
import os
import platform
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from router import render

ROOT = Path(__file__).resolve().parent
APP = Path('/opt/jira-relay')
CONFIG = Path('/etc/jira-relay')
STATE = Path('/var/lib/jira-relay')
CADDY = Path('/etc/caddy/Caddyfile')
NGINX = [Path('/etc/nginx/sites-available/' + x) for x in ('jira-form-upstream', 'jira-api-https', 'jira')]
POOL = Path('/etc/nginx/conf.d/jira-relay-pool.conf')
UNITS = ['jira-relay', 'jira-relay-frps', 'jira-relay-router']
SOURCE_FILES = ['service.py', 'auth.py', 'router.py', 'windows/install.ps1', 'web/index.html', 'web/app.js', 'web/style.css', 'web/login.html']


def sha(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def run(args, timeout=30):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('command_failed:' + args[0])
    return result.stdout


def fetch(url, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with opener.open(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def add_caddy_routes(text):
    marker = 'https://203.0.113.10 {'
    if text.count(marker) != 1 or '/relay-admin/' in text:
        raise RuntimeError('caddy_public_site_needs_review')
    public = '''
    @jira_relay_public path /relay-install/install.ps1 /relay-install/bundle.zip /relay-api/v1/enroll /relay-api/v1/config /relay-api/v1/status /relay-oidc/token /relay-oidc/jwks
    handle @jira_relay_public {
        reverse_proxy 127.0.0.1:18110 {
            header_up X-Jira-Relay-Surface public
            header_up X-Jira-Client-IP {remote_host}
            header_up -Cookie
            header_up -X-Workbench-Identity
        }
    }
'''
    text = text.replace(marker, marker + public, 1)
    marker = 'https://203.0.113.10:8090 {'
    if text.count(marker) != 1:
        raise RuntimeError('caddy_admin_site_needs_review')
    admin = '''
    @jira_relay_admin path /relay-admin/*
    handle @jira_relay_admin {
        reverse_proxy 127.0.0.1:18110 {
            header_up X-Jira-Relay-Surface admin
            header_up X-Jira-Client-IP {remote_host}
            header_up -Authorization
            header_up -X-Workbench-Identity
        }
    }
'''
    return text.replace(marker, marker + admin, 1)


def change_upstreams(text, expected):
    needle = 'proxy_pass http://127.0.0.1:8082'
    if text.count(needle) != expected:
        raise RuntimeError('jira_upstream_shape_changed')
    return text.replace(needle, 'proxy_pass http://127.0.0.1:18112')


def private_write(path, content, gid, mode=0o640):
    path.write_bytes(content)
    os.chown(path, 0, gid)
    path.chmod(mode)


def create_keys(gid):
    now = dt.datetime.now(dt.timezone.utc)
    key_bytes = lambda key: key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Jira Relay CA')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
          .not_valid_after(now + dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                      data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                      encipher_only=None, decipher_only=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'jira-relay')])
    server = (x509.CertificateBuilder().subject_name(subject).issuer_name(name).public_key(server_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
              .not_valid_after(now + dt.timedelta(days=825))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('jira-relay')]), critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .sign(ca_key, hashes.SHA256()))
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    for file, content, mode in [
        ('ca-private.pem', key_bytes(ca_key), 0o600), ('relay-ca.pem', ca.public_bytes(serialization.Encoding.PEM), 0o644),
        ('relay-server.pem', server.public_bytes(serialization.Encoding.PEM), 0o644),
        ('relay-server-key.pem', key_bytes(server_key), 0o640), ('oidc-signing.pem', key_bytes(signing_key), 0o640)]:
        private_write(CONFIG / file, content, gid, mode)


def rollback(backup):
    backup = backup.resolve()
    if backup.parent != Path('/var/backups/jira-relay'):
        raise RuntimeError('unexpected_backup_directory')
    manifest = json.loads((backup / 'manifest.json').read_text())
    for unit in reversed(UNITS):
        subprocess.run(['systemctl', 'disable', '--now', unit], capture_output=True)
    for path, slot in manifest['files'].items():
        target = Path(path)
        if target not in [CADDY, *NGINX, POOL, *[Path('/etc/systemd/system/' + u + '.service') for u in UNITS]]:
            raise RuntimeError('unexpected_backup_target')
        if slot is None:
            target.unlink(missing_ok=True)
        else:
            shutil.copy2(backup / slot, target)
    run(['systemctl', 'daemon-reload'])
    run(['nginx', '-t'])
    run(['caddy', 'validate', '--config', str(CADDY), '--adapter', 'caddyfile'])
    run(['systemctl', 'reload', 'nginx'])
    run(['systemctl', 'reload', 'caddy'])
    # Preserve new state and private keys for inspection/recovery; no secrets
    # are ever printed or deleted as a side effect of proxy rollback.
    return {'rolled_back': True, 'backup': str(backup)}


def install(review):
    if platform.machine() not in ('x86_64', 'amd64'):
        raise RuntimeError('server_requires_x86_64_release')
    expected = json.loads(Path(review).read_text())
    for path in [CADDY, *NGINX]:
        if sha(path) != expected[str(path)]:
            raise RuntimeError('live_config_changed_since_review:' + str(path))
    if APP.exists() or CONFIG.exists() or POOL.exists():
        raise RuntimeError('managed_relay_already_exists_repair_requires_review')
    if any(Path('/etc/systemd/system/' + u + '.service').exists() for u in UNITS):
        raise RuntimeError('service_name_in_use')
    for port in [7001, 18110, 18111, 18112, *range(18120, 18200)]:
        with socket.socket() as probe:
            try:
                probe.bind(('0.0.0.0' if port == 7001 else '127.0.0.1', port))
            except OSError:
                raise RuntimeError('required_port_in_use:' + str(port)) from None
    release = json.loads((ROOT / 'release' / 'release.json').read_text())
    if sha(ROOT / 'release' / 'bundle.zip') != release['bundle_sha256'] or sha(ROOT / 'release' / 'frps') != release['frps_sha256']:
        raise RuntimeError('release_checksum_mismatch')
    if sha(ROOT / 'windows' / 'install.ps1') != release['bootstrap_sha256']:
        raise RuntimeError('bootstrap_checksum_mismatch')
    # Review exact shapes before any change; modified auth/mitigation lines are
    # retained byte for byte. This also prevents patching a different server.
    caddy = add_caddy_routes(CADDY.read_text())
    nginx = [change_upstreams(p.read_text(), count) for p, count in zip(NGINX, [1, 3, 2])]
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups/jira-relay') / stamp
    backup.mkdir(parents=True, mode=0o700)
    backup.parent.chmod(0o700)
    paths = [CADDY, *NGINX, POOL, *[Path('/etc/systemd/system/' + u + '.service') for u in UNITS]]
    manifest = {'files': {}, 'deployed': False, 'release': release}
    for index, path in enumerate(paths):
        slot = str(index) if path.exists() else None
        manifest['files'][str(path)] = slot
        if slot is not None:
            shutil.copy2(path, backup / slot)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    (backup / 'manifest.json').chmod(0o600)
    try:
        try:
            account = pwd.getpwnam('jira-relay')
        except KeyError:
            run(['useradd', '--system', '--user-group', '--home-dir', str(STATE), '--shell', '/usr/sbin/nologin', 'jira-relay'])
            account = pwd.getpwnam('jira-relay')
        APP.mkdir(mode=0o755)
        for name in SOURCE_FILES:
            (APP / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, APP / name)
            (APP / name).chmod(0o644)
        for name in ['bundle.zip', 'frps']:
            shutil.copy2(ROOT / 'release' / name, APP / name)
        (APP / 'frps').chmod(0o755)
        CONFIG.mkdir(mode=0o750); os.chown(CONFIG, 0, account.pw_gid)
        STATE.mkdir(mode=0o700, exist_ok=True); os.chown(STATE, account.pw_uid, account.pw_gid); STATE.chmod(0o700)
        create_keys(account.pw_gid)
        settings = {'origin': 'https://203.0.113.10', 'admin_origin': 'https://203.0.113.10:8090',
                    'server_host': '203.0.113.10', 'frp_port': 7001,
                    'issuer': 'http://127.0.0.1:18110/relay-oidc',
                    'relay_ca': str(CONFIG / 'relay-ca.pem'), 'signing_key': str(CONFIG / 'oidc-signing.pem'),
                    'database': str(STATE / 'relay.db'), 'bundle_file': str(APP / 'bundle.zip'),
                    'bundle': 'https://203.0.113.10/relay-install/bundle.zip', 'bundle_sha256': release['bundle_sha256']}
        private_write(CONFIG / 'settings.json', json.dumps(settings, indent=2).encode(), account.pw_gid)
        frps = '''bindAddr = "0.0.0.0"
bindPort = 7001
proxyBindAddr = "127.0.0.1"
allowPorts = [{start = 18120, end = 18199}]
maxPortsPerClient = 1
auth.method = "oidc"
auth.additionalScopes = ["HeartBeats", "NewWorkConns"]
auth.oidc.issuer = "http://127.0.0.1:18110/relay-oidc"
auth.oidc.audience = "jira-relay"
transport.tls.force = true
transport.tls.certFile = "/etc/jira-relay/relay-server.pem"
transport.tls.keyFile = "/etc/jira-relay/relay-server-key.pem"
transport.heartbeatTimeout = 30
log.to = "console"
log.level = "warn"
[[httpPlugins]]
name = "managed-devices"
addr = "127.0.0.1:18111"
path = "/frp-hook"
ops = ["Login", "NewProxy", "NewWorkConn", "NewUserConn", "Ping", "CloseProxy"]
'''
        private_write(CONFIG / 'frps.toml', frps.encode(), account.pw_gid)
        for unit in UNITS:
            shutil.copy2(ROOT / (unit + '.service'), Path('/etc/systemd/system') / (unit + '.service'))
        POOL.write_text(render([]))
        for path, text in zip([CADDY, *NGINX], [caddy, *nginx]):
            path.write_text(text)
        run(['nginx', '-t'])
        run(['caddy', 'validate', '--config', str(CADDY), '--adapter', 'caddyfile'])
        run([str(APP / 'frps'), 'verify', '-c', str(CONFIG / 'frps.toml')])
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', '--now', 'jira-relay'])
        for _ in range(20):
            try:
                if fetch('http://127.0.0.1:18110/health', {'X-Jira-Relay-Surface': 'internal'}) == 200:
                    break
            except OSError:
                pass
            time.sleep(.3)
        if fetch('http://127.0.0.1:18110/health', {'X-Jira-Relay-Surface': 'internal'}) != 200:
            raise RuntimeError('manager_health_failed')
        if fetch('http://127.0.0.1:18110/relay-admin/api/report', {'X-Jira-Relay-Surface': 'admin'}) != 401:
            raise RuntimeError('admin_auth_failed')
        run(['systemctl', 'enable', '--now', 'jira-relay-frps'])
        run(['systemctl', 'reload', 'nginx'])
        run(['systemctl', 'reload', 'caddy'])
        run(['systemctl', 'enable', '--now', 'jira-relay-router'])
        time.sleep(1)
        run(['systemctl', 'is-active', '--quiet', *UNITS, 'nginx', 'caddy', 'frps', 'jira-access', 'jira-workbench-cloud'])
        if fetch('http://127.0.0.1:18112/login.jsp', {'Host': '203.0.113.10'}) != 200:
            raise RuntimeError('legacy_relay_regression')
        manifest.update({'deployed': True, 'config_hashes': {str(path): sha(path) for path in paths},
                         'admin_url': settings['admin_origin'] + '/relay-admin/', 'windows_acceptance': 'pending'})
        (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        return {'deployed': True, 'backup': str(backup), 'admin_url': manifest['admin_url'],
                'windows_acceptance': 'pending', 'firewall_port': 7001}
    except BaseException:
        rollback(backup)
        raise


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--review')
    modes.add_argument('--rollback')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('root_required')
    print(json.dumps(rollback(Path(args.rollback)) if args.rollback else install(args.review), indent=2))


if __name__ == '__main__':
    main()
