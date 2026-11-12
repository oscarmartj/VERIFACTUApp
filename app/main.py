"""Punto de entrada: uvicorn app.main:app"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import glob
import shutil
import tempfile

from . import copias, db, diario, errores, multiusuario
from .api import router
from .api_multiusuario import router as router_multiusuario
from .api_certificados import router as router_certificados
from .config import BASE_DIR, ajustes
from .remision import Trabajador

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; object-src 'none'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def _purgar_claves_temporales() -> None:
    """Restos de claves de certificados descifradas si el proceso murió durante un envío (solo en el modo sin memfd)."""
    for d in glob.glob(f"{tempfile.gettempdir()}/vf-cert-*") + glob.glob("/dev/shm/vf-cert-*"):
        shutil.rmtree(d, ignore_errors=True)


@asynccontextmanager
async def ciclo_vida(app: FastAPI):
    copias.proteger_ficheros()   # base de datos, copias y copias apartadas: solo para el usuario del servicio
    copias.comprobar_arranque()
    _purgar_claves_temporales()
    con = db.conectar()
    db.migrar(con)
    copias.reconciliar_diario(con)
    if diario.LINEAS_INVALIDAS:
        db.actividad(con, "diario.lineas_invalidas", detalle={"lineas": diario.LINEAS_INVALIDAS[:50]})
    multiusuario.avisar_nifs_no_ascii(con)   # NIF guardados con caracteres no ASCII (aceptados por versiones anteriores)
    con.close()
    app.state.trabajador = None
    if os.environ.get("VERIFACTU_TRABAJADOR", "1") == "1":
        t = Trabajador(intervalo=float(os.environ.get("VERIFACTU_INTERVALO", "3")))
        t.iniciar()
        app.state.trabajador = t
    yield
    if app.state.trabajador:
        app.state.trabajador.parar()
    errores.volcar(todos=True)   # errores internos contados, también los del minuto en curso


class LimiteCuerpo:
    """Middleware ASGI: rechaza (413) cuerpos de petición mayores que el límite antes de que se lean o se guarden en
    disco, tanto por Content-Length como contando los bytes recibidos en envíos fragmentados."""

    def __init__(self, app, limite_general: int, limite_subidas: int):
        self.app, self.general, self.subidas = app, limite_general, limite_subidas

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        ruta = scope.get("path", "")
        limite = self.subidas if (ruta.endswith("/documentos") or ruta.endswith("/certificados")) else self.general
        cabeceras = dict(scope.get("headers") or [])
        try:
            declarado = int(cabeceras.get(b"content-length", b"0") or 0)
        except ValueError:
            declarado = 0
        if declarado > limite:
            return await self._rechazar(send)
        recibido = 0

        async def receive_limitado():
            nonlocal recibido
            msg = await receive()
            if msg["type"] == "http.request":
                recibido += len(msg.get("body", b""))
                if recibido > limite:
                    raise _CuerpoExcesivo()
            return msg
        try:
            return await self.app(scope, receive_limitado, send)
        except _CuerpoExcesivo:
            return await self._rechazar(send)

    @staticmethod
    async def _rechazar(send):
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail": "La peticion supera el tamano maximo admitido"}'})


class _CuerpoExcesivo(Exception):
    pass


def crear_app() -> FastAPI:
    app = FastAPI(title="FacturaPDF VF", lifespan=ciclo_vida, docs_url=None, redoc_url=None, openapi_url=None)
    mb = ajustes().max_mb_pdf
    app.add_middleware(LimiteCuerpo, limite_general=1024 * 1024, limite_subidas=20 * mb * 1024 * 1024 + 1024 * 1024)

    @app.middleware("http")
    async def cabeceras(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(UnicodeEncodeError)
    async def error_texto(request: Request, exc: UnicodeEncodeError):
        # texto recibido que no puede guardarse (p. ej. un sustituto UTF-16 suelto): la transacción ya se ha deshecho
        return JSONResponse({"detail": "El texto enviado contiene caracteres no válidos."}, status_code=422)

    @app.exception_handler(OverflowError)
    async def error_numero(request: Request, exc: OverflowError):
        # número recibido (p. ej. un identificador en la ruta) fuera del rango de enteros de SQLite: la transacción ya se
        # ha deshecho
        return JSONResponse({"detail": "Número fuera del rango admitido."}, status_code=422)

    @app.exception_handler(Exception)
    async def error_generico(request: Request, exc: Exception):
        # Una fila por ruta, tipo y minuto (con contador), también en la actividad de la empresa si quien pide es miembro;
        # el mensaje solo remite a una actividad que esa persona puede ver (app/errores.py)
        return JSONResponse({"detail": errores.anotar(request, exc)}, status_code=500)

    app.include_router(router)
    app.include_router(router_multiusuario)
    app.include_router(router_certificados)
    estaticos = BASE_DIR / "static"
    app.mount("/static", StaticFiles(directory=estaticos), name="static")

    @app.get("/")
    def indice():
        return FileResponse(estaticos / "index.html", headers={"Cache-Control": "no-store"})
    return app


app = crear_app()
