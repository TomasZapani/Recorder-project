"""Resumen con cualquier servicio compatible con la API de OpenAI.

Cubre OpenAI, DeepSeek, Google Gemini, Mistral, Groq, OpenRouter, Ollama y
cualquier otro que hable el mismo formato: solo cambian la dirección y la key.
"""

import openai

from biblioteca import Frase
from resumidores import ErrorResumen, mensaje_usuario


def _traducir_error(e: Exception, servicio: str) -> ErrorResumen:
    if isinstance(e, openai.AuthenticationError):
        return ErrorResumen(f"{servicio} rechazó la API key. Revisala en Configuración.")
    if isinstance(e, openai.PermissionDeniedError):
        return ErrorResumen(f"Tu API key de {servicio} no tiene permiso para usar este modelo.")
    if isinstance(e, openai.NotFoundError):
        return ErrorResumen(f"{servicio} no encontró ese modelo. Elegí otro en Configuración.")
    if isinstance(e, openai.RateLimitError):
        return ErrorResumen(f"{servicio} está limitando los pedidos (o te quedaste sin saldo). Reintentá más tarde.")
    if isinstance(e, openai.BadRequestError):
        return ErrorResumen(f"{servicio} rechazó el pedido: {e.message}")
    if isinstance(e, openai.APIStatusError):
        return ErrorResumen(f"Los servidores de {servicio} fallaron ({e.status_code}). Reintentá en un rato.")
    if isinstance(e, openai.APIConnectionError):
        return ErrorResumen(f"No se pudo conectar con {servicio}. ¿Hay internet? ¿Está bien la dirección?")
    return ErrorResumen(str(e))


def _cliente(base_url: str, api_key: str) -> openai.OpenAI:
    # Ollama y otros servicios locales no usan key, pero la librería exige una.
    return openai.OpenAI(base_url=base_url, api_key=api_key or "sin-key", timeout=600)


class ResumidorCompatibleOpenAI:
    def __init__(self, servicio: str, base_url: str, api_key: str, modelo: str, sistema: str):
        self._servicio = servicio
        self._cliente = _cliente(base_url, api_key)
        self.modelo = modelo
        self._sistema = sistema
        self.nombre = f"{servicio} · {modelo}"

    def resumir(self, frases: list[Frase], materia: str, clase: str, contexto: str) -> str:
        try:
            respuesta = self._cliente.chat.completions.create(
                model=self.modelo,
                messages=[
                    {"role": "system", "content": self._sistema},
                    {"role": "user", "content": mensaje_usuario(frases, materia, clase, contexto)},
                ],
            )
        except openai.APIError as e:
            raise _traducir_error(e, self._servicio) from e

        if not respuesta.choices:
            raise ErrorResumen(f"{self._servicio} devolvió una respuesta vacía. Reintentá.")
        eleccion = respuesta.choices[0]
        if eleccion.finish_reason == "length":
            raise ErrorResumen("El resumen quedó cortado por largo. Probá con un modelo de más capacidad.")
        if eleccion.finish_reason == "content_filter":
            raise ErrorResumen(f"{self._servicio} bloqueó el resumen por su filtro de contenido. Probá con otro proveedor.")
        texto = (eleccion.message.content or "").strip()
        if not texto:
            raise ErrorResumen(f"{self._servicio} devolvió un resumen vacío. Reintentá.")
        return texto


def listar_modelos(base_url: str, api_key: str) -> list[str]:
    try:
        return sorted(m.id for m in _cliente(base_url, api_key).models.list())
    except openai.APIError as e:
        raise _traducir_error(e, "El servicio") from e
