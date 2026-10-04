"""Puerto de transcripción: audio → frases con marca de tiempo.

Cada proveedor (Whisper local, una API, ...) implementa `Transcriptor`.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from biblioteca import Frase


class Transcriptor(Protocol):
    nombre: str  # se guarda en el .md, ej. "Whisper large-v3-turbo (local)"

    def transcribir(
        self, audio: Path, prompt: str | None, progreso: Callable[[float], None]
    ) -> list[Frase]:
        """Transcribe `audio`. Llama a `progreso(0..1)` a medida que avanza."""
        ...


def por_defecto() -> Transcriptor:
    from transcriptores.whisper_local import WhisperLocal

    return WhisperLocal()
