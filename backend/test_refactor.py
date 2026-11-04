import asyncio
import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, Mock

import httpx
import app
import db as datastore
import events
import gateway
import rules
import security
import upstream
from adapters import to_anthropic, to_openai, IncompatibleRequest
from event_bus import EventBus
from retention import cleanup_batch, save_retention, RetentionInput

Client = httpx.AsyncClient


class RefactorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.paths = patch.multiple(
            datastore, DB_PATH=home / "router.db", BOOTSTRAP_KEY_PATH=home / "client.key"
        )
        self.paths.start()
        datastore.init_db()
        datastore.migrate_indexes()
        self.client = Client(
            transport=httpx.ASGITransport(app=app.app),
            base_url="http://test",
            headers={"X-Management-Token": app.app.state.management_token},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await upstream.client_pool.close()
        self.paths.stop()
        self.tmp.cleanup()

    def seed(self, count=630, stamp=None):
        stamp = stamp or datetime.now(timezone.utc).isoformat()
        with datastore.connect() as connection:
            connection.executemany(
                "INSERT INTO request_events(id,request_id,protocol,alias,status,created_at,total_tokens,latency_ms,supplier_name) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"{i:05}",
                        str(i),
                        "openai",
                        "model",
                        "success" if i % 2 else "error",
                        stamp,
                        10,
                        0 if i == 0 else 100,
                        "Vendor",
                    )
                    for i in range(count)
                ],
            )
        return stamp

    async def test_cursor_covers_more_than_500_with_identical_timestamps(self):
        self.seed()
        ids = []
        cursor = ""
        while True:
            response = await self.client.get(
                "/api/events/page",
                params={
                    "start": 0,
                    "end": int(datetime.now(timezone.utc).timestamp() * 1000) + 1000,
                    "cursor": cursor,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            page = response.json()
            ids.extend(x["id"] for x in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        self.assertEqual(len(ids), 630)
        self.assertEqual(len(set(ids)), 630)
        self.assertEqual(ids, sorted(ids, reverse=True))

    async def test_stats_overview_and_search_use_complete_range(self):
        self.seed()
        params = {"start": 0, "end": int(datetime.now(timezone.utc).timestamp() * 1000) + 1000}
        stats = (await self.client.get("/api/events/stats", params=params)).json()
        self.assertEqual((stats["total"], stats["tokens"], stats["failed"]), (630, 6300, 315))
        self.assertEqual(sum(b["success"] + b["error"] for b in stats["buckets"]), 630)
        overview = (await self.client.get("/api/overview", params=params)).json()
        self.assertEqual(overview["providers"][0]["count"], 630)
        page = (
            await self.client.get(
                "/api/events/page", params={**params, "q": "Vendor", "status": "success"}
            )
        ).json()
        self.assertTrue(all(x["status"] == "success" for x in page["items"]))
        filtered = (
            await self.client.get(
                "/api/events/stats", params={**params, "q": "Vendor", "status": "success"}
            )
        ).json()
        self.assertEqual(filtered["total"], 315)
        self.assertIsInstance((await self.client.get("/api/events?limit=500")).json(), list)

    async def test_invalid_cursor_and_range_are_client_errors(self):
        for params in (
            {"start": 0, "end": 1, "cursor": "bad"},
            {"start": 2, "end": 1},
            {"start": 0, "end": 10**30},
        ):
            response = await self.client.get("/api/events/page", params=params)
            self.assertEqual(response.status_code, 400, response.text)

    def test_24_hour_boundary_uses_same_time_format(self):
        # The old lexical comparison admitted an earlier time on yesterday's date.
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.seed(2, old)
        self.assertEqual(events.summary()["requests_24h"]["total"], 0)

    def test_retention_default_preserves_and_enabled_cleans_only_history(self):
        self.seed(2, (datetime.now(timezone.utc) - timedelta(days=31)).isoformat())
        with datastore.connect() as connection:
            connection.execute(
                "INSERT INTO ai_audits VALUES('old','analysis','complete','{}','[]',?)",
                ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),),
            )
        self.assertEqual(cleanup_batch(), 0)
        save_retention(RetentionInput(retention_days=30))
        self.assertEqual(cleanup_batch(), 3)
        self.assertEqual(cleanup_batch(), 0)

    def test_schema_and_query_indexes_are_repeatable(self):
        datastore.init_db()
        datastore.migrate_indexes()
        datastore.migrate_indexes()
        with datastore.connect() as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM request_events WHERE alias=? AND created_at>=? ORDER BY created_at DESC,id DESC LIMIT 25",
                ("x", "2020"),
            ).fetchall()
            self.assertIn("idx_events_alias_time", str([tuple(r) for r in plan]))
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    async def test_auth_precedes_validation_for_new_routes(self):
        async with Client(
            transport=httpx.ASGITransport(app=app.app), base_url="http://test"
        ) as client:
            for path in [
                "/api/events/page",
                "/api/events/stats",
                "/api/overview",
                "/api/settings/retention",
            ]:
                self.assertEqual((await client.get(path)).status_code, 401)

    async def test_invalid_connection_timeout_and_retention_return_422(self):
        response = await self.client.post(
            "/api/deployments/test-connection", json={"timeout_seconds": "oops"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            (
                await self.client.put("/api/settings/retention", json={"retention_days": -1})
            ).status_code,
            422,
        )

    async def test_non_object_and_oversize_bodies_use_both_protocol_error_shapes(self):
        for path in ["/v1/chat/completions", "/v1/messages"]:
            headers = {"Authorization": "Bearer " + security.client_key()}
            for index, raw in enumerate((b"[]", b"{", b"\xff")):
                request_id = f"invalid-json-{path}-{index}"
                response = await self.client.post(
                    path, content=raw, headers={**headers, "x-request-id": request_id}
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
                with datastore.connect() as connection:
                    rows = connection.execute(
                        "SELECT * FROM request_events WHERE request_id=?", (request_id,)
                    ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["status_code"], 400)

            async def chunks():
                for _ in range(21):
                    yield b"x" * (1024 * 1024)

            request_id = f"oversize-{path}"
            response = await self.client.post(
                path, content=chunks(), headers={**headers, "x-request-id": request_id}
            )
            self.assertEqual(response.status_code, 413)
            if path.endswith("messages"):
                self.assertEqual(response.json()["type"], "error")
            with datastore.connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM request_events WHERE request_id=?", (request_id,)
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status_code"], 413)

    async def test_bus_coalesces_with_bounded_memory(self):
        bus = EventBus()
        async with bus.subscribe() as queue:
            for i in range(100):
                bus.publish(f"{i:03}")
            await asyncio.sleep(0)
            self.assertEqual(queue.qsize(), 1)
            self.assertEqual(await queue.get(), ("000", "099"))
        self.assertFalse(bus._subscribers)

    async def test_invalid_nested_input_is_logged_once_before_routing(self):
        malformed = [
            {"messages": [{"role": "user", "content": [value]}]}
            for value in (
                None,
                3,
                "text",
                [],
                {},
                {"type": "text", "text": None},
                {"type": "image_url", "image_url": None},
                {"type": "image_url", "image_url": {"url": 3}},
                {"type": "image", "source": []},
                {"type": "image", "source": {"type": "unknown"}},
                {"type": "image", "source": {"type": "url", "url": None}},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": []},
                },
                {"type": "tool_use", "id": "t", "name": "tool", "input": []},
                {"type": "tool_result", "tool_use_id": "t", "content": [None]},
                {"type": "tool_result", "tool_use_id": "t", "content": None},
            )
        ] + [
            {"system": [None]},
            {"tools": [None]},
            {"tools": {}},
            {"tools": [{"function": None}]},
            {"tool_choice": None},
            {"messages": [{"role": "assistant", "tool_calls": [None]}]},
            {"messages": [{"role": "assistant", "tool_calls": [{"id": "t", "function": None}]}]},
            {"messages": [{"role": "tool", "content": "result"}]},
        ]
        headers = {"Authorization": "Bearer " + security.client_key()}
        for path, stream in (
            (path, stream)
            for path in ("/v1/chat/completions", "/v1/messages")
            for stream in (False, True)
        ):
            for index, extra in enumerate(malformed):
                request_id = f"{path}-{stream}-{index}"
                with (
                    self.subTest(path=path, extra=extra),
                    patch.object(gateway, "candidates") as route,
                ):
                    response = await self.client.post(
                        path,
                        json={"model": "model", "messages": [], "stream": stream, **extra},
                        headers={**headers, "x-request-id": request_id},
                    )
                    self.assertEqual(response.status_code, 400, response.text)
                    body = response.json()
                    self.assertEqual(body["error"]["type"], "invalid_request_error")
                    self.assertIn("must be", body["error"]["message"])
                    if path.endswith("messages"):
                        self.assertEqual(body["type"], "error")
                    route.assert_not_called()
                    with datastore.connect() as connection:
                        rows = connection.execute(
                            "SELECT * FROM request_events WHERE request_id=?", (request_id,)
                        ).fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["status_code"], 400)
                    self.assertEqual(rows[0]["error_kind"], "invalid_request_error")

    async def test_valid_nested_payloads_reach_routing(self):
        payloads = [
            (
                "/v1/chat/completions",
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {"id": "t", "function": {"name": "tool", "arguments": "{}"}}
                            ],
                        }
                    ],
                    "tools": [{"type": "function", "function": {"name": "tool", "parameters": {}}}],
                },
            ),
            (
                "/v1/messages",
                {
                    "system": [{"type": "text", "text": "system"}],
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "t",
                                    "content": [
                                        {"type": "text", "text": "ok"},
                                        {
                                            "type": "image",
                                            "source": {
                                                "type": "url",
                                                "url": "https://example.test/a.png",
                                            },
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                    "tools": [{"name": "tool", "input_schema": {}}],
                    "tool_choice": {"type": "tool", "name": "tool"},
                },
            ),
        ]
        for path, extra in payloads:
            with (
                self.subTest(path=path),
                patch.object(gateway, "candidates", return_value=[]) as route,
            ):
                response = await self.client.post(
                    path,
                    json={"model": "model", **extra},
                    headers={"Authorization": "Bearer " + security.client_key()},
                )
                self.assertEqual(response.status_code, 503, response.text)
                route.assert_called_once_with("model")

    async def test_fixed_sse_range_does_not_emit_outside_range(self):
        response = await events.stream_events(start=0, end=1000, live=False)
        iterator = response.body_iterator
        self.assertIn("ready", await anext(iterator))
        events.bus.publish("2026-01-01T00:00:00+00:00")
        await asyncio.sleep(0)
        # A notification in the fixed interval must still wake the subscription.
        events.bus.publish("1970-01-01T00:00:00.500000+00:00")
        self.assertIn("event: new", await asyncio.wait_for(anext(iterator), 1))
        await iterator.aclose()

    def test_image_round_trip_and_system_text(self):
        blocks = [
            {"type": "text", "text": "before"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            {"type": "text", "text": "after"},
        ]
        converted = to_anthropic(
            {
                "messages": [
                    {"role": "system", "content": [{"type": "text", "text": "system"}]},
                    {"role": "user", "content": blocks},
                ]
            },
            "up",
        )
        self.assertEqual(converted["system"], "system")
        result = to_openai(
            {"system": [{"type": "text", "text": "system"}], "messages": converted["messages"]},
            "up",
        )
        self.assertEqual(result["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(result["messages"][1]["content"], blocks)
        url = to_anthropic(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://example.test/image.png"},
                            }
                        ],
                    }
                ]
            },
            "up",
        )
        self.assertEqual(url["messages"][0]["content"][0]["source"]["type"], "url")

    def test_invalid_tool_and_unsupported_content_are_explicit(self):
        with self.assertRaises(IncompatibleRequest):
            to_anthropic(
                {
                    "messages": [
                        {"role": "assistant", "tool_calls": [{"function": {"arguments": "bad"}}]}
                    ]
                },
                "up",
            )
        with self.assertRaises(IncompatibleRequest):
            to_openai({"messages": [{"role": "user", "content": [{"type": "audio"}]}]}, "up")

    def test_redaction_includes_key_url_auth_and_query_token(self):
        text = security.redact(
            "secret https://user:pass@example.test?api_key=abc&token=def", ["secret"]
        )
        for value in ["secret", "user:pass", "abc", "def"]:
            self.assertNotIn(value, text)

    async def test_stream_releases_client_even_when_persistence_fails(self):
        dep = dict(
            id="fake",
            name="Fake",
            provider="openai",
            base_url="https://upstream.test",
            alias="model",
            upstream_model="up",
            api_keys=["key"],
            timeout_seconds=1,
            use_system_proxy=False,
        )
        clients = []

        def factory(**_):
            client = Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        content=b"data: [DONE]\n\n",
                        headers={"content-type": "text/event-stream"},
                    )
                )
            )
            clients.append(client)
            return client

        with (
            patch.object(gateway, "candidates", return_value=[dep]),
            patch.object(gateway, "choose_credential", return_value=("key", 0)),
            patch.object(gateway, "supplier_snapshot", return_value={}),
            patch.object(gateway, "record_result", side_effect=RuntimeError("db unavailable")),
            patch.object(upstream.httpx, "AsyncClient", side_effect=factory),
        ):
            response = await gateway.forward_stream(
                {"model": "model", "stream": True}, "openai", "request"
            )
            with self.assertRaises(RuntimeError):
                async for _ in response.body_iterator:
                    pass
        self.assertTrue(all(c.is_closed for c in clients))

    async def test_client_pool_reuses_and_closes_clients(self):
        pool = upstream.ClientPool()
        pool.active = True
        clients = []

        def factory(**_):
            client = Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
            clients.append(client)
            return client

        with patch.object(upstream.httpx, "AsyncClient", side_effect=factory):
            async with pool.lease({"use_system_proxy": False}) as first:
                pass
            async with pool.lease({"use_system_proxy": False}) as second:
                self.assertIs(first, second)
        await pool.close()
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].is_closed)

    def test_deployment_snapshot_uses_constant_query_count(self):
        from management import create_deployment
        from schemas import DeploymentInput

        for i in range(4):
            create_deployment(
                DeploymentInput(
                    name=str(i),
                    alias="model",
                    base_url="https://upstream.test",
                    upstream_model="up",
                    api_keys=["key"],
                )
            )
        calls = []
        original = datastore.connect

        def connect():
            connection = original()
            connection.set_trace_callback(calls.append)
            return connection

        with patch.object(rules, "connect", side_effect=connect):
            self.assertEqual(len(rules.candidates("model")), 4)
        self.assertEqual(len([sql for sql in calls if sql.startswith("SELECT")]), 4)

    async def test_sqlite_writer_wait_does_not_freeze_event_loop(self):
        from management import create_deployment
        from schemas import DeploymentInput

        dep = create_deployment(
            DeploymentInput(
                name="lock",
                alias="model",
                base_url="https://upstream.test",
                upstream_model="up",
                api_keys=["key"],
                use_system_proxy=False,
            )
        )
        locked = datastore.connect()
        locked.execute("BEGIN IMMEDIATE")
        factory = lambda **_: Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
            )
        )
        try:
            with patch.object(upstream.httpx, "AsyncClient", side_effect=factory):
                task = asyncio.create_task(
                    gateway.forward({"model": "model", "messages": []}, "openai", "lock-request")
                )
                # Credential rotation is also a write; the gateway must offload it.
                ticks = 0
                for _ in range(5):
                    await asyncio.sleep(0.02)
                    ticks += 1
                self.assertFalse(task.done())
                self.assertEqual(ticks, 5)
                locked.rollback()
                await asyncio.wait_for(task, 3)
        finally:
            locked.close()

    async def test_incompatible_deployment_is_skipped_without_penalty(self):
        incompatible = dict(
            id="responses",
            name="Responses",
            provider="responses",
            upstream_model="up",
            api_keys=["key"],
            base_url="https://upstream.test",
            timeout_seconds=1,
            use_system_proxy=False,
        )
        native = {**incompatible, "id": "native", "provider": "openai"}
        factory = lambda **_: Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
            )
        )
        with (
            patch.object(gateway, "candidates", return_value=[incompatible, native]),
            patch.object(gateway, "weighted_order", side_effect=lambda x: x),
            patch.object(gateway, "choose_credential", return_value=("key", 0)),
            patch.object(gateway, "supplier_snapshot", return_value={}),
            patch.object(gateway, "record_result") as record,
            patch.object(upstream.httpx, "AsyncClient", side_effect=factory),
        ):
            result = await gateway.forward(
                {"model": "model", "messages": [], "n": 2}, "openai", "fallback"
            )
            self.assertEqual(result[1]["id"], "native")
            self.assertEqual(result[2][0]["status"], "skipped")
            self.assertEqual(record.call_count, 1)
            self.assertEqual(record.call_args.args[0], "native")

    async def test_all_incompatible_is_400_without_penalty(self):
        dep = dict(
            id="responses",
            name="Responses",
            provider="responses",
            upstream_model="up",
            api_keys=["key"],
            base_url="https://upstream.test",
        )
        with (
            patch.object(gateway, "candidates", return_value=[dep]),
            patch.object(gateway, "record_result") as record,
            patch.object(gateway, "save_event") as save,
        ):
            with self.assertRaises(gateway.HTTPException) as error:
                await gateway.forward(
                    {"model": "model", "messages": [], "n": 2}, "openai", "incompatible"
                )
            self.assertEqual(error.exception.status_code, 400)
            record.assert_not_called()
            self.assertEqual(save.call_args.args[5], 400)

    async def test_stream_setup_error_closes_client(self):
        dep = dict(
            id="fake",
            name="Fake",
            provider="openai",
            base_url="https://upstream.test",
            upstream_model="up",
            api_keys=["key"],
            timeout_seconds=1,
            use_system_proxy=False,
        )
        clients = []

        def factory(**_):
            client = Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"ok"))
            )
            clients.append(client)
            return client

        with (
            patch.object(gateway, "candidates", return_value=[dep]),
            patch.object(gateway, "choose_credential", return_value=("key", 0)),
            patch.object(gateway, "supplier_snapshot", side_effect=ValueError("snapshot failed")),
            patch.object(upstream.httpx, "AsyncClient", side_effect=factory),
        ):
            with self.assertRaises(ValueError):
                await gateway.forward_stream({"model": "model", "stream": True}, "openai", "setup")
        self.assertTrue(clients[0].is_closed)

    async def test_client_abandonment_counts_once_and_closes_stream(self):
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"data: first\n\n"
                yield b"data: second\n\n"

        dep = dict(
            id="fake",
            name="Fake",
            provider="openai",
            base_url="https://upstream.test",
            upstream_model="up",
            api_keys=["key"],
            timeout_seconds=1,
            use_system_proxy=False,
        )
        clients = []

        def factory(**_):
            client = Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream()))
            )
            clients.append(client)
            return client

        with (
            patch.object(gateway, "candidates", return_value=[dep]),
            patch.object(gateway, "choose_credential", return_value=("key", 0)),
            patch.object(gateway, "supplier_snapshot", return_value={}),
            patch.object(gateway, "record_result") as record,
            patch.object(gateway, "save_event") as save,
            patch.object(upstream.httpx, "AsyncClient", side_effect=factory),
        ):
            response = await gateway.forward_stream(
                {"model": "model", "stream": True}, "openai", "abandoned"
            )
            await anext(response.body_iterator)
            await response.body_iterator.aclose()
            record.assert_called_once()
            self.assertFalse(record.call_args.args[1])
            self.assertEqual(save.call_args.args[7], "client_disconnected")
        self.assertTrue(clients[0].is_closed)


if __name__ == "__main__":
    unittest.main()
