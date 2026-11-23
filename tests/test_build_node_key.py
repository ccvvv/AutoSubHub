import unittest

from build import node_key


class BuildNodeIdentityTests(unittest.TestCase):
    def test_display_name_does_not_change_identity(self):
        first = {
            "name": "node-old",
            "type": "vless",
            "server": "example.com",
            "port": 443,
            "uuid": "user-id",
            "tls": True,
            "servername": "cdn.example.com",
            "network": "ws",
            "ws-opts": {"path": "/edge", "headers": {"Host": "cdn.example.com"}},
        }
        renamed = dict(first, name="node-new")
        self.assertEqual(node_key(first), node_key(renamed))

    def test_tls_reality_and_transport_options_are_part_of_identity(self):
        base = {
            "name": "same-name",
            "type": "vless",
            "server": "example.com",
            "port": 443,
            "uuid": "user-id",
            "tls": True,
            "servername": "cdn.example.com",
            "network": "ws",
            "ws-opts": {"path": "/edge"},
        }
        reality = dict(base, **{"reality-opts": {"public-key": "different-key"}})
        other_path = dict(base, **{"ws-opts": {"path": "/other"}})
        other_sni = dict(base, servername="other.example.com")
        self.assertNotEqual(node_key(base), node_key(reality))
        self.assertNotEqual(node_key(base), node_key(other_path))
        self.assertNotEqual(node_key(base), node_key(other_sni))

    def test_different_authentication_is_not_merged(self):
        first = {
            "name": "same",
            "type": "ss",
            "server": "example.com",
            "port": 8388,
            "cipher": "aes-128-gcm",
            "password": "first",
        }
        second = dict(first, password="second")
        self.assertNotEqual(node_key(first), node_key(second))


if __name__ == "__main__":
    unittest.main()
