# Plan — Grabador de clases en protoboard, funcionando de punta a punta

## Contexto

Grabador de clases: un ESP32 con micrófono graba a una microSD, y después el audio se
transcribe y se resume con un LLM elegido por el usuario, con su propia API key.

**Cambio de rumbo (2026-10-04).** El plan anterior apuntaba a una app de celular en
Flutter, formato lapicera y batería. Era demasiado alcance para una persona sola. El
objetivo ahora es más chico y concreto: **que todo funcione de 10 en la protoboard**,
con una web local como interfaz. La app nativa, la batería y la PCB pasan a "Para
después" (al final de este archivo), sin tirar nada de lo investigado.

## Cómo queda todo

Tres piezas:

1. **El grabador** (ESP32 en protoboard). Toque corto = empezar/terminar de grabar a la
   microSD. La OLED muestra estado, tiempo grabado y espacio libre. Al llegar a casa
   se conecta solo al WiFi y sube las grabaciones pendientes a la compu.
2. **La web** (Python, en la compu, se abre desde el navegador). Recibe las
   grabaciones, deduce la materia por el horario de cursada, transcribe con Whisper
   turbo local y resume con el LLM elegido. Muestra materias, clases, audio,
   transcripción y resumen.
3. **Las carpetas** (los archivos mandan):

   ```
   Facultad/<Materia>/<Clase>/
       audio.wav
       contexto.md         ← opcional, lo escribe el usuario
       transcripcion.md
       resumen.md
   ```

   Solo dos niveles: materias y, adentro, clases (ej. `Física 1/Clase 1/`). Sin
   subcarpetas intermedias.

Un día normal: grabás en clase → llegás a casa → el grabador sube solo → la web
procesa sola → abrís la web y la clase está.

## Decisiones tomadas

| Decisión | Valor | Por qué |
|---|---|---|
| Alcance | Todo en protoboard, sin batería ni PCB | Algo que funcione de punta a punta vale más que medio producto ambicioso |
| Interfaz | Web local en Python, no app de celular | Sin Flutter ni Mac; aprovecha todo el Python existente; se puede abrir desde el celu en la misma red |
| Transferencia | El grabador se conecta al WiFi de casa y sube solo | Cero pasos manuales. Es lo mismo que hace el "Fast Transfer" de Plaud |
| Credenciales | El dispositivo guarda **solo** la clave del WiFi de casa | Se acota la regla anterior: nunca API keys ni cuentas de nube. El audio va a tu compu, no a la nube de nadie |
| Materia | Se deduce del horario de cursada (cargado una vez) | Para que la subida automática no tenga que preguntar nada |
| Proveedor del resumen | El que quiera el usuario: Claude, OpenAI, DeepSeek, Gemini, Mistral, Groq, OpenRouter, Ollama (local) u otro compatible con OpenAI | Libertad de elección: es el diferenciador del proyecto |
| Transcripción | Whisper `large-v3-turbo` local (faster-whisper, int8, CPU) | Probado el 2026-10-03: bien en español, ~2,5× tiempo real. Gratis y privado. API como alternativa |
| Prompt de Whisper | Armado de la carpeta (materia + nombre de la clase) + `contexto.md` opcional | Probado: corrigió "Fugini" → "Fubini" con solo materia y tema |
| Estructura | `Materia/Clase/{audio, transcripción, resumen}`, dos niveles fijos | Simple de usar y de programar |
| Clases que llegan solas | En la materia del horario, como "Clase N" (siguiente número), marcadas "nueva" | El usuario la renombra si quiere |
| Archivos | Solo los de las clases (audio, transcripción, resumen, contexto) | Subir PDFs o apuntes propios, para después |

---

## Fase A — Ordenar la casa (~30 min)

- [x] Actualizar el CLAUDE.md con el plan nuevo y lo medido el 2026-10-03
- [x] Consolidar el parseo `AUD0` en `audio.py` (en el proyecto ya era así; quedan copias en `~/grabador/test.py` y `transcriber.py`, scripts viejos de la transcripción en vivo)
- [x] Borrar `script.py` (0 bytes)
- [x] ~~Bajar el venv a Python 3.13~~ — no hace falta por ahora: todo anda en 3.14. Solo si una dependencia de la Fase B no instala (3.13 no está en apt; usar `uv python install 3.13` o el PPA deadsnakes)

## Fase B — La web básica (se prueba con los WAV que ya hay)

- [x] Esqueleto en Flask (`web/`): `biblioteca.py` = lógica sobre carpetas, sin saber de la web; `app.py` = rutas; `templates/` = pantallas. Puertos solo para transcriptor y resumidor. Borrar manda a la papelera (send2trash). Carpetas en `~/Facultad` (variable `CLASES_DIR`). Arranca con `~/grabador/venv/bin/python web/app.py` → http://127.0.0.1:5000
- [x] Boceto de la interfaz aprobado (2026-10-04): https://claude.ai/artifact/Qqh6NaMCsEmryq687Am8gg — tres pantallas: materias, clases de una materia, una clase (audio arriba, resumen a la izquierda, transcripción con marcas de tiempo a la derecha, contexto abajo). Azul #2F4BC4 principal, naranja para "nueva", Space Grotesk + IBM Plex Sans
- [ ] Interfaz linda y simple siguiendo el boceto: no tiene que parecer una herramienta técnica
- [x] Materias: crear, renombrar, borrar
- [x] Clases dentro de una materia: crear, renombrar, borrar
- [x] Subir un audio a una clase desde la web (WAV, MP3, M4A, OGG → se convierte a WAV 16 kHz mono con PyAV; el anterior va a la papelera)
- [x] Pantalla de una clase: audio, transcripción y resumen
- [x] Reproductor de audio; tocar una frase de la transcripción salta a ese momento (y se resalta la que suena)
- [x] Botones: Transcribir, Resumir, Rehacer resumen. Con proveedor configurado, resume solo al terminar de transcribir
- [x] Transcripción con Whisper turbo en segundo plano (`tareas.py`, una a la vez, con progreso), prompt = materia + clase + `contexto.md`; frases cortadas por oración con `word_timestamps` (resolvió el bloque único que daba el prompt). Puerto en `transcriptores/`
- [x] Resumen multi-proveedor (`resumidores/`): Claude por SDK de Anthropic (Opus 5.5 por defecto, effort medium, fallbacks "default") + un adaptador "compatible con OpenAI" que cubre OpenAI, DeepSeek, Gemini, Mistral, Groq, OpenRouter, Ollama y cualquier otro. Los modelos se listan del proveedor ("Probar conexión"), no se inventan. Resumen en Markdown renderizado sin HTML (markdown-it, html=False)
- [x] Configuración (`/configuracion`): proveedor, modelo y API key por proveedor, en `~/.config/mis-clases/config.json` con permisos 600 — nunca en el dispositivo ni en la carpeta de clases
- [ ] Horario de cursada: cargar una vez, y deducir la materia por día y hora
- [x] Editar el contexto de una clase ya cargada

**Criterio de salida:** meter a mano un WAV de prueba, y desde la web obtener su
transcripción y su resumen, guardados en la carpeta correcta.

**Detalles que ya sabemos:**

- `vad_filter=True` siempre: saca los silencios y con eso desaparecieron las
  alucinaciones tipo "¡Suscríbete!".
- Con `initial_prompt`, Whisper tiende a devolver menos segmentos (en la prueba, uno
  solo), y se pierden marcas de tiempo intermedias. Para saltar dentro del audio
  conviene pedir `word_timestamps=True` o partir el texto por oraciones.
- El prompt tiene que ser corto: Whisper usa solo los últimos ~224 tokens.
- Si el audio corta una frase al final, Whisper la completa inventando. Con el prompt
  inventa más (completó "yo me llamo…" con el nombre del prompt).
- El resumen necesita internet; la transcripción local no.

## Fase C — Grabar a la SD con el touch y la pantalla (firmware)

- [ ] Toque corto en el TTP223 = empezar/terminar de grabar
- [ ] OLED: estado, tiempo grabado, espacio libre y **contador de buffers perdidos**
- [ ] Tarea productora (ADC) y tarea escritora (SD) en FreeRTOS, la escritora fijada al otro core
- [ ] Buffers preasignados (~8 de 4 KB ≈ 1 s), **nunca `malloc` en el camino del audio**
- [ ] Escribir `.pcm` crudo + un archivo chico al lado con tamaño, CRC32 y datos de la grabación
- [ ] Contador `seq` en NVS + `boot_id` aleatorio por arranque, para nombrar los archivos
- [ ] CRC32 calculado al vuelo mientras el audio pasa a la SD
- [ ] Prueba de 90 minutos continuos

**Por qué los buffers:** las microSD se cuelgan 100+ ms haciendo wear-leveling, y esa es
la causa número uno de audio con cortes. La cola tiene que cubrir cientos de ms.

**Por qué `.pcm` y no `.wav`:** el header WAV lleva el tamaño y se escribe al final. Un
corte de energía deja un archivo que miente sobre su tamaño. Con PCM crudo se pierde
como mucho el último bloque. La web le agrega el header WAV al recibirlo.

**La tarjeta queda en FAT32 con archivos planos**: si algo falla, se saca y se lee en
cualquier compu.

**Criterio de salida:** tocar, hablar 90 minutos, tocar de nuevo, y tener el audio
entero con cero buffers perdidos.

## Fase D — Subida automática por WiFi

- [ ] `wifi.txt` en la microSD: nombre de la red, clave y dirección de la compu
- [ ] Después de grabar, si hay pendientes, buscar el WiFi de casa cada tanto
- [ ] Nunca usar WiFi mientras graba
- [ ] Endpoint en la web para recibir grabaciones, con reanudación: la web dice cuántos bytes ya tiene y el grabador sigue desde ahí
- [ ] La web verifica el CRC32 y confirma; recién ahí el grabador marca el archivo como subido
- [ ] El grabador **nunca borra solo** lo que no fue confirmado; borra lo subido solo por falta de espacio
- [ ] Al recibir: armar la carpeta (materia por horario), transcribir y resumir solo
- [ ] OLED: "subiendo 2/3", "subido", o "compu no encontrada"
- [ ] Probar cortando el WiFi a mitad de una subida

**Cómo encuentra el grabador a la compu:** por la dirección escrita en `wifi.txt`, o
por mDNS (`<nombre-de-la-compu>.local`). Decidir en la fase.

**Si la compu está apagada**, el grabador reintenta más tarde. No se pierde nada.

**Criterio de salida:** grabar, llevar el grabador al alcance del WiFi de casa, y que
la clase aparezca transcripta y resumida en la web sin tocar nada.

---

## Para después

Nada de esto se tira. Arranca cuando lo de protoboard funcione de 10.

**Pronto (en la web es fácil):**

- Chat sobre una clase, con la transcripción entera como contexto (90 min ≈ 12-18k
  tokens, entra en cualquier modelo moderno; sin base vectorial)
- Diagramas y mapas conceptuales en Mermaid, con `mermaid.min.js` **local, no por CDN**
- Búsqueda en transcripciones y resúmenes; índice SQLite reconstruible, nunca fuente de verdad
- Vocabulario por materia armado solo a partir de las clases anteriores

**Mejorar el audio (DMA).** Hoy el audio se entiende pero suena "grueso" y con ruido de
fondo. El cambio es pasar de `analogRead()` en loop a `adc_continuous`. Trampas ya
verificadas en el core instalado:

| Trampa | Detalle |
|---|---|
| 16 kHz no se puede | `SOC_ADC_SAMPLE_FREQ_THRES_LOW` = 20000. Muestrear a 32 kHz y diezmar por 2 (promediar pares da ~3 dB de SNR y hace de antialias) |
| Formato de salida | `ADC_DIGI_OUTPUT_FORMAT_TYPE1`, 2 bytes por muestra. Leer `raw.type1.data`, no `val` |
| Canal | GPIO 35 = `ADC_CHANNEL_7` |
| ADC2 y WiFi | ADC2 no anda con WiFi encendido. El micrófono está en ADC1 y **nunca puede moverse** a GPIO 0, 2, 4, 12-15 o 25-27 |
| Convivencia | `analogRead()` y `adc_continuous` no coexisten en ADC1 |
| Sin ESP-IDF | Alcanza con `#include "esp_adc/adc_continuous.h"` en el `.ino` |

**Dispositivo real:**

- Dormir y despertar con el touch (deep sleep; GPIO 27 puede despertar a la ESP32)
- Batería, circuito de carga e interruptor físico
- Primera PCB (incluir un RTC DS3231)
- Formato lapicera y carcasa

**App de celular.** Solo si la web abierta desde el celu no alcanza. Lo investigado
sigue valiendo: Flutter; desarrollar para iPhone requiere una Mac; en Android, un AP
sin internet hace que el sistema mande el tráfico por datos móviles (se resuelve con
`WifiNetworkSpecifier` + `requestNetwork()`), y guardar en carpetas reales requiere SAF.

**Optimizaciones, solo si una medición lo pide:** ADPCM en el dispositivo si la subida
tarda demasiado; SD_MMC 1-bit si el SPI resulta ser el límite; transcripción por API
(Groq `whisper-large-v3-turbo` rondaba 0,04 USD por hora) si la compu es muy lenta.

## Riesgos

- **El grabador no encuentra la compu** (cambia de IP, firewall, compu dormida). Es lo
  primero a resolver en la Fase D.
- **WiFi y SD al mismo tiempo en el ESP32**: leer la SD mientras se sube compite por
  memoria. Medir la velocidad real de subida.
- **Orden de trabajo.** El chat y los diagramas son lo divertido; la grabación a SD y
  la subida confiable son lo difícil. No invertir el orden.
