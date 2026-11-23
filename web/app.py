"""Web local para organizar las clases grabadas.

Uso:  venv/Scripts/python web/app.py   (en Linux: venv/bin/python)
Abre: http://127.0.0.1:5000

Escucha en toda la red local (0.0.0.0) para que el grabador pueda subir por WiFi y se
pueda abrir desde el celu. HOST=127.0.0.1 la deja solo para esta compu; DEBUG=1 activa
el modo de desarrollo (solo con HOST=127.0.0.1: el depurador permite correr código).

Las carpetas viven en ~/Facultad (se cambia con la variable CLASES_DIR).
"""

import os
import tempfile
import zlib
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from markupsafe import Markup

import configuracion
import grabador
import resumidores
import transcriptores
from biblioteca import AUDIO, RESUMEN, Biblioteca, ErrorBiblioteca, validar_nombre
from configuracion import PROVEEDORES, TIPOS_DE_RESUMEN, key_oculta
from tareas import RESUMIR, TRANSCRIBIR, Cola

app = Flask(__name__)
app.secret_key = os.urandom(16)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024**3  # 2 GB: una clase larga en WAV ronda 170 MB
biblioteca = Biblioteca(Path(os.environ.get("CLASES_DIR", "~/Facultad")))
cola = Cola(biblioteca, transcriptores.por_defecto)
app.register_blueprint(grabador.crear(biblioteca, cola))

_OCUPADA = "Esperá a que termine la transcripción o el resumen para hacer cambios."

# html=False: el HTML que venga dentro del resumen se muestra como texto, nunca se ejecuta.
# dollarmath aparta las fórmulas ($...$ y $$...$$) antes de procesar el Markdown, para que
# un "_" de subíndice no se convierta en cursiva; después KaTeX las dibuja en el navegador.
# allow_digits=False: "$5" no abre una fórmula.
_markdown = (
    MarkdownIt("commonmark", {"html": False, "linkify": False})
    .enable("table")
    .use(dollarmath_plugin, double_inline=True, allow_digits=False)
)


@app.template_filter("markdown")
def markdown(texto: str) -> Markup:
    return Markup(_markdown.render(texto))


@app.context_processor
def configuracion_actual():
    config = configuracion.cargar()
    return {
        "falta_para_resumir": config.falta_para_resumir(),
        "proveedor_actual": PROVEEDORES[config.proveedor],
        "destino": (config.materia_grabador, config.clase_grabador),
    }


def _seguir_destino(materia: str, clase: str | None, nueva_materia: str | None, nueva_clase: str | None) -> None:
    """Si se renombra o se borra la carpeta marcada para el grabador, el destino la sigue."""
    config = configuracion.cargar()
    if config.materia_grabador != materia:
        return
    if clase is None:  # la materia entera
        if nueva_materia is None:
            config.materia_grabador, config.clase_grabador = configuracion.MATERIA_GRABADOR, ""
        else:
            config.materia_grabador = nueva_materia
    elif config.clase_grabador == clase:
        config.clase_grabador = nueva_clase or ""
    else:
        return
    configuracion.guardar(config)

# Pares (fondo, tinta) para el ícono de cada materia; contraste AA.
_COLORES = [
    ("#E4E9FB", "#2F4BC4"),
    ("#FDEBDD", "#9A3A0B"),
    ("#E3F2EA", "#1D6B3A"),
    ("#F3E6F8", "#7A2E95"),
    ("#E2F1F4", "#14606F"),
    ("#FBE7EA", "#A3233A"),
]
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_CONECTORES = {"de", "del", "la", "las", "los", "el", "y", "e", "a"}


@app.template_filter("sigla")
def sigla(nombre: str) -> str:
    palabras = [p for p in nombre.split() if p.lower() not in _CONECTORES]
    if len(palabras) >= 2:
        segunda = palabras[1]
        return (palabras[0][0] + (segunda if segunda.isdigit() and len(segunda) <= 2 else segunda[0])).upper()
    return nombre[:2].upper()


@app.template_filter("colores")
def colores(nombre: str) -> tuple[str, str]:
    return _COLORES[zlib.crc32(nombre.encode()) % len(_COLORES)]


@app.template_filter("fecha")
def fecha(momento: datetime | None) -> str:
    if momento is None:
        return "sin clases todavía"
    dias = (datetime.now().date() - momento.date()).days
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "ayer"
    texto = f"{_DIAS[momento.weekday()]} {momento.day} {_MESES[momento.month - 1]}"
    return texto if momento.year == datetime.now().year else f"{texto} {momento.year}"


@app.template_filter("duracion")
def duracion(segundos: float | None) -> str:
    if segundos is None:
        return "sin audio"
    minutos = round(segundos / 60)
    if segundos < 60:
        return f"{round(segundos)} s"
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60} h {minutos % 60:02d} min"


@app.template_filter("hms")
def hms(segundos: float) -> str:
    s = int(segundos)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


@app.template_filter("plural")
def plural(n: int, palabra: str) -> str:
    return f"{n} {palabra}" if n == 1 else f"{n} {palabra}s"


# --- materias ----------------------------------------------------------------

@app.get("/")
def inicio():
    return render_template("materias.html", materias=biblioteca.materias())


@app.post("/materias")
def crear_materia():
    try:
        nombre = biblioteca.crear_materia(request.form.get("nombre", ""))
        flash(f"Materia «{nombre}» creada.", "ok")
    except ErrorBiblioteca as e:
        flash(str(e), "error")
    return redirect(url_for("inicio"))


@app.get("/m/<materia>")
def ver_materia(materia):
    try:
        return render_template(
            "materia.html",
            materia=biblioteca.materia(materia),
            clases=biblioteca.clases(materia),
            estados={c.nombre: cola.estado(materia, c.nombre) for c in biblioteca.clases(materia)},
            siguiente=biblioteca.siguiente_nombre_de_clase(materia),
        )
    except ErrorBiblioteca:
        abort(404)


@app.post("/m/<materia>/renombrar")
def renombrar_materia(materia):
    if cola.ocupada(materia):
        flash(_OCUPADA, "error")
        return redirect(url_for("ver_materia", materia=materia))
    try:
        nuevo = biblioteca.renombrar_materia(materia, request.form.get("nombre", ""))
        _seguir_destino(materia, None, nuevo, None)
        flash("Materia renombrada.", "ok")
        return redirect(url_for("ver_materia", materia=nuevo))
    except ErrorBiblioteca as e:
        flash(str(e), "error")
        return redirect(url_for("ver_materia", materia=materia))


@app.post("/m/<materia>/borrar")
def borrar_materia(materia):
    if cola.ocupada(materia):
        flash(_OCUPADA, "error")
        return redirect(url_for("ver_materia", materia=materia))
    try:
        biblioteca.borrar_materia(materia)
        _seguir_destino(materia, None, None, None)
        flash(f"«{materia}» se movió a la papelera.", "ok")
    except ErrorBiblioteca as e:
        flash(str(e), "error")
    return redirect(url_for("inicio"))


# --- clases ------------------------------------------------------------------

@app.post("/m/<materia>/clases")
def crear_clase(materia):
    try:
        nombre = biblioteca.crear_clase(materia, request.form.get("nombre", ""))
        flash(f"Clase «{nombre}» creada.", "ok")
    except ErrorBiblioteca as e:
        flash(str(e), "error")
    return redirect(url_for("ver_materia", materia=materia))


@app.get("/m/<materia>/c/<clase>")
def ver_clase(materia, clase):
    try:
        c = biblioteca.clase(materia, clase)
    except ErrorBiblioteca:
        abort(404)
    ruta_resumen = c.ruta / RESUMEN
    return render_template(
        "clase.html",
        materia=biblioteca.materia(materia),
        clase=c,
        frases=biblioteca.leer_transcripcion(materia, clase),
        resumen=ruta_resumen.read_text(encoding="utf-8") if ruta_resumen.is_file() else None,
        contexto=biblioteca.leer_contexto(materia, clase),
        estado=cola.estado(materia, clase),
    )


@app.post("/m/<materia>/c/<clase>/renombrar")
def renombrar_clase(materia, clase):
    if cola.ocupada(materia, clase):
        flash(_OCUPADA, "error")
        return redirect(url_for("ver_clase", materia=materia, clase=clase))
    try:
        nuevo = biblioteca.renombrar_clase(materia, clase, request.form.get("nombre", ""))
        _seguir_destino(materia, clase, materia, nuevo)
        flash("Clase renombrada.", "ok")
        return redirect(url_for("ver_clase", materia=materia, clase=nuevo))
    except ErrorBiblioteca as e:
        flash(str(e), "error")
        return redirect(url_for("ver_clase", materia=materia, clase=clase))


@app.post("/m/<materia>/c/<clase>/borrar")
def borrar_clase(materia, clase):
    if cola.ocupada(materia, clase):
        flash(_OCUPADA, "error")
        return redirect(url_for("ver_clase", materia=materia, clase=clase))
    try:
        biblioteca.borrar_clase(materia, clase)
        _seguir_destino(materia, clase, materia, None)
        flash(f"«{clase}» se movió a la papelera.", "ok")
    except ErrorBiblioteca as e:
        flash(str(e), "error")
    return redirect(url_for("ver_materia", materia=materia))


@app.get("/m/<materia>/c/<clase>/audio")
def audio(materia, clase):
    try:
        ruta = biblioteca.ruta_archivo(materia, clase, AUDIO)
    except ErrorBiblioteca:
        abort(404)
    if not ruta.is_file():
        abort(404)
    return send_file(ruta, mimetype="audio/wav", conditional=True)


@app.post("/m/<materia>/c/<clase>/audio")
def subir_audio(materia, clase):
    if cola.ocupada(materia, clase):
        flash(_OCUPADA, "error")
        return redirect(url_for("ver_clase", materia=materia, clase=clase))
    archivo = request.files.get("audio")
    if not archivo or not archivo.filename:
        flash("Elegí un archivo de audio.", "error")
        return redirect(url_for("ver_clase", materia=materia, clase=clase))
    with tempfile.TemporaryDirectory() as tmp:
        origen = Path(tmp) / "subido"
        archivo.save(origen)
        try:
            segundos = biblioteca.guardar_audio(materia, clase, origen)
            flash(f"Audio guardado ({duracion(segundos)}).", "ok")
        except ErrorBiblioteca as e:
            flash(str(e), "error")
    return redirect(url_for("ver_clase", materia=materia, clase=clase))


@app.post("/m/<materia>/c/<clase>/transcribir")
def transcribir(materia, clase):
    try:
        if not biblioteca.clase(materia, clase).tiene_audio:
            flash("Primero subí el audio de la clase.", "error")
        else:
            cola.encolar(materia, clase, TRANSCRIBIR)
    except ErrorBiblioteca as e:
        flash(str(e), "error")
    return redirect(url_for("ver_clase", materia=materia, clase=clase))


@app.post("/m/<materia>/c/<clase>/resumir")
def resumir(materia, clase):
    try:
        if not biblioteca.clase(materia, clase).tiene_transcripcion:
            flash("Primero transcribí la clase.", "error")
        elif falta := configuracion.cargar().falta_para_resumir():
            flash(f"{falta} Completalo en Configuración.", "error")
        else:
            cola.encolar(materia, clase, RESUMIR)
    except ErrorBiblioteca as e:
        flash(str(e), "error")
    return redirect(url_for("ver_clase", materia=materia, clase=clase))


@app.get("/m/<materia>/c/<clase>/estado")
def estado(materia, clase):
    e = cola.estado(materia, clase)
    if e is None:
        return jsonify(fase="listo")
    return jsonify(tarea=e.tarea, fase=e.fase, progreso=round(e.progreso, 3), error=e.error)


@app.post("/m/<materia>/c/<clase>/descartar-error")
def descartar_error(materia, clase):
    cola.descartar_error(materia, clase)
    return redirect(url_for("ver_clase", materia=materia, clase=clase))


@app.post("/m/<materia>/c/<clase>/contexto")
def guardar_contexto(materia, clase):
    try:
        biblioteca.guardar_contexto(materia, clase, request.form.get("contexto", ""))
        flash("Contexto guardado. Se usa la próxima vez que transcribas.", "ok")
    except ErrorBiblioteca as e:
        flash(str(e), "error")
    return redirect(url_for("ver_clase", materia=materia, clase=clase))


# --- destino del grabador ----------------------------------------------------

@app.post("/m/<materia>/destino")
def destino_materia(materia):
    try:
        biblioteca.materia(materia)
    except ErrorBiblioteca:
        abort(404)
    config = configuracion.cargar()
    config.materia_grabador, config.clase_grabador = materia, ""
    configuracion.guardar(config)
    flash(f"Listo: cada grabación nueva va a llegar como una clase nueva en «{materia}».", "ok")
    return redirect(url_for("ver_materia", materia=materia))


@app.post("/m/<materia>/c/<clase>/destino")
def destino_clase(materia, clase):
    try:
        c = biblioteca.clase(materia, clase)
    except ErrorBiblioteca:
        abort(404)
    if c.tiene_audio:
        flash("Esta clase ya tiene audio: la próxima grabación no puede caer acá.", "error")
    else:
        config = configuracion.cargar()
        config.materia_grabador, config.clase_grabador = materia, clase
        configuracion.guardar(config)
        flash(f"Listo: la próxima grabación va a caer en «{materia} / {clase}».", "ok")
    return redirect(url_for("ver_clase", materia=materia, clase=clase))


# --- configuración -----------------------------------------------------------

def _pagina_configuracion(config, pid: str, modelos: list[str] | None = None):
    cuenta = config.cuenta(pid)
    return render_template(
        "configuracion.html",
        config=config,
        proveedores=PROVEEDORES.values(),
        p=PROVEEDORES[pid],
        cuenta=cuenta,
        key_oculta=key_oculta(cuenta.api_key),
        base_url=config.base_url(pid),
        modelos=modelos or [],
        tipos_de_resumen=TIPOS_DE_RESUMEN,
        materias=[m.nombre for m in biblioteca.materias()],
    )


@app.get("/configuracion")
def ver_configuracion():
    config = configuracion.cargar()
    pid = request.args.get("proveedor", config.proveedor)
    return _pagina_configuracion(config, pid if pid in PROVEEDORES else config.proveedor)


@app.post("/configuracion")
def guardar_configuracion():
    config = configuracion.cargar()
    pid = request.form.get("proveedor", "")
    if pid not in PROVEEDORES:
        abort(400)
    cuenta = config.cuenta(pid)
    if nueva_key := request.form.get("api_key", "").strip():
        cuenta.api_key = nueva_key  # vacío = dejar la que estaba
    if request.form.get("borrar_key"):
        cuenta.api_key = ""
    cuenta.modelo = request.form.get("modelo", "").strip()
    if "base_url" in request.form:
        url = request.form["base_url"].strip()
        cuenta.base_url = "" if url == PROVEEDORES[pid].base_url else url
    config.proveedor = pid
    config.resumir_al_transcribir = bool(request.form.get("resumir_al_transcribir"))
    if (tipo := request.form.get("tipo_resumen")) in TIPOS_DE_RESUMEN:
        config.tipo_resumen = tipo
    if (materia := request.form.get("materia_grabador", "").strip()) and materia != config.materia_grabador:
        try:
            config.materia_grabador, config.clase_grabador = validar_nombre(materia), ""
        except ErrorBiblioteca as e:
            flash(f"Materia del grabador: {e}", "error")
    configuracion.guardar(config)

    if request.form.get("accion") == "probar":
        try:
            modelos = resumidores.listar_modelos(config, pid)
            flash(f"Conexión correcta con {PROVEEDORES[pid].nombre}: {len(modelos)} modelos disponibles. Elegí uno y guardá.", "ok")
            return _pagina_configuracion(config, pid, modelos)
        except resumidores.ErrorResumen as e:
            flash(str(e), "error")
    else:
        flash("Configuración guardada.", "ok")
    return redirect(url_for("ver_configuracion", proveedor=pid))


@app.errorhandler(413)
def demasiado_grande(_):
    flash("El archivo es demasiado grande (máximo 2 GB).", "error")
    return redirect(request.referrer or url_for("inicio"))


@app.errorhandler(404)
def no_encontrado(_):
    return render_template("no_encontrado.html"), 404


if __name__ == "__main__":
    print(f"Carpetas en: {biblioteca.raiz}")
    host = os.environ.get("HOST", "0.0.0.0")
    debug = bool(os.environ.get("DEBUG")) and host == "127.0.0.1"
    app.run(host=host, port=5000, debug=debug, threaded=True)
