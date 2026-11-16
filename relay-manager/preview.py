#!/usr/bin/env python3
"""Loopback-only UI fixture. All records/commands are disposable simulation."""
import argparse
import datetime as dt
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from service import Application, BoundedServer, Handler, Store, Tokens


class PreviewHandler(Handler):
    def serve(self):
        public = self.path.startswith('/relay-start/')
        self.headers['X-Jira-Relay-Surface'] = 'public' if public else 'admin'
        if self.path in ('/relay-admin/', '/relay-start/'):
            file = 'install.html' if public else 'index.html'
            html = (Path(__file__).parent / 'web' / file).read_text()
            html = html.replace('data-mode="live"', 'data-mode="demo"')
            return self.reply(200, html.encode(), 'text/html; charset=utf-8')
        return super().serve()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, default=18328)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='relay-ui-preview-') as temp:
        root = Path(temp)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        (root / 'key.pem').write_bytes(pem)
        store = Store(root / 'preview.db')
        for label, host, enabled in [('研发同事 · 白班', 'TEST-DAY', True), ('测试同事 · 晚班', 'TEST-NIGHT', True), ('旧电脑', 'TEST-OLD', False)]:
            invitation = store.create_code(label, 24, 'preview')
            row = store.enroll(invitation['code'], secrets.token_hex(16), secrets.token_hex(32), host, '1.0.0')
            if enabled:
                store.login(row['id'], secrets.token_hex(16))
                latest = store.get(row['id'])
                store.record_health(row['id'], latest['generation'], True, '模拟：云端已确认此电脑可回源 Jira')
            else:
                store.disable(row['id'], 'preview')
        origin = f'http://127.0.0.1:{args.port}'
        app = Application({'origin': 'https://example.invalid', 'admin_origin': origin}, store,
                          Tokens(root / 'key.pem', origin), lambda cookie: (200, {'username': '本地预览', 'role': 'admin'}))
        server = BoundedServer(('127.0.0.1', args.port), PreviewHandler)
        server.app, server.hooks = app, False
        print(origin + '/relay-admin/', flush=True)
        def keep_simulated_online():
            while True:
                for row in store.probe_candidates():
                    store.heartbeat(row['id'], row['run_id'])
                    store.record_health(row['id'], row['generation'], True, '模拟：云端已确认此电脑可回源 Jira')
                time.sleep(5)
        threading.Thread(target=keep_simulated_online, daemon=True).start()
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
