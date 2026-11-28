import unittest

from scripts.node_identity import node_fingerprint, validate_candidate_identity


class NodeIdentityTests(unittest.TestCase):
    def test_display_name_does_not_change_full_identity(self):
        first = {"name": "one", "type": "ss", "server": "example.com", "port": 443}
        renamed = {**first, "name": "renamed"}
        self.assertEqual(node_fingerprint(first), node_fingerprint(renamed))
        self.assertEqual(len(node_fingerprint(first)), 64)

    def test_phone_validation_requires_full_fingerprint_matching_config(self):
        proxy = {"name": "node-a", "type": "ss", "server": "example.com", "port": 443}
        fingerprint = node_fingerprint(proxy)
        manifest = {"nodes": [{
            "name": "node-a", "node_id": fingerprint, "fingerprint": fingerprint,
        }]}
        self.assertEqual(validate_candidate_identity({"proxies": [proxy]}, manifest)["node-a"], fingerprint)

    def test_rejects_short_or_forged_node_id(self):
        proxy = {"name": "node-a", "type": "ss", "server": "example.com", "port": 443}
        fingerprint = node_fingerprint(proxy)
        for node_id in (fingerprint[:20], "0" * 64):
            manifest = {"nodes": [{
                "name": "node-a", "node_id": node_id, "fingerprint": fingerprint,
            }]}
            with self.subTest(node_id=node_id), self.assertRaises(ValueError):
                validate_candidate_identity({"proxies": [proxy]}, manifest)


if __name__ == "__main__":
    unittest.main()
