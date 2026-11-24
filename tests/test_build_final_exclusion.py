import unittest

from build import exclude_final_nodes, final_node_fingerprints, node_key


class FinalNodeExclusionTests(unittest.TestCase):
    def test_excludes_final_node_even_when_display_name_changes(self):
        upstream = {
            "name": "fresh-source-name",
            "type": "vless",
            "server": "same.example",
            "port": 443,
            "uuid": "same-user",
            "tls": True,
            "servername": "cdn.example",
        }
        published = dict(upstream, name="official-name [高速]")
        remaining = exclude_final_nodes([upstream], {node_key(published)})
        self.assertEqual(remaining, [])

    def test_keeps_node_when_any_configuration_field_differs(self):
        upstream = {
            "name": "same-name",
            "type": "vless",
            "server": "same.example",
            "port": 443,
            "uuid": "same-user",
            "tls": True,
            "servername": "new-cdn.example",
        }
        published = dict(upstream, servername="old-cdn.example")
        remaining = exclude_final_nodes([upstream], {node_key(published)})
        self.assertEqual(remaining, [upstream])

    def test_ignores_proxy_groups_and_keeps_only_valid_proxy_nodes(self):
        real_node = {
            "name": "official",
            "type": "vless",
            "server": "official.example",
            "port": 443,
            "uuid": "user",
        }
        data = {
            "proxies": [
                real_node,
                {"name": "AUTO", "type": "url-test", "proxies": ["official"]},
                {"name": "Manual", "type": "select", "proxies": ["AUTO"]},
                {"name": "broken", "type": "vless", "server": "bad.example", "port": "443"},
            ]
        }
        self.assertEqual(final_node_fingerprints(data), {node_key(real_node)})

    def test_rejects_final_subscription_without_real_proxy_nodes(self):
        data = {"proxies": [
            {"name": "AUTO", "type": "url-test", "proxies": ["DIRECT"]},
        ]}
        with self.assertRaisesRegex(RuntimeError, "no valid proxy nodes"):
            final_node_fingerprints(data)

    def test_keeps_nodes_not_in_final_list(self):
        node = {"name": "new", "type": "ss", "server": "new.example", "port": 8388}
        self.assertEqual(exclude_final_nodes([node], set()), [node])


if __name__ == "__main__":
    unittest.main()
