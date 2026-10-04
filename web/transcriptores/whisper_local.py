"""Whisper corriendo en la compu con faster-whisper. Gratis y privado."""

from collections.abc import Callable
from pathlib import Path

from biblioteca import Frase

_FIN_DE_ORACION = (".", "?", "!", "…")
_MIN_PALABRAS_COMA = 15  # si Whisper encadena todo con comas, cortar en una coma pasada esta longitud
_MAX_PALABRAS = 40  # corta frases larguísimas aunque no haya puntuación


class WhisperLocal:
    def __init__(self, modelo: str = "large-v3-turbo"):
        self.modelo = modelo
        self.nombre = f"Whisper {modelo} (local)"
        self._whisper = None

    def _cargar(self):
        # Import y carga diferidos: tardan unos segundos y solo hacen falta al transcribir.
        if self._whisper is None:
            from faster_whisper import WhisperModel

            self._whisper = WhisperModel(self.modelo, device="cpu", compute_type="int8")
        return self._whisper

    def transcribir(
        self, audio: Path, prompt: str | None, progreso: Callable[[float], None]
    ) -> list[Frase]:
        segmentos, info = self._cargar().transcribe(
            str(audio),
            language="es",
            vad_filter=True,  # saca los silencios: sin esto aparecen "¡Suscríbete!" y compañía
            initial_prompt=prompt or None,
            word_timestamps=True,  # para cortar en oraciones aunque Whisper devuelva bloques largos
        )
        frases: list[Frase] = []
        inicio, palabras = None, []
        for segmento in segmentos:  # es un generador: transcribe a medida que se recorre
            for p in segmento.words or []:
                if inicio is None:
                    inicio = p.start
                palabras.append(p.word)
                fin = p.word.strip()
                if (
                    fin.endswith(_FIN_DE_ORACION)
                    or (fin.endswith(",") and len(palabras) >= _MIN_PALABRAS_COMA)
                    or len(palabras) >= _MAX_PALABRAS
                ):
                    frases.append(Frase(inicio, "".join(palabras).strip()))
                    inicio, palabras = None, []
            if info.duration:
                progreso(min(segmento.end / info.duration, 1.0))
        if palabras:
            frases.append(Frase(inicio, "".join(palabras).strip()))
        progreso(1.0)
        return frases
