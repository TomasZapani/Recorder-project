"""Convierte cualquier audio (wav, mp3, m4a, ogg, ...) a WAV mono 16 bits a 16 kHz.

Es el mismo formato que graba el dispositivo y el que usa Whisper por dentro.
Usa PyAV, que viene instalado con faster-whisper.
"""

import wave
from pathlib import Path

import av

TASA = 16000


class AudioInvalido(Exception):
    pass


def a_wav(origen: Path, destino: Path) -> float:
    """Escribe `destino` y devuelve la duración en segundos."""
    try:
        contenedor = av.open(str(origen))
    except av.error.FFmpegError as e:
        raise AudioInvalido("No se pudo leer el archivo: no parece un audio.") from e

    with contenedor:
        if not contenedor.streams.audio:
            raise AudioInvalido("El archivo no tiene audio.")
        pista = contenedor.streams.audio[0]
        remuestreo = av.AudioResampler(format="s16", layout="mono", rate=TASA)
        muestras = 0
        with wave.open(str(destino), "wb") as salida:
            salida.setnchannels(1)
            salida.setsampwidth(2)
            salida.setframerate(TASA)
            try:
                for cuadro in contenedor.decode(pista):
                    for bloque in remuestreo.resample(cuadro):
                        salida.writeframes(bloque.to_ndarray().tobytes())
                        muestras += bloque.samples
                for bloque in remuestreo.resample(None):
                    salida.writeframes(bloque.to_ndarray().tobytes())
                    muestras += bloque.samples
            except av.error.FFmpegError as e:
                raise AudioInvalido("El audio está dañado o incompleto.") from e

    if muestras == 0:
        raise AudioInvalido("El archivo de audio está vacío.")
    return muestras / TASA
