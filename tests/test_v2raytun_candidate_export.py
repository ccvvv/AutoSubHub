import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


class V2RayTunCandidateExportTests(unittest.TestCase):
    def test_exports_subscription_and_node_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidates.yaml"
            manifest_path = root / "manifest.json"
            output = root / "out"
            proxies = [{
                "name": "test-ss",
                "type": "ss",
                "server": "example.com",
                "port": 443,
                "cipher": "aes-128-gcm",
                "password": "secret",
            }]
            raw = yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False).encode()
            candidate.write_bytes(raw)
            manifest_path.write_text(json.dumps({
                "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                "nodes": [{"name": "test-ss", "node_id": "node-123"}],
            }), encoding="utf-8")

            subprocess.run([
                sys.executable, "tools/build_v2raytun_candidate.py",
                "--candidate", str(candidate),
                "--manifest", str(manifest_path),
                "--output-dir", str(output),
            ], check=True)

            encoded = (output / "v2raytun-sub.txt").read_text(encoding="utf-8").strip()
            links = base64.b64decode(encoded).decode().strip().splitlines()
            self.assertEqual(len(links), 1)
            self.assertTrue(links[0].startswith("ss://"))
            mapping = json.loads((output / "v2raytun-map.json").read_text(encoding="utf-8"))
            self.assertEqual(mapping["nodes"][0]["node_id"], "node-123")
            self.assertEqual(mapping["exported_count"], 1)

    def test_rejects_manifest_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidates.yaml"
            manifest_path = root / "manifest.json"
            candidate.write_text("proxies: []\n", encoding="utf-8")
            manifest_path.write_text(json.dumps({
                "candidate_sha256": "wrong",
                "nodes": [],
            }), encoding="utf-8")
            result = subprocess.run([
                sys.executable, "tools/build_v2raytun_candidate.py",
                "--candidate", str(candidate),
                "--manifest", str(manifest_path),
                "--output-dir", str(root / "out"),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA256 mismatch", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
