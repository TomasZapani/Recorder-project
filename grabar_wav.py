#!/usr/bin/env python3
"""Graba N segundos desde la ESP32 a un WAV, para escucharlo y diagnosticar.

Separa las tres hipótesis del problema de transcripción:

  1. Se entiende la voz aunque suene fea  -> el problema es el troceado.
  2. Suena entrecortado o acelerado       -> se pierden muestras / la tasa real
                                             no es 16 kHz. Mirá "tasa efectiva".
  3. Puro ruido sin voz                    -> problema acústico, revisar montaje.

Uso:
    python grabar_wav.py                    # 10 s a grabacion.wav
    python grabar_wav.py -s 30 -o clase.wav
    python grabar_wav.py --tasa-real        # usa la tasa medida en el header WAV
"""

import argparse
import sys
import time
from array import array

import audio

try:
    import serial
except ImportError:
    sys.exit("Falta pyserial. Instalalo con:  pip install pyserial")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-s", "--segundos", type=float, default=10.0)
    p.add_argument("-p", "--puerto", default="/dev/ttyUSB0")
    p.add_argument("-b", "--baudios", type=int, default=921600)
    p.add_argument("-o", "--salida", default="grabacion.wav")
    p.add_argument("-g", "--ganancia", type=float, default=audio.GANANCIA_POR_DEFECTO)
    p.add_argument("--offset", default="auto",
                   help=f"'auto' (promedio de la grabación) o un valor fijo "
                        f"(nominal de esta placa: {audio.OFFSET_DC_NOMINAL})")
    p.add_argument("--tasa-real", action="store_true",
                   help="escribe el WAV con la tasa medida en vez de 16000 Hz, "
                        "para oírlo a velocidad correcta si la ESP32 no llega a 16 kHz")
    args = p.parse_args()

    n_bloques = max(1, int(args.segundos * audio.SAMPLE_RATE / audio.BLOCK))
    esperadas = n_bloques * audio.BLOCK

    try:
        ser = serial.Serial(args.puerto, args.baudios, timeout=2)
    except serial.SerialException as e:
        sys.exit(f"No se pudo abrir {args.puerto}: {e}\n"
                 f"Si está abierto el monitor serie del Arduino IDE, cerralo.")

    crudas = array("H")
    resyncs = 0
    with ser:
        time.sleep(0.2)          # que el driver se asiente
        ser.reset_input_buffer()  # descartar lo que se acumuló antes de arrancar
        print(f"Grabando {args.segundos:g} s desde {args.puerto} ...")
        t0 = time.monotonic()
        try:
            for muestras, resync in audio.leer_bloques(ser, n_bloques):
                crudas.extend(muestras)
                resyncs += resync
        except TimeoutError as e:
            sys.exit(f"\n{e}")
        except KeyboardInterrupt:
            print("\nInterrumpido, guardo lo que hay.")
        transcurrido = time.monotonic() - t0

    if not crudas:
        sys.exit("No llegó ninguna muestra.")

    tasa_efectiva = len(crudas) / transcurrido
    offset = (audio.offset_medido(crudas) if args.offset == "auto"
              else float(args.offset))
    stats = audio.estadisticas(crudas, offset)
    pcm, recortadas = audio.centrar_y_escalar(crudas, offset, args.ganancia)

    tasa_wav = tasa_efectiva if args.tasa_real else audio.SAMPLE_RATE
    audio.escribir_wav(args.salida, pcm, tasa_wav)

    print(f"\n  Archivo          {args.salida}  ({len(crudas)/tasa_wav:.2f} s a {int(round(tasa_wav))} Hz)")
    print(f"  Muestras         {len(crudas)} de {esperadas} esperadas")
    print(f"  Tasa efectiva    {tasa_efectiva:.0f} Hz  (nominal 16000)")
    print(f"  Offset DC        {offset:.1f}  (nominal {audio.OFFSET_DC_NOMINAL})")
    print(f"  RMS              {stats['rms']:.1f}  (silencio ~41, hablando ~600)")
    print(f"  Pico             {stats['pico']:.0f}")
    print(f"  Clipping ADC     {stats['clipping']} muestras ({stats['clipping_pct']:.2f} %)")
    if resyncs:
        print(f"  Resincronizadas  {resyncs} bloques  <-- se perdieron datos en el serie")
    if recortadas:
        print(f"  Recortadas       {recortadas} muestras al convertir (bajá --ganancia)")

    desvio = abs(tasa_efectiva - audio.SAMPLE_RATE) / audio.SAMPLE_RATE
    if desvio > 0.02:
        lento = tasa_efectiva < audio.SAMPLE_RATE
        print(f"\n  OJO: la tasa real se desvía {desvio*100:.1f} % del nominal. "
              f"El WAV va a sonar {'acelerado' if lento else 'lento'} a 16 kHz.")
        print(f"  Probá:  python {sys.argv[0]} --tasa-real")

    print(f"\nEscuchalo:  aplay {args.salida}")


if __name__ == "__main__":
    main()
