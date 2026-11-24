import os
import tempfile
import unittest

from build import node_key, parse_uri, parse_source, valid_reality_short_id, write_yaml


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

    def test_yaml_writer_quotes_scientific_notation_reality_short_id(self):
        config = {
            "proxies": [{
                "name": "scientific-short-id",
                "type": "vless",
                "reality-opts": {"short-id": "172066e9"},
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            write_yaml(path, config)
            with open(path, encoding="utf-8") as handle:
                output = handle.read()
        self.assertIn("short-id: '172066e9'", output)

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

    def test_yaml_with_non_mapping_reality_options_is_skipped(self):
        source = """proxies:
  - name: malformed-reality
    type: vless
    server: example.com
    port: 443
    uuid: user-id
    tls: true
    reality-opts: "not-a-map"
  - name: good-node
    type: vless
    server: example.org
    port: 443
    uuid: user-id
"""
        nodes, _ = parse_source(source)
        self.assertEqual([node["name"] for node in nodes], ["good-node"])

    def test_final_gate_rejects_non_mapping_reality_options(self):
        from build import valid_reality_node
        self.assertFalse(valid_reality_node({
            "name": "bad", "reality-opts": "invalid"
        }))

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
