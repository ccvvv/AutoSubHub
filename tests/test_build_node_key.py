import unittest

from build import node_key, parse_uri, parse_source, valid_reality_short_id


class BuildNodeIdentityTests(unittest.TestCase):
    def test_reality_short_id_validation(self):
        for valid in ("", "ab", "0123456789abcdef", "ABCDEF"):
            self.assertTrue(valid_reality_short_id(valid), valid)
        for invalid in ("abc", "0123456789abcdef0", "zz", "12 4"):
            self.assertFalse(valid_reality_short_id(invalid), invalid)

    def test_uri_with_invalid_reality_short_id_is_skipped(self):
        uri = ("vless://user-id@example.com:443?security=reality"
               "&type=tcp&pb=public-key&sid=abc#bad-reality")
        self.assertIsNone(parse_uri(uri))

    def test_uri_with_missing_reality_short_id_omits_empty_field(self):
        uri = ("vless://user-id@example.com:443?security=reality"
               "&type=tcp&pb=public-key#no-short-id")
        node = parse_uri(uri)
        self.assertIsNotNone(node)
        self.assertNotIn("short-id", node["reality-opts"])

    def test_yaml_numeric_reality_short_id_is_normalized_to_string(self):
        source = """proxies:
  - name: numeric-short-id
    type: vless
    server: example.com
    port: 443
    uuid: user-id
    tls: true
    reality-opts:
      public-key: public-key
      short-id: 1234
"""
        nodes, _ = parse_source(source)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["reality-opts"]["short-id"], "1234")
        self.assertIsInstance(nodes[0]["reality-opts"]["short-id"], str)

    def test_yaml_with_empty_reality_short_id_omits_empty_field(self):
        source = """proxies:
  - name: empty-short-id
    type: vless
    server: example.com
    port: 443
    uuid: user-id
    tls: true
    reality-opts:
      public-key: public-key
      short-id: ""
"""
        nodes, _ = parse_source(source)
        self.assertEqual(len(nodes), 1)
        self.assertNotIn("short-id", nodes[0]["reality-opts"])

    def test_yaml_with_invalid_reality_short_id_is_skipped(self):
        source = """proxies:
  - name: bad-reality
    type: vless
    server: example.com
    port: 443
    uuid: user-id
    tls: true
    reality-opts:
      public-key: public-key
      short-id: abc
  - name: good-reality
    type: vless
    server: example.org
    port: 443
    uuid: user-id
    tls: true
    reality-opts:
      public-key: public-key
      short-id: ab12
"""
        nodes, _ = parse_source(source)
        self.assertEqual([node["name"] for node in nodes], ["good-reality"])
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
