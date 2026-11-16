import unittest
from deploy import add_caddy_routes, change_upstreams


class DeployTests(unittest.TestCase):
    def test_additive_caddy_routes_preserve_existing_services_and_set_surface(self):
        original = '''https://203.0.113.10 {
    handle_path /jira-api/* { reverse_proxy 127.0.0.1:18093 }
    handle { reverse_proxy 127.0.0.1:18094 }
}
https://203.0.113.10:8090 {
    handle /access-dashboard/* { reverse_proxy 127.0.0.1:18096 }
    handle { import workbench_mac_priority }
}
notes.example { reverse_proxy 127.0.0.1:18787 }
'''
        changed = add_caddy_routes(original)
        for line in original.splitlines():
            self.assertIn(line, changed)
        self.assertIn('header_up X-Jira-Relay-Surface public', changed)
        self.assertIn('header_up X-Jira-Relay-Surface admin', changed)
        self.assertNotIn('/frp-hook', changed)
        self.assertNotIn('/.well-known/', changed)
        with self.assertRaises(RuntimeError):
            add_caddy_routes(changed)

    def test_upstream_change_retains_auth_uri_suffix_and_mitigations(self):
        original = '''if ($jira_bad_raw_uri) { return 403; }
auth_request /verify;
proxy_set_header Authorization $http_authorization;
proxy_pass http://127.0.0.1:8082/rest/api/2/myself;
proxy_pass http://127.0.0.1:8082;
proxy_pass http://127.0.0.1:8080;
'''
        changed = change_upstreams(original, 2)
        self.assertEqual(changed.replace('127.0.0.1:18112', '127.0.0.1:8082'), original)
        self.assertIn('18112/rest/api/2/myself;', changed)
        with self.assertRaises(RuntimeError):
            change_upstreams(original, 1)
