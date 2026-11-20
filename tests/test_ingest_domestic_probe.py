import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import yaml


MODULE_PATH = Path(__file__).resolve().parents[1] / "ingest_domestic_probe.py"
SPEC = importlib.util.spec_from_file_location("ingest_domestic_probe", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

NODE_A = "a" * 24
NODE_B = "b" * 24


class IngestReceiptTests(unittest.TestCase):
    def setUp(self):
        self.previous_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        os.chdir(self.temp.name)
        Path("site").mkdir()
        manifest = {
            "schema_version": 1,
            "generated_at": "2026-11-20T10:00:00Z",
            "candidate_count": 2,
            "nodes": [
                {"id": NODE_A, "name": "Node A"},
                {"id": NODE_B, "name": "Node B"},
            ],
        }
        Path("site/candidate-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        profile = {
            "proxies": [
                {"name": f"probe_{NODE_A}__Node A", "type": "ss",
                 "server": "192.0.2.1", "port": 443, "cipher": "aes-128-gcm",
                 "password": "test"},
                {"name": f"probe_{NODE_B}__Node B", "type": "ss",
                 "server": "192.0.2.2", "port": 443, "cipher": "aes-128-gcm",
                 "password": "test"},
            ],
            "proxy-groups": [
                {"name": "AUTO", "type": "url-test",
                 "proxies": [f"probe_{NODE_A}__Node A", f"probe_{NODE_B}__Node B"]},
                {"name": "Manual", "type": "select",
                 "proxies": ["AUTO", f"probe_{NODE_A}__Node A",
                             f"probe_{NODE_B}__Node B", "DIRECT"]},
            ],
            "rules": ["MATCH,Manual"],
        }
        Path("site/candidates.yaml").write_text(
            yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8"
        )
        Path("site/index.html").write_text("<html></html>", encoding="utf-8")
        self.event = {
            "client_payload": {
                "candidate_revision": manifest["generated_at"],
                "device_id": "mi6-probe-01",
                "network": "mainland-wifi",
                "results": [
                    {"node_id": NODE_A, "status": "passed",
                     "speed_kbps": 1250.5, "latency_ms": 180},
                    {"node_id": NODE_B, "status": "failed",
                     "speed_kbps": 0, "latency_ms": None},
                ],
            }
        }
        Path("event.json").write_text(json.dumps(self.event), encoding="utf-8")
        self.env = contextlib.ExitStack()
        self.env.enter_context(
            unittest.mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": "event.json"})
        )

    def tearDown(self):
        self.env.close()
        os.chdir(self.previous_cwd)
        self.temp.cleanup()

    def run_ingest(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            MODULE.main()
        return output.getvalue()

    def test_publishes_only_passed_nodes_and_remaps_groups(self):
        output = self.run_ingest()
        self.assertIn("publish_final=true", output)
        final = yaml.safe_load(Path("site/subscription.yaml").read_text())
        self.assertEqual([p["name"] for p in final["proxies"]], ["Node A"])
        self.assertEqual(final["proxy-groups"][0]["proxies"], ["Node A"])
        self.assertEqual(
            final["proxy-groups"][1]["proxies"], ["AUTO", "Node A", "DIRECT"]
        )

    def test_rejects_stale_candidate_revision(self):
        self.event["client_payload"]["candidate_revision"] = "stale"
        Path("event.json").write_text(json.dumps(self.event), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "does not match"):
            self.run_ingest()

    def test_rejects_unknown_node_id(self):
        self.event["client_payload"]["results"][0]["node_id"] = "c" * 24
        Path("event.json").write_text(json.dumps(self.event), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "unknown node ID"):
            self.run_ingest()

    def test_rejects_passed_result_without_measured_speed(self):
        self.event["client_payload"]["results"][0]["speed_kbps"] = None
        Path("event.json").write_text(json.dumps(self.event), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "positive measured speed"):
            self.run_ingest()

    def test_no_pass_preserves_previous_final_subscription(self):
        previous = "previous-final-version"
        Path("site/subscription.yaml").write_text(previous, encoding="utf-8")
        self.event["client_payload"]["results"] = [
            {"node_id": NODE_A, "status": "failed", "speed_kbps": 0, "latency_ms": None},
            {"node_id": NODE_B, "status": "inconclusive", "speed_kbps": None, "latency_ms": None},
        ]
        Path("event.json").write_text(json.dumps(self.event), encoding="utf-8")
        output = self.run_ingest()
        self.assertIn("publish_final=false", output)
        self.assertEqual(Path("site/subscription.yaml").read_text(), previous)


if __name__ == "__main__":
    unittest.main()
