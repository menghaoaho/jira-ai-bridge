#!/usr/bin/env python3
"""Real FRP + nginx test with two simulated Windows-side Jira backends.

All listeners use loopback. No request is sent to the company Jira or cloud.
This validates the wire protocol, not Windows boot/task-scheduler behavior.
"""
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from service import Application, Store, Tokens, make_server
from router import healthy_ports, render

ROOT = Path(__file__).resolve().parent


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except Exception:
            pass
        time.sleep(.2)
    raise AssertionError('expected integration state was not reached')


class Jira(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.server.healthy else 503)
        self.send_header('X-Jira-Relay-Device', self.server.ident)
        body = (b'<meta name="ajs-version-number" content="TEST">' if self.path == '/__jira_relay_health'
                else self.server.ident.encode())
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    frps, frpc = ROOT / '.cache/frps', ROOT / '.cache/frpc'
    if not frps.exists() or not frpc.exists() or not shutil.which('nginx'):
        raise RuntimeError('run build_release.py and install nginx before integration verification')
    processes, servers, streams = [], [], []
    with tempfile.TemporaryDirectory(prefix='jira-relay-integration-') as temp:
        root = Path(temp)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        (root / 'key.pem').write_bytes(pem)
        now = dt.datetime.now(dt.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'jira-relay')])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('jira-relay')]), False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
        (root / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        store = Store(root / 'relay.db', 18120, 18123)
        tokens = Tokens(root / 'key.pem', 'http://placeholder')
        app = Application({'origin': 'https://test.invalid', 'admin_origin': 'https://test.invalid:8090'}, store, tokens)
        public, hooks = make_server(app, 0), make_server(app, 0, True)
        tokens.issuer = f'http://127.0.0.1:{public.server_port}/relay-oidc'
        for server in [public, hooks]:
            threading.Thread(target=server.serve_forever, daemon=True).start(); servers.append(server)
        control_port, frontend = free_port(), free_port()
        frps_config = f'''bindAddr = "127.0.0.1"
bindPort = {control_port}
proxyBindAddr = "127.0.0.1"
allowPorts = [{{start = 18120, end = 18123}}]
auth.method = "oidc"
auth.additionalScopes = ["HeartBeats", "NewWorkConns"]
auth.oidc.issuer = "{tokens.issuer}"
auth.oidc.audience = "jira-relay"
transport.tls.force = true
transport.tls.certFile = "{root}/cert.pem"
transport.tls.keyFile = "{root}/key.pem"
transport.heartbeatTimeout = 15
log.level = "warn"
[[httpPlugins]]
name = "test-auth"
addr = "127.0.0.1:{hooks.server_port}"
path = "/frp-hook"
ops = ["Login", "NewProxy", "NewWorkConn", "NewUserConn", "Ping", "CloseProxy"]
'''
        (root / 'frps.toml').write_text(frps_config)

        def launch(args, name):
            log = (root / (name + '.log')).open('wb'); streams.append(log)
            process = subprocess.Popen([str(x) for x in args], stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            return process

        def get(url):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=3) as response:
                return response.read().decode()

        try:
            launch([frps, '-c', root / 'frps.toml'], 'frps')
            def control_ready():
                with socket.create_connection(('127.0.0.1', control_port), timeout=.2): return True
            wait_for(control_ready)
            identities = []
            for index in range(2):
                invite = store.create_code('模拟电脑' + str(index), 24, 'test-owner')
                ident, secret = secrets.token_hex(16), secrets.token_hex(32)
                device = store.enroll(invite['code'], ident, secret, 'TEST-' + str(index), '1.0.0')
                source = ThreadingHTTPServer(('127.0.0.1', 0), Jira)
                source.ident, source.healthy = ident, True
                threading.Thread(target=source.serve_forever, daemon=True).start(); servers.append(source)
                client_config = f'''serverAddr = "127.0.0.1"
serverPort = {control_port}
user = "{ident}"
auth.method = "oidc"
auth.additionalScopes = ["HeartBeats", "NewWorkConns"]
auth.oidc.clientID = "{ident}"
auth.oidc.clientSecret = "{secret}"
auth.oidc.audience = "jira-relay"
auth.oidc.tokenEndpointURL = "http://127.0.0.1:{public.server_port}/relay-oidc/token"
transport.tls.enable = true
transport.tls.trustedCaFile = "{root}/cert.pem"
transport.tls.serverName = "jira-relay"
transport.heartbeatInterval = 2
transport.heartbeatTimeout = 10
log.level = "warn"
[[proxies]]
name = "jira"
type = "tcp"
localIP = "127.0.0.1"
localPort = {source.server_port}
remotePort = {device['port']}
'''
                file = root / ('frpc-' + str(index) + '.toml'); file.write_text(client_config)
                process = launch([frpc, '-c', file], 'frpc-' + str(index))
                identities.append((device, secret, source, process))
            for device, secret, source, process in identities:
                wait_for(lambda: get(f'http://127.0.0.1:{device["port"]}/') == device['id'])
                app.probe(store.get(device['id']))
                assert store.public_device(store.get(device['id']))['status'] == '正常转发'
            assert set(healthy_ports(store.path)) == {x[0]['port'] for x in identities}
            print('PASS actual FRP OIDC login, device proof, two independently verified backends', flush=True)
            legacy = ThreadingHTTPServer(('127.0.0.1', 0), Jira)
            legacy.ident, legacy.healthy = 'LEGACY-FALLBACK', True
            threading.Thread(target=legacy.serve_forever, daemon=True).start(); servers.append(legacy)
            config = root / 'nginx.conf'
            def write_nginx():
                pool = render(healthy_ports(store.path)).replace('127.0.0.1:8082', '127.0.0.1:' + str(legacy.server_port)).replace('127.0.0.1:18112', '127.0.0.1:' + str(frontend))
                config.write_text(f'pid "{root}/nginx.pid"; error_log "{root}/nginx-error.log" warn; events {{}} http {{ {pool} }}')
            write_nginx()
            subprocess.run(['nginx', '-t', '-p', str(root) + '/', '-c', str(config)], check=True, capture_output=True)
            nginx = launch(['nginx', '-p', str(root) + '/', '-c', config, '-g', 'daemon off;'], 'nginx')
            url = f'http://127.0.0.1:{frontend}/'
            wait_for(lambda: get(url))
            observed = {get(url) for _ in range(12)}
            assert observed == {x[0]['id'] for x in identities}
            print('PASS real nginx distributes traffic to both healthy devices', flush=True)
            first = identities[0][0]
            store.disable(first['id'], 'test-owner')
            write_nginx()
            subprocess.run(['nginx', '-p', str(root) + '/', '-c', str(config), '-s', 'reload'], check=True, capture_output=True)
            wait_for(lambda: get(url) == identities[1][0]['id'])
            assert {get(url) for _ in range(8)} == {identities[1][0]['id']}
            def revoked_cannot_forward():
                try: get(f'http://127.0.0.1:{first["port"]}/'); return False
                except Exception: return True
            wait_for(revoked_cannot_forward, 5)
            print('PASS revoking one device stops its actual tunnel and preserves the other', flush=True)
            second = identities[1][0]
            identities[1][2].healthy = False
            app.probe(store.get(second['id']))
            assert not healthy_ports(store.path)
            write_nginx()
            subprocess.run(['nginx', '-p', str(root) + '/', '-c', str(config), '-s', 'reload'], check=True, capture_output=True)
            wait_for(lambda: get(url) == 'LEGACY-FALLBACK')
            print('PASS invalid Jira backend excluded; existing path takes over', flush=True)
            identities[1][2].healthy = True
            app.probe(store.get(second['id']))
            assert healthy_ports(store.path) == [second['port']]
            print('PASS recovered device becomes eligible without re-enrollment', flush=True)
        except BaseException:
            # Test logs contain only disposable simulated identities, but still
            # redact JWTs/credential fields before surfacing diagnostic context.
            import re
            for file in root.glob('*.log'):
                text = file.read_text(errors='replace')[-6000:]
                text = re.sub(r'eyJ[A-Za-z0-9_.-]+', '[JWT]', text)
                text = re.sub(r'[0-9a-f]{64}', '[secret]', text)
                print(file.name, text)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
            for server in reversed(servers):
                server.shutdown(); server.server_close()
            for stream in streams: stream.close()


if __name__ == '__main__':
    main()
