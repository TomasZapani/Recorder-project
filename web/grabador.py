"""API para el grabador: recibe las grabaciones por WiFi y le cuenta qué pasa con ellas.

El grabador sube el audio crudo de la microSD (muestras uint16 del ADC a 16 kHz, las
mismas que manda por USB) en pedazos, y puede retomar una subida cortada: pregunta
cuántos bytes ya hay y sigue desde ahí. Al terminar manda el tamaño y el CRC32; recién
si coinciden se arma la clase y el grabador da el archivo por subido.

    GET  /api/grabador/<id>                  → {"recibidos": n}
    PUT  /api/grabador/<id>?desde=n          → agrega el pedazo (409 si "desde" no coincide)
    POST /api/grabador/<id>/fin?bytes=&crc=  → verifica, crea la clase y la manda a transcribir
    GET  /api/grabador/<id>/estado           → {"fase": ..., "progreso": ..., "clase": ...}

Las subidas a medias viven en <raíz>/.grabador/, que la biblioteca ignora por empezar
con punto. Ahí también queda qué clase salió de cada grabación, para que repetir el
"fin" (si se cortó la respuesta) no cree la clase dos veces.
"""

import json
import re
import sys
import wave
import zlib
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from flask import Blueprint, abort, jsonify, request

import configuracion
from biblioteca import AUDIO, Biblioteca, ErrorBiblioteca
from tareas import TRANSCRIBIR, Cola

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audio  # noqa: E402  (vive en la raíz del proyecto: una sola definición del formato del ADC)

_ID = re.compile(r"[A-Za-z0-9-]{1,40}")
_PEDAZO = 1024 * 1024  # muestras por pasada al convertir: no carga 170 MB de golpe


def crear(biblioteca: Biblioteca, cola: Cola) -> Blueprint:
    bp = Blueprint("grabador", __name__, url_prefix="/api/grabador")
    carpeta = biblioteca.raiz / ".grabador"
    carpeta.mkdir(exist_ok=True)

    def rutas(id_: str) -> tuple[Path, Path]:
        if not _ID.fullmatch(id_):
            abort(400)
        return carpeta / f"{id_}.raw", carpeta / f"{id_}.json"

    def hecho(ruta: Path) -> dict | None:
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    @bp.get("/<id_>")
    def recibidos(id_):
        crudo, registro = rutas(id_)
        if datos := hecho(registro):
            return jsonify(recibidos=datos["bytes"], terminada=True, **datos)
        return jsonify(recibidos=crudo.stat().st_size if crudo.exists() else 0)

    @bp.put("/<id_>")
    def recibir(id_):
        crudo, registro = rutas(id_)
        if hecho(registro):
            return jsonify(error="Esa grabación ya se recibió entera."), 409
        tamaño = crudo.stat().st_size if crudo.exists() else 0
        if request.args.get("desde", type=int) != tamaño:
            return jsonify(error="La subida no sigue desde donde quedó.", recibidos=tamaño), 409
        with crudo.open("ab") as f:
            while pedazo := request.stream.read(64 * 1024):
                f.write(pedazo)
        return jsonify(recibidos=crudo.stat().st_size)

    @bp.post("/<id_>/fin")
    def terminar(id_):
        crudo, registro = rutas(id_)
        if datos := hecho(registro):
            return jsonify(**datos)  # el grabador repitió el fin: ya está hecha
        tamaño = crudo.stat().st_size if crudo.exists() else 0
        if tamaño == 0 or request.args.get("bytes", type=int) != tamaño or tamaño % 2:
            return jsonify(error="Faltan bytes de la grabación.", recibidos=tamaño), 409
        crc = 0
        with crudo.open("rb") as f:
            while pedazo := f.read(1024 * 1024):
                crc = zlib.crc32(pedazo, crc)
        if request.args.get("crc", "") != f"{crc:08x}":
            crudo.unlink()  # llegó dañada: el grabador la vuelve a subir desde cero
            return jsonify(error="La grabación llegó dañada (CRC distinto).", recibidos=0), 409

        # fromfile y no memmap: en Windows un archivo mapeado no se puede borrar después.
        muestras = np.fromfile(crudo, dtype="<u2")
        segundos = len(muestras) / audio.SAMPLE_RATE
        inicio = datetime.now() - timedelta(seconds=segundos)
        config = configuracion.cargar()
        materia, marcada = config.materia_grabador, config.clase_grabador
        # La subcarpeta marcada se usa si todavía no tiene audio: nunca se pisa una grabación.
        usar_marcada = bool(marcada) and not (biblioteca.raiz / materia / marcada / AUDIO).exists()
        try:
            if not (biblioteca.raiz / materia).is_dir():
                biblioteca.crear_materia(materia)
            if usar_marcada:
                clase = marcada
                if not (biblioteca.raiz / materia / clase).is_dir():
                    biblioteca.crear_clase(materia, clase)
            else:
                clase = f"Grabación {inicio:%Y-%m-%d %H.%M}"
                n = 2
                while (biblioteca.raiz / materia / clase).exists():
                    clase, n = f"Grabación {inicio:%Y-%m-%d %H.%M} ({n})", n + 1
                biblioteca.crear_clase(materia, clase)
            _escribir_wav(muestras, biblioteca.ruta_archivo(materia, clase, AUDIO))
        except ErrorBiblioteca as e:
            return jsonify(error=str(e)), 500

        datos = {"materia": materia, "clase": clase, "bytes": tamaño, "segundos": round(segundos, 1)}
        registro.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        if marcada:  # ya se usó (o tenía audio): las siguientes van a subcarpetas nuevas
            config = configuracion.cargar()
            if (config.materia_grabador, config.clase_grabador) == (materia, marcada):
                config.clase_grabador = ""
                configuracion.guardar(config)
        cola.encolar(materia, clase, TRANSCRIBIR)
        try:
            crudo.unlink()
        except OSError:
            pass  # el audio ya está en la clase; si el crudo no se puede borrar, no importa
        return jsonify(**datos)

    @bp.get("/<id_>/estado")
    def estado(id_):
        _, registro = rutas(id_)
        datos = hecho(registro)
        if not datos:
            abort(404)
        materia, clase = datos["materia"], datos["clase"]
        e = cola.estado(materia, clase)
        if e is not None:
            if e.fase == "error":
                fase = "error"
            elif e.tarea == TRANSCRIBIR:
                fase = "transcribiendo" if e.fase == "trabajando" else "en cola"
            else:
                fase = "resumiendo"
            return jsonify(fase=fase, progreso=round(e.progreso, 2), clase=clase)
        try:
            c = biblioteca.clase(materia, clase)
        except ErrorBiblioteca:
            return jsonify(fase="movida", progreso=1, clase=clase)  # la renombraron o borraron en la web
        fase = "lista" if c.tiene_resumen else "transcripta" if c.tiene_transcripcion else "sin procesar"
        return jsonify(fase=fase, progreso=1, clase=clase)

    return bp


def _escribir_wav(muestras: np.ndarray, destino: Path) -> None:
    """Raw del ADC → WAV de 16 bits, igual que audio.centrar_y_escalar pero vectorizado."""
    offset = float(muestras.mean(dtype=np.float64))
    temporal = destino.with_name(f".{destino.name}.tmp")
    with wave.open(str(temporal), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.SAMPLE_RATE)
        for i in range(0, len(muestras), _PEDAZO):
            bloque = (muestras[i:i + _PEDAZO].astype(np.float32) - offset) * audio.GANANCIA_POR_DEFECTO
            w.writeframes(np.clip(bloque, -32768, 32767).astype("<i2").tobytes())
    temporal.replace(destino)
