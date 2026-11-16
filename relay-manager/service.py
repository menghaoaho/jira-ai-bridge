#!/usr/bin/env python3
"""Per-device enrollment, OIDC, FRP authorization, and read-only backend health.

Public/admin HTTP and FRP hooks have separate loopback listeners. No caller can
choose an upstream URL, shell command, proxy name, or remote port.
"""
import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from auth import admin_identity, NoRedirect

ROOT = Path(__file__).resolve().parent
VERSION = '1.0.0'
ID_RE = re.compile(r'^[0-9a-f]{32}$')
SECRET_RE = re.compile(r'^[0-9a-f]{64}$')
CODE_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
AUDIENCE = 'jira-relay'
PUBLIC_PREFIX = '/relay-api/v1'
ADMIN_PREFIX = '/relay-admin'
OIDC_PREFIX = '/relay-oidc'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def clean(value, limit=80):
    return ''.join(c for c in str(value or '') if c.isprintable())[:limit].strip()


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


class APIError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


class Store:
    def __init__(self, path, first_port=18120, last_port=18199):
        self.path = Path(path)
        self.first_port, self.last_port = first_port, last_port
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS enrollments (
                  id TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
                  created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                  consumed_by TEXT, actor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                  id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, label TEXT NOT NULL,
                  hostname TEXT NOT NULL, port INTEGER UNIQUE, created REAL NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1, version TEXT NOT NULL DEFAULT '',
                  last_seen REAL NOT NULL DEFAULT 0, run_id TEXT NOT NULL DEFAULT '',
                  health_ok INTEGER NOT NULL DEFAULT 0, health_at REAL NOT NULL DEFAULT 0,
                  health_reason TEXT NOT NULL DEFAULT '等待连接', generation INTEGER NOT NULL DEFAULT 0,
                  disabled_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS audit (
                  ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def connect(self, immediate=False):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if immediate:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_code(self, label, hours, actor):
        label = clean(label)
        if not label or not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 168:
            raise APIError(400, '请填写设备备注，有效时间为1至168小时')
        ident, code, now = secrets.token_hex(16), secrets.token_urlsafe(32), time.time()
        with self.connect(True) as db:
            db.execute('INSERT INTO enrollments (id,code_hash,label,created,expires,actor) VALUES (?,?,?,?,?,?)',
                       (ident, digest(code), label, now, now + hours * 3600, actor))
            db.execute('INSERT INTO audit VALUES (?,?,?,?)', (now, actor, 'create_command', ident))
        return {'id': ident, 'code': code, 'expires': now + hours * 3600, 'label': label}

    def enroll(self, code, ident, secret, hostname, version):
        if not (isinstance(code, str) and CODE_RE.fullmatch(code)
                and isinstance(ident, str) and ID_RE.fullmatch(ident)
                and isinstance(secret, str) and SECRET_RE.fullmatch(secret)):
            raise APIError(400, '安装信息无效，请重新复制完整命令')
        now = time.time()
        with self.connect(True) as db:
            invitation = db.execute('SELECT * FROM enrollments WHERE code_hash=?', (digest(code),)).fetchone()
            if not invitation or invitation['revoked'] or invitation['expires'] < now:
                raise APIError(403, '安装命令已过期或撤销，请向管理员获取新命令')
            row = db.execute('SELECT * FROM devices WHERE id=?', (ident,)).fetchone()
            if invitation['consumed_by']:
                if (invitation['consumed_by'] != ident or not row or not row['enabled']
                        or not hmac.compare_digest(row['secret_hash'], digest(secret))):
                    raise APIError(409, '此安装命令已经被使用，请向管理员获取新命令')
                return dict(row)
            if row:
                raise APIError(409, '本机已登记，请使用检查或修复功能')
            used = {r[0] for r in db.execute('SELECT port FROM devices')}
            port = next((p for p in range(self.first_port, self.last_port + 1) if p not in used), None)
            if port is None:
                # Retain device history, but recycle an inactive port only after
                # the revocation grace period AND its old listener has closed.
                for old in db.execute('SELECT id,port FROM devices WHERE enabled=0 AND disabled_at<? AND port IS NOT NULL ORDER BY disabled_at', (now - 60,)):
                    if not self.first_port <= old['port'] <= self.last_port:
                        continue
                    with socket.socket() as probe:
                        probe.settimeout(.1)
                        listening = probe.connect_ex(('127.0.0.1', old['port'])) == 0
                    if not listening:
                        port = old['port']
                        db.execute('UPDATE devices SET port=NULL WHERE id=?', (old['id'],))
                        break
            if port is None:
                raise APIError(503, '可接入设备数量已满，请联系管理员')
            db.execute('INSERT INTO devices (id,secret_hash,label,hostname,port,created,version) VALUES (?,?,?,?,?,?,?)',
                       (ident, digest(secret), invitation['label'], clean(hostname), port, now, clean(version, 30)))
            db.execute('UPDATE enrollments SET consumed_by=? WHERE id=? AND consumed_by IS NULL', (ident, invitation['id']))
            db.execute('INSERT INTO audit VALUES (?,?,?,?)', (now, 'device:' + ident, 'enroll', invitation['id']))
            return dict(db.execute('SELECT * FROM devices WHERE id=?', (ident,)).fetchone())

    def authenticate(self, ident, secret, enabled=True):
        if not (ID_RE.fullmatch(str(ident)) and SECRET_RE.fullmatch(str(secret))):
            raise APIError(401, '设备凭证无效，请联系管理员重新授权')
        with self.connect() as db:
            row = db.execute('SELECT * FROM devices WHERE id=?', (ident,)).fetchone()
        if not row or not hmac.compare_digest(row['secret_hash'], digest(secret)):
            raise APIError(401, '设备凭证无效，请联系管理员重新授权')
        if enabled and not row['enabled']:
            raise APIError(403, '这台电脑已被管理员停用')
        return dict(row)

    def get(self, ident):
        with self.connect() as db:
            row = db.execute('SELECT * FROM devices WHERE id=?', (ident,)).fetchone()
        if not row or not row['enabled']:
            raise APIError(403, 'device_not_authorized')
        return dict(row)

    def revoke_code(self, ident, actor):
        with self.connect(True) as db:
            row = db.execute('SELECT consumed_by FROM enrollments WHERE id=?', (ident,)).fetchone()
            if not row:
                raise APIError(404, '未找到安装命令')
            if row['consumed_by']:
                raise APIError(409, '命令已使用，请在设备列表中停用对应电脑')
            db.execute('UPDATE enrollments SET revoked=1 WHERE id=?', (ident,))
            db.execute('INSERT INTO audit VALUES (?,?,?,?)', (time.time(), actor, 'revoke_command', ident))

    def disable(self, ident, actor):
        with self.connect(True) as db:
            if not db.execute('SELECT id FROM devices WHERE id=?', (ident,)).fetchone():
                raise APIError(404, '未找到设备')
            db.execute("UPDATE devices SET enabled=0,health_ok=0,health_reason='已停用',generation=generation+1,disabled_at=? WHERE id=?", (time.time(), ident))
            db.execute('INSERT INTO audit VALUES (?,?,?,?)', (time.time(), actor, 'disable_device', ident))

    def reauthorize(self, ident, actor):
        # Old credentials never become valid again. The next installation gets a
        # new identity; keep the disabled row for audit.
        with self.connect() as db:
            row = db.execute('SELECT label,enabled FROM devices WHERE id=?', (ident,)).fetchone()
        if not row or row['enabled']:
            raise APIError(409, '请先停用设备，再生成新的安装命令')
        return self.create_code(row['label'], 24, actor)

    def login(self, ident, run_id):
        with self.connect(True) as db:
            result = db.execute("UPDATE devices SET run_id=?,last_seen=?,health_ok=0,health_reason='正在检查回源',generation=generation+1 WHERE id=? AND enabled=1",
                                (run_id, time.time(), ident))
            if not result.rowcount:
                raise APIError(403, 'device_not_authorized')

    def heartbeat(self, ident, run_id):
        with self.connect(True) as db:
            result = db.execute('UPDATE devices SET last_seen=? WHERE id=? AND run_id=? AND enabled=1',
                                (time.time(), ident, run_id))
            if not result.rowcount:
                raise APIError(403, 'session_not_authorized')

    def close(self, ident, run_id):
        with self.connect(True) as db:
            db.execute("UPDATE devices SET health_ok=0,health_reason='连接已断开',last_seen=0,generation=generation+1 WHERE id=? AND run_id=?",
                       (ident, run_id))

    def record_health(self, ident, generation, ok, reason):
        with self.connect(True) as db:
            db.execute('UPDATE devices SET health_ok=?,health_at=?,health_reason=? WHERE id=? AND generation=? AND enabled=1',
                       (int(ok), time.time(), reason, ident, generation))

    def probe_candidates(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM devices WHERE enabled=1 AND last_seen>?', (time.time() - 30,))]

    @staticmethod
    def public_device(row, now=None):
        now = time.time() if now is None else now
        if not row['enabled']:
            status = '已停用'
        elif row['last_seen'] < now - 30:
            status = '离线'
        elif row['health_ok'] and row['health_at'] > now - 20:
            status = '正常转发'
        elif row['health_at'] and row['health_at'] > now - 20:
            status = '内网不可达'
        else:
            status = '正在检查'
        keys = ('id', 'label', 'hostname', 'created', 'version', 'last_seen', 'health_at', 'health_reason', 'enabled')
        return {**{k: row[k] for k in keys}, 'status': status}

    def report(self):
        with self.connect() as db:
            devices = [self.public_device(r) for r in db.execute('SELECT * FROM devices ORDER BY created DESC')]
            codes = [dict(r) for r in db.execute('SELECT id,label,created,expires,revoked,consumed_by FROM enrollments ORDER BY created DESC LIMIT 100')]
        now = time.time()
        for code in codes:
            code['status'] = '已使用' if code['consumed_by'] else '已撤销' if code['revoked'] else '已过期' if code['expires'] < now else '待安装'
        return {'devices': devices, 'commands': codes, 'now': now, 'version': VERSION}


class Tokens:
    """Small client-credentials issuer. RSA operations use cryptography/OpenSSL."""
    def __init__(self, pem, issuer):
        self.key = serialization.load_pem_private_key(Path(pem).read_bytes(), password=None)
        if not isinstance(self.key, rsa.RSAPrivateKey) or self.key.key_size < 2048:
            raise ValueError('RSA signing key must be at least 2048 bits')
        public = self.key.public_key().public_numbers()
        n = b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, 'big'))
        e = b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, 'big'))
        self.kid = digest(n)[:16]
        self.jwks = {'keys': [{'kty': 'RSA', 'use': 'sig', 'alg': 'RS256', 'kid': self.kid, 'n': n, 'e': e}]}
        self.issuer = issuer.rstrip('/')

    def issue(self, device):
        now = int(time.time())
        parts = [b64(json.dumps(x, separators=(',', ':')).encode()) for x in (
            {'alg': 'RS256', 'typ': 'JWT', 'kid': self.kid},
            {'iss': self.issuer, 'sub': device['id'], 'aud': AUDIENCE, 'iat': now, 'exp': now + 120, 'jti': secrets.token_hex(16)})]
        signed = '.'.join(parts).encode()
        return '.'.join(parts + [b64(self.key.sign(signed, padding.PKCS1v15(), hashes.SHA256()))])

    def verify(self, token, ident):
        try:
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError()
            header, payload, signature = token.split('.')
            dec = lambda s: base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
            h = json.loads(dec(header)); p = json.loads(dec(payload))
            if h.get('alg') != 'RS256' or h.get('kid') != self.kid:
                raise ValueError()
            self.key.public_key().verify(dec(signature), (header + '.' + payload).encode(), padding.PKCS1v15(), hashes.SHA256())
            now = time.time()
            if (p.get('sub') != ident or p.get('iss') != self.issuer or p.get('aud') != AUDIENCE
                    or not isinstance(p.get('exp'), int) or not isinstance(p.get('iat'), int)
                    or p['exp'] <= now or p['iat'] > now + 10 or p['exp'] - p['iat'] > 120):
                raise ValueError()
        except Exception:
            raise APIError(403, 'invalid_device_proof') from None


class Application:
    def __init__(self, settings, store, tokens, authenticate=None):
        self.settings, self.store, self.tokens = settings, store, tokens
        self.authenticate = authenticate or (lambda cookie: admin_identity(cookie, settings.get('auth_url', 'http://127.0.0.1:18097/api/auth/me')))
        self.limiter = Limiter()
        self.stop = threading.Event()

    def config(self, device):
        s = self.settings
        return {'version': VERSION, 'device_id': device['id'], 'remote_port': device['port'],
                'server_host': s['server_host'], 'server_port': s.get('frp_port', 7001),
                'origin': s['origin'], 'jira_origin': 'https://jira.example.internal',
                'token_url': s['origin'] + OIDC_PREFIX + '/token', 'audience': AUDIENCE,
                'relay_ca': Path(s['relay_ca']).read_text(), 'relay_server_name': 'jira-relay',
                'bundle': s['bundle'], 'bundle_sha256': s['bundle_sha256']}

    def command(self, invitation):
        s = self.settings
        sha = hashlib.sha256((ROOT / 'windows' / 'install.ps1').read_bytes()).hexdigest()
        script_url = s['origin'] + '/relay-install/install.ps1'
        # Only generated HTTPS origins and hex/base64url values are interpolated.
        script = ("& {$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; "
                  "$p=Join-Path $env:TEMP ('jira-relay-'+[guid]::NewGuid().ToString('N')+'.ps1'); "
                  f"Invoke-WebRequest -UseBasicParsing '{script_url}' -OutFile $p; "
                  f"if((Get-FileHash $p -Algorithm SHA256).Hash -ne '{sha}'){{Remove-Item $p; throw 'Installation file verification failed'}}; "
                  f"& $p -EnrollmentToken '{invitation['code']}' -Origin '{s['origin']}' -ExpectedScriptHash '{sha}'" + "}")
        # EncodedCommand avoids the outer PowerShell expanding $p/$env before
        # the child runs, and works identically when pasted into cmd.exe.
        encoded = base64.b64encode(script.encode('utf-16le')).decode()
        return 'powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + encoded

    def hook(self, op, content):
        if not isinstance(content, dict):
            raise APIError(403, 'invalid_request')
        if op == 'Login':
            ident = content.get('user', '')
            self.store.get(ident)
            self.tokens.verify(content.get('privilege_key'), ident)
            run_id = secrets.token_hex(16)
            self.store.login(ident, run_id)
            return {'unchange': False, 'content': {**content, 'run_id': run_id}}
        user = content.get('user', {})
        if not isinstance(user, dict):
            raise APIError(403, 'invalid_user')
        ident, run_id = user.get('user', ''), user.get('run_id', '')
        if op == 'CloseProxy':
            self.store.close(ident, run_id)
            return {'unchange': True}
        row = self.store.get(ident)
        if not run_id or not hmac.compare_digest(row['run_id'], run_id):
            raise APIError(403, 'invalid_session')
        if op in ('Ping', 'NewWorkConn'):
            self.tokens.verify(content.get('privilege_key'), ident)
            if op == 'Ping':
                self.store.heartbeat(ident, run_id)
        elif op == 'NewProxy':
            if (content.get('proxy_name') != ident + '.jira' or content.get('proxy_type') != 'tcp'
                    or type(content.get('remote_port')) is not int or content['remote_port'] != row['port']
                    or content.get('group') or content.get('custom_domains') or content.get('subdomain')):
                raise APIError(403, 'proxy_not_allowed')
        elif op == 'NewUserConn':
            if content.get('proxy_name') != ident + '.jira' or content.get('proxy_type') != 'tcp':
                raise APIError(403, 'proxy_not_allowed')
        else:
            raise APIError(403, 'unsupported_operation')
        return {'unchange': True}

    def probe(self, row):
        ok, reason = False, '公司 Jira 回源不可达'
        req = urllib.request.Request(f'http://127.0.0.1:{row["port"]}/__jira_relay_health',
                                     headers={'Host': 'jira-relay-health', 'Connection': 'close'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=3) as response:
                body = response.read(262144)
                ok = (response.status == 200 and response.headers.get('X-Jira-Relay-Device') == row['id']
                      and bool(re.search(rb'(?i)(ajs-version-number|ajs-build-number|name="application-name"\s+content="JIRA|id="jira)', body)))
            reason = '云端已确认此电脑可回源 Jira' if ok else '回源内容不是预期的 Jira 页面'
        except (OSError, ValueError):
            pass
        self.store.record_health(row['id'], row['generation'], ok, reason)

    def health_loop(self, interval=5):
        # One slot per supported device prevents slow backends from holding up
        # healthy devices. Never wait for an entire batch before scheduling the
        # next probe, and never overlap two checks for the same identity.
        running = {}
        with ThreadPoolExecutor(max_workers=80) as pool:
            while not self.stop.is_set():
                for row in self.store.probe_candidates():
                    previous = running.get(row['id'])
                    if previous is None or previous.done():
                        running[row['id']] = pool.submit(self.probe, row)
                running = {ident: future for ident, future in running.items() if not future.done()}
                self.stop.wait(interval)


class Limiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = OrderedDict()

    def take(self, key, limit=20, interval=60):
        now = time.monotonic()
        with self.lock:
            start, count = self.items.pop(key, (now, 0))
            if now - start >= interval:
                start, count = now, 0
            self.items[key] = (start, count + 1)
            while len(self.items) > 4096:
                self.items.popitem(last=False)
            return count < limit


class Handler(BaseHTTPRequestHandler):
    server_version = 'JiraRelay'
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Never record paths, arguments, request bodies, cookies, or tokens.

    def reply(self, status, data, ctype='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode() if ctype.startswith('application/json') else data
        self.send_response(status)
        for k, v in {'Content-Type': ctype, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store',
                     'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                     'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def body(self, form=False):
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if not 0 < n <= 16384 or self.headers.get('Transfer-Encoding'):
                raise ValueError()
            expected = 'application/x-www-form-urlencoded' if form else 'application/json'
            if self.headers.get('Content-Type', '').split(';')[0].strip() != expected:
                raise ValueError()
            raw = self.rfile.read(n)
            if len(raw) != n:
                raise ValueError()
            data = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode(), strict_parsing=True).items()} if form else json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise APIError(400, '请求格式无效') from None

    def device(self, enabled=True):
        token = self.headers.get('Authorization', '')
        match = re.fullmatch(r'Bearer ([0-9a-f]{32})\.([0-9a-f]{64})', token)
        if not match:
            raise APIError(401, '设备凭证无效')
        return self.server.app.store.authenticate(*match.groups(), enabled=enabled)

    def serve(self):
        app = self.server.app
        if self.client_address[0] not in ('127.0.0.1', '::1'):
            raise APIError(403, 'direct_access_denied')
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.query or parsed.fragment:
            raise APIError(400, '不接受网址参数')
        path = parsed.path
        if self.server.hooks:
            # FRP supplies its protocol version and operation as query arguments.
            raise APIError(404, 'not_found')
        surface = self.headers.get('X-Jira-Relay-Surface', '')
        # The FRP verifier fetches discovery/JWKS directly over loopback. The
        # edge always overwrites the surface header, so public callers cannot
        # use this exception to reach any internal management route.
        if not surface and path in (OIDC_PREFIX + '/.well-known/openid-configuration', OIDC_PREFIX + '/jwks', OIDC_PREFIX + '/token'):
            surface = 'internal'
        if surface not in ('public', 'admin', 'internal'):
            raise APIError(403, '请通过正式入口访问')
        if path == '/health' and surface == 'internal' and self.command in ('GET', 'HEAD'):
            return self.reply(200, {'ok': True, 'version': VERSION})
        if path.startswith(ADMIN_PREFIX + '/'):
            if surface != 'admin':
                raise APIError(404, 'not_found')
            return self.admin(path)
        if surface not in ('public', 'internal'):
            raise APIError(404, 'not_found')
        if self.command in ('GET', 'HEAD'):
            assets = {'/relay-install/install.ps1': (ROOT / 'windows' / 'install.ps1', 'text/plain; charset=utf-8'),
                      '/relay-install/bundle.zip': (None, 'application/zip')}
            if path in assets:
                file, ctype = assets[path]
                if file is None:
                    file = Path(app.settings['bundle_file'])
                self.send_response(200)
                for key, value in {'Content-Type': ctype, 'Content-Length': str(file.stat().st_size),
                                   'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}.items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command != 'HEAD':
                    with file.open('rb') as stream:
                        while True:
                            chunk = stream.read(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                return
            if path == OIDC_PREFIX + '/.well-known/openid-configuration':
                issuer = app.tokens.issuer
                return self.reply(200, {'issuer': issuer, 'jwks_uri': issuer + '/jwks',
                                       'token_endpoint': app.settings['origin'] + OIDC_PREFIX + '/token',
                                       'response_types_supported': ['token'], 'subject_types_supported': ['public'],
                                       'id_token_signing_alg_values_supported': ['RS256'],
                                       'grant_types_supported': ['client_credentials'],
                                       'token_endpoint_auth_methods_supported': ['client_secret_basic', 'client_secret_post']})
            if path == OIDC_PREFIX + '/jwks':
                return self.reply(200, app.tokens.jwks)
            if path == PUBLIC_PREFIX + '/config':
                return self.reply(200, app.config(self.device()))
            if path == PUBLIC_PREFIX + '/status':
                return self.reply(200, app.store.public_device(self.device(enabled=False)))
        if self.command == 'POST' and path == PUBLIC_PREFIX + '/enroll':
            peer = clean(self.headers.get('X-Jira-Client-IP', 'loopback'), 64)
            if not app.limiter.take('enroll:' + peer):
                raise APIError(429, '尝试次数较多，请稍后重试')
            data = self.body()
            row = app.store.enroll(data.get('code'), data.get('device_id'), data.get('device_secret'), data.get('hostname'), data.get('version'))
            return self.reply(200, app.config(row))
        if self.command == 'POST' and path == OIDC_PREFIX + '/token':
            data = self.body(form=True)
            if data.get('grant_type') != 'client_credentials' or data.get('audience', AUDIENCE) != AUDIENCE:
                raise APIError(400, 'unsupported_grant')
            ident, secret = data.get('client_id', ''), data.get('client_secret', '')
            header = self.headers.get('Authorization', '')
            if header.startswith('Basic '):
                try:
                    ident, secret = base64.b64decode(header[6:], validate=True).decode().split(':', 1)
                    ident, secret = urllib.parse.unquote(ident), urllib.parse.unquote(secret)
                except (ValueError, UnicodeError):
                    raise APIError(401, 'invalid_client') from None
            peer = clean(self.headers.get('X-Jira-Client-IP', 'loopback'), 64)
            if not app.limiter.take('token:' + peer + ':' + str(ident), 30):
                raise APIError(429, 'rate_limited')
            device = app.store.authenticate(ident, secret)
            return self.reply(200, {'access_token': app.tokens.issue(device), 'token_type': 'Bearer', 'expires_in': 120})
        raise APIError(404, '未找到页面')

    def admin(self, path):
        app = self.server.app
        if self.headers.get('Sec-Fetch-Site') in ('same-site', 'cross-site') and not (
                self.headers.get('Sec-Fetch-Mode') == 'navigate' and path == ADMIN_PREFIX + '/'):
            raise APIError(403, '请直接打开管理员页面')
        status, user = app.authenticate(self.headers.get('Cookie', ''))
        if status != 200:
            if path == ADMIN_PREFIX + '/' and self.command == 'GET' and status == 401:
                return self.reply(200, (ROOT / 'web' / 'login.html').read_bytes(), 'text/html; charset=utf-8')
            raise APIError(status, {401: '请先登录工作台管理员账号', 403: '仅管理员可以管理转发电脑', 503: '账号验证暂时不可用'}[status])
        if self.command in ('GET', 'HEAD'):
            if path == ADMIN_PREFIX + '/api/report':
                return self.reply(200, {**app.store.report(), 'admin': clean(user.get('display_name') or user['username'])})
            assets = {ADMIN_PREFIX + '/': ('index.html', 'text/html'),
                      ADMIN_PREFIX + '/app.js': ('app.js', 'text/javascript'),
                      ADMIN_PREFIX + '/style.css': ('style.css', 'text/css')}
            if path in assets:
                file, ctype = assets[path]
                return self.reply(200, (ROOT / 'web' / file).read_bytes(), ctype + '; charset=utf-8')
        if self.command == 'POST':
            if (self.headers.get('Origin') != app.settings['admin_origin']
                    or self.headers.get('X-Jira-Relay-Admin') != '1'):
                raise APIError(403, '请从管理员页面操作')
            data = self.body()
            actor = user['username']
            if path == ADMIN_PREFIX + '/api/commands':
                invitation = app.store.create_code(data.get('label'), data.get('hours', 24), actor)
                return self.reply(200, {k: v for k, v in {**invitation, 'command': app.command(invitation)}.items() if k != 'code'})
            ident = data.get('id', '')
            if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
                raise APIError(400, '设备或命令编号无效')
            if path == ADMIN_PREFIX + '/api/revoke-command':
                app.store.revoke_code(ident, actor)
            elif path == ADMIN_PREFIX + '/api/disable-device':
                app.store.disable(ident, actor)
            elif path == ADMIN_PREFIX + '/api/reauthorize':
                invitation = app.store.reauthorize(ident, actor)
                return self.reply(200, {k: v for k, v in {**invitation, 'command': app.command(invitation)}.items() if k != 'code'})
            else:
                raise APIError(404, '未找到操作')
            return self.reply(200, {'ok': True})
        raise APIError(404, '未找到页面')

    def dispatch(self):
        try:
            if self.server.hooks:
                if self.client_address[0] not in ('127.0.0.1', '::1') or self.command != 'POST':
                    raise APIError(403, 'not_allowed')
                path = urllib.parse.urlsplit(self.path)
                args = urllib.parse.parse_qs(path.query)
                if path.path != '/frp-hook' or args.get('version') != ['0.1.0']:
                    raise APIError(403, 'invalid_protocol')
                result = self.server.app.hook(args.get('op', [''])[0], self.body().get('content'))
                return self.reply(200, {'reject': False, **result})
            self.serve()
        except APIError as exc:
            if self.server.hooks:
                self.reply(200, {'reject': True, 'reject_reason': 'device_authorization_denied'})
            else:
                self.reply(exc.status, {'detail': exc.detail})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            if self.server.hooks:
                self.reply(200, {'reject': True, 'reject_reason': 'authorization_unavailable'})
            else:
                self.reply(503, {'detail': '服务暂时不可用，请稍后重试'})

    do_GET = dispatch
    do_HEAD = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch


class BoundedServer(ThreadingHTTPServer):
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_server(app, port=18110, hooks=False):
    server = BoundedServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.app, server.hooks = app, hooks
    return server


def load_settings(path):
    settings = json.loads(Path(path).read_text())
    if not re.fullmatch(r'https://(?:[A-Za-z0-9.-]+)(?::\d{1,5})?', settings['origin']):
        raise ValueError('invalid HTTPS origin')
    if not re.fullmatch(r'https://(?:[A-Za-z0-9.-]+)(?::\d{1,5})?', settings['admin_origin']):
        raise ValueError('invalid admin origin')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', settings['server_host']):
        raise ValueError('invalid relay host')
    if not re.fullmatch(r'[0-9a-f]{64}', settings['bundle_sha256']):
        raise ValueError('invalid bundle digest')
    if settings['bundle'] != settings['origin'] + '/relay-install/bundle.zip':
        raise ValueError('invalid bundle location')
    return settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', default='/etc/jira-relay/settings.json')
    args = parser.parse_args()
    settings = load_settings(args.settings)
    store = Store(settings.get('database', '/var/lib/jira-relay/relay.db'))
    tokens = Tokens(settings['signing_key'], settings.get('issuer', settings['origin'] + OIDC_PREFIX))
    app = Application(settings, store, tokens)
    public = make_server(app, settings.get('http_port', 18110))
    hooks = make_server(app, settings.get('hook_port', 18111), True)
    threading.Thread(target=hooks.serve_forever, daemon=True).start()
    threading.Thread(target=app.health_loop, daemon=True).start()
    try:
        public.serve_forever()
    finally:
        app.stop.set()
        hooks.shutdown(); hooks.server_close(); public.server_close()


if __name__ == '__main__':
    main()
