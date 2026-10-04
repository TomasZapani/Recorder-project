"""Configuración del usuario: qué proveedor de IA usa, con qué modelo y qué API key.

El usuario elige libremente el proveedor. Cada uno guarda su propia key, así se
puede cambiar de proveedor sin volver a pegarla.

Vive en ~/.config/mis-clases/config.json, con permisos 600. Nunca dentro de la
carpeta de clases: esa se copia y se respalda, y las keys no tienen que viajar con ella.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

RUTA = Path(os.environ.get("CLASES_CONFIG", "~/.config/mis-clases/config.json")).expanduser()


@dataclass(frozen=True)
class Proveedor:
    id: str
    nombre: str
    tipo: str  # "claude" (SDK de Anthropic) | "openai" (formato compatible con OpenAI)
    base_url: str = ""  # vacío en "otro": lo escribe el usuario
    necesita_key: bool = True
    donde_key: str = ""  # dónde se consigue la key
    nota: str = ""


PROVEEDORES = {
    p.id: p
    for p in [
        Proveedor("claude", "Claude (Anthropic)", "claude", donde_key="https://console.anthropic.com/settings/keys"),
        Proveedor("openai", "OpenAI (ChatGPT)", "openai", "https://api.openai.com/v1", donde_key="https://platform.openai.com/api-keys"),
        Proveedor("deepseek", "DeepSeek", "openai", "https://api.deepseek.com", donde_key="https://platform.deepseek.com/api_keys"),
        Proveedor("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai/", donde_key="https://aistudio.google.com/apikey"),
        Proveedor("mistral", "Mistral", "openai", "https://api.mistral.ai/v1", donde_key="https://console.mistral.ai/api-keys"),
        Proveedor("groq", "Groq", "openai", "https://api.groq.com/openai/v1", donde_key="https://console.groq.com/keys"),
        Proveedor("openrouter", "OpenRouter (cientos de modelos)", "openai", "https://openrouter.ai/api/v1", donde_key="https://openrouter.ai/keys"),
        Proveedor("ollama", "Ollama (en tu compu, sin internet)", "openai", "http://localhost:11434/v1", necesita_key=False,
                  nota="Necesitás Ollama instalado y un modelo descargado (ollama pull ...)."),
        Proveedor("otro", "Otro compatible con OpenAI", "openai", necesita_key=False,
                  nota="Cualquier servicio con API compatible con OpenAI: escribí su dirección."),
    ]
}


@dataclass
class Cuenta:
    """Lo que el usuario cargó para un proveedor."""

    api_key: str = ""
    modelo: str = ""
    base_url: str = ""  # solo se usa en "otro" (o para pisar la dirección de un proveedor)


@dataclass
class Configuracion:
    proveedor: str = "claude"
    cuentas: dict[str, Cuenta] = field(default_factory=dict)
    resumir_al_transcribir: bool = True

    def cuenta(self, proveedor: str | None = None) -> Cuenta:
        return self.cuentas.setdefault(proveedor or self.proveedor, Cuenta())

    def base_url(self, proveedor: str | None = None) -> str:
        pid = proveedor or self.proveedor
        return self.cuenta(pid).base_url or PROVEEDORES[pid].base_url

    def falta_para_resumir(self) -> str | None:
        """Qué le falta configurar al proveedor activo, o None si está listo."""
        p, c = PROVEEDORES[self.proveedor], self.cuenta()
        if p.necesita_key and not c.api_key:
            return f"Falta la API key de {p.nombre}."
        if p.tipo == "openai" and not self.base_url():
            return "Falta la dirección del servicio."
        if not c.modelo:
            return f"Falta elegir el modelo de {p.nombre}."
        return None


def key_oculta(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("•" * len(key))


def cargar() -> Configuracion:
    try:
        datos = json.loads(RUTA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return Configuracion()
    cuentas = {
        pid: Cuenta(**{k: v for k, v in c.items() if k in Cuenta.__dataclass_fields__})
        for pid, c in datos.get("cuentas", {}).items()
        if pid in PROVEEDORES
    }
    proveedor = datos.get("proveedor", "claude")
    return Configuracion(
        proveedor=proveedor if proveedor in PROVEEDORES else "claude",
        cuentas=cuentas,
        resumir_al_transcribir=bool(datos.get("resumir_al_transcribir", True)),
    )


def guardar(config: Configuracion) -> None:
    RUTA.parent.mkdir(parents=True, exist_ok=True)
    temporal = RUTA.with_suffix(".tmp")
    # Crear el archivo ya con permisos 600: las keys nunca quedan legibles para otros.
    fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(config), f, indent=2, ensure_ascii=False)
    temporal.replace(RUTA)
