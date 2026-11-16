import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from service import APIError, Application, Store, Tokens, make_server


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        (self.root / 'signing.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (self.root / 'ca.pem').write_text('TEST PUBLIC CERTIFICATE')
        (self.root / 'bundle.zip').write_bytes(b'test bundle')
        self.store = Store(self.root / 'relay.db', 18120, 18123)
        self.tokens = Tokens(self.root / 'signing.pem', 'http://127.0.0.1/relay-oidc')
        settings = {'origin': 'https://relay.example', 'admin_origin': 'https://relay.example:8090', 'server_host': 'relay.example',
                    'relay_ca': str(self.root / 'ca.pem'), 'bundle': 'https://relay.example/relay-install/bundle.zip',
                    'bundle_file': str(self.root / 'bundle.zip'), 'bundle_sha256': 'a' * 64}
        def authenticate(cookie):
            if cookie == 'admin=yes': return 200, {'username': 'owner', 'role': 'admin'}
            if cookie == 'member=yes': return 403, None
            return 401, None
        self.app = Application(settings, self.store, self.tokens, authenticate)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown(); server.server_close()
        self.temp.cleanup()

    def enrolled(self, invitation=None):
        invitation = invitation or self.store.create_code('测试电脑', 24, 'owner')
        ident, secret = secrets.token_hex(16), secrets.token_hex(32)
        row = self.store.enroll(invitation['code'], ident, secret, 'TEST-PC', '1.0.0')
        return invitation, row, secret

    def server(self, hooks=False):
        server = make_server(self.app, 0, hooks)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server

    def request(self, server, path, method='GET', body=None, headers=None):
        h = {'X-Jira-Relay-Surface': 'public', **(headers or {})}
        if isinstance(body, dict):
            body = json.dumps(body)
            h.setdefault('Content-Type', 'application/json')
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        connection.request(method, path, body, h)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_two_pcs_racing_only_one_redeems(self):
        invite = self.store.create_code('共享命令', 24, 'owner')
        def redeem(_):
            try:
                self.enrolled(invite)
                return 200
            except APIError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(redeem, range(2))), [200, 409])

    def test_resume_after_lost_response_uses_same_device(self):
        invite, row, secret = self.enrolled()
        retry = self.store.enroll(invite['code'], row['id'], secret, 'renamed', '1.0.0')
        self.assertEqual(retry['port'], row['port'])
        self.assertEqual(len(self.store.report()['devices']), 1)
        with self.assertRaises(APIError):
            self.store.enroll(invite['code'], row['id'], secrets.token_hex(32), 'other', '1.0.0')

    def test_expired_revoked_and_disabled_credentials(self):
        invite = self.store.create_code('expired', 1, 'owner')
        with self.store.connect() as db:
            db.execute('UPDATE enrollments SET expires=0 WHERE id=?', (invite['id'],))
        with self.assertRaises(APIError): self.enrolled(invite)
        invite = self.store.create_code('revoked', 24, 'owner')
        self.store.revoke_code(invite['id'], 'owner')
        with self.assertRaises(APIError): self.enrolled(invite)
        invite, row, secret = self.enrolled()
        self.store.disable(row['id'], 'owner')
        with self.assertRaises(APIError): self.store.authenticate(row['id'], secret)
        new = self.store.reauthorize(row['id'], 'owner')
        self.assertNotEqual(new['code'], invite['code'])
        with self.assertRaises(APIError): self.store.authenticate(row['id'], secret)

    def test_secret_not_persisted_or_reported_and_ports_not_recycled(self):
        invite, row, secret = self.enrolled()
        report = json.dumps(self.store.report())
        with self.store.connect() as db:
            all_values = str([tuple(r) for r in db.execute('SELECT * FROM devices')]) + str([tuple(r) for r in db.execute('SELECT * FROM enrollments')])
        for text in (report, all_values):
            self.assertNotIn(secret, text)
            self.assertNotIn(invite['code'], text)
        self.store.disable(row['id'], 'owner')
        _, second, _ = self.enrolled()
        self.assertNotEqual(row['port'], second['port'])

    def test_port_capacity_is_explicit(self):
        for _ in range(4): self.enrolled()
        with self.assertRaises(APIError) as error: self.enrolled()
        self.assertEqual(error.exception.status, 503)

    def test_retired_ports_recycled_only_after_grace_and_listener_closed(self):
        rows = [self.enrolled()[1] for _ in range(4)]
        first = rows[0]
        self.store.disable(first['id'], 'owner')
        with self.assertRaises(APIError): self.enrolled()
        with self.store.connect() as db:
            db.execute('UPDATE devices SET disabled_at=? WHERE id=?', (time.time()-61, first['id']))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', first['port'])); listener.listen()
            with self.assertRaises(APIError): self.enrolled()
        _, replacement, _ = self.enrolled()
        self.assertEqual(replacement['port'], first['port'])
        self.assertFalse(next(d for d in self.store.report()['devices'] if d['id']==first['id'])['enabled'])

    def test_signed_token_is_bound_to_device_and_cannot_be_forged(self):
        _, row, _ = self.enrolled()
        token = self.tokens.issue(row)
        self.tokens.verify(token, row['id'])
        with self.assertRaises(APIError): self.tokens.verify(token, secrets.token_hex(16))
        a, b, c = token.split('.')
        payload = json.loads(base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)))
        payload['exp'] += 3600
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
        with self.assertRaises(APIError): self.tokens.verify('.'.join((a, forged, c)), row['id'])

    def test_hooks_enforce_port_proxy_identity_and_disable_even_with_valid_token(self):
        _, row, _ = self.enrolled()
        token = self.tokens.issue(row)
        result = self.app.hook('Login', {'user': row['id'], 'privilege_key': token})
        user = {'user': row['id'], 'run_id': result['content']['run_id']}
        proxy = {'user': user, 'proxy_name': row['id'] + '.jira', 'proxy_type': 'tcp', 'remote_port': row['port']}
        self.assertTrue(self.app.hook('NewProxy', proxy)['unchange'])
        for changes in ({'remote_port': 22}, {'proxy_type': 'http'}, {'proxy_name': 'another.jira'}, {'group': 'steal'}, {'custom_domains': ['x']}):
            with self.assertRaises(APIError): self.app.hook('NewProxy', {**proxy, **changes})
        self.app.hook('Ping', {'user': user, 'privilege_key': token})
        self.app.hook('NewWorkConn', {'user': user, 'privilege_key': token})
        self.app.hook('NewUserConn', {'user': user, 'proxy_name': row['id'] + '.jira', 'proxy_type': 'tcp'})
        self.store.disable(row['id'], 'owner')
        for op, content in [('Login', {'user': row['id'], 'privilege_key': token}), ('Ping', {'user': user, 'privilege_key': token}), ('NewProxy', proxy), ('NewUserConn', proxy)]:
            with self.assertRaises(APIError): self.app.hook(op, content)

    def test_old_session_and_stale_health_cannot_revive_device(self):
        _, row, _ = self.enrolled()
        first = self.app.hook('Login', {'user': row['id'], 'privilege_key': self.tokens.issue(row)})
        old = self.store.get(row['id'])
        second = self.app.hook('Login', {'user': row['id'], 'privilege_key': self.tokens.issue(row)})
        self.assertNotEqual(first['content']['run_id'], second['content']['run_id'])
        self.store.record_health(row['id'], old['generation'], True, 'stale')
        self.assertFalse(self.store.get(row['id'])['health_ok'])
        with self.assertRaises(APIError): self.app.hook('Ping', {'user': {'user': row['id'], 'run_id': first['content']['run_id']}, 'privilege_key': self.tokens.issue(row)})
        self.store.disable(row['id'], 'owner')
        self.store.record_health(row['id'], old['generation'], True, 'stale')
        self.assertEqual(self.store.report()['devices'][0]['status'], '已停用')

    def test_slow_device_does_not_block_rechecking_healthy_devices(self):
        slow = self.enrolled()[1]
        fast = self.enrolled()[1]
        release_slow, fast_rechecked = threading.Event(), threading.Event()
        counts = {slow['id']: 0, fast['id']: 0}
        self.store.login(slow['id'], 'slow-run')
        self.store.login(fast['id'], 'fast-run')
        def probe(row):
            counts[row['id']] += 1
            if row['id'] == slow['id']:
                release_slow.wait(3)
            elif counts[row['id']] >= 2:
                fast_rechecked.set()
        self.app.probe = probe
        worker = threading.Thread(target=lambda: self.app.health_loop(.03), daemon=True)
        worker.start()
        try:
            self.assertTrue(fast_rechecked.wait(1), 'healthy device was blocked behind a slow peer')
            self.assertEqual(counts[slow['id']], 1, 'overlapping probes for one device')
        finally:
            self.app.stop.set(); release_slow.set(); worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_admin_auth_surface_and_csrf(self):
        server = self.server()
        path = '/relay-admin/api/report'
        self.assertEqual(self.request(server, path)[0], 404)
        h = {'X-Jira-Relay-Surface': 'admin'}
        self.assertEqual(self.request(server, path, headers=h)[0], 401)
        h['Cookie'] = 'member=yes'
        self.assertEqual(self.request(server, path, headers=h)[0], 403)
        h['Cookie'] = 'admin=yes'
        self.assertEqual(self.request(server, path, headers=h)[0], 200)
        self.assertEqual(self.request(server, '/relay-admin/api/commands', 'POST', {'label': 'test'}, h)[0], 403)
        h.update({'Origin': self.app.settings['admin_origin'], 'X-Jira-Relay-Admin': '1'})
        status, body, _ = self.request(server, '/relay-admin/api/commands', 'POST', {'label': 'test'}, h)
        self.assertEqual(status, 200)
        command = json.loads(body)['command']
        decoded = base64.b64decode(command.split()[-1]).decode('utf-16le')
        self.assertIn('-ExpectedScriptHash', decoded)
        self.assertNotIn('clientSecret', decoded)
        self.assertNotIn('code', json.loads(body))

    def test_config_requires_credential_and_hook_cannot_be_reached_on_public_listener(self):
        server = self.server()
        self.assertEqual(self.request(server, '/relay-api/v1/config')[0], 401)
        _, row, secret = self.enrolled()
        h = {'Authorization': 'Bearer ' + row['id'] + '.' + secret}
        self.assertEqual(self.request(server, '/relay-api/v1/config', headers=h)[0], 200)
        self.assertEqual(self.request(server, '/frp-hook?version=0.1.0&op=Login', 'POST', {'content': {}})[0], 400)
        self.store.disable(row['id'], 'owner')
        self.assertEqual(self.request(server, '/relay-api/v1/config', headers=h)[0], 403)
        status = json.loads(self.request(server, '/relay-api/v1/status', headers=h)[1])
        self.assertEqual(status['status'], '已停用')

    def test_oauth_basic_and_post_auth_and_revocation(self):
        server = self.server()
        _, row, secret = self.enrolled()
        body = 'grant_type=client_credentials&audience=jira-relay'
        h = {'Content-Type': 'application/x-www-form-urlencoded', 'Authorization': 'Basic ' + base64.b64encode((row['id'] + ':' + secret).encode()).decode()}
        status, raw, _ = self.request(server, '/relay-oidc/token', 'POST', body, h)
        self.assertEqual(status, 200)
        self.tokens.verify(json.loads(raw)['access_token'], row['id'])
        self.store.disable(row['id'], 'owner')
        self.assertEqual(self.request(server, '/relay-oidc/token', 'POST', body, h)[0], 403)

    def test_discovery_and_jwks_work_without_any_download_state(self):
        del self.app.settings['bundle_file']
        server = self.server()
        for path in ('/relay-oidc/.well-known/openid-configuration', '/relay-oidc/jwks'):
            self.assertEqual(self.request(server, path, headers={'X-Jira-Relay-Surface': ''})[0], 200)

    def test_hook_failure_is_fail_closed(self):
        server = self.server(True)
        status, raw, _ = self.request(server, '/frp-hook?version=0.1.0&op=Login', 'POST', {'content': {'user': secrets.token_hex(16), 'privilege_key': 'invalid'}})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)['reject'])
        self.assertEqual(self.request(server, '/relay-admin/api/report', headers={'Cookie': 'admin=yes'})[0], 200)
        self.assertTrue(json.loads(self.request(server, '/relay-admin/api/report')[1])['reject'])

    def test_untrusted_requests_never_choose_probe_destination(self):
        server = self.server()
        invite = self.store.create_code('test', 24, 'owner')
        payload = {'code': invite['code'], 'device_id': secrets.token_hex(16), 'device_secret': secrets.token_hex(32),
                   'hostname': 'test', 'version': '1.0', 'port': 22, 'url': 'http://metadata.internal'}
        status, raw, _ = self.request(server, '/relay-api/v1/enroll', 'POST', payload)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['remote_port'], 18120)


if __name__ == '__main__':
    unittest.main()
