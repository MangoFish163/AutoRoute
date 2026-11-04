import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import app
import db as datastore
import gateway
import rules
import security
import upstream
from management import create_deployment
from schemas import DeploymentInput

Client = httpx.AsyncClient


class CredentialFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.paths = patch.multiple(
            datastore, DB_PATH=home / "router.db", BOOTSTRAP_KEY_PATH=home / "client.key"
        )
        self.paths.start()
        datastore.init_db()
        self.client = Client(
            transport=httpx.ASGITransport(app=app.app, raise_app_exceptions=False),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await upstream.client_pool.close()
        self.paths.stop()
        self.tmp.cleanup()

    async def request_after_cooldown(
        self, protocol, stream, exhausted=False, upstream_status=200, incompatible_first=False
    ):
        alias = f"{protocol}-{stream}-{exhausted}-{upstream_status}-{incompatible_first}"
        deployments = []
        for name in ("first", "backup"):
            deployments.append(
                create_deployment(
                    DeploymentInput(
                        name=name,
                        alias=alias,
                        provider="responses"
                        if incompatible_first and name == "first"
                        else protocol,
                        base_url=f"https://{name}.test/v1",
                        upstream_model="up",
                        api_keys=[f"{name}-a", f"{name}-b"],
                        use_system_proxy=False,
                    )
                )
            )
        selected = [dep["id"] for dep in deployments]

        def candidates_then_cool(model):
            snapshot = rules.candidates(model)
            # Reproduce the race with real SQLite state and real credential
            # selection, after candidates were read but before any selection.
            with datastore.connect() as connection:
                cooled = (
                    selected[1:] if incompatible_first else selected if exhausted else selected[:1]
                )
                for deployment_id in cooled:
                    connection.execute(
                        "UPDATE deployment_state SET consecutive_failures=3 WHERE deployment_id=?",
                        (deployment_id,),
                    )
                    for index in range(2):
                        connection.execute(
                            "INSERT INTO credential_state VALUES(?,?,?,?,?)",
                            (deployment_id, index, time.time() + 3600, 3, datastore.now_iso()),
                        )
            return sorted(snapshot, key=lambda dep: selected.index(dep["id"]))

        received = []

        def respond(request):
            received.append(request.url.host)
            if upstream_status != 200:
                return httpx.Response(upstream_status, text="upstream failure")
            if stream:
                return httpx.Response(
                    200,
                    content=b'data: {"text":"ok"}\n\n',
                    headers={"content-type": "text/event-stream"},
                )
            body = (
                {"content": [{"type": "text", "text": "ok"}]}
                if protocol == "anthropic"
                else {"choices": [{"message": {"content": "ok"}}]}
            )
            return httpx.Response(200, json=body)

        def factory(**_):
            return Client(transport=httpx.MockTransport(respond))

        with (
            patch.object(gateway, "candidates", side_effect=candidates_then_cool),
            patch.object(gateway, "weighted_order", side_effect=lambda pool: pool),
            patch.object(gateway, "record_result", wraps=rules.record_result) as record,
            patch.object(upstream.httpx, "AsyncClient", side_effect=factory),
        ):
            response = await self.client.post(
                "/v1/messages" if protocol == "anthropic" else "/v1/chat/completions",
                json={
                    "model": alias,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": stream,
                    "n": 2 if incompatible_first else 1,
                },
                headers={"Authorization": "Bearer " + security.client_key(), "x-request-id": alias},
            )
        with datastore.connect() as connection:
            logs = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM request_events WHERE request_id=?", (alias,)
                )
            ]
            first_state = dict(
                connection.execute(
                    "SELECT * FROM deployment_state WHERE deployment_id=?", (selected[0],)
                ).fetchone()
            )
        return response, logs, received, record, selected, first_state

    async def test_cooled_first_deployment_falls_back_and_logs_once(self):
        for protocol in ("openai", "anthropic"):
            for stream in (False, True):
                with self.subTest(protocol=protocol, stream=stream):
                    (
                        response,
                        logs,
                        received,
                        record,
                        selected,
                        state,
                    ) = await self.request_after_cooldown(protocol, stream)
                    self.assertEqual(
                        response.status_code,
                        200,
                        f"{response.text}; logs={len(logs)}, upstream={received}",
                    )
                    self.assertIn("ok", response.text)
                    self.assertEqual(received, ["backup.test"])
                    self.assertEqual(len(logs), 1)
                    self.assertEqual(logs[0]["status"], "success")
                    self.assertEqual(logs[0]["deployment_id"], selected[1])
                    attempts = json.loads(logs[0]["attempts"])
                    self.assertEqual(len(attempts), 2)
                    self.assertEqual(attempts[0]["status"], "skipped")
                    self.assertEqual(attempts[0]["error_kind"], "credentials_unavailable")
                    self.assertNotIn("credential", attempts[0])
                    self.assertEqual(record.call_count, 1)
                    self.assertEqual(record.call_args.args[0], selected[1])
                    self.assertEqual((state["failures"], state["consecutive_failures"]), (0, 3))

    async def test_all_keys_cooled_returns_503_and_logs_once(self):
        for protocol in ("openai", "anthropic"):
            for stream in (False, True):
                with self.subTest(protocol=protocol, stream=stream):
                    response, logs, received, record, _, _ = await self.request_after_cooldown(
                        protocol, stream, exhausted=True
                    )
                    self.assertEqual(response.status_code, 503, response.text)
                    self.assertEqual(response.json()["error"]["type"], "model_unavailable")
                    if protocol == "anthropic":
                        self.assertEqual(response.json()["type"], "error")
                    self.assertEqual(received, [])
                    record.assert_not_called()
                    self.assertEqual(len(logs), 1)
                    self.assertEqual(logs[0]["status_code"], 503)
                    self.assertEqual(logs[0]["error_kind"], "model_unavailable")
                    attempts = json.loads(logs[0]["attempts"])
                    self.assertEqual(
                        [a["error_kind"] for a in attempts], ["credentials_unavailable"] * 2
                    )

    async def test_cooled_skip_with_real_upstream_failure_keeps_upstream_error(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                response, logs, received, record, selected, _ = await self.request_after_cooldown(
                    "openai", stream, upstream_status=500
                )
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(response.json()["error"]["type"], "upstream_error")
                self.assertEqual(received, ["backup.test"])
                self.assertEqual(len(logs), 1)
                self.assertEqual(record.call_count, 1)
                self.assertEqual(record.call_args.args[0], selected[1])

    async def test_incompatible_and_cooled_deployments_are_unavailable_not_invalid_input(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                response, logs, received, record, _, _ = await self.request_after_cooldown(
                    "openai", stream, incompatible_first=True
                )
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["error"]["type"], "model_unavailable")
                self.assertEqual(received, [])
                record.assert_not_called()
                self.assertEqual(len(logs), 1)
                self.assertEqual(
                    [a["error_kind"] for a in json.loads(logs[0]["attempts"])],
                    ["incompatible_request", "credentials_unavailable"],
                )

    async def test_unexpected_runtime_errors_are_not_treated_as_credential_exhaustion(self):
        dep = {"id": "fake", "name": "fake", "provider": "openai", "upstream_model": "up"}
        with (
            patch.object(gateway, "candidates", return_value=[dep]),
            patch.object(gateway, "weighted_order", side_effect=lambda pool: pool),
            patch.object(
                gateway, "choose_credential", side_effect=RuntimeError("unexpected failure")
            ),
            patch.object(gateway, "record_result") as record,
            patch.object(gateway, "save_event") as save,
        ):
            for stream in (False, True):
                forward = gateway.forward_stream if stream else gateway.forward
                with (
                    self.subTest(stream=stream),
                    self.assertRaisesRegex(RuntimeError, "unexpected failure"),
                ):
                    await forward(
                        {"model": "model", "messages": [], "stream": stream}, "openai", "unexpected"
                    )
            record.assert_not_called()
            save.assert_not_called()
