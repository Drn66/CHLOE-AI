"""
Authenticated LAN dashboard, available exclusively over validated TLS.

The OS firewall is never modified automatically. Pairing, bearer sessions,
WebSockets and queued commands share device-scoped, revocable authorization.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import ssl
import threading
import base64
import hashlib
import re
import secrets
import socket
import string
import time
from pathlib import Path

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
PORT        = 8000
MAX_UPLOAD_MB = 500


# Preserve the existing upload location without creating personal directories at
# module import. Creation happens only as part of an authorized upload.
_UPLOAD_LOCATIONS = (Path.home() / "Downloads" / "JARVIS Uploads",
                     Path.home() / "Documents" / "JARVIS Uploads", BASE_DIR / "uploads")
UPLOADS_DIR = next((path for path in _UPLOAD_LOCATIONS if path.is_dir()), _UPLOAD_LOCATIONS[0])

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Explain network access without altering OS policy, profiles or firewall.

    This operation is deliberately idempotent and needs no elevation. An
    administrator can authorize a specific TCP port on a private LAN following
    the project's documented instructions; no broad Python rule is created.
    """
    print(f"[Dashboard] TLS usa TCP {port}. O firewall e o perfil de rede foram preservados.")


def windows_firewall_commands(port: int) -> dict[str, list[str]]:
    """Explicit, reversible administrator instructions; never executed here.

    A dedicated rule name means unrelated and legacy rules are not changed.
    Query before adding prevents duplicates when these instructions are used.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("Invalid dashboard port")
    name = f"CHLOE Dashboard TLS {port}"
    prefix = ["netsh", "advfirewall", "firewall"]
    return {
        "query": prefix + ["show", "rule", f"name={name}"],
        "add": prefix + ["add", "rule", f"name={name}", "dir=in", "action=allow",
                         "protocol=TCP", f"localport={port}", "profile=private",
                         "remoteip=LocalSubnet", "enable=yes"],
        "remove": prefix + ["delete", "rule", f"name={name}", "protocol=TCP", f"localport={port}"],
    }


def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _ensure_certs() -> bool:
    """
    Make sure config/certs holds a TLS key pair, generating a self-signed one the
    first time the dashboard runs.

    The pair is deliberately NOT shipped in the repository. A private key that
    every user downloads is the same as having no private key at all: anyone can
    present a certificate that matches it. Generating locally gives each install
    its own key, costs about a second, and happens exactly once.

    Returns True only for a usable pair. Failure disables the remote listener;
    an existing invalid or partial pair is preserved for operator recovery.
    """
    certs = BASE_DIR / "config" / "certs"
    key_p = certs / "jarvis.key"
    crt_p = certs / "jarvis.crt"
    if key_p.exists() or crt_p.exists():
        return _tls_context() is not None

    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("[Dashboard] Painel remoto desativado: suporte TLS indisponível.")
        print("[Dashboard] For HTTPS run:  pip install cryptography")
        return False

    try:
        certs.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        who = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "JARVIS Dashboard"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVIS"),
        ])

        # The SAN has to cover every address the phone might use: the LAN IP the
        # QR code encodes, plus localhost when testing on the machine itself.
        alt = [x509.DNSName("localhost"),
               x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]
        try:
            lan = _local_ip()
            if not lan.startswith("127."):
                alt.append(x509.IPAddress(ipaddress.IPv4Address(lan)))
        except Exception:
            pass          # no LAN address resolvable — localhost entries still work

        # Timezone-aware UTC: datetime.utcnow() is deprecated from Python 3.12 on,
        # and the builder normalises aware values to UTC itself.
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(who)
            .issuer_name(who)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )

        key_p.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        try:
            import os as _os
            _os.chmod(key_p, 0o600)   # best effort — largely a no-op on Windows
        except Exception:
            pass

        print(f"[Dashboard] Generated a self-signed certificate for this machine: {certs}")
        return _tls_context() is not None
    except Exception:
        print("[Dashboard] Painel remoto desativado: não foi possível preparar o certificado TLS.")
        return False


def _tls_context() -> ssl.SSLContext | None:
    """Validate date, certificate/key consistency and OpenSSL usability before bind."""
    try:
        from cryptography import x509
        certs = BASE_DIR / "config" / "certs"
        certificate = x509.load_pem_x509_certificate((certs / "jarvis.crt").read_bytes())
        now = datetime.now(timezone.utc)
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            return None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # A password-protected/unusable key must fail without an interactive prompt.
        context.load_cert_chain(str(certs / "jarvis.crt"), str(certs / "jarvis.key"), password=lambda: "")
        return context
    except Exception:
        # All certificate/provider failures must leave the network listener closed.
        return None


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────

_SESSION_TTL = 8 * 60 * 60
_DEVICE_TTL = 30 * 24 * 60 * 60
_COMMAND_TTL = 60 * 60
_COMMAND_WAIT_SECONDS = 10.0
_MAX_COMMANDS = 1000
_MAX_TEXT = 20000


@dataclass(repr=False)
class _Session:
    device_id: str
    expires_at: float


@dataclass(repr=False)
class DashboardCommand:
    """A delivery attempt retains its device/session identity until completion."""
    id: str
    text: str = field(repr=False)
    token: str = field(repr=False)
    device_id: str = field(repr=False)
    created_at: float
    status: str = "pending"
    started: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class DashboardServer:
    def __init__(self):
        self._ip = _local_ip()
        self._auth_lock = threading.RLock()
        self._tokens: dict[str, _Session] = {}
        self._token_keys: dict[str, str] = {}
        self._aes_cache: dict[str, bytes] = {}
        self._clients: set[WebSocket] = set()
        self._connections: dict[WebSocket, str] = {}
        self._history: list[dict] = []
        self._command_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._commands: OrderedDict[tuple[str, str], DashboardCommand] = OrderedDict()
        self._wake_callback = None
        self._connect_callback = None
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}
        self._phone_audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._uploads_dir = UPLOADS_DIR
        self._login_html = _read("login.html")
        self._app_html = _read("app.html")
        self._login_attempts: OrderedDict[str, list[float]] = OrderedDict()
        self.available = False
        self.status_message = "Painel remoto aguardando validação TLS."
        self._server = None
        self._serve_task = None
        self._stop_lock = asyncio.Lock()
        self._stopping = False
        self.app = self._build_app()

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.monotonic()
        with self._auth_lock:
            self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
            if len(self._pending_keys) >= 128:
                self._pending_keys.pop(next(iter(self._pending_keys)))
            key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
            self._pending_keys[key] = now + max(1, min(expiry_secs, 600))
            return key

    @staticmethod
    def _ssl_enabled() -> bool:
        return _tls_context() is not None

    def get_url(self) -> str:
        if not self._ssl_enabled():
            raise RuntimeError("Painel remoto indisponível: certificado TLS inválido ou ausente.")
        return f"https://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        return self.get_url().removeprefix("https://")

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _session(self, token: str) -> _Session | None:
        with self._auth_lock:
            session = self._tokens.get(token)
            if session is None:
                return None
            if session.expires_at <= time.monotonic():
                self._tokens.pop(token, None)
                self._token_keys.pop(token, None)
                return None
            return session

    def _issue_session(self, key: str, device_id: str) -> str:
        # Called under _auth_lock; existing sessions cannot be re-created by revoke.
        now = time.monotonic()
        for token, session in list(self._tokens.items()):
            if session.expires_at <= now:
                self._tokens.pop(token, None)
                self._token_keys.pop(token, None)
        if len(self._tokens) >= 1024:
            raise RuntimeError("Session limit")
        token = secrets.token_urlsafe(32)
        self._tokens[token] = _Session(device_id, now + _SESSION_TTL)
        self._token_keys[token] = key
        self._aes_key(key)
        return token

    def _pair(self, key: str) -> dict | None:
        with self._auth_lock:
            now = time.monotonic()
            if key not in self._pending_keys or self._pending_keys[key] <= now:
                return None
            self._device_sessions = {token: device for token, device in self._device_sessions.items()
                                     if device["expires_at"] > now}
            if len(self._device_sessions) >= 256:
                return None
            device_id = secrets.token_urlsafe(16)
            try:
                token = self._issue_session(key, device_id)
            except RuntimeError:
                return None
            del self._pending_keys[key]
            device_token = secrets.token_urlsafe(32)
            self._device_sessions[device_token] = {
                "device_id": device_id, "session_key": key, "expires_at": now + _DEVICE_TTL,
                "paired_at": time.time(),
            }
            return {"ok": True, "token": token, "key": key,
                    "device_token": device_token, "device_id": device_id}

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        if self._session(token) is None:
            return None
        key = self._token_keys.get(token)
        if not key:
            return None
        try:
            return _decrypt_cbc(self._aes_key(key), enc_b64)
        except Exception:
            return None

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def command_authorized(self, command: DashboardCommand) -> bool:
        session = self._session(command.token)
        return bool(session and session.device_id == command.device_id
                    and command.status == "pending")

    def begin_command(self, command: DashboardCommand) -> bool:
        """Atomic authorization boundary immediately before starting the transport."""
        with self._auth_lock:
            if not self.command_authorized(command) or command.started:
                return False
            command.started = True
            return True

    def complete_command(self, command: DashboardCommand, success: bool, *, uncertain: bool = False) -> None:
        with self._auth_lock:
            if command.status != "pending":
                return
            command.status = "delivered" if success else "unknown" if uncertain else "failed"
            command.done.set()

    async def next_command(self, timeout: float | None = None) -> DashboardCommand:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            command = await asyncio.wait_for(self._command_queue.get(), timeout=remaining)
            if self.command_authorized(command):
                return command
            self.complete_command(command, False)

    def phone_audio_authorized(self, frame: dict) -> bool:
        return self._session(frame.get("_token", "")) is not None

    def _queue_command(self, token: str, text: str, command_id: str,
                       created_at: float | None = None) -> tuple[DashboardCommand | None, int]:
        with self._auth_lock:
            session = self._session(token)
            if session is None:
                return None, 401
            now = time.monotonic()
            for key, command in list(self._commands.items()):
                if now - command.created_at > _COMMAND_TTL and command.status != "pending":
                    del self._commands[key]
            key = (session.device_id, command_id)
            old = self._commands.get(key)
            if old:
                if old.text != text:
                    return None, 409
                if old.status != "failed":
                    return old, 200
                # Only a definite failure before delivery permits another attempt.
            elif created_at is not None:
                paired_at = next((device["paired_at"] for device in self._device_sessions.values()
                                  if device["device_id"] == session.device_id), time.time())
                # After TTL expiry or re-pairing, an old browser attempt must not
                # become a fresh command merely because its dedup record vanished.
                if created_at < max(time.time() - _COMMAND_TTL, paired_at - 60):
                    return None, 408
            if old is None and len(self._commands) >= _MAX_COMMANDS:
                return None, 429
            command = DashboardCommand(command_id, text, token, session.device_id, now)
            try:
                self._command_queue.put_nowait(command)
            except asyncio.QueueFull:
                return None, 429
            self._commands[key] = command
            return command, 200

    @staticmethod
    def _command_response(command: DashboardCommand) -> JSONResponse:
        status = command.status
        message = {
            "pending": "Aguardando confirmação de entrega. Consulte novamente sem alterar a mensagem.",
            "unknown": "A entrega não pôde ser confirmada. O comando não será reenviado automaticamente.",
            "failed": "A mensagem não foi encaminhada. Você pode tentar novamente.",
        }
        payload = {"ok": status == "delivered", "id": command.id, "status": status}
        if status in message:
            payload["error"] = message[status]
        return JSONResponse(payload, status_code=200 if status == "delivered" else 503 if status == "failed" else 202)

    async def _revoke(self, device_id: str | None) -> int:
        with self._auth_lock:
            devices = {session.device_id for session in self._tokens.values()}
            devices.update(device["device_id"] for device in self._device_sessions.values())
            revoked = devices if device_id is None else devices.intersection({device_id})
            tokens = {token for token, session in self._tokens.items() if session.device_id in revoked}
            for token in tokens:
                self._tokens.pop(token, None)
                self._token_keys.pop(token, None)
            self._device_sessions = {token: device for token, device in self._device_sessions.items()
                                     if device["device_id"] not in revoked}
            if device_id is None:
                self._pending_keys.clear()
            self._aes_cache.clear()
            for command in self._commands.values():
                if command.device_id in revoked and command.status == "pending":
                    self.complete_command(command, False, uncertain=command.started)
            queued = []
            while not self._command_queue.empty():
                command = self._command_queue.get_nowait()
                if command.device_id not in revoked:
                    queued.append(command)
            for command in queued:
                self._command_queue.put_nowait(command)
            connections = [ws for ws, token in self._connections.items() if token in tokens]
            # Purge buffered audio before the relay can consume frames after revocation.
            retained = []
            while not self._phone_audio_queue.empty():
                frame = self._phone_audio_queue.get_nowait()
                if frame.get("_token") not in tokens:
                    retained.append(frame)
            for frame in retained:
                self._phone_audio_queue.put_nowait(frame)
            for websocket in connections:
                self._clients.discard(websocket)
                self._connections.pop(websocket, None)
        # Credentials are invalid before yielding to network close operations.
        async def close_connection(websocket):
            try:
                await asyncio.wait_for(websocket.close(code=4001, reason="Sessão revogada"), timeout=0.5)
            except (asyncio.TimeoutError, RuntimeError, WebSocketDisconnect):
                pass
        await asyncio.gather(*(close_connection(websocket) for websocket in connections))
        return len(revoked)

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        del self._history[:-300]
        for websocket in list(self._clients):
            try:
                if self._session(self._connections.get(websocket, "")) is None:
                    await websocket.close(code=4001)
                    self._clients.discard(websocket)
                    self._connections.pop(websocket, None)
                    continue
                await websocket.send_json(msg)
            except (RuntimeError, WebSocketDisconnect):
                self._clients.discard(websocket)
                self._connections.pop(websocket, None)

    def _login_allowed(self, request: Request) -> bool:
        now = time.monotonic()
        host = request.client.host if request.client else "unknown"
        attempts = [t for t in self._login_attempts.pop(host, []) if now - t < 60]
        attempts.append(now)
        self._login_attempts[host] = attempts[-11:]
        while len(self._login_attempts) > 1024:
            self._login_attempts.popitem(last=False)
        return len(attempts) <= 10

    async def _connected(self, websocket: WebSocket, token: str, *, history: bool) -> bool:
        if self._session(token) is None or len(self._connections) >= 512:
            await websocket.close(code=4001)
            return False
        await websocket.accept()
        # A revoke may run during accept; registration must not resurrect access.
        if self._session(token) is None or len(self._connections) >= 512:
            await websocket.close(code=4001)
            return False
        self._connections[websocket] = token
        if history:
            self._clients.add(websocket)
        return True

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def private_headers(request: Request, call_next):
            # Run before multipart/form-data parsing as well as before endpoint code.
            # Unauthorized clients must not consume upload storage or parse payloads.
            path = request.url.path
            if path.startswith("/api/") and path != "/api/device-login" and not authenticated(request):
                return unauthorized()
            if path.startswith("/uploads/") and self._session(request.query_params.get("token", "")) is None:
                return unauthorized()
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        def bearer(request: Request) -> str:
            authorization = request.headers.get("authorization", "")
            return authorization[7:].strip() if authorization.startswith("Bearer ") else ""

        def authenticated(request: Request) -> bool:
            return self._session(bearer(request)) is not None

        async def body_object(request: Request) -> dict | None:
            # Bound data before JSON parsing; command/upload bodies have separate limits.
            length = request.headers.get("content-length", "0")
            try:
                if int(length) > 100000:
                    return None
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 100000:
                        return None
                value = json.loads(body)
                return value if isinstance(value, dict) else None
            except (ValueError, TypeError):
                return None

        def unauthorized():
            return JSONResponse({"ok": False, "error": "Sessão inválida ou revogada."}, status_code=401)

        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE), media_type="application/javascript")
            return JSONResponse({"error": "Biblioteca local indisponível."}, status_code=503)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return HTMLResponse(self._app_html.replace("__IP__", self._ip).replace("__PORT__", str(PORT)))

        @app.post("/login")
        async def login(request: Request):
            if not self._login_allowed(request):
                return JSONResponse({"ok": False, "error": "Aguarde antes de tentar novamente."}, status_code=429)
            body = await body_object(request)
            if body is None or not isinstance(body.get("pin"), str):
                return JSONResponse({"ok": False}, status_code=400)
            paired = self._pair(body["pin"].strip().upper())
            if not paired:
                return unauthorized()
            if self._connect_callback:
                self._connect_callback()
            return JSONResponse(paired)

        @app.get("/auto-login")
        async def auto_login(request: Request, key: str = ""):
            if not self._login_allowed(request):
                return HTMLResponse("Muitas tentativas. Aguarde um minuto.", status_code=429)
            paired = self._pair(key)
            if not paired:
                return HTMLResponse("Link expirado. Gere um novo código na CHLOE AI.", status_code=401)
            if self._connect_callback:
                self._connect_callback()
            # All interpolated fields are generated URL-safe tokens or alphabet-only PINs.
            return HTMLResponse("<!doctype html><meta charset='utf-8'><script>"
                                f"sessionStorage.setItem('jarvis_token','{paired['token']}');"
                                f"sessionStorage.setItem('jarvis_key','{paired['key']}');"
                                f"sessionStorage.setItem('jarvis_device_id','{paired['device_id']}');"
                                f"localStorage.setItem('jarvis_device_token','{paired['device_token']}');"
                                "location.replace('/');</script><p>Conectando à CHLOE AI…</p>")

        @app.post("/api/device-login")
        async def device_login(request: Request):
            body = await body_object(request)
            if body is None or not isinstance(body.get("device_token"), str):
                return JSONResponse({"ok": False}, status_code=400)
            with self._auth_lock:
                device = self._device_sessions.get(body["device_token"])
                if device is None or device["expires_at"] <= time.monotonic():
                    return unauthorized()
                try:
                    token = self._issue_session(device["session_key"], device["device_id"])
                except RuntimeError:
                    return JSONResponse({"ok": False}, status_code=503)
            if self._connect_callback:
                self._connect_callback()
            return JSONResponse({"ok": True, "token": token, "key": device["session_key"],
                                 "device_id": device["device_id"]})

        @app.post("/api/revoke-devices")
        async def revoke_devices(request: Request):
            if not authenticated(request):
                return unauthorized()
            body = await body_object(request) if await request.body() else {}
            if body is None or ("device_id" in body and not isinstance(body["device_id"], str)):
                return JSONResponse({"ok": False}, status_code=400)
            # JSON/body reads yield control; authorization must be checked again.
            if not authenticated(request):
                return unauthorized()
            return JSONResponse({"ok": True, "revoked": await self._revoke(body.get("device_id"))})

        @app.post("/api/command")
        async def command(request: Request):
            if not authenticated(request):
                return unauthorized()
            body = await body_object(request)
            if body is None:
                return JSONResponse({"ok": False}, status_code=400)
            token = bearer(request)
            if self._session(token) is None:
                return unauthorized()
            enc = body.get("enc")
            text = self._decrypt(token, enc) if isinstance(enc, str) and enc else body.get("text")
            command_id = body.get("id", secrets.token_urlsafe(16))
            created_at = body.get("created_at")
            if (not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT
                    or not isinstance(command_id, str)
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", command_id) is None
                    or (created_at is not None and (isinstance(created_at, bool)
                        or not isinstance(created_at, (int, float))
                        or not math.isfinite(created_at) or created_at > time.time() + 60))):
                return JSONResponse({"ok": False, "error": "Mensagem inválida."}, status_code=400)
            queued, code = self._queue_command(token, text.strip(), command_id, created_at)
            if queued is None:
                if code == 408:
                    return JSONResponse({"ok": False, "id": command_id, "status": "unknown",
                                         "error": "Tentativa antiga: entrega não confirmada; nenhum reenvio foi iniciado."},
                                        status_code=202)
                return unauthorized() if code == 401 else JSONResponse(
                    {"ok": False, "id": command_id, "status": "failed", "error": "Não foi possível aceitar a mensagem."},
                    status_code=code)
            if self._wake_callback:
                self._wake_callback()
            if queued.status == "pending":
                try:
                    await asyncio.wait_for(queued.done.wait(), timeout=_COMMAND_WAIT_SECONDS)
                except asyncio.TimeoutError:
                    pass
            if self._session(token) is None:
                return unauthorized()
            return self._command_response(queued)

        @app.post("/api/wake")
        async def wake(request: Request):
            if not authenticated(request):
                return unauthorized()
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        @app.websocket("/ws/phone-audio")
        async def phone_audio(websocket: WebSocket, token: str = ""):
            if not await self._connected(websocket, token, history=False):
                return
            try:
                while self._session(token) is not None:
                    try:
                        data = await asyncio.wait_for(websocket.receive_bytes(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if self._session(token) is None:
                        break
                    if len(data) > 65536:
                        await websocket.close(code=1009)
                        break
                    try:
                        self._phone_audio_queue.put_nowait({"data": data, "mime_type": "audio/pcm", "_token": token})
                    except asyncio.QueueFull:
                        pass
            except (WebSocketDisconnect, RuntimeError, KeyError, TypeError):
                pass
            finally:
                self._connections.pop(websocket, None)
                try:
                    await websocket.close(code=4001)
                except (RuntimeError, WebSocketDisconnect):
                    pass

        @app.websocket("/ws")
        async def messages(websocket: WebSocket, token: str = ""):
            if not await self._connected(websocket, token, history=True):
                return
            try:
                for entry in self._history[-50:]:
                    if self._session(token) is None:
                        return
                    await websocket.send_json(entry)
                while self._session(token) is not None:
                    try:
                        data = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if self._session(token) is None:
                        break
                    # Commands share the HTTP path and its ID/delivery protocol.
                    # Legacy command frames are rejected explicitly, never queued unaudited.
                    if isinstance(data, dict) and data.get("type") == "command":
                        await websocket.send_json({"type": "error", "text": "Envie comandos pela API de entrega confirmada."})
            except (WebSocketDisconnect, RuntimeError, ValueError, KeyError, TypeError):
                pass
            finally:
                self._clients.discard(websocket)
                self._connections.pop(websocket, None)
                try:
                    await websocket.close(code=4001)
                except (RuntimeError, WebSocketDisconnect):
                    pass

        def safe_download(filename: str) -> Path | None:
            if Path(filename).name != filename or filename in ("", ".", "..") or "\\" in filename:
                return None
            root = self._uploads_dir.resolve()
            path = root / filename
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                    return None
            except OSError:
                return None
            return path

        @app.get("/uploads/{filename}")
        async def download(filename: str, token: str = ""):
            if self._session(token) is None:
                return unauthorized()
            path = safe_download(filename)
            if path is None:
                return JSONResponse({"error": "Arquivo indisponível."}, status_code=404)
            async def content():
                try:
                    with path.open("rb") as stream:
                        while self._session(token) is not None:
                            chunk = stream.read(65536)
                            if not chunk:
                                break
                            yield chunk
                            await asyncio.sleep(0)
                except OSError:
                    return
            return StreamingResponse(content(), media_type="application/octet-stream")

        @app.get("/api/files")
        async def files(request: Request):
            if not authenticated(request):
                return unauthorized()
            result = []
            try:
                for path in sorted(self._uploads_dir.iterdir()):
                    if safe_download(path.name) is not None:
                        result.append({"name": path.name, "size": path.stat().st_size})
            except OSError:
                pass
            return JSONResponse({"files": result})

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload(request: Request, file: UploadFile = FastAPIFile(...)):
                if not authenticated(request):
                    return unauthorized()
                safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', file.filename or 'upload').strip('. ')
                if not safe or len(safe) > 200:
                    return JSONResponse({"error": "Nome de arquivo inválido."}, status_code=400)
                dest = None
                success = False
                try:
                    self._uploads_dir.mkdir(parents=True, exist_ok=True)
                    for number in range(1000):
                        candidate = self._uploads_dir / (safe if number == 0 else f"{Path(safe).stem}_{number}{Path(safe).suffix}")
                        try:
                            output = candidate.open("xb")
                            dest = candidate
                            break
                        except FileExistsError:
                            continue
                    if dest is None:
                        return JSONResponse({"error": "Limite de nomes atingido."}, status_code=409)
                    size = 0
                    with output:
                        while True:
                            chunk = await file.read(65536)
                            if not authenticated(request):
                                return unauthorized()
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > MAX_UPLOAD_MB * 1024 * 1024:
                                return JSONResponse({"error": "Arquivo excede o limite permitido."}, status_code=413)
                            output.write(chunk)
                    success = True
                    await self.broadcast({"type": "file_received", "name": dest.name,
                                          "size": size, "saved_to": str(self._uploads_dir)})
                    return JSONResponse({"ok": True, "name": dest.name, "size": size})
                except OSError:
                    return JSONResponse({"error": "Não foi possível salvar o arquivo."}, status_code=500)
                finally:
                    await file.close()
                    if dest is not None and not success:
                        dest.unlink(missing_ok=True)
        else:
            @app.post("/api/upload")
            async def upload_unavailable(request: Request):
                if not authenticated(request):
                    return unauthorized()
                return JSONResponse({"error": "Upload indisponível nesta instalação."}, status_code=503)
        return app

    async def _serve_alias(self) -> None:
        # One canonical TLS port avoids a second listener with a separate lifecycle.
        return

    def _close_listeners(self) -> None:
        """Stop accepting immediately, including when graceful shutdown times out."""
        if self._server is not None:
            self._server.should_exit = True
            for listener in getattr(self._server, "servers", ()):
                listener.close()

    async def stop(self) -> None:
        """Idempotent shutdown: invalidate access, close sockets, await the owner task."""
        async with self._stop_lock:
            self._stopping = True
            self.available = False
            deadline = time.monotonic() + 3.0
            self._close_listeners()
            try:
                await asyncio.wait_for(self._revoke(None), timeout=0.75)
            except asyncio.TimeoutError:
                # Revocation invalidates credentials before its first network await.
                pass
            task = self._serve_task
            if task is not None and task is not asyncio.current_task() and not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=max(0.01, deadline - time.monotonic() - 1.0))
                except asyncio.TimeoutError:
                    self._close_listeners()
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=max(0.01, deadline - time.monotonic()))
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        self._close_listeners()
            self.status_message = "Painel remoto encerrado."

    async def serve(self) -> None:
        if self._stopping or (self._serve_task is not None and not self._serve_task.done()):
            return
        self._serve_task = asyncio.current_task()
        try:
            await self._serve_tls()
        finally:
            self._serve_task = None

    async def _serve_tls(self) -> None:
        self.available = False
        if not _DEPS_OK:
            self.status_message = "Painel remoto indisponível: dependências ausentes."
            print("[Dashboard] " + self.status_message)
            return
        if not _ensure_certs():
            self.status_message = "Painel remoto desativado: certificado TLS ausente, inválido ou expirado."
            print("[Dashboard] " + self.status_message)
            return
        context = _tls_context()
        if context is None:
            self.status_message = "Painel remoto desativado: certificado TLS inutilizável."
            print("[Dashboard] " + self.status_message)
            return
        _ensure_network_access(PORT)
        # Uvicorn must use the exact context validated above, avoiding a second
        # key-file read between validation and bind (certificate replacement race).
        config = uvicorn.Config(self.app, host="0.0.0.0", port=PORT, log_level="warning",
                                ssl_keyfile=str(BASE_DIR / "config" / "certs" / "jarvis.key"),
                                ssl_certfile=str(BASE_DIR / "config" / "certs" / "jarvis.crt"),
                                access_log=False, ws_max_size=100000,
                                timeout_graceful_shutdown=0.75)
        try:
            config.load()
            config.ssl = context
            self.available = True
            self.status_message = "Painel remoto disponível por HTTPS."
            self._server = uvicorn.Server(config)
            await self._server.serve()
        except asyncio.CancelledError:
            self._close_listeners()
            if self._server is not None and getattr(self._server, "started", False):
                # Uvicorn's normal shutdown is skipped if its serve task is cancelled.
                try:
                    await asyncio.wait_for(self._server.shutdown(), timeout=0.75)
                except asyncio.TimeoutError:
                    self._close_listeners()
            raise
        except (OSError, ValueError, ssl.SSLError, SystemExit):
            self.status_message = "Painel remoto indisponível: não foi possível iniciar HTTPS."
            print("[Dashboard] " + self.status_message)
        finally:
            self.available = False
            await self._revoke(None)
            self._server = None
