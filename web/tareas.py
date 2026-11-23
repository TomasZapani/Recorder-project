"""Cola de trabajos en segundo plano: transcribir y resumir.

Uno a la vez: cada transcripción ya ocupa toda la CPU. El estado vive en
memoria; si la web se reinicia, lo que estaba en la cola se pierde (los archivos
ya escritos no).
"""

import threading
import traceback
from collections import deque
from dataclasses import dataclass

import configuracion
import resumidores
from biblioteca import AUDIO, Biblioteca

_MAX_CONTEXTO = 600  # Whisper solo usa los últimos ~224 tokens del prompt

TRANSCRIBIR = "transcribir"
RESUMIR = "resumir"


@dataclass
class Estado:
    tarea: str  # TRANSCRIBIR | RESUMIR
    fase: str  # "en cola" | "trabajando" | "error"
    progreso: float = 0.0  # 0..1; el resumen no informa avance
    error: str = ""


def armar_prompt(materia: str, clase: str, contexto: str, tipo: str = "clase") -> str:
    """Contexto para Whisper: le dice de qué se habla para que acierte los términos."""
    prompt = f"Reunión de {materia}. {clase}." if tipo == "reunion" else f"Clase de {materia}. {clase}."
    if contexto:
        prompt += " " + contexto[:_MAX_CONTEXTO]
    return prompt


class Cola:
    def __init__(self, biblioteca: Biblioteca, fabrica_transcriptor):
        self._biblioteca = biblioteca
        self._fabrica = fabrica_transcriptor
        self._transcriptor = None
        self._pendientes: deque[tuple[str, str]] = deque()
        self._estados: dict[tuple[str, str], Estado] = {}
        self._lock = threading.Lock()
        self._hay_trabajo = threading.Condition(self._lock)
        self._hilo = None

    def encolar(self, materia: str, clase: str, tarea: str) -> None:
        clave = (materia, clase)
        with self._lock:
            if clave in self._estados and self._estados[clave].fase != "error":
                return  # esa clase ya tiene un trabajo en la cola o en curso
            self._estados[clave] = Estado(tarea, "en cola")
            self._pendientes.append(clave)
            if self._hilo is None:
                self._hilo = threading.Thread(target=self._trabajar, daemon=True, name="tareas")
                self._hilo.start()
            self._hay_trabajo.notify()

    def estado(self, materia: str, clase: str) -> Estado | None:
        with self._lock:
            return self._estados.get((materia, clase))

    def ocupada(self, materia: str, clase: str | None = None) -> bool:
        """¿Hay trabajo activo sobre esa clase (o sobre alguna clase de la materia)?"""
        with self._lock:
            return any(
                e.fase != "error" and m == materia and (clase is None or c == clase)
                for (m, c), e in self._estados.items()
            )

    def descartar_error(self, materia: str, clase: str) -> None:
        with self._lock:
            estado = self._estados.get((materia, clase))
            if estado and estado.fase == "error":
                del self._estados[(materia, clase)]

    def _trabajar(self) -> None:
        while True:
            with self._lock:
                while not self._pendientes:
                    self._hay_trabajo.wait()
                materia, clase = self._pendientes.popleft()
                estado = self._estados[(materia, clase)]
                estado.fase = "trabajando"
            try:
                siguiente = None
                if estado.tarea == TRANSCRIBIR:
                    self._transcribir(materia, clase, estado)
                    config = configuracion.cargar()
                    if config.resumir_al_transcribir and not config.falta_para_resumir():
                        siguiente = RESUMIR
                else:
                    self._resumir(materia, clase)
                with self._lock:
                    if siguiente:  # transcribir → resumir, sin soltar la clase entre medio
                        self._estados[(materia, clase)] = Estado(siguiente, "en cola")
                        self._pendientes.append((materia, clase))
                    else:
                        del self._estados[(materia, clase)]
            except Exception as e:  # el hilo no puede morir: el error se muestra en la página
                if not isinstance(e, resumidores.ErrorResumen):
                    traceback.print_exc()
                with self._lock:
                    estado.fase, estado.error = "error", str(e) or e.__class__.__name__

    def _transcribir(self, materia: str, clase: str, estado: Estado) -> None:
        audio = self._biblioteca.ruta_archivo(materia, clase, AUDIO)
        if not audio.is_file():
            raise RuntimeError("La clase no tiene audio.")
        if self._transcriptor is None:
            self._transcriptor = self._fabrica()
        contexto = self._biblioteca.leer_contexto(materia, clase)
        prompt = armar_prompt(materia, clase, contexto, configuracion.cargar().tipo_resumen)

        def progreso(valor: float) -> None:
            estado.progreso = valor

        frases = self._transcriptor.transcribir(audio, prompt, progreso)
        self._biblioteca.guardar_transcripcion(materia, clase, frases, self._transcriptor.nombre)

    def _resumir(self, materia: str, clase: str) -> None:
        frases = self._biblioteca.leer_transcripcion(materia, clase)
        if not frases:
            raise resumidores.ErrorResumen("Primero hay que transcribir la clase.")
        resumidor = resumidores.desde_config(configuracion.cargar())
        contexto = self._biblioteca.leer_contexto(materia, clase)
        markdown = resumidor.resumir(frases, materia, clase, contexto)
        self._biblioteca.guardar_resumen(materia, clase, markdown, resumidor.nombre)
