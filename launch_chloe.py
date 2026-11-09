"""Windows desktop entry point: one instance, no console, private diagnostics."""
from __future__ import annotations

import ctypes
from contextlib import redirect_stderr, redirect_stdout
from ctypes import wintypes
from datetime import datetime
import hashlib
import logging
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import threading

BASE = Path(__file__).resolve().parent
LOG = BASE / "logs" / "launcher.log"
EVENTS = {
    "launcher_failed", "application_failed", "worker_failed", "uncaught_error",
}

# Exceções que escapam de um callback (slot/evento Qt, thread, tarefa asyncio)
# sem encerrar o processo. Códigos = origem + categoria, sempre de conjuntos fechados.
_ORIGINS = ("ui", "worker", "async")
_CATEGORIES = (
    (MemoryError, "memory"),
    (ImportError, "dependency"),
    (OSError, "io"),                 # inclui TimeoutError e ConnectionError
    (RuntimeError, "runtime"),       # inclui objeto Qt já destruído
    ((AttributeError, TypeError, NameError, LookupError, ValueError), "logic"),
)
RECOVERABLE_EVENTS = {
    f"{origin}_{category}_error"
    for origin in _ORIGINS
    for category in ("memory", "dependency", "io", "runtime", "logic", "unexpected")
}
_shown_signatures: set[str] = set()
_shown_lock = threading.Lock()


def _log_line(text: str) -> None:
    """Linha de diagnóstico com data/hora; nunca levanta."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
    except OSError:
        pass


_CODE_SKIP = {"tests", "chloe_island", "docs", "logs", "config"}


def code_mtime() -> float:
    """Data da última edição do código que roda (protótipo e testes não contam)."""
    newest = 0.0
    for pattern in ("*.py", "*/*.py", "*/*/*.py"):
        for path in BASE.glob(pattern):
            parts = path.relative_to(BASE).parts
            if len(parts) > 1 and (parts[0].startswith((".", "_")) or parts[0] in _CODE_SKIP):
                continue
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else "?"


_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(api[_-]?key|key|token|secret|password|authorization)\b(\s*[=:]\s*)\S+"), r"\1\2<omitido>"),
    (re.compile(r"(https?://[^\s?#]+)[?#]\S*"), r"\1?<omitido>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "<omitido>"),
)


def redact(text: str) -> str:
    """Saída bruta do processo filho sem pasta do usuário, query strings ou chaves."""
    for home in {str(Path.home()), str(Path.home()).replace("\\", "/")}:
        text = text.replace(home, "~")
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def log_child_exit(returncode: int, stderr: bytes, max_lines: int = 60) -> None:
    _log_line(f"app_exit code={returncode}")
    lines = [ln for ln in redact(stderr.decode("utf-8", errors="replace")).splitlines() if ln.strip()]
    if lines:
        _log_line(f"app_stderr ({len(lines)} linhas, últimas {min(len(lines), max_lines)}):")
        try:
            with LOG.open("a", encoding="utf-8") as stream:
                stream.writelines(f"    | {ln}\n" for ln in lines[-max_lines:])
        except OSError:
            pass


def report_failure(event: str, exit_code: int | None = None) -> None:
    # Never serialize exception messages, tracebacks, stdout, config or locals.
    event = event if event in EVENTS else "launcher_failed"
    suffix = f" exit={exit_code}" if type(exit_code) is int else ""
    _log_line(f"{event}{suffix}")
    ctypes.windll.user32.MessageBoxW(
        None,
        "Não foi possível iniciar ou manter a CHLOE AI em execução.\n\n"
        "Feche a janela da CHLOE, caso esteja aberta, e tente novamente.\n"
        "Se o problema persistir, confira o ambiente Python do projeto.\n\n"
        "Diagnóstico sem credenciais: logs\\launcher.log\n"
        f"Código: {event}{suffix}",
        "CHLOE AI — falha na execução", 0x10 | 0x10000,
    )


def classify(exc_type: type, origin: str) -> str:
    origin = origin if origin in _ORIGINS else "ui"
    category = "unexpected"
    for types, name in _CATEGORIES:
        if isinstance(exc_type, type) and issubclass(exc_type, types):
            category = name
            break
    return f"{origin}_{category}_error"


def _safe_path(filename: str) -> str:
    """Caminho sem a pasta do usuário: relativo ao projeto ou ao site-packages."""
    path = Path(filename)
    try:
        return path.resolve().relative_to(BASE).as_posix()
    except (ValueError, OSError):
        pass
    parts = path.parts
    if "site-packages" in parts:
        return "site-packages/" + "/".join(parts[parts.index("site-packages") + 1:])
    return "<python>/" + path.name


def _frames(tb) -> list[tuple[str, int, str]]:
    frames = []
    while tb is not None:
        code = tb.tb_frame.f_code
        frames.append((_safe_path(code.co_filename), tb.tb_lineno,
                       getattr(code, "co_qualname", code.co_name)))
        tb = tb.tb_next
    return frames


def _is_project(path: str) -> bool:
    return not path.startswith(("<", "site-packages/", ".venv/"))


class _SafeTracebackFormatter(logging.Formatter):
    """Traceback com tipo, arquivo, linha e função — nunca a mensagem da exceção,
    que pode conter URLs com chave, conteúdo do usuário ou caminhos privados."""

    def formatException(self, exc_info) -> str:  # noqa: N802
        exc_type, exc, tb = exc_info
        lines = ["    Traceback (chamada mais recente por último; mensagem omitida):"]
        lines += [f"      {path}:{line} em {name}" for path, line, name in _frames(tb)]
        lines.append(f"    {getattr(exc_type, '__qualname__', 'Exception')}")
        seen, cause = 0, exc
        while seen < 3 and cause is not None:
            cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
            if cause is not None:
                lines.append(f"    causada por: {type(cause).__qualname__}")
            seen += 1
        return "\n".join(lines)


def _crash_logger() -> logging.Logger:
    logger = logging.getLogger("chloe.launcher.crash")
    for old in list(logger.handlers):
        if getattr(old, "baseFilename", None) != os.path.abspath(LOG):
            logger.removeHandler(old)
            old.close()
    if not logger.handlers:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG, encoding="utf-8", delay=True)
        handler.setFormatter(_SafeTracebackFormatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
    return logger


def log_exception(origin: str, exc_type, exc, tb, thread_name: str | None = None,
                  dialog: bool = True) -> str:
    """Registra uma exceção recuperável e avisa o usuário uma vez por assinatura.

    Retorna o código do evento. Nunca levanta: um hook que falha esconderia o erro original.
    """
    event = classify(exc_type, origin)
    try:
        frames = _frames(tb)
        project = [f for f in frames if _is_project(f[0])]
        where = project[0] if project else (frames[0] if frames else ("?", 0, "?"))
        signature = f"{event}|{exc_type.__qualname__}|" + "|".join(f"{p}:{n}" for p, _, n in frames)
        ref = hashlib.sha256(signature.encode()).hexdigest()[:8]
        thread_name = thread_name or threading.current_thread().name
        _crash_logger().error(
            "%s ref=%s thread=%s origem=%s (%s:%d) tipo=%s",
            event, ref, thread_name, where[2], where[0], where[1], exc_type.__qualname__,
            exc_info=(exc_type, exc, tb),
        )
    except Exception:  # noqa: BLE001 - último recurso: ao menos o código do evento
        ref = "-"
        try:
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(f"{datetime.now().isoformat(timespec='seconds')} {event}\n")
        except OSError:
            pass
    if dialog:
        with _shown_lock:
            first = ref not in _shown_signatures
            _shown_signatures.add(ref)
        if first:
            ctypes.windll.user32.MessageBoxW(
                None,
                "A CHLOE AI encontrou um erro interno, mas continua em execução.\n\n"
                "Se algo na tela parar de responder, use ⤢ na Ilha para abrir a janela "
                "completa, ou feche e abra a CHLOE novamente. Este aviso não se repete "
                "para o mesmo erro nesta sessão.\n\n"
                "Detalhes técnicos, sem credenciais: logs\\launcher.log\n"
                f"Código: {event} · ref {ref}",
                "CHLOE AI — erro interno", 0x30 | 0x10000 | 0x40000,  # aviso, frente, topo
            )
    return event


def _ui_excepthook(exc_type, exc, tb) -> None:
    # PyQt6 entrega aqui exceções de slots e eventos; o laço de eventos continua.
    log_exception("ui", exc_type, exc, tb)


def _thread_excepthook(args) -> None:
    if args.exc_type is SystemExit or (isinstance(args.exc_type, type) and issubclass(args.exc_type, SystemExit)):
        return
    log_exception("worker", args.exc_type, args.exc_value, args.exc_traceback,
                  thread_name=getattr(args.thread, "name", None))


class _AsyncioErrorHandler(logging.Handler):
    """Tarefas asyncio com exceção não recuperada: só registra (o laço segue vivo)."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and record.exc_info[0] is not None:
            log_exception("async", *record.exc_info, thread_name=record.threadName, dialog=False)


def install_exception_hooks() -> logging.Handler:
    """Instala os hooks globais; devolve o handler do asyncio para ser removido no fim."""
    sys.excepthook = _ui_excepthook
    threading.excepthook = _thread_excepthook
    handler = _AsyncioErrorHandler(logging.ERROR)
    logging.getLogger("asyncio").addHandler(handler)
    return handler


def existing_processes(supervisors: bool = False) -> set[int]:
    """PIDs da CHLOE já abertos a partir desta pasta.

    Padrão: o processo do app (main.py direto ou launch_chloe.py --run-app).
    supervisors=True: os launchers que esperam por esse app terminar.
    """
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
                is_app = Path(arg).name == "main.py" or "--run-app" in info["cmdline"]
                if is_app == supervisors:
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


def stale_instances(pids: set[int]) -> dict[int, float]:
    """Instâncias abertas antes da última edição do código: rodam a versão antiga."""
    import psutil

    newest, stale = code_mtime(), {}
    for pid in pids:
        try:
            started = psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if started < newest:
            stale[pid] = started
    return stale


def terminate(pids: set[int], timeout: float = 5.0) -> None:
    import psutil

    procs = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def ask_restart_stale(started: float) -> bool:
    answer = ctypes.windll.user32.MessageBoxW(
        None,
        "Já existe uma CHLOE AI aberta, mas ela foi iniciada antes da última "
        "atualização do código (" + _stamp(started) + ") e continua rodando a versão "
        "antiga — inclusive erros já corrigidos.\n\n"
        "Reiniciar agora com a versão atual? Conversas em andamento nessa janela serão fechadas.",
        "CHLOE AI — versão desatualizada aberta", 0x4 | 0x30 | 0x10000,  # Sim/Não, aviso, frente
    )
    return answer == 6  # IDYES


def run_application() -> int:
    _log_line(f"app_started pid={os.getpid()} python={_safe_path(sys.executable)} "
              f"v{sys.version.split()[0]} entry=main.py code={_stamp(code_mtime())}")
    # pythonw has no standard streams. Give legacy print calls a safe sink.
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
        asyncio_handler = install_exception_hooks()
        try:
            runpy.run_path(str(BASE / "main.py"), run_name="__main__",
                           init_globals={"_CHLOE_SUPERVISED": True})
        except SystemExit as exc:
            return exc.code if type(exc.code) is int else (0 if exc.code is None else 1)
        except BaseException:
            return 1  # The supervisor displays the failure, without raw details.
        finally:
            logging.getLogger("asyncio").removeHandler(asyncio_handler)
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
        supervisors = existing_processes(supervisors=True) - ancestors - {os.getpid()}
        if already_running or pids:
            stale = stale_instances(pids)
            _log_line(f"instance_already_running app={sorted(pids)} launchers={sorted(supervisors)} "
                      f"stale={sorted(stale)} code={_stamp(code_mtime())}")
            if not stale or not ask_restart_stale(min(stale.values())):
                show_existing(pids)
                return 0
            # Launcher antigo primeiro: senão ele veria o app morrer e mostraria falha.
            terminate(supervisors)
            terminate(pids)
            _log_line(f"stale_instance_closed app={sorted(pids)} launchers={sorted(supervisors)}")
        pythonw = BASE / ".venv" / "Scripts" / "pythonw.exe"
        command = [str(pythonw), "-B", str(Path(__file__).resolve()), "--run-app"]
        _log_line(f"app_launch python={_safe_path(pythonw)} script={_safe_path(command[2])} args=-B,--run-app")
        # stderr do filho = fd 2 nativo: Qt, falhas fatais do Python e erros antes dos hooks.
        result = subprocess.run(
            command, cwd=BASE, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log_child_exit(result.returncode, result.stderr or b"")
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
