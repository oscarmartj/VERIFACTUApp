"""Configuración de la aplicación.

Todo lo que distingue pruebas de producción está aquí y es explícito. Los valores
sensibles (clave maestra, permisos de remisión real) se leen del entorno del servidor,
nunca del navegador.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ESQUEMAS_DIR = BASE_DIR / "verifactu" / "esquemas"

APP_NOMBRE = "FacturaPDF VF"
APP_VERSION = "0.1.0"

# --- Entornos -----------------------------------------------------------------
# preparacion : borradores, extracción y vista previa. Sin efectos fiscales.
# simulador   : simulación local del servicio AEAT (pruebas técnicas, sin red).
# pruebas     : entorno externo de pruebas (preproducción) de la AEAT.
# produccion  : facturación real.
ENTORNOS_EMISION = ("simulador", "pruebas", "produccion")

# Direcciones tomadas del WSDL oficial SistemaFacturacion.wsdl (ver esquemas/PROCEDENCIA.md)
ENDPOINTS_SOAP = {
    "pruebas": {
        "certificado": "https://prewww1.aeat.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP",
        "sello": "https://prewww10.aeat.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP",
    },
    "produccion": {
        "certificado": "https://www1.agenciatributaria.gob.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP",
        "sello": "https://www10.agenciatributaria.gob.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP",
    },
}

# Servicio de cotejo del QR (documento AEAT "Características del QR y especificaciones del
# servicio de cotejo"). El simulador local usa la URL de pruebas: nunca una página propia.
URL_QR = {
    "simulador": "https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR",
    "pruebas": "https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR",
    "produccion": "https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR",
}

ZONA_HORARIA = "Europe/Madrid"


def _bool(nombre: str, defecto: bool = False) -> bool:
    v = os.environ.get(nombre)
    if v is None:
        return defecto
    return v.strip().lower() in ("1", "true", "si", "sí", "yes")


@dataclass
class Ajustes:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("VERIFACTU_DATA_DIR", BASE_DIR.parent / "data")))
    clave_maestra_fichero: Path | None = None
    # Interruptores de servidor (no configurables desde la interfaz):
    permitir_pruebas_aeat: bool = field(default_factory=lambda: _bool("VERIFACTU_PERMITIR_PRUEBAS_AEAT", False))
    permitir_produccion: bool = field(default_factory=lambda: _bool("VERIFACTU_PERMITIR_PRODUCCION", False))
    # Límites de procesamiento de PDF
    max_mb_pdf: int = field(default_factory=lambda: int(os.environ.get("VERIFACTU_MAX_MB_PDF", "15")))
    max_paginas_pdf: int = field(default_factory=lambda: int(os.environ.get("VERIFACTU_MAX_PAGINAS", "30")))
    max_paginas_ocr: int = field(default_factory=lambda: int(os.environ.get("VERIFACTU_MAX_PAGINAS_OCR", "10")))
    timeout_ocr_s: int = 120
    # Remisión
    max_registros_envio: int = 1000
    espera_inicial_s: int = 60
    timeout_http_s: int = 60
    sesion_horas: int = 12
    cookie_segura: bool = field(default_factory=lambda: _bool("VERIFACTU_COOKIE_SEGURA", False))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "verifactu.sqlite3"

    @property
    def almacen_dir(self) -> Path:
        return self.data_dir / "almacen"

    @property
    def copias_dir(self) -> Path:
        return self.data_dir / "copias"

    def ruta_clave_maestra(self) -> Path:
        if self.clave_maestra_fichero:
            return self.clave_maestra_fichero
        env = os.environ.get("VERIFACTU_CLAVE_MAESTRA_FICHERO")
        if env:
            return Path(env)
        # Por defecto fuera del directorio de datos, para que una copia de la base de datos
        # no contenga a la vez los certificados cifrados y la clave que los descifra.
        return self.data_dir.parent / "secretos" / "clave_maestra.bin"


_ajustes: Ajustes | None = None


def ajustes() -> Ajustes:
    global _ajustes
    if _ajustes is None:
        _ajustes = Ajustes()
    return _ajustes


def configurar(nuevos: Ajustes) -> None:
    global _ajustes
    _ajustes = nuevos


def clave_maestra() -> bytes:
    """Clave AES-256 para cifrar certificados en reposo. Se crea con permisos 0600."""
    ruta = ajustes().ruta_clave_maestra()
    if not ruta.exists():
        ruta.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(ruta.parent, 0o700)
        fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    data = ruta.read_bytes()
    if len(data) != 32:
        raise RuntimeError("La clave maestra no tiene 32 bytes")
    return data
