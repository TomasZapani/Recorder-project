# Grabación_clases — contexto del proyecto

## Objetivo

Dispositivo propio para grabar clases de la facultad. Graba audio a una microSD; al
llegar a casa se conecta solo al WiFi y sube las grabaciones a una **web local en
Python** que corre en la compu. La web hace el pipeline:

```
audio (PCM en la SD) → subida por WiFi → transcripción → texto → LLM → markdown en carpetas
```

Diferenciador frente a productos tipo Plaud: bring-your-own-API-key, elección
libre del modelo, sin suscripción, y el audio va a la compu del usuario, no a la
nube de una empresa.

**Alcance actual (2026-10-04): todo funcionando de 10 en protoboard.** La app de
celular, la batería, la PCB y el formato lapicera quedaron para después. El plan
por fases está en `PLAN.md`.

## Cómo queda todo

- **El grabador** (ESP32 en protoboard): toque corto = empezar/terminar de grabar.
  La OLED muestra estado, tiempo y espacio libre. Con grabaciones pendientes, busca
  el WiFi de casa y las sube solo.
- **La web** (Python, en la compu, se abre desde el navegador, interfaz linda y no técnica): recibe las
  grabaciones, deduce la materia por el horario de cursada, transcribe, resume y
  muestra materias, clases, audio, transcripción y resumen. Se puede abrir desde el
  celular si está en la misma red.
- **Las carpetas**: la web es una ventana sobre archivos comunes, no una caja fuerte.

  ```
  Facultad/<Materia>/<Clase>/{audio.wav, contexto.md, transcripcion.md, resumen.md}
  ```

  Dos niveles fijos: el usuario crea materias (`Física 1`) y adentro clases
  (`Clase 1`, `Clase 2`). Las clases que llegan solas por WiFi entran como
  "Clase N" en la materia del horario, marcadas como nuevas.

  `contexto.md` es opcional: texto libre del usuario (profe, temas vistos). Backup
  con copiar-pegar, Obsidian funciona sobre la misma carpeta, y si la web muere no
  se pierde nada. Un índice SQLite para búsqueda es válido, pero como caché
  reconstruible: los archivos mandan.
- **Diagramas y mapas conceptuales en Mermaid** (texto, no imágenes): el modelo los
  genera como cualquier otro texto, quedan dentro del `.md` y los renderiza tanto la
  web como Obsidian.

## Decisiones cerradas (no revisitar sin motivo nuevo)

- **Pipeline en dos etapas: transcribir primero, resumir después.** No se le manda
  audio al modelo. Motivos: es la única forma de que el usuario pueda elegir
  cualquier modelo (varios no aceptan audio — Claude, por ejemplo, solo acepta
  texto, imágenes y PDF); la transcripción queda en disco como artefacto
  reusable, así se puede regenerar el resumen sin volver a transcribir; y
  mandar texto en vez de audio sale mucho más barato en tokens.
- **El usuario elige el proveedor y el modelo del resumen, el que quiera.** Implementado en `web/resumidores/`: Claude por su SDK, y un adaptador compatible con OpenAI que cubre OpenAI, DeepSeek, Gemini, Mistral, Groq, OpenRouter, Ollama (local) y cualquier otro servicio con esa API. En el
  código: una interfaz `resumir(texto) → markdown` con una implementación por
  proveedor, elegida desde la configuración de la web. Lo mismo para la
  transcripción (Whisper local por defecto, o API).
- **Interfaz: web local en Python, no app de celular** (decidido 2026-10-04). Sin
  Flutter ni Mac, y reaprovecha todo el Python existente.
- **Todo el lado host en Python; el firmware sigue en C++/Arduino.** Se descartó Go:
  el post-procesamiento es glue code I/O-bound. Python permite una sola
  implementación del manejo de PCM compartida entre diagnóstico y web.
- **Store-then-process, no streaming en vivo.** Nunca WiFi mientras graba. Las redes
  del campus son poco confiables y procesar con contexto completo da mejores
  resúmenes.
- **Subida automática por el WiFi de casa** (decidido 2026-10-04). El grabador se
  conecta al WiFi de casa y sube a la web. Es lo mismo que hace el "Fast Transfer"
  de Plaud. Se descartaron el AP propio (obliga a conectar la compu a mano y la deja
  sin internet) y Bluetooth (48 a 144 min por clase).
- **Credenciales en el dispositivo: solo la clave del WiFi de casa.** Nunca API keys
  ni cuentas de nube; esas viven en la compu. Va en `wifi.txt` en la microSD.
- **La materia se deduce del horario de cursada**, cargado una vez en la web, para
  que la subida automática no tenga que preguntar nada.
- **Transcripción: Whisper `large-v3-turbo` local** con `vad_filter=True`, y un
  `initial_prompt` armado desde la carpeta (materia + tema) más `contexto.md`.
- **El audio se escribe streameando directo a la SD**, con ~8 buffers de 4 KB de
  colchón. El ESP32-WROOM-32 tiene 320 KB de RAM.
- **La microSD queda en FAT32 con archivos planos**: si todo falla, se saca y se lee
  en cualquier compu.
- **Upgrade a ESP32-S3 diferido.** Su ventaja era USB MSC, que no aporta con subida
  por WiFi.
- **El MAX9814 no resuelve rechazo de ruido.** El AGC no separa señal de ruido, y
  ni el MAX9814 ni el INMP441 son direccionales. Lo que importa es la distancia al
  hablante, no el micrófono.

## Hardware

| Módulo | Señal | GPIO |
|---|---|---|
| MAX9814 (mic analógico) | OUT | **35 (ADC1_CH7)** |
| OLED I2C (SSD1306, 0x3C) | SDA | 22 |
| | SCL | 23 |
| TTP223 (touch capacitivo) | SIG | 27 |
| microSD (SPI) | CS | 5 |
| | SCK | 17 |
| | MISO | 18 |
| | MOSI | 19 |

Detalles que importan:

- **MAX9814**: GAIN y A/R al aire (60 dB, release 1:4000). Bias interno ~1.25 V.
  Atenuación `ADC_ATTEN_DB_11`.
- **Offset de DC medido en GPIO 35: 1412–1428** (raw ADC, 12 bits). El valor viejo
  de 1430 era de otra medición; el script usa offset automático.
- **El micrófono está en ADC1 y nunca puede moverse a un pin de ADC2** (GPIO 0, 2,
  4, 12-15, 25-27): ADC2 no funciona con WiFi encendido.
- **TTP223**: modo `INPUT`, no `INPUT_PULLUP`. Lee HIGH al tocar. Se movió de
  GPIO 12 porque es strapping pin (selección de voltaje de flash).
- **Pines no-default**: hay que declararlos sí o sí.
  ```cpp
  Wire.begin(22, 23);        // SDA, SCL
  SPI.begin(17, 18, 19, 5);  // SCK, MISO, MOSI, CS
  SD.begin(5, SPI);
  ```
- **Alimentación**: microSD desde el riel de 5 V (tiene AMS1117 a bordo), todo lo
  demás desde 3V3. Topología en estrella desde los rieles, sin encadenar GND.
- **Otras piezas disponibles**: DFPlayer Mini (solo reproduce, no graba — podría
  servir para feedback sonoro).

## Estado actual (2026-10-03)

Funciona:

- **Hardware completo en protoboard, todo probado (2026-10-04)** con
  `firmware/prueba_hardware/`: OLED responde solo en 0x3C, microSD SDHC ~15 GB
  escribe y lee, touch cuenta toques, micrófono graba. Hubo que corregir pines de la
  SD y de la OLED mal conectados (antes el bus I2C mostraba ~50 direcciones
  fantasma: síntoma de OLED mal alimentada). Vigilar: la OLED se apagó sola un par de
  veces antes de la corrección; si vuelve a pasar, cambiar los cables de VCC/GND.
- Firmwares en `firmware/`: `microfono/` (para `grabar_wav.py`) y
  `prueba_hardware/` (OLED + SD + touch, informa por serie a 115200).
- Firmware del micrófono: samplea GPIO 35 a 16 kHz y manda bloques de 512 muestras
  uint16 por USB serie a 921600 baudios, con header `AUD0`. **Todavía no usa el
  touch, la OLED ni la SD.**
- `grabar_wav.py` graba N segundos a WAV con informe. Medido en 4 grabaciones: tasa
  efectiva 15998–16016 Hz, siempre 1–2 bloques "resincronizados" (pasa al
  engancharse al arrancar, no durante la grabación: no crece con la duración).
- **Transcripción desde archivo: resuelta.** Con turbo, sin prompt, transcribió bien
  una clase simulada de Física y otra de Análisis. Con prompt de materia y tema
  corrigió "Fugini" → "Fubini". Turbo tarda ~4 s por 10 s de audio en esta CPU.

Diagnóstico cerrado: **las alucinaciones ("¡Suscríbete!", "Merci", "Maradona")
venían del script de transcripción en vivo** (`~/grabador/transcriber.py`): trozos
fijos de 4 s que cortan palabras, umbral de silencio igual al piso de ruido, y el
buffer serie desbordándose mientras Whisper procesaba. El audio no era el problema.

Pendientes conocidos:

- El audio se entiende pero suena "grueso" y con algo de ruido de fondo. Lo mejora
  el muestreo por DMA (en "Para después" del `PLAN.md`).
- Clipping: 0,04 % hablando cerca y fuerte (RMS ~800). Si se repite en clases
  reales, bajar ganancia poniendo GAIN a GND (50 dB).
- La ESP32 una vez dejó de mandar datos; se arregló con el botón EN.

## Entorno

- Ubuntu. Venv en **`~/grabador/venv`** (Python 3.14 — ojo, algunos paquetes no
  tienen wheels). Bajar a 3.13 solo si una dependencia nueva no instala (todo lo actual anda).
- `faster-whisper` con modelos `small` y `large-v3-turbo` descargados (CPU, int8).
- Arduino IDE: se usó el AppImage (el snap rompe al flashear por un problema de
  `flasher.py` con Python). **Core ESP32 3.3.11** en `~/.arduino15`. Con este core
  `I2S_MODE_ADC_BUILT_IN` no existe; el camino de DMA es `adc_continuous`.
- Arduino IDE: tiene instalada Adafruit SSD1306 (+ GFX, BusIO). El aviso
  `quad_psram: PSRAM ID read error` al arrancar es inofensivo (la placa no tiene
  PSRAM); se quita con Herramientas → PSRAM → Disabled.
- Puerto serie: `/dev/ttyUSB0`. El monitor de Arduino y el script de Python no
  pueden tenerlo abierto al mismo tiempo.
- Al grabar con `grabar_wav.py`: un comando que solo graba, y avisar que arranca al
  aprobarlo. Transcribir en un comando aparte.

## Firmware actual (referencia)

```cpp
const int MIC_PIN = 35;  // ADC1_CH7 — coincide con el cableado
const int SAMPLE_RATE = 16000;
const int BLOCK = 512;

uint16_t buf[BLOCK];

void setup() {
  Serial.begin(921600);
  analogReadResolution(12);
  analogSetPinAttenuation(MIC_PIN, ADC_11db);
}

void loop() {
  static uint64_t n = 0;
  static uint64_t t0 = micros();

  for (int i = 0; i < BLOCK; i++) {
    uint64_t target = t0 + (n * 1000000ULL) / SAMPLE_RATE;
    while ((int64_t)(micros() - target) < 0) { }
    buf[i] = analogRead(MIC_PIN);
    n++;
  }

  Serial.write((const uint8_t*)"AUD0", 4);
  Serial.write((const uint8_t*)buf, BLOCK * 2);
}
```
