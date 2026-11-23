import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class V2RayTunSurvivorTests(unittest.TestCase):
    def test_unmatched_export_is_unresolved_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            map_path = root / "v2raytun-import.map.json"
            links_path = root / "v2raytun-import.links.txt"
            input_path = root / "export.txt"
            metadata_path = root / "candidate-local.meta.json"
            output_path = root / "survivors.json"

            map_path.write_text(json.dumps({
                "candidate_sha256": "a" * 64,
                "nodes": [{"name": "good-node", "node_id": "id-good", "line": 1}],
            }), encoding="utf-8")
            links_path.write_text("vless://uuid@example.com:443#good-node\n", encoding="utf-8")
            input_path.write_text(
                "vless://uuid@example.com:443#good-node\n"
                "trojan://unknown@example.net:443#not-in-batch\n",
                encoding="utf-8",
            )
            metadata_path.write_text(json.dumps({
                "candidate_commit": "b" * 40,
                "candidate_commits": ["b" * 40],
                "candidate_batch_count": 1,
            }), encoding="utf-8")

            subprocess.run([
                sys.executable, "tools/collect_v2raytun_survivors.py",
                "--map", str(map_path),
                "--input", str(input_path),
                "--metadata", str(metadata_path),
                "--output", str(output_path),
                "--confirm-invalid-removed",
            ], check=True)

            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["matched_survivor_count"], 1)
            self.assertEqual(report["results"][0]["node_id"], "id-good")
            self.assertEqual(report["results"][0]["status"], "passed")
            self.assertEqual(report["unresolved_count"], 1)
            self.assertNotIn("failed", [x.get("status") for x in report["unresolved"]])


if __name__ == "__main__":
    unittest.main()
