"""Preferências locais da interface, separadas de credenciais e memória pessoal."""

import json
import os
import tempfile
import threading
from pathlib import Path

from memory.config_manager import BASE_DIR


PREFERENCES_FILE = BASE_DIR / "config" / "ui_preferences.json"
_LOCK = threading.RLock()
_STARTUP_DEFAULTS = {
    "enabled": True,
    "spoken": False,
    "include_time": True,
    "include_cpu": False,
    "include_ram": False,
    "include_gpu": False,
}


def _read() -> dict:
    """Lê somente este arquivo; nenhum diagnóstico inclui dados do usuário."""
    try:
        data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Formato de preferências inválido")
    return data


def _load() -> dict:
    with _LOCK:
        try:
            return _read()
        except (OSError, ValueError, UnicodeError):
            return {}


def _update(values: dict) -> bool:
    """Atualiza atomicamente; nunca sobrescreve um arquivo existente ilegível."""
    temporary = None
    with _LOCK:
        try:
            data = _read()
            for key, value in values.items():
                if isinstance(value, dict):
                    previous = data.get(key)
                    merged = dict(previous) if isinstance(previous, dict) else {}
                    merged.update(value)
                    data[key] = merged
                else:
                    data[key] = value
            PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=PREFERENCES_FILE.parent,
                prefix=".ui_preferences_", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, PREFERENCES_FILE)
            return True
        except (OSError, ValueError, UnicodeError):
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def load_preferences() -> dict:
    """Preferências da janela, com valores utilizáveis em uma instalação nova."""
    values = _load()
    collapsed = values.get("chat_collapsed", True)
    values["chat_collapsed"] = collapsed if isinstance(collapsed, bool) else True
    width = values.get("chat_width", 340)
    if isinstance(width, bool) or not isinstance(width, (int, float)):
        width = 340
    try:
        values["chat_width"] = max(260, min(720, int(width)))
    except (OverflowError, ValueError):
        values["chat_width"] = 340
    startup = values.get("startup")
    startup = startup if isinstance(startup, dict) else {}
    values["startup"] = {
        key: startup[key] if isinstance(startup.get(key), bool) else default
        for key, default in _STARTUP_DEFAULTS.items()
    }
    return values


def save_preferences(values: dict) -> bool:
    """Grava apenas campos da interface conhecidos, sem tocar em credenciais."""
    if not isinstance(values, dict):
        return False
    update = {}
    if isinstance(values.get("chat_collapsed"), bool):
        update["chat_collapsed"] = values["chat_collapsed"]
    if "chat_width" in values and not isinstance(values["chat_width"], bool):
        try:
            update["chat_width"] = max(260, min(720, int(values["chat_width"])))
        except (TypeError, ValueError, OverflowError):
            pass
    if isinstance(values.get("startup"), dict):
        update["startup"] = {
            key: value for key, value in values["startup"].items()
            if key in _STARTUP_DEFAULTS and isinstance(value, bool)
        }
    return _update(update)


def get_chat_collapsed() -> bool:
    return load_preferences()["chat_collapsed"]


def save_chat_collapsed(collapsed: bool) -> bool:
    return _update({"chat_collapsed": bool(collapsed)})


def get_chat_width() -> int:
    return load_preferences()["chat_width"]


def save_chat_width(width: int) -> bool:
    try:
        width = max(260, min(720, int(width)))
    except (TypeError, ValueError, OverflowError):
        return False
    return _update({"chat_width": width})


def get_startup_preferences() -> dict:
    return load_preferences()["startup"]


def save_startup_preferences(values: dict) -> bool:
    if not isinstance(values, dict):
        return False
    update = {
        key: value for key, value in values.items()
        if key in _STARTUP_DEFAULTS and isinstance(value, bool)
    }
    return _update({"startup": update})
