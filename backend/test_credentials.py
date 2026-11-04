import json
import tempfile
import time
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import db as datastore
import rules
from management import create_deployment, update_deployment
from schemas import DeploymentInput


class CredentialSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = patch.object(datastore, "DB_PATH", Path(self.tmp.name) / "router.db")
        self.paths.start()
        datastore.init_db()
        self.body = DeploymentInput(
            name="rotation",
            alias="model",
            base_url="https://example.test/v1",
            upstream_model="model",
            api_keys=["key-a", "key-b", "key-c"],
        )
        self.saved = create_deployment(self.body)
        self.dep = rules.candidates("model")[0]
        self.set_cursor(0)

    def tearDown(self):
        self.paths.stop()
        self.tmp.cleanup()

    def set_cursor(self, value):
        with datastore.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                (f"key_cursor:{self.dep['id']}", str(value)),
            )

    def cool_keys(self, indices):
        with datastore.connect() as connection:
            connection.execute(
                "UPDATE deployment_state SET consecutive_failures=3 WHERE deployment_id=?",
                (self.dep["id"],),
            )
            for index in indices:
                connection.execute(
                    "INSERT OR REPLACE INTO credential_state VALUES(?,?,?,?,?)",
                    (self.dep["id"], index, time.time() + 3600, 3, datastore.now_iso()),
                )

    def test_selection_uses_one_connection_and_one_read_inside_rotation_transaction(self):
        statements = []
        original = datastore.connect

        def connect():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(rules, "connect", side_effect=connect) as opened:
            self.assertEqual(rules.choose_credential(self.dep), ("key-a", 0))
        self.assertEqual(opened.call_count, 1)
        reads = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(reads), 1)
        self.assertEqual(statements[0], "BEGIN IMMEDIATE")
        self.assertEqual(statements[-1], "COMMIT")
        self.assertEqual(rules.choose_credential(self.dep), ("key-b", 1))

    def test_selection_observes_rules_and_cooldowns_changed_after_candidate_snapshot(self):
        self.cool_keys([0])
        self.assertEqual(rules.choose_credential(self.dep), ("key-b", 1))
        # Disable rules without refreshing the original candidate object.
        with datastore.connect() as connection:
            connection.execute(
                "UPDATE settings SET value=? WHERE key='rule_settings'",
                (json.dumps({"enabled": False}),),
            )
        self.set_cursor(0)
        self.assertEqual(rules.choose_credential(self.dep), ("key-a", 0))

    def test_no_eligible_keys_does_not_advance_cursor_and_expired_key_recovers(self):
        self.cool_keys([0, 1, 2])
        with self.assertRaisesRegex(RuntimeError, "No available credentials"):
            rules.choose_credential(self.dep)
        with datastore.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM settings WHERE key=?", (f"key_cursor:{self.dep['id']}",)
                ).fetchone()[0],
                "0",
            )
            connection.execute(
                "UPDATE credential_state SET cooldown_until=0 WHERE deployment_id=? AND key_index=2",
                (self.dep["id"],),
            )
        self.assertEqual(rules.choose_credential(self.dep), ("key-c", 2))

    def test_concurrent_requests_rotate_without_lost_cursor_updates(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            selected = list(executor.map(lambda _: rules.choose_credential(self.dep)[1], range(36)))
        self.assertEqual(Counter(selected), {0: 12, 1: 12, 2: 12})

    def test_save_resets_rotation_and_old_results_cannot_restore_key_cooldown(self):
        old_revision = self.dep["config_revision"]
        self.cool_keys([0, 1, 2])
        self.set_cursor(2)
        updated = update_deployment(self.saved["id"], self.body)
        rules.record_result(
            updated["id"],
            False,
            401,
            key_index=0,
            config_revision=old_revision,
        )
        current = rules.candidates("model")[0]
        with patch.object(rules.random, "randrange", return_value=0):
            self.assertEqual(rules.choose_credential(current), ("key-a", 0))
        self.assertEqual(rules.available_key_indices(current["id"], 3), [0, 1, 2])
