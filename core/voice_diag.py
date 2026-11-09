"""Diagnóstico da voz da CHLOE: uma linha por resposta em logs/voice.log.

Grava só números e sim/não — nunca o texto falado ou transcrito. Serve para
responder, com evidência, por que uma fala saiu cortada:

    audio      segundos de áudio recebidos do Gemini neste turno
    gaps       pausas no meio da resposta (o áudio acabou e o próximo pedaço
               ainda não tinha chegado), e a maior delas em ms
    server_int o servidor cortou a própria resposta (server_content.interrupted)
    gen_done   o servidor avisou que terminou de gerar o áudio
    user_int   você interrompeu (Esc / botão)
    mic_held   blocos do microfone segurados porque a resposta ainda não tinha
               terminado (antes eles iam para o servidor e podiam cortar a fala)
    chunks     pedaços de transcrição; lead_space = quantos começam com espaço
"""
from __future__ import annotations

import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


class VoiceDiagnostics:
    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._log = logging.getLogger("chloe.voice")
        self._log.propagate = False
        self._log.setLevel(logging.INFO)
        if path is not None and not self._log.handlers:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
                self._log.addHandler(handler)
            except OSError:
                pass
        self._reset()

    def _reset(self) -> None:
        self.audio_bytes = 0
        self.gaps: list[float] = []
        self._gap_from: float | None = None
        self.server_interrupted = False
        self.generation_complete = False
        self.user_interrupted = False
        self.mic_held = 0
        self.chunks = 0
        self.lead_space = 0
        self.tool_calls = 0

    # ── eventos (chamados do laço asyncio e do callback do microfone) ───────
    def note_chunk(self, text: str) -> None:
        if text.strip():
            self.chunks += 1
            if text[:1].isspace():
                self.lead_space += 1

    def note_audio(self, nbytes: int) -> None:
        self.audio_bytes += nbytes

    def gap_started(self, at: float) -> None:
        if self._gap_from is None:
            self._gap_from = at

    def gap_ended(self, now: float) -> None:
        if self._gap_from is not None:
            self.gaps.append(max(0.0, now - self._gap_from))
            self._gap_from = None

    def note_mic_held(self) -> None:
        with self._lock:
            self.mic_held += 1

    def note_server_interrupted(self) -> None:
        self.server_interrupted = True

    def note_generation_complete(self) -> None:
        self.generation_complete = True

    def note_user_interrupt(self) -> None:
        self.user_interrupted = True

    def note_tool_call(self) -> None:
        self.tool_calls += 1

    def turn_complete(self, sample_rate: int = 24000) -> str | None:
        """Escreve a linha do turno (se houve áudio ou algo a registrar) e zera."""
        self._gap_from = None
        has_something = (self.audio_bytes or self.server_interrupted or self.user_interrupted
                         or self.mic_held or self.tool_calls)
        line = None
        if has_something:
            yn = lambda v: "sim" if v else "não"   # noqa: E731
            max_gap = int(max(self.gaps) * 1000) if self.gaps else 0
            line = (f"turno audio={self.audio_bytes / (2 * sample_rate):.1f}s "
                    f"gaps={len(self.gaps)} max_gap={max_gap}ms "
                    f"server_int={yn(self.server_interrupted)} gen_done={yn(self.generation_complete)} "
                    f"user_int={yn(self.user_interrupted)} mic_held={self.mic_held} "
                    f"tools={self.tool_calls} chunks={self.chunks} lead_space={self.lead_space}")
            self._log.info(line)
        self._reset()
        return line


def default_diagnostics() -> VoiceDiagnostics:
    return VoiceDiagnostics(Path(__file__).resolve().parent.parent / "logs" / "voice.log")
