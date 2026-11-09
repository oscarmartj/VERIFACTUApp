"""Autenticación, sesiones, CSRF, cifrado en reposo y autorización por empresa."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import db
from .config import ajustes, clave_maestra

ROLES_ORDEN = {"lector": 0, "emisor": 1, "admin": 2}


# --- Contraseñas (scrypt, biblioteca estándar) --------------------------------
def hash_clave(clave: str) -> str:
    if len(clave) < 10:
        raise ValueError("La contraseña debe tener al menos 10 caracteres")
    sal = os.urandom(16)
    h = hashlib.scrypt(clave.encode(), salt=sal, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(sal).decode() + "$" + base64.b64encode(h).decode()


def verificar_clave(clave: str, almacenado: str) -> bool:
    try:
        _, sal_b64, h_b64 = almacenado.split("$")
        sal, h = base64.b64decode(sal_b64), base64.b64decode(h_b64)
    except ValueError:
        return False
    calc = hashlib.scrypt(clave.encode(), salt=sal, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(calc, h)


# --- Sesiones -----------------------------------------------------------------
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def crear_sesion(con, usuario_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    ahora = db.ahora()
    con.execute(
        "INSERT INTO sesiones (token_hash, usuario_id, csrf, creada_en, expira_en) VALUES (?,?,?,?,?)",
        (_hash_token(token), usuario_id, csrf, ahora.isoformat(),
         (ahora + timedelta(hours=ajustes().sesion_horas)).isoformat()),
    )
    return token, csrf


def sesion_por_token(con, token: str | None) -> dict | None:
    if not token:
        return None
    s = db.fila(con, """SELECT s.*, u.email, u.nombre, u.activo FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id
                        WHERE s.token_hash = ?""", (_hash_token(token),))
    if not s or not s["activo"]:
        return None
    if s["expira_en"] < db.ahora().isoformat():
        con.execute("DELETE FROM sesiones WHERE id = ?", (s["id"],))
        return None
    return s


def cerrar_sesion(con, token: str) -> None:
    con.execute("DELETE FROM sesiones WHERE token_hash = ?", (_hash_token(token),))


def login(con, email: str, clave: str) -> dict | None:
    u = db.fila(con, "SELECT * FROM usuarios WHERE email = ?", (email.strip(),))
    if not u or not u["activo"]:
        hash_clave_ficticio = "scrypt$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        verificar_clave(clave, hash_clave_ficticio)  # tiempo constante aproximado
        return None
    if u["bloqueado_hasta"] and u["bloqueado_hasta"] > db.ahora().isoformat():
        verificar_clave(clave, u["hash_clave"])  # mismo coste que los demás casos: no revela si la cuenta existe
        return None
    if not verificar_clave(clave, u["hash_clave"]):
        # Incremento atómico: varias peticiones simultáneas no pueden contarse como un solo fallo
        bloqueo = (db.ahora() + timedelta(minutes=15)).isoformat()
        con.execute("""UPDATE usuarios SET
                         bloqueado_hasta = CASE WHEN intentos_fallidos + 1 >= 5 THEN ? ELSE bloqueado_hasta END,
                         intentos_fallidos = CASE WHEN intentos_fallidos + 1 >= 5 THEN 0 ELSE intentos_fallidos + 1 END
                       WHERE id=?""", (bloqueo, u["id"]))
        return None
    # Una petición correcta concurrente con un bloqueo recién impuesto no debe colarse
    u2 = db.fila(con, "SELECT bloqueado_hasta FROM usuarios WHERE id=?", (u["id"],))
    if u2["bloqueado_hasta"] and u2["bloqueado_hasta"] > db.ahora().isoformat():
        return None
    con.execute("UPDATE usuarios SET intentos_fallidos=0, bloqueado_hasta=NULL WHERE id=?", (u["id"],))
    return u


def crear_usuario(con, email: str, nombre: str, clave: str) -> int:
    cur = con.execute("INSERT INTO usuarios (email, nombre, hash_clave, creado_en) VALUES (?,?,?,?)",
                      (email.strip(), nombre.strip(), hash_clave(clave), db.ahora_iso()))
    return cur.lastrowid


# --- Autorización por empresa ---------------------------------------------------
class AccesoDenegado(Exception):
    pass


def rol_en_empresa(con, usuario_id: int, empresa_id: int) -> str | None:
    m = db.fila(con, "SELECT rol FROM miembros WHERE usuario_id=? AND empresa_id=?", (usuario_id, empresa_id))
    return m["rol"] if m else None


def exigir_rol(con, usuario_id: int, empresa_id: int, minimo: str) -> str:
    rol = rol_en_empresa(con, usuario_id, empresa_id)
    if rol is None or ROLES_ORDEN[rol] < ROLES_ORDEN[minimo]:
        raise AccesoDenegado("No tiene acceso a este recurso")
    return rol


# --- Cifrado en reposo (AES-256-GCM) ------------------------------------------
def cifrar(datos: bytes, contexto: bytes) -> bytes:
    nonce = os.urandom(12)
    return b"v1" + nonce + AESGCM(clave_maestra()).encrypt(nonce, datos, contexto)


def descifrar(blob: bytes, contexto: bytes) -> bytes:
    if blob[:2] != b"v1":
        raise ValueError("Formato de cifrado desconocido")
    return AESGCM(clave_maestra()).decrypt(blob[2:14], blob[14:], contexto)
