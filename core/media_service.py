"""
core/media_service.py — Monitor do player do Windows (GSMTC) para a Ilha Dinâmica.

Usa a API oficial GlobalSystemMediaTransportControlsSessionManager, a mesma que
alimenta o painel de mídia do Windows. Funciona com Spotify, YouTube no Chrome/Edge/
Firefox, Deezer, VLC, Mídia do Windows etc.

Dependências (uma das duas opções):
    pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Media.Control winrt-Windows.Storage.Streams
    pip install winsdk          # pacote antigo, ainda funciona

Uso:
    media = MediaService()
    media.media_changed.connect(island.update_media)   # recebe MediaInfo | None
    island.media_command.connect(media.send_command)    # "previous" | "play_pause" | "next"
    media.start()
    ...
    media.stop()
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger("chloe.media")

# ── importação das bindings WinRT (pywinrt moderno → winsdk) ─────────────────
_BINDINGS = None
try:
    from winrt.windows.media.control import (  # type: ignore
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
    )
    from winrt.windows.storage.streams import (  # type: ignore
        Buffer as _Buffer,
        DataReader as _DataReader,
        InputStreamOptions as _InputStreamOptions,
    )
    _BINDINGS = "winrt"
except ImportError:
    try:
        from winsdk.windows.media.control import (  # type: ignore
            GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
        )
        from winsdk.windows.storage.streams import (  # type: ignore
            Buffer as _Buffer,
            DataReader as _DataReader,
            InputStreamOptions as _InputStreamOptions,
        )
        _BINDINGS = "winsdk"
    except ImportError:
        _SessionManager = None

# GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
_STATUS_PLAYING = 4

_FRIENDLY_SOURCES = (
    ("spotify", "Spotify"),
    ("chrome", "Chrome"),
    ("msedge", "Edge"),
    ("firefox", "Firefox"),
    ("opera", "Opera"),
    ("brave", "Brave"),
    ("deezer", "Deezer"),
    ("vlc", "VLC"),
    ("zunemusic", "Mídia do Windows"),
    ("microsoft.media", "Mídia do Windows"),
    ("applemusic", "Apple Music"),
    ("tidal", "TIDAL"),
    ("youtube", "YouTube"),
)


def friendly_source(app_id: str) -> str:
    low = (app_id or "").lower()
    for key, name in _FRIENDLY_SOURCES:
        if key in low:
            return name
    tail = (app_id or "Mídia").split("!")[-1].split("\\")[-1]
    return tail.removesuffix(".exe").removesuffix(".EXE") or "Mídia"


@dataclass
class MediaInfo:
    title: str
    artist: str
    album: str
    source_app_id: str
    source_name: str
    is_playing: bool
    position: float          # segundos
    duration: float          # segundos (0 = ao vivo / desconhecido)
    thumbnail: Optional[bytes]

    @property
    def track_key(self) -> tuple:
        return (self.source_app_id, self.title, self.artist, self.album)


def _seconds(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, timedelta):
        return value.total_seconds()
    # algumas versões expõem TimeSpan com .duration em ticks de 100 ns
    ticks = getattr(value, "duration", None)
    return ticks / 10_000_000 if isinstance(ticks, (int, float)) else 0.0


class MediaService(QThread):
    """Faz polling da sessão de mídia atual numa thread com loop asyncio próprio."""

    media_changed = pyqtSignal(object)   # MediaInfo | None
    unavailable = pyqtSignal(str)

    def __init__(self, poll_interval: float = 1.0, parent=None) -> None:
        super().__init__(parent)
        self._interval = poll_interval
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None
        self._manager = None
        self._stopping = False
        self._last_signature: Optional[tuple] = None
        self._thumb_key: Optional[tuple] = None
        self._thumb_data: Optional[bytes] = None

    @staticmethod
    def is_supported() -> bool:
        return _SessionManager is not None

    # ── ciclo de vida ───────────────────────────────────────────────────────
    def run(self) -> None:  # noqa: D401 — roda na thread do serviço
        if _SessionManager is None:
            msg = "Bindings WinRT ausentes: instale 'winrt-Windows.Media.Control' ou 'winsdk'."
            log.warning(msg)
            self.unavailable.emit(msg)
            return
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:  # pragma: no cover — log defensivo
            log.exception("MediaService terminou com erro: %s", exc)
        finally:
            self._loop.close()
            self._loop = None

    def stop(self) -> None:
        self._stopping = True
        loop = self._loop
        if loop is not None and self._wake is not None:
            try:
                loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                pass  # o loop terminou entre a verificação e a chamada
        if not self.wait(3000):
            log.warning("MediaService não terminou a tempo; finalizando a thread.")
            self.terminate()
            self.wait(1000)

    async def _main(self) -> None:
        self._wake = asyncio.Event()
        try:
            self._manager = await _SessionManager.request_async()
        except Exception as exc:
            msg = f"GSMTC indisponível: {exc}"
            log.warning(msg)
            self.unavailable.emit(msg)
            return
        log.info("MediaService ativo (bindings: %s)", _BINDINGS)
        while not self._stopping:
            try:
                info = await self._snapshot()
                self._emit_if_changed(info)
            except Exception as exc:
                log.debug("Falha ao ler sessão de mídia: %s", exc)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _emit_if_changed(self, info: Optional[MediaInfo]) -> None:
        if info is None:
            sig = None
        else:
            sig = (info.track_key, info.is_playing, int(info.position), int(info.duration),
                   self._thumb_key if info.thumbnail else None)
        if sig != self._last_signature:
            self._last_signature = sig
            self.media_changed.emit(info)

    # ── leitura ─────────────────────────────────────────────────────────────
    async def _snapshot(self) -> Optional[MediaInfo]:
        session = self._manager.get_current_session() if self._manager else None
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        if props is None or not (props.title or props.artist):
            return None

        playback = session.get_playback_info()
        playing = int(playback.playback_status) == _STATUS_PLAYING if playback else False

        timeline = session.get_timeline_properties()
        start = _seconds(getattr(timeline, "start_time", None))
        end = _seconds(getattr(timeline, "end_time", None))
        pos = _seconds(getattr(timeline, "position", None)) - start
        duration = max(0.0, end - start)
        # A posição só é atualizada pelo app de tempos em tempos: extrapola se estiver tocando.
        last = getattr(timeline, "last_updated_time", None)
        if playing and isinstance(last, datetime):
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            pos += max(0.0, (datetime.now(timezone.utc) - last).total_seconds())
        if duration > 0:
            pos = min(pos, duration)

        app_id = session.source_app_user_model_id or ""
        key = (app_id, props.title, props.artist, props.album_title)
        if key != self._thumb_key:
            self._thumb_key = key
            self._thumb_data = await self._read_thumbnail(props.thumbnail)

        return MediaInfo(
            title=props.title or "",
            artist=props.artist or props.album_artist or "",
            album=props.album_title or "",
            source_app_id=app_id,
            source_name=friendly_source(app_id),
            is_playing=playing,
            position=max(0.0, pos),
            duration=duration,
            thumbnail=self._thumb_data,
        )

    @staticmethod
    async def _read_thumbnail(ref) -> Optional[bytes]:
        if ref is None:
            return None
        try:
            stream = await ref.open_read_async()
            size = int(stream.size)
            if size <= 0:
                return None
            buf = await stream.read_async(_Buffer(size), size, _InputStreamOptions.READ_AHEAD)
            try:
                return bytes(memoryview(buf))           # pywinrt moderno: protocolo de buffer
            except TypeError:
                reader = _DataReader.from_buffer(buf)
                data = bytearray(buf.length)
                reader.read_bytes(data)
                return bytes(data)
        except Exception as exc:
            log.debug("Sem miniatura: %s", exc)
            return None

    # ── comandos (chamados da thread da UI) ─────────────────────────────────
    def send_command(self, command: str) -> None:
        actions: dict[str, Callable] = {
            "play_pause": self._toggle,
            "play": lambda s: s.try_play_async(),
            "pause": lambda s: s.try_pause_async(),
            "next": lambda s: s.try_skip_next_async(),
            "previous": lambda s: s.try_skip_previous_async(),
        }
        action = actions.get(command)
        if action is None:
            log.warning("Comando de mídia desconhecido: %s", command)
            return
        self._submit(action)

    def play_pause(self) -> None:
        self.send_command("play_pause")

    def next(self) -> None:
        self.send_command("next")

    def previous(self) -> None:
        self.send_command("previous")

    @staticmethod
    def _toggle(session) -> Awaitable:
        playback = session.get_playback_info()
        if playback and int(playback.playback_status) == _STATUS_PLAYING:
            return session.try_pause_async()
        return session.try_play_async()

    def _submit(self, action: Callable) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._run_command(action), loop)

    async def _run_command(self, action: Callable) -> None:
        session = self._manager.get_current_session() if self._manager else None
        if session is None:
            return
        try:
            await action(session)
        except Exception as exc:
            log.warning("Comando de mídia falhou: %s", exc)
        await asyncio.sleep(0.15)   # dá tempo do app atualizar o estado
        if self._wake is not None:
            self._wake.set()        # atualiza a ilha imediatamente
