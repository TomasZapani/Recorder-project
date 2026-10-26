# Pasar el proyecto a Windows

El código está en GitHub. Lo que vive fuera del repo hay que llevarlo a mano.

## 1. Backup antes de formatear (desde Ubuntu)

| Qué | Dónde |
|---|---|
| Clases (audio, transcripción, resumen) | `~/Facultad/` |
| Config de la web (proveedor, modelo, **API keys**) | `~/.config/mis-clases/config.json` |
| Memoria de Claude Code | `~/.claude/projects/-home-tomas-Desktop-Grabaciones/memory/` |
| Grabaciones de prueba (fuera de git a propósito) | `grabacion*` en la carpeta del proyecto |
| Permisos de Claude Code del proyecto | `.claude/settings.local.json` |

```bash
tar czf /media/PENDRIVE/backup-grabador.tgz -C ~ Facultad .config/mis-clases .claude/projects/-home-tomas-Desktop-Grabaciones/memory Desktop/Grabaciones/.claude Desktop/Grabaciones/grabacion*
```

El backup tiene las API keys: guardarlo en un lugar privado.

No hace falta guardar: el venv, los modelos de Whisper, Arduino IDE ni
`~/grabador/` (el script viejo de transcripción en vivo, descartado).

## 2. Programas

- **Python 3.13** (python.org, tildar "Add python.exe to PATH"). No 3.14: en
  Windows puede faltar el paquete listo para instalar de `faster-whisper` o `av`.
- **Git for Windows** y **GitHub CLI**, después `gh auth login`.
- **Arduino IDE 2**, instalador normal.

## 3. Proyecto (PowerShell)

```powershell
gh repo clone TomasZapani/Recorder-project Grabaciones
cd Grabaciones
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Si no deja activar el venv: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 4. Restaurar el backup

```powershell
tar xzf E:\backup-grabador.tgz -C $HOME
```

`Facultad` y `.config\mis-clases` quedan en `C:\Users\<usuario>\`, donde los
busca la web. Las grabaciones de prueba y `.claude` caen en
`$HOME\Desktop\Grabaciones`: moverlas si el repo se clonó en otro lado.

## 5. Arduino y ESP32

- Core **esp32 3.3.11** y librería **Adafruit SSD1306** (trae GFX y BusIO).
- Si no aparece ningún puerto COM al enchufar la placa, falta el driver
  USB-serie: **CP2102** (Silicon Labs) o **CH340** (WCH), según el chip de la placa.
- El puerto se ve en Administrador de dispositivos → "Puertos (COM y LPT)".
  `grabar_wav.py` usa `/dev/ttyUSB0` por defecto, así que en Windows:
  `python grabar_wav.py -p COM3 ...`

## 6. Primera vez

- **Firewall**: cuando Windows pregunte por Python, permitir en **redes
  privadas**. Sin eso no entra el celular ni sube el grabador.
- **Whisper**: `large-v3-turbo` (~1,6 GB) se baja solo la primera vez.
- **Memoria de Claude Code**: abrir `claude` una vez en el proyecto y copiar los
  `.md` del backup a `C:\Users\<usuario>\.claude\projects\<carpeta nueva>\memory\`.

## Pendiente

Actualizar las partes de Linux de `CLAUDE.md` (ruta del venv, puerto serie,
AppImage).
