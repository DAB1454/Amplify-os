"""Tests for the local node runtime."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.config import NodeConfig
from app.heartbeat import HeartbeatClient, create_heartbeat_app
from app.offline_queue import OfflineQueue
from app.secrets_store import SecretsStore
from app.supervisor import Supervisor, ProcessState
from app.filesystem_cache import FilesystemCache
from app.ffmpeg_runner import FFmpegRunner


# ── Config ───────────────────────────────────────────────────────────


class TestConfig:
    def test_defaults(self):
        config = NodeConfig()
        assert config.api_url == "http://localhost:8000"
        assert config.tenant_id == "local"
        assert config.heartbeat_port == 8100
        assert config.max_restart_attempts == 10

    def test_ensure_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = NodeConfig(
                data_dir=Path(tmpdir) / "data",
                cache_dir=Path(tmpdir) / "cache",
                media_output_dir=Path(tmpdir) / "media",
            )
            config.ensure_dirs()
            assert config.data_dir.exists()
            assert config.cache_dir.exists()
            assert config.media_output_dir.exists()

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AMPLIFY_NODE_API_URL", "http://remote:9000")
        config = NodeConfig()
        assert config.api_url == "http://remote:9000"


# ── Encrypted Secrets Store ──────────────────────────────────────────


class TestSecretsStore:
    def test_set_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SecretsStore(
                path=Path(tmpdir) / "s.enc",
                key_path=Path(tmpdir) / "s.key",
            )
            store.set("api_key", "sk-12345")
            assert store.get("api_key") == "sk-12345"

    def test_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "s.enc"
            store = SecretsStore(path=path, key_path=Path(tmpdir) / "s.key")
            store.set("secret", "my-value")
            raw = path.read_bytes()
            assert b"my-value" not in raw

    def test_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SecretsStore(
                path=Path(tmpdir) / "s.enc",
                key_path=Path(tmpdir) / "s.key",
            )
            store.set("x", "y")
            store.delete("x")
            assert store.get("x") is None

    def test_list_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SecretsStore(
                path=Path(tmpdir) / "s.enc",
                key_path=Path(tmpdir) / "s.key",
            )
            store.set("a", "1")
            store.set("b", "2")
            assert sorted(store.list_keys()) == ["a", "b"]

    def test_passphrase_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "s.enc"
            store = SecretsStore(path=path, passphrase="my-passphrase")
            store.set("key", "value")
            store2 = SecretsStore(path=path, passphrase="my-passphrase")
            assert store2.get("key") == "value"

    def test_wrong_passphrase_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "s.enc"
            store = SecretsStore(path=path, passphrase="correct")
            store.set("key", "value")
            store2 = SecretsStore(path=path, passphrase="wrong")
            with pytest.raises(ValueError, match="Cannot decrypt"):
                store2.get("key")

    def test_has(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SecretsStore(
                path=Path(tmpdir) / "s.enc",
                key_path=Path(tmpdir) / "s.key",
            )
            assert store.has("nope") is False
            store.set("yep", "1")
            assert store.has("yep") is True

    def test_nonexistent_key_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SecretsStore(
                path=Path(tmpdir) / "s.enc",
                key_path=Path(tmpdir) / "s.key",
            )
            assert store.get("nonexistent") is None

    def test_overwrite_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SecretsStore(
                path=Path(tmpdir) / "s.enc",
                key_path=Path(tmpdir) / "s.key",
            )
            store.set("k", "v1")
            store.set("k", "v2")
            assert store.get("k") == "v2"


# ── Offline Queue ────────────────────────────────────────────────────


class TestOfflineQueue:
    def test_enqueue_and_peek(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q = OfflineQueue(db_path=Path(tmpdir) / "q.db")
            job_id = q.enqueue("render_media", {"input": "test.mp4"})
            assert q.size() == 1
            jobs = q.peek()
            assert len(jobs) == 1
            assert jobs[0].job_id == job_id
            assert jobs[0].job_type == "render_media"
            assert jobs[0].payload == {"input": "test.mp4"}
            q.close()

    def test_ack_removes_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q = OfflineQueue(db_path=Path(tmpdir) / "q.db")
            job_id = q.enqueue("test", {})
            q.ack(job_id)
            assert q.size() == 0
            q.close()

    def test_nack_increments_attempts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q = OfflineQueue(db_path=Path(tmpdir) / "q.db")
            job_id = q.enqueue("test", {})
            q.nack(job_id, "connection refused")
            jobs = q.peek()
            assert jobs[0].attempts == 1
            assert jobs[0].last_error == "connection refused"
            q.close()

    def test_max_size_evicts_oldest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q = OfflineQueue(db_path=Path(tmpdir) / "q.db", max_size=3)
            ids = [q.enqueue("test", {"n": i}) for i in range(4)]
            assert q.size() == 3
            jobs = q.peek(limit=10)
            job_ids = [j.job_id for j in jobs]
            assert ids[0] not in job_ids
            q.close()

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q = OfflineQueue(db_path=Path(tmpdir) / "q.db")
            q.enqueue("a", {})
            q.enqueue("b", {})
            removed = q.clear()
            assert removed == 2
            assert q.size() == 0
            q.close()

    def test_persistence_across_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "q.db"
            q1 = OfflineQueue(db_path=db)
            q1.enqueue("persist_test", {"key": "value"})
            q1.close()

            q2 = OfflineQueue(db_path=db)
            assert q2.size() == 1
            jobs = q2.peek()
            assert jobs[0].payload == {"key": "value"}
            q2.close()

    def test_fifo_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q = OfflineQueue(db_path=Path(tmpdir) / "q.db")
            q.enqueue("first", {"order": 1})
            q.enqueue("second", {"order": 2})
            q.enqueue("third", {"order": 3})
            jobs = q.peek(limit=3)
            assert jobs[0].job_type == "first"
            assert jobs[1].job_type == "second"
            assert jobs[2].job_type == "third"
            q.close()

    def test_empty_queue_peek(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            q = OfflineQueue(db_path=Path(tmpdir) / "q.db")
            assert q.peek() == []
            assert q.size() == 0
            q.close()


# ── Supervisor ───────────────────────────────────────────────────────


class TestSupervisor:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        called = []

        async def worker():
            called.append(True)
            await asyncio.sleep(100)

        sup = Supervisor(max_attempts=3)
        sup.register("test_worker", worker)
        await sup.start()
        await asyncio.sleep(0.1)
        await sup.stop()

        assert len(called) >= 1
        status = sup.status()
        assert "test_worker" in status

    @pytest.mark.asyncio
    async def test_auto_restart_on_crash(self):
        crash_count = 0

        async def flaky():
            nonlocal crash_count
            crash_count += 1
            if crash_count <= 2:
                raise RuntimeError("boom")
            await asyncio.sleep(100)

        sup = Supervisor(max_attempts=5, backoff_base=0.01, backoff_max=0.05)
        sup.register("flaky", flaky)
        await sup.start()
        await asyncio.sleep(0.5)
        await sup.stop()

        assert crash_count >= 3

    @pytest.mark.asyncio
    async def test_fatal_after_max_attempts(self):
        async def always_crash():
            raise RuntimeError("permanent failure")

        sup = Supervisor(max_attempts=3, backoff_base=0.01, backoff_max=0.02)
        sup.register("crasher", always_crash)
        await sup.start()
        await asyncio.sleep(1)

        status = sup.status()
        assert status["crasher"]["state"] == ProcessState.FATAL.value

    @pytest.mark.asyncio
    async def test_status_reporting(self):
        async def noop():
            await asyncio.sleep(100)

        sup = Supervisor()
        sup.register("proc_a", noop)
        sup.register("proc_b", noop)
        await sup.start()
        await asyncio.sleep(0.1)

        status = sup.status()
        assert len(status) == 2
        assert status["proc_a"]["state"] == "running"
        assert status["proc_b"]["state"] == "running"

        await sup.stop()

    @pytest.mark.asyncio
    async def test_multiple_processes(self):
        results = {}

        async def process_a():
            results["a"] = True
            await asyncio.sleep(100)

        async def process_b():
            results["b"] = True
            await asyncio.sleep(100)

        sup = Supervisor()
        sup.register("a", process_a)
        sup.register("b", process_b)
        await sup.start()
        await asyncio.sleep(0.1)
        await sup.stop()

        assert results.get("a") is True
        assert results.get("b") is True

    @pytest.mark.asyncio
    async def test_clean_exit_no_restart(self):
        call_count = 0

        async def one_shot():
            nonlocal call_count
            call_count += 1

        sup = Supervisor()
        sup.register("one_shot", one_shot)
        await sup.start()
        await asyncio.sleep(0.2)
        await sup.stop()

        assert call_count == 1  # ran once, exited cleanly, no restart


# ── Heartbeat Client ─────────────────────────────────────────────────


class TestHeartbeatClient:
    @pytest.mark.asyncio
    async def test_send_success(self):
        client = HeartbeatClient()
        with patch("app.heartbeat.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=MagicMock(status_code=200))
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_http

            ok = await client.send("http://localhost:8000", "node1", ["ffmpeg"])
            assert ok is True
            assert client.api_reachable is True
            assert client.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_send_failure_tracks_consecutive(self):
        client = HeartbeatClient()
        with patch("app.heartbeat.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=Exception("refused"))
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_http

            ok = await client.send("http://localhost:8000", "node1", [])
            assert ok is False
            assert client.consecutive_failures == 1
            assert client.api_reachable is False

    @pytest.mark.asyncio
    async def test_success_resets_failures(self):
        client = HeartbeatClient()
        client.consecutive_failures = 5
        with patch("app.heartbeat.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=MagicMock(status_code=200))
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_http

            ok = await client.send("http://localhost:8000", "node1", [])
            assert ok is True
            assert client.consecutive_failures == 0


# ── Heartbeat Server ─────────────────────────────────────────────────


class TestHeartbeatApp:
    def test_health_endpoint(self):
        from starlette.testclient import TestClient

        app = create_heartbeat_app(
            node_id="test123",
            supervisor_status_fn=lambda: {"worker": {"state": "running", "restart_count": 0}},
            offline_queue_size_fn=lambda: 5,
        )
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "test123"
        assert data["offline_queue_size"] == 5
        assert "uptime_seconds" in data
        assert "system" in data

    def test_ready_endpoint_all_running(self):
        from starlette.testclient import TestClient

        app = create_heartbeat_app(
            node_id="test123",
            supervisor_status_fn=lambda: {"w": {"state": "running"}},
            offline_queue_size_fn=lambda: 0,
        )
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["ready"] is True

    def test_ready_endpoint_not_ready(self):
        from starlette.testclient import TestClient

        app = create_heartbeat_app(
            node_id="test123",
            supervisor_status_fn=lambda: {"w": {"state": "backoff"}},
            offline_queue_size_fn=lambda: 0,
        )
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False


# ── Filesystem Cache ─────────────────────────────────────────────────


class TestFilesystemCache:
    def test_put_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = FilesystemCache(Path(tmpdir))
            path = cache.put("my-key", b"my-data")
            assert path.exists()
            result = cache.get("my-key")
            assert result is not None
            assert result.read_bytes() == b"my-data"

    def test_get_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = FilesystemCache(Path(tmpdir))
            assert cache.get("nonexistent") is None

    def test_evict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = FilesystemCache(Path(tmpdir))
            cache.put("k", b"data")
            cache.evict("k")
            assert cache.get("k") is None


# ── FFmpeg Runner ────────────────────────────────────────────────────


class TestFFmpegRunner:
    def test_check_installed_returns_bool(self):
        runner = FFmpegRunner()
        result = runner.check_installed()
        assert isinstance(result, bool)
