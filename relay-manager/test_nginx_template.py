"""Exercise the Windows nginx template using real nginx and a local HTTPS Jira."""
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class Source(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.observed.append({'path': self.path, 'headers': dict(self.headers)})
        body = b'<meta name="ajs-version-number" content="SIMULATED">'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Set-Cookie', 'SIMULATED=session; Secure; HttpOnly')
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which('nginx'), 'real nginx unavailable')
class TemplateTests(unittest.TestCase):
    def test_health_strips_auth_and_regular_requests_preserve_host_cookie_and_origin(self):
        with tempfile.TemporaryDirectory(prefix='relay template spaces ') as temp:
            root = Path(temp)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            now = dt.datetime.now(dt.timezone.utc)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'jira.example.internal')])
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(days=1))
                    .not_valid_after(now+dt.timedelta(days=1))
                    .add_extension(x509.SubjectAlternativeName([x509.DNSName('jira.example.internal')]),False)
                    .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
            pem = cert.public_bytes(serialization.Encoding.PEM)
            (root/'windows-roots.pem').write_bytes(pem)
            (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            source = ThreadingHTTPServer(('127.0.0.1',0),Source)
            source.observed = []
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(root/'windows-roots.pem',root/'key.pem')
            source.socket = tls.wrap_socket(source.socket,server_side=True)
            threading.Thread(target=source.serve_forever,daemon=True).start()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            (root/'conf').mkdir();(root/'conf/mime.types').write_text('types {}')
            (root/'logs').mkdir()
            text = (Path(__file__).parent/'windows/nginx.conf.template').read_text()
            for before,after in {'@LOCAL_PORT@':str(port),'@DEVICE_ID@':'a'*32,'@DATA_ROOT@':str(root),
                                 '@NGINX_ROOT@':str(root),'@PUBLIC_ORIGIN@':'https://203.0.113.10',
                                 '@ORIGIN_REGEX@':r'https://203\.0\.113\.10',
                                 'server jira.example.internal:443;':f'server 127.0.0.1:{source.server_port};'}.items():
                text=text.replace(before,after)
            config=root/'nginx.conf';config.write_text(text)
            result=subprocess.run(['nginx','-t','-p',str(root)+'/', '-c',str(config)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            process=subprocess.Popen(['nginx','-p',str(root)+'/', '-c',str(config),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                url=f'http://127.0.0.1:{port}'
                deadline=time.monotonic()+5
                while True:
                    try:
                        with opener.open(url+'/__jira_relay_health',timeout=1) as response:
                            self.assertEqual(response.headers['X-Jira-Relay-Device'],'a'*32)
                            response.read()
                        break
                    except OSError:
                        if time.monotonic()>deadline:raise
                        time.sleep(.1)
                request=urllib.request.Request(url+'/__jira_relay_health',headers={'Authorization':'Bearer SIMULATED','Cookie':'SIMULATED=session'})
                with opener.open(request,timeout=3) as response: response.read()
                health=source.observed[-1]
                self.assertEqual(health['path'],'/login.jsp')
                self.assertEqual(health['headers']['Host'],'jira.example.internal')
                self.assertNotIn('Authorization',health['headers']);self.assertNotIn('Cookie',health['headers'])
                request=urllib.request.Request(url+'/browse/TEST-1?x=2',headers={
                    'Authorization':'Bearer SIMULATED','Cookie':'SIMULATED=session','Origin':'https://203.0.113.10',
                    'Referer':'https://203.0.113.10/browse/TEST-1','Accept-Encoding':'gzip','Range':'bytes=0-12'})
                with opener.open(request,timeout=3) as response:
                    self.assertIn('Secure',response.headers['Set-Cookie']);response.read()
                headers=source.observed[-1]['headers']
                self.assertEqual(headers['Host'],'jira.example.internal')
                self.assertEqual(headers['Origin'],'https://jira.example.internal')
                self.assertEqual(headers['Referer'],'https://jira.example.internal/browse/TEST-1')
                self.assertEqual(headers['Authorization'],'Bearer SIMULATED')
                self.assertEqual(headers['Cookie'],'SIMULATED=session')
                self.assertEqual(headers['Accept-Encoding'],'gzip');self.assertEqual(headers['Range'],'bytes=0-12')
                self.assertEqual(source.observed[-1]['path'],'/browse/TEST-1?x=2')
            finally:
                process.terminate();process.wait(timeout=5);source.shutdown();source.server_close()
