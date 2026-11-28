import unittest

from scripts.source_snapshots import (
    apply_source_response, reconcile_sources, node_fingerprint,
    build_queue_from_pool, apply_completed_receipt,
)


def proxy(name, server, port=443):
    return {"name": name, "type": "ss", "server": server, "port": port,
            "cipher": "aes-128-gcm", "password": "test"}


class SourceSnapshotTests(unittest.TestCase):
    def test_304_reuses_its_own_previous_snapshot(self):
        old, _ = apply_source_response(None, "source-001", "https://feed.example/a",
                                       200, [proxy("a", "a.example")])
        kept, replaced = apply_source_response(old, "source-001", "https://feed.example/a",
                                               304, None)
        self.assertFalse(replaced)
        self.assertEqual(kept["nodes"], old["nodes"])
        self.assertEqual(kept["last_status"], 304)

    def test_changed_source_url_cannot_reuse_old_304_snapshot(self):
        old, _ = apply_source_response(None, "source-001", "https://feed.example/old",
                                       200, [proxy("a", "a.example")])
        with self.assertRaises(ValueError):
            apply_source_response(old, "source-001", "https://feed.example/new", 304)

    def test_failed_or_empty_response_does_not_replace_previous_snapshot(self):
        old, _ = apply_source_response(None, "source-001", "https://feed.example/a",
                                       200, [proxy("a", "a.example")])
        for status, nodes in ((500, None), (200, []), (200, [{"name": "broken"}])):
            with self.assertRaises(ValueError):
                apply_source_response(old, "source-001", "https://feed.example/a",
                                      status, nodes)
        self.assertEqual(old["nodes"][0]["server"], "a.example")

    def test_one_source_update_and_two_304_keep_all_three_groups(self):
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1",
                                     200, [proxy("a", "a.example")])
        b, _ = apply_source_response(None, "source-002", "https://feed.example/2",
                                     200, [proxy("b", "b.example")])
        c, _ = apply_source_response(None, "source-003", "https://feed.example/3",
                                     200, [proxy("c", "c.example")])
        a2, changed = apply_source_response(a, "source-001", "https://feed.example/1",
                                            200, [proxy("a-new", "new.example")])
        b2, _ = apply_source_response(b, "source-002", "https://feed.example/2", 304)
        c2, _ = apply_source_response(c, "source-003", "https://feed.example/3", 304)
        self.assertTrue(changed)
        pool, queue, _ = reconcile_sources(
            {"source-001": a2, "source-002": b2, "source-003": c2},
            generated_at="2026-01-01T00:00:00Z")
        self.assertEqual({p["server"] for p in pool["proxies"]},
                         {"new.example", "b.example", "c.example"})
        self.assertEqual(len(queue["nodes"]), 3)

    def test_same_node_from_multiple_sources_is_deduplicated_with_provenance(self):
        shared1 = proxy("first-name", "shared.example")
        shared2 = proxy("renamed", "shared.example")
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1", 200, [shared1])
        b, _ = apply_source_response(None, "source-002", "https://feed.example/2", 200, [shared2])
        pool, queue, _ = reconcile_sources({"source-001": a, "source-002": b})
        self.assertEqual(len(pool["proxies"]), 1)
        self.assertEqual(len(queue["nodes"]), 1)
        self.assertEqual(pool["nodes"][0]["sources"], ["source-001", "source-002"])

    def test_node_remains_if_any_source_still_contains_it(self):
        shared = proxy("shared", "shared.example")
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1", 200, [shared])
        b, _ = apply_source_response(None, "source-002", "https://feed.example/2", 200, [shared])
        pool1, _, state = reconcile_sources({"source-001": a, "source-002": b})
        state["nodes"][node_fingerprint(shared)]["status"] = "completed"
        a_empty_replacement, _ = apply_source_response(a, "source-001", "https://feed.example/1",
                                                        200, [proxy("other", "other.example")])
        pool2, queue2, state2 = reconcile_sources(
            {"source-001": a_empty_replacement, "source-002": b}, state)
        self.assertEqual(len(pool1["proxies"]), 1)
        self.assertEqual({p["server"] for p in pool2["proxies"]}, {"shared.example", "other.example"})
        self.assertEqual(len(queue2["nodes"]), 1)
        self.assertIn(node_fingerprint(shared), state2["nodes"])

    def test_node_removed_from_all_sources_leaves_pool_but_completed_history_remains(self):
        node = proxy("a", "a.example")
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1", 200, [node])
        pool, queue, state = reconcile_sources({"source-001": a})
        fp = node_fingerprint(node)
        state["nodes"][fp]["status"] = "completed"
        replacement, _ = apply_source_response(a, "source-001", "https://feed.example/1",
                                                200, [proxy("b", "b.example")])
        pool2, queue2, state2 = reconcile_sources({"source-001": replacement}, state)
        self.assertEqual({p["server"] for p in pool2["proxies"]}, {"b.example"})
        self.assertEqual(len(queue2["nodes"]), 1)
        self.assertEqual(state2["nodes"][fp]["status"], "completed")

    def test_old_node_does_not_reenter_queue_on_unchanged_configuration(self):
        node = proxy("a", "a.example")
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1", 200, [node])
        _, queue1, state = reconcile_sources({"source-001": a})
        state["nodes"][node_fingerprint(node)]["status"] = "completed"
        updated, _ = apply_source_response(a, "source-001", "https://feed.example/1",
                                           200, [proxy("renamed", "a.example"), proxy("new", "b.example")])
        _, queue2, _ = reconcile_sources({"source-001": updated}, state)
        self.assertEqual(len(queue1["nodes"]), 1)
        self.assertEqual([item["name"] for item in queue2["nodes"]], ["new"])

    def test_configuration_change_creates_a_new_fingerprint_and_queue_item(self):
        old = proxy("node", "a.example")
        new = proxy("node", "a.example", port=8443)
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1", 200, [old])
        _, _, state = reconcile_sources({"source-001": a})
        state["nodes"][node_fingerprint(old)]["status"] = "completed"
        changed, _ = apply_source_response(a, "source-001", "https://feed.example/1", 200, [new])
        _, queue, _ = reconcile_sources({"source-001": changed}, state)
        self.assertEqual(len(queue["nodes"]), 1)
        self.assertEqual(queue["nodes"][0]["fingerprint"], node_fingerprint(new))


    def test_receipt_marks_passed_and_failed_as_completed_and_never_requeues(self):
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1",
                                     200, [proxy("a", "a.example"), proxy("b", "b.example")])
        pool, queue, state = reconcile_sources({"source-001": a})
        manifest = {"nodes": queue["nodes"]}
        receipt = {"task_id": "receipt-1", "results": [
            {"node_id": item["node_id"], "status": status}
            for item, status in zip(queue["nodes"], ("passed", "failed"))
        ]}
        completed = apply_completed_receipt(state, manifest, receipt)
        pending, _ = build_queue_from_pool(pool, completed)
        self.assertEqual(pending["nodes"], [])
        outcomes = {v["result"] for v in completed["nodes"].values()}
        self.assertEqual(outcomes, {"passed", "failed"})


    def test_passed_nodes_are_requeued_but_failed_nodes_are_removed(self):
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1",
                                     200, [proxy("a", "a.example"), proxy("b", "b.example")])
        pool, queue, state = reconcile_sources({"source-001": a})
        manifest = {"nodes": queue["nodes"]}
        receipt = {"task_id": "receipt-2", "results": [
            {"node_id": item["node_id"], "status": status}
            for item, status in zip(queue["nodes"], ("passed", "failed"))
        ]}
        completed = apply_completed_receipt(state, manifest, receipt)
        next_pool, next_queue, _ = reconcile_sources({"source-001": a}, completed)
        self.assertEqual({p["server"] for p in next_pool["proxies"]}, {"a.example"})
        self.assertEqual({p["server"] for p in next_queue["proxies"]}, {"a.example"})

    def test_failed_node_does_not_return_when_source_snapshot_is_unchanged(self):
        node = proxy("a", "a.example")
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1", 200, [node])
        _, queue, state = reconcile_sources({"source-001": a})
        receipt = {"task_id": "receipt-3", "results": [
            {"node_id": queue["nodes"][0]["node_id"], "status": "failed"}
        ]}
        failed_state = apply_completed_receipt(state, {"nodes": queue["nodes"]}, receipt)
        _, next_queue, _ = reconcile_sources({"source-001": a}, failed_state)
        self.assertEqual(next_queue["nodes"], [])

    def test_receipt_must_match_full_fingerprint_manifest_exactly(self):
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1",
                                     200, [proxy("a", "a.example")])
        _, queue, state = reconcile_sources({"source-001": a})
        with self.assertRaises(ValueError):
            apply_completed_receipt(state, {"nodes": queue["nodes"]},
                                    {"results": []})

    def test_precheck_failed_nodes_are_not_requeued(self):
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1",
                                     200, [proxy("a", "a.example")])
        pool, queue, state = reconcile_sources({"source-001": a})
        fp = queue["nodes"][0]["fingerprint"]
        state["nodes"][fp]["status"] = "precheck_failed"
        pending, _ = build_queue_from_pool(pool, state)
        self.assertEqual(pending["nodes"], [])


    def test_duplicate_receipt_is_idempotent_but_conflicting_receipt_is_rejected(self):
        a, _ = apply_source_response(None, "source-001", "https://feed.example/1",
                                     200, [proxy("a", "a.example")])
        _, queue, state = reconcile_sources({"source-001": a})
        manifest = {"nodes": queue["nodes"]}
        result = {"results": [{"node_id": queue["nodes"][0]["node_id"], "status": "passed"}]}
        first = apply_completed_receipt(state, manifest, {**result, "task_id": "same"})
        second = apply_completed_receipt(first, manifest, {**result, "task_id": "same"})
        self.assertEqual(first["nodes"], second["nodes"])
        with self.assertRaises(ValueError):
            apply_completed_receipt(first, manifest, {**result, "task_id": "different"})


if __name__ == "__main__":
    unittest.main()
