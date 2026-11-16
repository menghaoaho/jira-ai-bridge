import unittest
from router import render


class RouterTests(unittest.TestCase):
    def test_legacy_only_until_new_devices_are_healthy(self):
        text = render([])
        self.assertIn('server 127.0.0.1:8082;', text)
        self.assertNotIn(' backup;', text)

    def test_two_managed_devices_share_traffic_and_legacy_is_backup(self):
        text = render([18121, 18120, 18121])
        self.assertEqual(text.count('server 127.0.0.1:18121 '), 1)
        self.assertIn('least_conn;', text)
        self.assertIn('server 127.0.0.1:8082 backup;', text)
        self.assertNotIn('non_idempotent', text)

    def test_database_values_cannot_inject_nginx_config_or_other_ports(self):
        for ports in ([22], [18119], [18200], ['18120; include /tmp/evil;'], [True]):
            with self.assertRaises((ValueError, TypeError)):
                render(ports)


if __name__ == '__main__':
    unittest.main()
