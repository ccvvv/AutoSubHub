import unittest

from build import exclude_final_nodes, node_key


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

    def test_keeps_nodes_not_in_final_list(self):
        node = {"name": "new", "type": "ss", "server": "new.example", "port": 8388}
        self.assertEqual(exclude_final_nodes([node], set()), [node])


if __name__ == "__main__":
    unittest.main()
