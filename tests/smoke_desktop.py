"""Run the real Windows/Qt shell without credentials, microphone or network.

python -B tests/smoke_desktop.py --seconds 8
The real main() owns startup/shutdown; its backend waits for an API key that this
temporary, empty configuration never supplies. This is an offline smoke test,
not a live Gemini/hardware validation.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtCore import QTimer
import main
import ui
from memory import config_manager, ui_preferences


def smoke(seconds):
    with tempfile.TemporaryDirectory(prefix="chloe-smoke-") as directory, ExitStack() as stack:
        config_directory = Path(directory) / "config"
        api_file = config_directory / "api_keys.json"
        for owner, attribute, value in (
            (ui, "CONFIG_DIR", config_directory), (ui, "API_FILE", api_file),
            (config_manager, "CONFIG_DIR", config_directory), (config_manager, "CONFIG_FILE", api_file),
            (ui_preferences, "PREFERENCES_FILE", config_directory / "ui_preferences.json"),
            (main, "API_CONFIG_PATH", api_file),
        ):
            stack.enter_context(patch.object(owner, attribute, value))
        stack.enter_context(patch.object(ui.MainWindow, "_check_autostart", return_value=False))
        stack.enter_context(patch.object(ui.MainWindow, "_warm_wake_state"))
        backend = stack.enter_context(patch.object(main, "JarvisLive", side_effect=AssertionError("backend not permitted in offline smoke")))
        real_ui = ui.JarvisUI
        windows = []

        def create(*args, **kwargs):
            facade = real_ui(*args, **kwargs)
            windows.append(facade)
            facade._win.setWindowTitle("CHLOE AI — auditoria local sem API")
            # Same preview offered by the setup screen; _ready remains false.
            QTimer.singleShot(0, lambda: facade._win._overlay.hide() if facade._win._overlay else None)
            QTimer.singleShot(max(1, int(seconds * 1000)), facade._win.close)
            return facade

        stack.enter_context(patch.object(main, "JarvisUI", side_effect=create))
        main.main()
        backend.assert_not_called()
        assert windows and not windows[0]._win.isVisible()
        assert not any(t.name == "CHLOE runtime" for t in threading.enumerate())
    print(json.dumps({"window_opened": True, "window_closed": True,
                      "backend_started": False, "runtime_worker_closed": True,
                      "temporary_configuration_removed": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=8.0)
    smoke(parser.parse_args().seconds)
