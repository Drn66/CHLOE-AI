"""Window closure must stop both pre-configuration wait and active runtime."""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import main
import ui


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self):
        for callback in self.callbacks:
            callback()


class RuntimeShutdownTests(unittest.TestCase):
    def test_closing_without_key_releases_waiting_worker(self):
        closing = threading.Event()
        facade = SimpleNamespace(_win=SimpleNamespace(_ready=False), _closing=closing)
        result = []
        worker = threading.Thread(target=lambda: result.append(ui.JarvisUI.wait_for_api_key(facade)))
        worker.start()
        closing.set()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])

    def test_closing_window_cancels_runtime_and_closes_services(self):
        entered, completed = threading.Event(), threading.Event()
        signal = Signal()
        facade = SimpleNamespace(_closing=threading.Event(), _app=SimpleNamespace(aboutToQuit=signal),
                                 wait_for_api_key=Mock(return_value=True))

        def window_loop():
            self.assertTrue(entered.wait(timeout=2), "runtime did not start")
            signal.emit()

        facade.root = SimpleNamespace(mainloop=window_loop)

        async def run():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                completed.set()

        assistant = SimpleNamespace(run=run, _dashboard=SimpleNamespace(stop=AsyncMock()),
                                    _wake_detector=Mock(), _ptt=Mock())
        with patch.object(main, "JarvisUI", return_value=facade), patch.object(
            main, "JarvisLive", return_value=assistant
        ), patch.object(ui, "_metrics") as metrics:
            main.main()
        self.assertTrue(completed.is_set())
        assistant._dashboard.stop.assert_awaited_once()
        assistant._wake_detector.stop.assert_called_once()
        assistant._ptt.stop.assert_called_once()
        metrics.stop.assert_called_once()
        self.assertFalse(any(thread.name == "CHLOE runtime" for thread in threading.enumerate()))


class ShutdownCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_tool_requests_normal_ui_close_instead_of_exiting_process(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = Mock()
        live._refresh_idle_state = Mock()
        live._save_session_summary = AsyncMock()
        live.session = SimpleNamespace(send_client_content=AsyncMock())
        live._action_registry = Mock(scheduling=Mock(return_value=None))
        live._plugin_registry = Mock(scheduling=Mock(return_value=None))
        tasks = []
        real_create_task = asyncio.create_task

        def track(coroutine):
            task = real_create_task(coroutine)
            tasks.append(task)
            return task

        with patch.object(main.asyncio, "create_task", side_effect=track), patch.object(
            main.asyncio, "sleep", new=AsyncMock()
        ), patch("os._exit") as hard_exit:
            await live._execute_tool(SimpleNamespace(name="shutdown_jarvis", args={}, id="fixture"))
            await asyncio.gather(*tasks)
        hard_exit.assert_not_called()
        live.ui.request_close.assert_called_once()
        live._save_session_summary.assert_awaited_once()
