"""Windows desktop entry point: one instance, no console, private diagnostics."""
from __future__ import annotations

import ctypes
from contextlib import redirect_stderr, redirect_stdout
from ctypes import wintypes
from datetime import datetime
import hashlib
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading

BASE = Path(__file__).resolve().parent
LOG = BASE / "logs" / "launcher.log"
EVENTS = {
    "launcher_failed", "application_failed", "worker_failed", "uncaught_error",
}


def report_failure(event: str, exit_code: int | None = None) -> None:
    # Never serialize exception messages, tracebacks, stdout, config or locals.
    event = event if event in EVENTS else "launcher_failed"
    suffix = f" exit={exit_code}" if type(exit_code) is int else ""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().isoformat(timespec='seconds')} {event}{suffix}\n")
    except OSError:
        pass
    ctypes.windll.user32.MessageBoxW(
        None,
        "Não foi possível iniciar ou manter a CHLOE AI em execução.\n\n"
        "Feche a janela da CHLOE, caso esteja aberta, e tente novamente.\n"
        "Se o problema persistir, confira o ambiente Python do projeto.\n\n"
        "Diagnóstico sem credenciais: logs\\launcher.log\n"
        f"Código: {event}{suffix}",
        "CHLOE AI — falha na execução", 0x10 | 0x10000,
    )


def existing_processes() -> set[int]:
    import psutil

    found = set()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            info = proc.info
            if info["pid"] == os.getpid() or (info["name"] or "").lower() not in (
                "python.exe", "pythonw.exe",
            ):
                continue
            # Recognize both this launcher and an existing direct main.py launch.
            for arg in (info["cmdline"] or [])[1:]:
                if Path(arg).name not in ("main.py", "launch_chloe.py"):
                    continue
                if Path(arg).name == "launch_chloe.py" and "--run-app" not in info["cmdline"]:
                    continue
                path = Path(arg)
                if not path.is_absolute():
                    path = Path(info["cwd"] or "") / path
                if path.resolve().parent == BASE:
                    found.add(info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return found


def show_existing(pids: set[int]) -> None:
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return False
        return True

    user32.EnumWindows(visit, 0)


def run_application() -> int:
    # pythonw has no standard streams. Give legacy print calls a safe sink.
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
        sys.excepthook = lambda *_: report_failure("uncaught_error")
        threading.excepthook = lambda _: report_failure("worker_failed")
        try:
            runpy.run_path(str(BASE / "main.py"), run_name="__main__",
                           init_globals={"_CHLOE_SUPERVISED": True})
        except SystemExit as exc:
            return exc.code if type(exc.code) is int else (0 if exc.code is None else 1)
        except BaseException:
            return 1  # The supervisor displays the failure, without raw details.
    return 0


def supervise() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    name = hashlib.sha256(str(BASE).lower().encode()).hexdigest()[:24]
    mutex = kernel32.CreateMutexW(None, False, "Local\\CHLOE_AI_" + name)
    already_running = ctypes.get_last_error() == 183
    if not mutex:
        raise OSError("mutex")
    try:
        pids = existing_processes()
        # A venv pythonw launcher has a parent process with this same command.
        import psutil
        ancestors = {p.pid for p in psutil.Process().parents()}
        pids -= ancestors
        if already_running or pids:
            show_existing(pids)
            return 0
        pythonw = BASE / ".venv" / "Scripts" / "pythonw.exe"
        result = subprocess.run(
            [str(pythonw), "-B", str(Path(__file__).resolve()), "--run-app"],
            cwd=BASE, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            report_failure("application_failed", result.returncode)
        return result.returncode
    finally:
        kernel32.CloseHandle(mutex)


def main() -> int:
    os.chdir(BASE)
    if sys.argv[1:] == ["--run-app"]:
        return run_application()
    try:
        return supervise()
    except Exception:
        report_failure("launcher_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
