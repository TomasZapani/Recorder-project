"""Resumen con Claude, por el SDK oficial de Anthropic."""

import anthropic

from biblioteca import Frase
from resumidores import SISTEMA, ErrorResumen, mensaje_usuario


def _traducir_error(e: Exception) -> ErrorResumen:
    """Los errores de la API, en palabras que el usuario entienda. Del más específico al más general."""
    if isinstance(e, anthropic.AuthenticationError):
        return ErrorResumen("Anthropic rechazó la API key. Revisala en Configuración.")
    if isinstance(e, anthropic.PermissionDeniedError):
        return ErrorResumen("Tu API key no tiene permiso para usar este modelo.")
    if isinstance(e, anthropic.NotFoundError):
        return ErrorResumen("Ese modelo no existe o no está disponible para tu cuenta. Elegí otro en Configuración.")
    if isinstance(e, anthropic.RateLimitError):
        return ErrorResumen("Anthropic está limitando los pedidos de tu cuenta. Reintentá en unos minutos.")
    if isinstance(e, anthropic.BadRequestError):
        return ErrorResumen(f"Anthropic rechazó el pedido: {e.message}")  # ej.: saldo insuficiente
    if isinstance(e, anthropic.APIStatusError):
        return ErrorResumen(f"Los servidores de Anthropic fallaron ({e.status_code}). Reintentá en un rato.")
    if isinstance(e, anthropic.APIConnectionError):
        return ErrorResumen("No se pudo conectar con Anthropic. ¿Hay internet?")
    return ErrorResumen(str(e))


class ResumidorClaude:
    def __init__(self, api_key: str, modelo: str):
        self._cliente = anthropic.Anthropic(api_key=api_key)
        self.modelo = modelo
        self.nombre = f"Claude (Anthropic) · {modelo}"

    def resumir(self, frases: list[Frase], materia: str, clase: str, contexto: str) -> str:
        try:
            # Streaming: una clase larga produce un pedido grande, y así no hay timeouts.
            with self._cliente.beta.messages.stream(
                model=self.modelo,
                max_tokens=32000,
                output_config={"effort": "medium"},
                # Si un filtro de seguridad rechaza por error (puede pasar con química o
                # biología), la API reintenta sola con el modelo que Anthropic recomienda.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SISTEMA,
                messages=[{"role": "user", "content": mensaje_usuario(frases, materia, clase, contexto)}],
            ) as stream:
                respuesta = stream.get_final_message()
        except anthropic.APIError as e:
            raise _traducir_error(e) from e

        if respuesta.stop_reason == "refusal":
            raise ErrorResumen("Claude se negó a resumir esta clase. Probá con otro modelo o proveedor.")
        if respuesta.stop_reason == "max_tokens":
            raise ErrorResumen("El resumen quedó cortado por largo. Reintentá.")
        texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()
        if not texto:
            raise ErrorResumen("Claude devolvió un resumen vacío. Reintentá.")
        if respuesta.model != self.modelo:  # lo resolvió el modelo de respaldo
            self.nombre = f"Claude (Anthropic) · {respuesta.model}"
        return texto


def listar_modelos(api_key: str) -> list[str]:
    try:
        return [m.id for m in anthropic.Anthropic(api_key=api_key).models.list()]
    except anthropic.APIError as e:
        raise _traducir_error(e) from e
