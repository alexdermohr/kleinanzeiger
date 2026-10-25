"""Deterministic SIGTERM/SIGINT reentry tests for protected service shutdown.

Invoke each installed role entrypoint's registered handler while the synthetic
Event.wait() owns its non-reentrant condition lock. The old Event.set()-inside-
handler implementation fails this test without hanging the test process.
Fresh multi-UID installed acceptance separately tests real signals and sockets.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import signal
import threading
import unittest
from unittest.mock import MagicMock, patch

from mark_api import protected_actor, protected_owner, protected_witness


class _SignalInsideLockedWait:
    def __init__(self, handlers: dict, signum: int) -> None:
        self._lock = threading.Lock()
        self.handlers = handlers
        self.signum = signum
        self.fired = False
        self.cleared = False
        self.reentered = False
        self.timeout: float | None = None

    def wait(self, timeout: float | None = None) -> bool:
        self.timeout = timeout
        with self._lock:
            if not self.fired:
                self.fired = True
                # Python runs the signal handler on the same main thread.
                # Simulate delivery at the point Event.wait holds its lock.
                self.handlers[self.signum](self.signum, None)
        return False

    def set(self) -> None:
        if not self._lock.acquire(blocking=False):
            self.reentered = True
            raise AssertionError("signal handler reentered Event lock")
        try:
            self.cleared = True
        finally:
            self._lock.release()

    def is_set(self) -> bool:
        return self.cleared


class ProtectedSignalShutdownTests(unittest.TestCase):
    def _exercise(self, role: str, signum: int) -> None:
        handlers: dict[int, object] = {}

        def install(sig: int, handler: object) -> object:
            if callable(handler):
                handlers[sig] = handler
            return signal.SIG_DFL

        event = _SignalInsideLockedWait(handlers, signum)
        runtime = MagicMock()
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch("signal.signal", side_effect=install),
            patch("mark_api.container_draft.container_draft_denied", return_value=False),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            if role == "actor":
                config = {
                    "socket": "/tmp/synthetic-actor.sock",
                    "witness_socket": "/tmp/synthetic-witness.sock",
                    "owner_uid": 50111, "owner_gid": 50111,
                    "witness_uid": 50113, "runtime_uid": 50115,
                    "principal": "synthetic", "cdp_port": 9222,
                    "timeout_seconds": 5, "media_enabled": False,
                    "media_directory": None,
                }
                with (
                    patch("mark_api.protected_actor._read_private_actor_config", return_value=config),
                    patch("mark_api.protected_actor.create_protected_actor_runtime", return_value=runtime),
                    patch("mark_api.protected_actor.Event", return_value=event),
                ):
                    result = protected_actor.main(["--config-fd", "0"])
            elif role == "owner":
                config = {
                    "database": "/tmp/synthetic-owner.sqlite",
                    "dashboard_socket": "/tmp/synthetic-dashboard.sock",
                    "witness_socket": "/tmp/synthetic-witness.sock",
                    "actor_socket": "/tmp/synthetic-actor.sock",
                    "witness_uid": 50113, "actor_uid": 50112,
                    "runtime_uid": 50115, "runtime_gid": 50115,
                    "principal": "synthetic", "bearer_token": "synthetic",
                    "media_enabled": False, "write_port": 0,
                    "sync_interval_seconds": 0,
                }
                runtime.write_server.server_port = 17723
                runtime.write_server.wait_for_active_handlers.return_value = True
                server_thread = MagicMock()
                server_thread.is_alive.return_value = False
                second_event = threading.Event()
                with (
                    patch("mark_api.protected_owner._read_private_owner_config", return_value=config),
                    patch("mark_api.protected_owner.create_protected_owner_runtime", return_value=runtime),
                    patch("threading.Event", side_effect=[event, second_event]),
                    patch("threading.Thread", return_value=server_thread),
                ):
                    result = protected_owner.main(["--config-fd", "0"])
            else:
                config = {
                    "database": "/tmp/synthetic-witness.sqlite",
                    "owner_socket": "/tmp/synthetic-owner.sock",
                    "actor_socket": "/tmp/synthetic-actor.sock",
                    "owner_uid": 50111, "owner_gid": 50111,
                    "actor_uid": 50112, "actor_gid": 50112,
                    "runtime_uid": 50115, "principal": "synthetic",
                }
                runtime.server._threads = (MagicMock(), MagicMock())
                for worker in runtime.server._threads:
                    worker.is_alive.return_value = True
                with (
                    patch("mark_api.protected_witness._read_private_witness_config", return_value=config),
                    patch("mark_api.protected_witness.create_protected_witness_runtime", return_value=runtime),
                    patch("mark_api.protected_witness.Event", return_value=event),
                ):
                    result = protected_witness.main(["--config-fd", "0"])

        self.assertTrue(event.fired, "test did not deliver the signal inside Event.wait")
        self.assertFalse(event.reentered, "signal handler attempted lock-taking Event.set")
        self.assertIsNotNone(event.timeout, "unbounded wait can lose a signal")
        self.assertLessEqual(event.timeout, 0.2)
        self.assertEqual(result, 0, errors.getvalue())
        runtime.close.assert_called_once()
        if role == "owner":
            self.assertTrue(event.cleared, "main thread must wake the sync worker")
            runtime.write_server.shutdown.assert_called_once()
        self.assertIn('"ready":true', output.getvalue())

    def test_actor_signal_arriving_inside_wait_drains(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signal.Signals(signum).name):
                self._exercise("actor", signum)

    def test_owner_signal_arriving_inside_wait_drains(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signal.Signals(signum).name):
                self._exercise("owner", signum)

    def test_witness_signal_arriving_inside_wait_drains(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signal.Signals(signum).name):
                self._exercise("witness", signum)
