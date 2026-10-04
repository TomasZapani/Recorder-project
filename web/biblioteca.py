"""Materias y clases sobre carpetas comunes.

Estructura (dos niveles fijos):

    <raíz>/<Materia>/<Clase>/{audio.wav, contexto.md, transcripcion.md, resumen.md}

Los archivos mandan: no hay base de datos. Este módulo no sabe nada de la web.
"""

import re
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from send2trash import send2trash

from conversion import AudioInvalido, a_wav

AUDIO = "audio.wav"
CONTEXTO = "contexto.md"
TRANSCRIPCION = "transcripcion.md"
RESUMEN = "resumen.md"

_PROHIBIDOS = set('/\\:*?"<>|')


class ErrorBiblioteca(Exception):
    """Error pensado para mostrárselo al usuario tal cual."""


@dataclass
class Clase:
    nombre: str
    ruta: Path
    modificada: datetime
    tiene_audio: bool
    tiene_transcripcion: bool
    tiene_resumen: bool
    duracion: float | None  # segundos; None si no hay audio legible


@dataclass
class Frase:
    inicio: float  # segundos desde el comienzo del audio
    texto: str


@dataclass
class Materia:
    nombre: str
    ruta: Path
    cantidad_clases: int
    ultima_clase: datetime | None


def _clave_natural(nombre: str):
    """Ordena 'Clase 2' antes que 'Clase 10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", nombre)]


_LINEA_FRASE = re.compile(r"^\*\*\[(\d+):(\d\d):(\d\d)\]\*\* (.+)$")


def _hms(segundos: float) -> str:
    s = int(segundos)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _escribir_atomico(ruta: Path, texto: str) -> None:
    """Escribe a un temporal y lo renombra: nunca queda un archivo a medias."""
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_text(texto, encoding="utf-8")
    temporal.replace(ruta)


def _duracion_wav(ruta: Path) -> float | None:
    try:
        with wave.open(str(ruta), "rb") as w:
            return w.getnframes() / w.getframerate()
    except (FileNotFoundError, wave.Error, EOFError):
        return None


def validar_nombre(nombre: str) -> str:
    nombre = nombre.strip()
    if not nombre:
        raise ErrorBiblioteca("El nombre no puede estar vacío.")
    if nombre in (".", "..") or nombre.startswith("."):
        raise ErrorBiblioteca("El nombre no puede empezar con un punto.")
    malos = sorted(_PROHIBIDOS & set(nombre))
    if malos:
        raise ErrorBiblioteca(f"El nombre no puede tener estos caracteres: {' '.join(malos)}")
    if len(nombre) > 120:
        raise ErrorBiblioteca("El nombre es demasiado largo (máximo 120 caracteres).")
    return nombre


class Biblioteca:
    def __init__(self, raiz: Path):
        self.raiz = raiz.expanduser().resolve()
        self.raiz.mkdir(parents=True, exist_ok=True)

    # --- rutas seguras -------------------------------------------------------

    def _carpeta(self, *partes: str) -> Path:
        ruta = self.raiz.joinpath(*(validar_nombre(p) for p in partes)).resolve()
        if self.raiz not in ruta.parents:
            raise ErrorBiblioteca("Ruta inválida.")
        return ruta

    def _existente(self, *partes: str) -> Path:
        ruta = self._carpeta(*partes)
        if not ruta.is_dir():
            que = "La clase" if len(partes) == 2 else "La materia"
            raise ErrorBiblioteca(f"{que} «{partes[-1]}» no existe.")
        return ruta

    def _nueva(self, *partes: str) -> Path:
        ruta = self._carpeta(*partes)
        if ruta.exists():
            que = "una clase" if len(partes) == 2 else "una materia"
            raise ErrorBiblioteca(f"Ya existe {que} llamada «{ruta.name}».")
        return ruta

    @staticmethod
    def _subcarpetas(ruta: Path) -> list[Path]:
        hijas = [p for p in ruta.iterdir() if p.is_dir() and not p.name.startswith(".")]
        return sorted(hijas, key=lambda p: _clave_natural(p.name))

    # --- materias ------------------------------------------------------------

    def materias(self) -> list[Materia]:
        resultado = []
        for ruta in self._subcarpetas(self.raiz):
            clases = self._subcarpetas(ruta)
            ultima = max((datetime.fromtimestamp(c.stat().st_mtime) for c in clases), default=None)
            resultado.append(Materia(ruta.name, ruta, len(clases), ultima))
        return resultado

    def materia(self, nombre: str) -> Materia:
        ruta = self._existente(nombre)
        clases = self._subcarpetas(ruta)
        ultima = max((datetime.fromtimestamp(c.stat().st_mtime) for c in clases), default=None)
        return Materia(ruta.name, ruta, len(clases), ultima)

    def crear_materia(self, nombre: str) -> str:
        ruta = self._nueva(nombre)
        ruta.mkdir()
        return ruta.name

    def renombrar_materia(self, actual: str, nuevo: str) -> str:
        origen = self._existente(actual)
        destino = self._nueva(nuevo)
        origen.rename(destino)
        return destino.name

    def borrar_materia(self, nombre: str) -> None:
        send2trash(self._existente(nombre))

    # --- clases --------------------------------------------------------------

    def clases(self, materia: str) -> list[Clase]:
        return [self._clase(r) for r in self._subcarpetas(self._existente(materia))]

    def clase(self, materia: str, nombre: str) -> Clase:
        return self._clase(self._existente(materia, nombre))

    @staticmethod
    def _clase(ruta: Path) -> Clase:
        return Clase(
            nombre=ruta.name,
            ruta=ruta,
            modificada=datetime.fromtimestamp(ruta.stat().st_mtime),
            tiene_audio=(ruta / AUDIO).is_file(),
            tiene_transcripcion=(ruta / TRANSCRIPCION).is_file(),
            tiene_resumen=(ruta / RESUMEN).is_file(),
            duracion=_duracion_wav(ruta / AUDIO),
        )

    def siguiente_nombre_de_clase(self, materia: str) -> str:
        """'Clase N' con el primer N libre después del más alto."""
        numeros = [
            int(m.group(1))
            for c in self._subcarpetas(self._existente(materia))
            if (m := re.fullmatch(r"Clase (\d+)", c.name))
        ]
        return f"Clase {max(numeros, default=0) + 1}"

    def crear_clase(self, materia: str, nombre: str) -> str:
        self._existente(materia)
        ruta = self._nueva(materia, nombre)
        ruta.mkdir()
        return ruta.name

    def renombrar_clase(self, materia: str, actual: str, nuevo: str) -> str:
        origen = self._existente(materia, actual)
        destino = self._nueva(materia, nuevo)
        origen.rename(destino)
        return destino.name

    def borrar_clase(self, materia: str, nombre: str) -> None:
        send2trash(self._existente(materia, nombre))

    def guardar_audio(self, materia: str, clase: str, origen: Path) -> float:
        """Convierte `origen` a audio.wav de la clase. Devuelve la duración en segundos.

        Si la clase ya tenía audio, el anterior va a la papelera.
        """
        carpeta = self._existente(materia, clase)
        temporal = carpeta / ".audio.subiendo.wav"
        try:
            duracion = a_wav(origen, temporal)
        except AudioInvalido as e:
            temporal.unlink(missing_ok=True)
            raise ErrorBiblioteca(str(e)) from e
        final = carpeta / AUDIO
        if final.exists():
            send2trash(final)
        temporal.rename(final)
        return duracion

    # --- textos de la clase ----------------------------------------------------

    def guardar_transcripcion(self, materia: str, clase: str, frases: list[Frase], origen: str) -> None:
        """Guarda transcripcion.md: una frase por línea con su marca de tiempo."""
        carpeta = self._existente(materia, clase)
        lineas = [f"# Transcripción — {clase}", "", f"_{origen}_", ""]
        lineas += [f"**[{_hms(f.inicio)}]** {f.texto}\n" for f in frases]
        _escribir_atomico(carpeta / TRANSCRIPCION, "\n".join(lineas))

    def leer_transcripcion(self, materia: str, clase: str) -> list[Frase] | None:
        """Las frases de transcripcion.md, o None si no hay. Tolera ediciones a mano."""
        ruta = self._existente(materia, clase) / TRANSCRIPCION
        if not ruta.is_file():
            return None
        frases = []
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if m := _LINEA_FRASE.match(linea.strip()):
                h, mi, s, texto = m.groups()
                frases.append(Frase(int(h) * 3600 + int(mi) * 60 + int(s), texto))
        return frases

    def guardar_resumen(self, materia: str, clase: str, markdown: str, origen: str) -> None:
        """Guarda resumen.md. Si había uno, el anterior va a la papelera."""
        carpeta = self._existente(materia, clase)
        ruta = carpeta / RESUMEN
        if ruta.exists():
            send2trash(ruta)
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        _escribir_atomico(ruta, f"{markdown.strip()}\n\n---\n\n_Resumen hecho con {origen} · {fecha}_\n")

    def leer_contexto(self, materia: str, clase: str) -> str:
        ruta = self._existente(materia, clase) / CONTEXTO
        return ruta.read_text(encoding="utf-8").strip() if ruta.is_file() else ""

    def guardar_contexto(self, materia: str, clase: str, texto: str) -> None:
        ruta = self._existente(materia, clase) / CONTEXTO
        texto = texto.strip()
        if texto:
            _escribir_atomico(ruta, texto + "\n")
        else:
            ruta.unlink(missing_ok=True)

    def ruta_archivo(self, materia: str, clase: str, archivo: str) -> Path:
        if archivo not in (AUDIO, CONTEXTO, TRANSCRIPCION, RESUMEN):
            raise ErrorBiblioteca("Archivo desconocido.")
        return self._existente(materia, clase) / archivo
