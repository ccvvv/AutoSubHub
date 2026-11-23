import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from v2raytun_bridge import convert, load_verified


class V2RayTunBridgeTests(unittest.TestCase):
    def test_vless_reality_link(self):
        uri, reason = convert({
            "name": "test-vless", "type": "vless", "server": "example.com",
            "port": 443, "uuid": "11111111-1111-4111-8111-111111111111",
            "reality-opts": {"public-key": "pubkey", "short-id": "abcd"},
        })
        self.assertIsNone(reason)
        self.assertTrue(uri.startswith("vless://"))
        self.assertIn("security=reality", uri)
        self.assertIn("pb=pubkey", uri)

    def test_unsupported_protocol_is_reported(self):
        uri, reason = convert({"name": "h3", "type": "hysteria2", "server": "x", "port": 443})
        self.assertIsNone(uri)
        self.assertIn("unsupported", reason)

    def test_candidate_hash_and_node_id_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate-local.yaml"
            raw = yaml.safe_dump({"proxies": [{
                "name": "node-a", "type": "trojan", "server": "example.com",
                "port": 443, "password": "secret",
            }]}, allow_unicode=True).encode()
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            path.with_suffix(".manifest.json").write_text(json.dumps({
                "candidate_sha256": digest,
                "nodes": [{"name": "node-a", "node_id": "id-a"}],
            }), encoding="utf-8")
            proxies, ids, found = load_verified(path)
            self.assertEqual(len(proxies), 1)
            self.assertEqual(ids["node-a"], "id-a")
            self.assertEqual(found, digest)


if __name__ == "__main__":
    unittest.main()
