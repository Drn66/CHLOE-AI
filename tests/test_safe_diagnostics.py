"""Diagnostic channels must not serialize tool data or transport credentials."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core import action_loader, gemini, plugin_loader
from main import JarvisLive


MARKER = "PRIVATE_SYNTHETIC_MARKER"


class SafeToolDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def assistant(self):
        live = JarvisLive.__new__(JarvisLive)
        live.ui = Mock()
        live.speak = Mock()
        live._refresh_idle_state = Mock()
        live._action_registry = Mock()
        live._action_registry.has.return_value = True
        live._action_registry.scheduling.return_value = None
        live._plugin_registry = Mock()
        live._plugin_registry.scheduling.return_value = None
        return live

    async def test_tool_arguments_and_results_are_not_copied_to_console(self):
        live = self.assistant()
        live._action_registry.run.return_value = MARKER
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = await live._execute_tool(SimpleNamespace(name="read_fixture", id="fixture", args={"text": MARKER}))
        self.assertEqual(result.response["result"], MARKER)  # legitimate tool return is preserved
        self.assertNotIn(MARKER, output.getvalue())

    async def test_memory_values_are_not_copied_to_console(self):
        live = self.assistant()
        output = io.StringIO()
        with patch("main.update_memory") as save, redirect_stdout(output), redirect_stderr(output):
            await live._execute_tool(SimpleNamespace(name="save_memory", id="fixture", args={
                "category": "notes", "key": "fixture", "value": MARKER}))
        save.assert_called_once()
        self.assertNotIn(MARKER, output.getvalue())

    async def test_tool_exception_does_not_reach_console_ui_or_speech(self):
        live = self.assistant()
        live._action_registry.run.side_effect = RuntimeError("https://example.invalid/?key=" + MARKER)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = await live._execute_tool(SimpleNamespace(name="read_fixture", id="fixture", args={}))
        self.assertIn("RuntimeError", result.response["result"])
        for value in (output.getvalue(), str(result.response), str(live.ui.write_log.call_args_list),
                      str(live.speak.call_args_list)):
            self.assertNotIn(MARKER, value)


class SafeAdapterDiagnosticsTests(unittest.TestCase):
    def test_gemini_exception_logs_type_without_raw_url(self):
        client = Mock()
        client.models.generate_content.side_effect = RuntimeError("https://example.invalid/?key=" + MARKER)
        output = io.StringIO()
        with patch.object(gemini, "_LADDERS", {"fixture-tier": ("fixture-model",)}), patch.object(
            gemini, "client", return_value=client
        ), patch.object(gemini, "_cooling", return_value=False), redirect_stdout(output), redirect_stderr(output):
            result = gemini.call("fixture", tier="fixture-tier", key="synthetic-placeholder")
        self.assertIsNone(result)
        self.assertIn("RuntimeError", output.getvalue())
        self.assertNotIn(MARKER, output.getvalue())

    def test_action_and_plugin_wrappers_do_not_expose_exception_messages(self):
        def fail(parameters):
            raise RuntimeError(MARKER)

        logs = []
        action = action_loader.ActionRecord(name="fixture", handler=fail, valid=True)
        actions = action_loader.ActionRegistry({"fixture": action}, logs.append)
        plugin = plugin_loader.PluginRecord(name="fixture", run=fail, valid=True)
        plugins = plugin_loader.PluginRegistry({"fixture": plugin}, logs.append, logs.append)
        output = io.StringIO()
        with patch.object(plugin_loader, "get_plugin_enabled", return_value=True), redirect_stdout(output), redirect_stderr(output):
            results = [actions.run("fixture", {}), plugins.run("fixture", {})]
        self.assertNotIn(MARKER, output.getvalue() + str(logs) + str(results))
        self.assertTrue(all("RuntimeError" in result for result in results))

    def test_first_party_missing_module_never_suggests_installing_a_stranger(self):
        error = ModuleNotFoundError(MARKER, name="core.fixture")
        message = plugin_loader._load_error(Path("fixture.py"), Path("plugins"), error)
        self.assertIn("Update the app", message)
        self.assertNotIn("pip install", message)
        self.assertNotIn(MARKER, message)
