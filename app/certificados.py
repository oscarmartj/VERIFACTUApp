"""Certificados de autenticación (PKCS#12) y comprobación de la legitimación para remitir.

En VERI*FACTU el certificado se usa para la autenticación TLS ante el servicio de la AEAT; los
registros no se firman electrónicamente (la firma XAdES es propia de los sistemas no verificables).
El archivo y su contraseña se guardan cifrados (AES-256-GCM) y solo se descifran en memoria o en un
directorio temporal 0600 durante el envío. Nunca se devuelven a la interfaz.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from . import db
from . import nif as nifmod
from .seguridad import cifrar, descifrar

OID_ORG_ID = x509.ObjectIdentifier("2.5.4.97")


class CertificadoNoValido(Exception):
    pass


def _attr(nombre: x509.Name, oid) -> str | None:
    v = nombre.get_attributes_for_oid(oid)
    return v[0].value if v else None


def _nif_de(valor: str | None) -> str | None:
    if not valor:
        return None
    v = valor.upper()
    for pref in ("IDCES-", "VATES-", "IDCES", "VATES", "ES"):
        if v.startswith(pref):
            v = v[len(pref):]
            break
    v = nifmod.normalizar(v.strip("- "))
    return v if nifmod.es_valido(v) else None


def analizar(p12: bytes, clave: str) -> dict:
    try:
        key, cert, extra = pkcs12.load_key_and_certificates(p12, clave.encode() if clave else None)
    except Exception as e:
        raise CertificadoNoValido("No se puede abrir el certificado (archivo o contraseña incorrectos)") from e
    if cert is None or key is None:
        raise CertificadoNoValido("El archivo no contiene certificado y clave privada")
    suj = cert.subject
    serial_persona = _attr(suj, NameOID.SERIAL_NUMBER)
    org_id = _attr(suj, OID_ORG_ID)
    titular = _nif_de(serial_persona)
    representado = _nif_de(org_id)
    if representado and titular:
        tipo = "representante_pj"
    elif representado:
        tipo = "persona_juridica"
    elif titular:
        tipo = "persona_fisica"
    else:
        tipo = "desconocido"
    return {
        "sujeto": suj.rfc4514_string(),
        "emisor": cert.issuer.rfc4514_string(),
        "numero_serie": format(cert.serial_number, "X"),
        "valido_desde": cert.not_valid_before_utc.isoformat(),
        "valido_hasta": cert.not_valid_after_utc.isoformat(),
        "titular_nif": titular,
        "representado_nif": representado,
        "tipo": tipo,
        "huella_cert": cert.fingerprint(hashes.SHA256()).hex(),
        "nombre": _attr(suj, NameOID.COMMON_NAME),
    }


def evaluar_legitimacion(info: dict, empresa: dict, legitimaciones: list[dict]) -> dict:
    nif_emp = nifmod.normalizar(empresa["nif"])
    ahora = datetime.now(timezone.utc).isoformat()
    vigente = info["valido_desde"] <= ahora <= info["valido_hasta"]
    res = {"vigente": vigente, "ok": False, "metodo": None, "detalle": ""}
    if not vigente:
        res["detalle"] = "El certificado no está vigente."
        return res
    if info["tipo"] in ("persona_juridica", "representante_pj") and info["representado_nif"] == nif_emp:
        res.update(ok=True, metodo="certificado_empresa" if info["tipo"] == "persona_juridica" else "representante_pj",
                   detalle="El certificado identifica a la empresa como titular o representada.")
        return res
    if info["tipo"] == "persona_fisica" and info["titular_nif"] == nif_emp:
        res.update(ok=True, metodo="titular", detalle="El titular del certificado es el obligado tributario.")
        return res
    decl = [l for l in legitimaciones if l["metodo"] in ("apoderamiento_aeat", "colaborador_social") and l.get("evidencia_documento_id")]
    if decl:
        res.update(ok=True, metodo=decl[-1]["metodo"],
                   detalle="Legitimación declarada con evidencia (apoderamiento/colaboración social). La AEAT la "
                           "comprobará en cada envío (error 4112 si el titular no está autorizado).")
        return res
    res["detalle"] = ("El certificado no corresponde a la empresa ni a un representante suyo. Para usarlo debe existir "
                      "un apoderamiento en la AEAT o un acuerdo de colaboración social; aporte la evidencia.")
    return res


def guardar(con, *, empresa: dict, entorno: str, p12: bytes, clave: str, usuario_id: int, alias: str | None = None) -> int:
    info = analizar(p12, clave)
    legs = db.filas(con, "SELECT * FROM legitimaciones WHERE empresa_id=?", (empresa["id"],))
    leg = evaluar_legitimacion(info, empresa, legs)
    contexto = f"cert:{empresa['id']}:{entorno}".encode()
    blob = cifrar(json.dumps({"p12": base64.b64encode(p12).decode(), "clave": clave}).encode(), contexto)
    con.execute("UPDATE certificados SET activo=0 WHERE empresa_id=? AND entorno=?", (empresa["id"], entorno))
    cur = con.execute("""INSERT INTO certificados (empresa_id, entorno, alias, sujeto, emisor, numero_serie, valido_desde,
        valido_hasta, titular_nif, representado_nif, tipo, huella_cert, blob_cifrado, legitimacion, activo, subido_por, subido_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                      (empresa["id"], entorno, alias, info["sujeto"], info["emisor"], info["numero_serie"],
                       info["valido_desde"], info["valido_hasta"], info["titular_nif"], info["representado_nif"],
                       info["tipo"], info["huella_cert"], blob, json.dumps(leg, ensure_ascii=False), usuario_id, db.ahora_iso()))
    return cur.lastrowid


def activo(con, empresa_id: int, entorno: str) -> dict | None:
    c = db.fila(con, "SELECT * FROM certificados WHERE empresa_id=? AND entorno=? AND activo=1", (empresa_id, entorno))
    if c:
        leg = json.loads(c["legitimacion"] or "{}")
        leg["vigente"] = vigente_ahora(c)  # la vigencia se comprueba en cada uso, no solo al cargarlo
        if not leg["vigente"]:
            leg["ok"] = False
            leg["detalle"] = "El certificado no está vigente (caducado o aún no válido)."
        c["legitimacion_dict"] = leg
    return c


def vigente_ahora(cert_row: dict) -> bool:
    ahora = datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(cert_row["valido_desde"]) <= ahora <= datetime.fromisoformat(cert_row["valido_hasta"])
    except (KeyError, TypeError, ValueError):
        return False


@contextlib.contextmanager
def ficheros_pem(cert_row: dict):
    """Entrega rutas a certificado y clave en PEM para el cliente TLS.

    En Linux se usan ficheros anónimos en memoria (memfd), accesibles como /proc/self/fd/N: la clave privada no
    llega a escribirse en disco. En otros sistemas se usa un directorio temporal 0600 (preferentemente en /dev/shm)
    que se sobrescribe y borra al terminar; los restos se purgan al arrancar."""
    contexto = f"cert:{cert_row['empresa_id']}:{cert_row['entorno']}".encode()
    d = json.loads(descifrar(cert_row["blob_cifrado"], contexto))
    key, cert, extra = pkcs12.load_key_and_certificates(base64.b64decode(d["p12"]), d["clave"].encode() if d["clave"] else None)
    pem_cert = cert.public_bytes(serialization.Encoding.PEM) + b"".join(c.public_bytes(serialization.Encoding.PEM) for c in (extra or []))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    if hasattr(os, "memfd_create") and Path("/proc/self/fd").exists():
        fds = []
        try:
            for datos in (pem_cert, pem_key):
                fd = os.memfd_create("vf-cert", 0)
                os.write(fd, datos)
                fds.append(fd)
            yield f"/proc/self/fd/{fds[0]}", f"/proc/self/fd/{fds[1]}"
        finally:
            for fd in fds:
                os.close(fd)
        return
    base = "/dev/shm" if Path("/dev/shm").is_dir() else None
    td = tempfile.mkdtemp(prefix="vf-cert-", dir=base)
    os.chmod(td, 0o700)
    try:
        cp, kp = Path(td) / "c.pem", Path(td) / "k.pem"
        for ruta_, datos in ((cp, pem_cert), (kp, pem_key)):
            fd = os.open(ruta_, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(datos)
        yield str(cp), str(kp)
    finally:
        for p in Path(td).glob("*"):
            p.write_bytes(b"\0" * p.stat().st_size)
            p.unlink()
        os.rmdir(td)


def huella_archivo(p12: bytes) -> str:
    return hashlib.sha256(p12).hexdigest()
