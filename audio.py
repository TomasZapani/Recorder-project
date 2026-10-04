"""Utilidades de audio compartidas del proyecto.

Todo el lado host (diagnóstico y, más adelante, el pipeline de transcripción)
usa este módulo, para que exista una sola implementación del formato de stream
de la ESP32 y de la conversión a WAV. Si el troceado tiene un bug, se arregla
en un solo lugar.

Solo stdlib a propósito: el venv está en Python 3.14 y varios paquetes todavía
no publican wheels para esa versión.
"""

import sys
import wave
from array import array

# Formato del stream que manda el firmware: header mágico + BLOCK muestras
# uint16 little-endian, repetido indefinidamente.
MAGIC = b"AUD0"
BLOCK = 512
SAMPLE_RATE = 16000

# Offset de DC medido empíricamente en esta placa (raw ADC de 12 bits).
OFFSET_DC_NOMINAL = 1430
ADC_MAX = 4095

# El ADC llega hasta 4095 y el offset está en ~1430, así que la excursión
# máxima sin recortar es 4095-1430 = 2665. Esta ganancia mapea esa excursión
# al fondo de escala de 16 bits sin introducir clipping propio.
GANANCIA_POR_DEFECTO = 12


def _leer_exacto(ser, n):
    """Lee exactamente n bytes del puerto serie o levanta TimeoutError."""
    buf = bytearray()
    while len(buf) < n:
        chunk = ser.read(n - len(buf))
        if not chunk:
            raise TimeoutError(
                f"El puerto dejó de mandar datos (se esperaban {n} bytes, "
                f"llegaron {len(buf)}). ¿Está enchufada y flasheada la ESP32?"
            )
        buf += chunk
    return bytes(buf)


def _a_uint16(payload):
    muestras = array("H")
    muestras.frombytes(payload)
    if sys.byteorder == "big":
        muestras.byteswap()  # el ESP32 manda little-endian
    return muestras


def leer_bloques(ser, n_bloques):
    """Itera n_bloques de BLOCK muestras crudas, resincronizando si hace falta.

    Devuelve tuplas (muestras, resincronizado). El resync se hace corriendo una
    ventana de 4 bytes hasta encontrar el header. Un payload puede contener los
    bytes "AUD0" por casualidad, pero el error se corrige solo en el bloque
    siguiente, cuando el header esperado no aparece donde corresponde.
    """
    for _ in range(n_bloques):
        ventana = bytearray(_leer_exacto(ser, 4))
        resincronizado = False
        while bytes(ventana) != MAGIC:
            ventana.pop(0)
            ventana += _leer_exacto(ser, 1)
            resincronizado = True
        yield _a_uint16(_leer_exacto(ser, BLOCK * 2)), resincronizado


def offset_medido(crudas):
    """Offset de DC real de esta grabación (promedio del raw ADC)."""
    return sum(crudas) / len(crudas)


def estadisticas(crudas, offset):
    """Métricas sobre las muestras crudas, en unidades de ADC."""
    n = len(crudas)
    suma_cuad = 0
    pico = 0
    al_tope = 0
    al_piso = 0
    for m in crudas:
        d = m - offset
        suma_cuad += d * d
        if abs(d) > pico:
            pico = abs(d)
        if m >= ADC_MAX:
            al_tope += 1
        elif m <= 0:
            al_piso += 1
    return {
        "rms": (suma_cuad / n) ** 0.5,
        "pico": pico,
        "clipping": al_tope + al_piso,
        "clipping_pct": 100.0 * (al_tope + al_piso) / n,
    }


def centrar_y_escalar(crudas, offset, ganancia=GANANCIA_POR_DEFECTO):
    """Convierte raw ADC (uint16, 12 bits) a PCM de 16 bits con signo."""
    salida = array("h", bytes(2 * len(crudas)))
    recortadas = 0
    for i, m in enumerate(crudas):
        v = int((m - offset) * ganancia)
        if v > 32767:
            v, recortadas = 32767, recortadas + 1
        elif v < -32768:
            v, recortadas = -32768, recortadas + 1
        salida[i] = v
    return salida, recortadas


def escribir_wav(ruta, muestras, sample_rate=SAMPLE_RATE):
    """Escribe PCM mono de 16 bits a un WAV."""
    datos = array("h", muestras)
    if sys.byteorder == "big":
        datos.byteswap()  # WAV es little-endian
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(round(sample_rate)))
        w.writeframes(datos.tobytes())
