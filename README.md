# 🎙️ Recorder

**Un grabador de clases casero.** Lo apretás al empezar la clase, lo apretás al terminar, y en casa tenés el audio, la transcripción y tus apuntes, ordenados por materia.

Sin suscripciones, sin cuentas y sin nube obligatoria: **vos elegís qué inteligencia artificial usar, y tus clases son archivos comunes en tu compu.**

---

## Cómo funciona

```
 🎓 En clase                 🏠 En casa                          📚 Para estudiar
┌───────────────┐  WiFi    ┌──────────────────────────────┐    ┌──────────────────┐
│ ESP32 graba   │ ───────► │ Whisper transcribe (local)   │ ─► │ Audio            │
│ a la microSD  │          │ La IA que elijas resume      │    │ Transcripción    │
└───────────────┘          └──────────────────────────────┘    │ Apuntes          │
                                                               └──────────────────┘
```

1. **Grabás.** Un toque empieza, otro termina. El grabador no guarda contraseñas de nada.
2. **Se transcribe en tu compu** con Whisper: gratis y privado.
3. **Se resume con la IA que quieras:** Claude, ChatGPT, Gemini, DeepSeek, Mistral, Groq, OpenRouter, o un modelo local con Ollama.

## Qué tiene hoy

- 📁 Materias y clases, como carpetas: `Facultad/Física 1/Clase 3/`
- 🎧 Reproductor: tocás una frase de la transcripción y el audio salta ahí
- ✍️ Apuntes con títulos por tema, el minuto de cada uno y fórmulas bien dibujadas
- 🔌 Cualquier proveedor de IA, con tu propia API key
- 🗂️ Todo en Markdown: también se abre con Obsidian

## Cómo usarlo

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python web/app.py
```

Abrí **http://127.0.0.1:5000**, creá una materia, subí un audio y listo. La API key se carga desde **Configuración**.

## El proyecto

| Carpeta | Qué hay |
|---|---|
| `web/` | La aplicación: Flask + Whisper + proveedores de IA |
| `firmware/` | Lo que corre en la ESP32 (Arduino) |
| `grabar_wav.py` | Diagnóstico: graba audio desde la ESP32 por cable |
| `PLAN.md` | Qué falta y en qué orden |

**Hardware:** ESP32 · micrófono MAX9814 · pantalla OLED SSD1306 · lector microSD · botón táctil TTP223.

## Estado

- 🟢 **Web:** materias, transcripción y resúmenes funcionando
- 🟡 **Grabador:** el micrófono anda; falta grabar solo a la microSD
- ⚪ **Subida automática por WiFi:** pendiente

Hecho para la facultad, en una protoboard. 🛠️
