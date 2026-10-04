"""Puerto de resumen: transcripción → resumen en Markdown.

Dos implementaciones cubren a casi todos los proveedores:
- `claude.py`: SDK oficial de Anthropic.
- `compatible_openai.py`: cualquier servicio con API compatible con OpenAI
  (OpenAI, DeepSeek, Gemini, Mistral, Groq, OpenRouter, Ollama, ...).
"""

from typing import Protocol

from biblioteca import Frase
from configuracion import PROVEEDORES, Configuracion

SISTEMA = """\
Armás apuntes de estudio a partir de la transcripción de una clase universitaria. \
El estudiante va a usarlos para repasar, así que tienen que ser fieles a lo que se dijo y fáciles de leer.

La transcripción es automática: puede tener palabras mal reconocidas (un apellido o un término \
técnico escrito parecido). Corregilas cuando el contexto deje claro qué se quiso decir; si no \
está claro, no inventes.

Escribí en español, en Markdown:
- Un párrafo corto al principio que diga de qué trató la clase.
- Secciones con títulos (##) por tema, en el orden en que se dieron. Listas y **negritas** para \
los conceptos clave.
- Fórmulas en LaTeX: $...$ dentro del texto y $$...$$ para las que van solas en su línea. \
Fórmulas químicas con mhchem: $\\ce{H2SO4}$. Los montos de dinero, con el signo escapado: \\$500.
- Junto al título de cada tema, entre paréntesis, el minuto donde arranca según las marcas de la \
transcripción, por ejemplo (12:40).
- Si se mencionaron tareas, fechas, parciales o bibliografía, terminá con una sección \
"## Para tener en cuenta".

No agregues información que no esté en la clase."""


class ErrorResumen(Exception):
    """Error pensado para mostrárselo al usuario tal cual."""


class Resumidor(Protocol):
    nombre: str  # se guarda en el .md, ej. "Claude (Anthropic) · claude-opus-5-5"

    def resumir(self, frases: list[Frase], materia: str, clase: str, contexto: str) -> str:
        """Devuelve el resumen en Markdown."""
        ...


def mensaje_usuario(frases: list[Frase], materia: str, clase: str, contexto: str) -> str:
    def mmss(s: float) -> str:
        s = int(s)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"

    partes = [f"Materia: {materia}", f"Clase: {clase}"]
    if contexto:
        partes.append(f"Contexto que agregó el estudiante: {contexto}")
    texto = "\n".join(f"[{mmss(f.inicio)}] {f.texto}" for f in frases)
    partes.append(f"\n<transcripcion>\n{texto}\n</transcripcion>")
    return "\n".join(partes)


def desde_config(config: Configuracion) -> Resumidor:
    if falta := config.falta_para_resumir():
        raise ErrorResumen(f"{falta} Completalo en Configuración.")
    proveedor, cuenta = PROVEEDORES[config.proveedor], config.cuenta()
    if proveedor.tipo == "claude":
        from resumidores.claude import ResumidorClaude

        return ResumidorClaude(cuenta.api_key, cuenta.modelo)
    from resumidores.compatible_openai import ResumidorCompatibleOpenAI

    return ResumidorCompatibleOpenAI(proveedor.nombre, config.base_url(), cuenta.api_key, cuenta.modelo)


def listar_modelos(config: Configuracion, proveedor_id: str) -> list[str]:
    """Le pregunta al proveedor qué modelos tiene disponibles. También sirve para probar la key."""
    proveedor, cuenta = PROVEEDORES[proveedor_id], config.cuenta(proveedor_id)
    if proveedor.necesita_key and not cuenta.api_key:
        raise ErrorResumen(f"Pegá la API key de {proveedor.nombre} antes de probar.")
    if proveedor.tipo == "claude":
        from resumidores.claude import listar_modelos as listar
        return listar(cuenta.api_key)
    if not config.base_url(proveedor_id):
        raise ErrorResumen("Escribí la dirección del servicio antes de probar.")
    from resumidores.compatible_openai import listar_modelos as listar
    return listar(config.base_url(proveedor_id), cuenta.api_key)
