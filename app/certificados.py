"""Certificados de autenticación (PKCS#12), «Mis certificados» y legitimación para remitir.

En VERI*FACTU el certificado se usa para la autenticación TLS ante el servicio de la AEAT; los
registros no se firman electrónicamente (la firma XAdES es propia de los sistemas no verificables).
El archivo y su contraseña se guardan cifrados (AES-256-GCM) y solo se descifran en memoria o en un
directorio temporal 0600 durante el envío. Nunca se devuelven a la interfaz.

Origen del certificado que usa una empresa en un entorno (uno solo en uso en cada momento):
- «de la empresa»: cargado por un administrador para esa empresa y entorno (tabla `certificados`);
- «de Mis certificados»: cargado UNA vez por un usuario (`certificados_usuario`, propietario = ese usuario) y asignado
  por él a empresas que administra (`certificado_asignaciones`). Solo el propietario lo asigna, retira o elimina; un
  administrador de la empresa puede sustituirlo por otro, pero nunca usarlo en otra empresa ni descargarlo. Si el
  propietario deja de ser administrador de la empresa (o se desactiva su usuario), la asignación deja de poder usarse:
  se comprueba en cada uso, no solo al asignar.

Titularidad: el certificado es «propio» si identifica a la empresa (sello o persona jurídica con su NIF, o representante
de esa entidad) o si su titular persona física es el obligado. Si no, es de un tercero (p. ej. una gestoría) y solo se
usa si la empresa ha declarado una legitimación (apoderamiento en la AEAT o colaboración social) para ESE titular, con
evidencia; entonces cada envío lleva Cabecera/Representante con su NIF y nombre. La AEAT comprueba el apoderamiento al
remitir (error 4112 si el titular no está autorizado).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from . import db, diario
from . import nif as nifmod
from .seguridad import cifrar, descifrar

OID_ORG_ID = x509.ObjectIdentifier("2.5.4.97")
ENTORNOS_CERT = ("pruebas", "produccion")                       # certificados cargados por empresa
ENTORNOS_ASIGNACION = ("simulador", "pruebas", "produccion")    # en el simulador solo sirve para probar la representación
METODOS_TERCERO = ("apoderamiento_aeat", "colaborador_social")
TEXTO_METODO = {"apoderamiento_aeat": "apoderamiento en la AEAT", "colaborador_social": "colaboración social"}
# Filas anotadas en el diario (sin archivo ni contraseña). De `certificados` (carga por empresa) solo se reproduce el
# estado «en uso»: la fila necesita el archivo cifrado, que el diario nunca guarda.
TABLAS_DIARIO = ("certificados_usuario", "certificado_asignaciones", "legitimaciones", "certificados")
AYUDA_4112 = ("El titular del certificado no consta como obligado, apoderado ni colaborador social: revise en Configuración "
              "→ Certificados qué certificado se usa y la legitimación declarada para su titular, y compruebe el "
              "apoderamiento o la colaboración social en la AEAT antes de reintentar.")
AYUDA_4123 = ("La AEAT no encuentra en su censo el NIF del representante (el titular del certificado de un tercero que se usa "
              "para remitir): compruebe en Configuración → Certificados qué certificado se usa y el NIF de la legitimación "
              "declarada. Si el NIF es correcto, su titular debe resolver su situación en el censo de la AEAT antes de "
              "reintentar.")
AYUDA_4124 = ("La AEAT no reconoce el nombre del representante para ese NIF. Los envíos llevan el nombre que figura en el "
              "certificado (la razón social en los sellos y certificados de representante de persona jurídica; apellidos y "
              "nombre en los de persona física): compruebe en Configuración → Certificados el certificado en uso y su titular. "
              "Si el censo tiene otro nombre (p. ej., tras un cambio de denominación), use un certificado con el nombre "
              "actual o actualice el censo antes de reintentar.")
AYUDAS_AEAT = {"4112": AYUDA_4112, "4123": AYUDA_4123, "4124": AYUDA_4124}


def con_ayuda(texto: str) -> str:
    """Añade al error de la AEAT la explicación de los rechazos de la cabecera relacionados con el certificado y el
    representante (4112, 4123, 4124)."""
    m = re.search(r"\b(4112|4123|4124)\b", texto or "")
    return f"{texto} — {AYUDAS_AEAT[m.group(1)]}" if m else texto


class CertificadoNoValido(Exception):
    pass


class CertificadoDuplicado(CertificadoNoValido):
    pass


class CertificadoNoUtilizable(Exception):
    pass


def _attr(nombre: x509.Name, oid) -> str | None:
    v = nombre.get_attributes_for_oid(oid)
    return v[0].value if v else None


def _nif_de(valor: str | None) -> str | None:
    if not valor or not valor.isascii():   # un NIF solo tiene caracteres ASCII (nif.normalizar)
        return None
    v = valor.upper()
    for pref in ("IDCES-", "VATES-", "IDCES", "VATES", "ES"):
        if v.startswith(pref):
            v = v[len(pref):]
            break
    v = nifmod.normalizar(v.strip("- "))
    return v if nifmod.es_valido(v) else None


_NO_XML = re.compile("[\x00-\x1f\x7f\ud800-\udfff￾￿]")


def limpio(texto: str | None, maximo: int = 120) -> str | None:
    """Texto de una línea apto para el XML (sin caracteres de control) y con la longitud del esquema."""
    t = re.sub(r"\s+", " ", _NO_XML.sub(" ", texto or "")).strip()
    return t[:maximo].strip() or None


def _nombre_cn(cn: str | None, nif: str | None) -> str | None:
    """Nombre a partir del CN («APELLIDOS NOMBRE - 12345678Z», «12345678Z NOMBRE (R: B…)»)."""
    t = re.sub(r"\(R:[^)]*\)", " ", cn or "")
    if nif:
        t = t.replace(nif, " ")
    return limpio(re.sub(r"^[\s\-–]+|[\s\-–]+$", "", re.sub(r"\s+-\s+", " ", t)))


def _nombre_titular(suj: x509.Name, tipo: str, titular_nif: str | None) -> str | None:
    """Nombre de quien presenta con el certificado: la entidad (O) en los de persona jurídica o representante de persona
    jurídica; la persona (nombre y apellidos o CN) en los de persona física."""
    if tipo in ("persona_juridica", "representante_pj"):
        o = limpio(_attr(suj, NameOID.ORGANIZATION_NAME))
        if o:
            return o
    gn, sn = _attr(suj, NameOID.GIVEN_NAME), _attr(suj, NameOID.SURNAME)
    if tipo == "persona_fisica" and gn and sn:
        return limpio(f"{gn} {sn}")
    return _nombre_cn(_attr(suj, NameOID.COMMON_NAME), titular_nif)


def _nombre_de_sujeto(sujeto: str | None, tipo: str | None, titular_nif: str | None) -> str | None:
    """Para filas antiguas sin titular_nombre: lo deduce del sujeto guardado (RFC 4514)."""
    try:
        return _nombre_titular(x509.Name.from_rfc4514_string(sujeto or ""), tipo or "", titular_nif)
    except Exception:  # noqa: BLE001 — sujeto no interpretable: sin nombre
        return None


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
        "titular_nombre": _nombre_titular(suj, tipo, titular),
    }


def vigente_ahora(cert_row: dict) -> bool:
    ahora = datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(cert_row["valido_desde"]) <= ahora <= datetime.fromisoformat(cert_row["valido_hasta"])
    except (KeyError, TypeError, ValueError):
        return False


def nombre_certificado(c: dict) -> str | None:
    """Nombre del titular tal como lo identifica el certificado, en el orden del censo: la entidad (O) en los sellos y
    certificados de representante de persona jurídica; «APELLIDOS NOMBRE» (SN y GN, o el CN) en los de persona física."""
    try:
        suj = x509.Name.from_rfc4514_string(c.get("sujeto") or "")
    except Exception:  # noqa: BLE001 — sujeto no interpretable: el nombre guardado
        suj = None
    if suj is not None:
        if c.get("tipo") in ("persona_juridica", "representante_pj"):
            o = limpio(_attr(suj, NameOID.ORGANIZATION_NAME))
            if o:
                return o
        elif c.get("tipo") == "persona_fisica":
            gn, sn = _attr(suj, NameOID.GIVEN_NAME), _attr(suj, NameOID.SURNAME)
            n = limpio(f"{sn} {gn}") if gn and sn else _nombre_cn(_attr(suj, NameOID.COMMON_NAME), c.get("titular_nif"))
            if n:
                return n
    return limpio(c.get("titular_nombre"))


def _palabras(nombre: str | None) -> list[str]:
    """Palabras de un nombre sin tildes, puntuación ni orden («Gestoría, S.L.» = «GESTORIA SL»)."""
    t = "".join(ch for ch in unicodedata.normalize("NFKD", (nombre or "").upper()) if not unicodedata.combining(ch))
    return sorted(re.sub(r"[^0-9A-Z]+", " ", t.replace(".", "")).split())


def mismo_nombre(a: str | None, b: str | None) -> bool:
    return _palabras(a) == _palabras(b)


def titular_presentador(c: dict) -> tuple[str | None, str | None]:
    """NIF y nombre de quien presenta con el certificado ante la AEAT: la entidad en los certificados de persona jurídica
    (sello) y de representante de persona jurídica; la persona en los de persona física."""
    nif = c.get("representado_nif") if c.get("tipo") in ("persona_juridica", "representante_pj") else c.get("titular_nif")
    nombre = c.get("titular_nombre") or _nombre_de_sujeto(c.get("sujeto"), c.get("tipo"), c.get("titular_nif"))
    return nif, nombre


# --- Titularidad y legitimación -----------------------------------------------------------------------------------
def evaluar_legitimacion(info: dict, empresa: dict, legitimaciones: list[dict]) -> dict:
    """Evalúa si el certificado puede usarse para la empresa: «propio» o de un tercero con legitimación declarada (con
    evidencia y sin revocar) para ese mismo titular. Devuelve también el representante de la cabecera, si lo hay."""
    nif_emp = nifmod.normalizar(empresa["nif"])
    vigente = vigente_ahora(info)
    nif_t, nombre_t = titular_presentador(info)
    res = {"vigente": vigente, "ok": False, "metodo": None, "detalle": "", "propio": False,
           "titular": {"nif": nif_t, "nombre": nombre_t}, "requiere_legitimacion": False, "legitimacion_ok": False,
           "legitimacion_id": None, "representante": None, "aviso_nombre": None}
    if info.get("tipo") in ("persona_juridica", "representante_pj") and info.get("representado_nif") == nif_emp:
        res.update(propio=True, legitimacion_ok=True,
                   metodo="certificado_empresa" if info["tipo"] == "persona_juridica" else "representante_pj",
                   detalle="El certificado identifica a la empresa como titular o representada.")
    elif info.get("tipo") == "persona_fisica" and info.get("titular_nif") == nif_emp:
        res.update(propio=True, legitimacion_ok=True, metodo="titular",
                   detalle="El titular del certificado es el obligado tributario.")
    else:
        res["requiere_legitimacion"] = True
        del_titular = [l for l in legitimaciones if l.get("metodo") in METODOS_TERCERO and not l.get("revocada_en")
                       and nif_t and nifmod.normalizar(l.get("titular_nif")) == nif_t]
        # La evidencia debe existir y ser un documento de la propia empresa (`evidencia_valida` lo calcula
        # _legitimaciones con la tabla de documentos; un identificador suelto no basta)
        legs = [l for l in del_titular if l.get("evidencia_documento_id") and l.get("evidencia_valida", True)]
        if not nif_t:
            res["detalle"] = ("No se identifica el NIF de quien presenta con este certificado: no puede usarse en nombre de "
                              "la empresa.")
        elif not legs and del_titular:
            res["detalle"] = (f"La legitimación declarada para {nombre_t or 'el titular'} (NIF {nif_t}) no tiene su documento "
                              "de evidencia (p. ej., se perdió al restaurar una copia de seguridad): no cuenta. Vuelva a "
                              "declararla en Configuración → Certificados con el documento que la acredite.")
        elif legs:
            l = legs[-1]
            # El nombre del Representante es el del certificado (la AEAT lo contrasta con su censo: error 4124); el
            # declarado en la legitimación solo se usa si el certificado no trae ninguno, y si difiere se avisa
            declarado, del_cert = limpio(l.get("titular_nombre")), nombre_certificado(info)
            nombre = del_cert or declarado or nif_t
            res.update(legitimacion_ok=True, metodo=l["metodo"], legitimacion_id=l.get("id"),
                       representante={"nif": nif_t, "nombre": nombre})
            res["detalle"] = (f"Certificado de un tercero ({nombre} · NIF {nif_t}) con {TEXTO_METODO[l['metodo']]} "
                              "declarado con evidencia: cada envío llevará Cabecera/Representante con su NIF y nombre. La "
                              "AEAT lo comprobará en cada envío (error 4112 si el titular no está autorizado).")
            if del_cert and declarado and not mismo_nombre(del_cert, declarado):
                res["aviso_nombre"] = (f"El nombre declarado en la legitimación («{declarado}») no coincide con el del "
                                       f"certificado («{del_cert}»): los envíos llevan el del certificado. Si la AEAT "
                                       "responde 4124, compruebe con qué nombre figura el titular en su censo.")
                res["detalle"] += " " + res["aviso_nombre"]
        else:
            res["detalle"] = (f"El certificado es de un tercero ({nombre_t or 'titular sin nombre'} · NIF {nif_t}), no de la "
                              "empresa ni de su representante legal. Para usarlo declare en Configuración → Certificados el "
                              "apoderamiento en la AEAT o la colaboración social de ese titular, con el documento que lo "
                              "acredite.")
    if not vigente:
        res["detalle"] = "El certificado no está vigente (caducado o aún no válido). " + res["detalle"]
    res["ok"] = vigente and res["legitimacion_ok"]
    return res


def _legitimaciones(con, empresa_id: int) -> list[dict]:
    """Legitimaciones vigentes de la empresa, con `evidencia_valida`: el documento de evidencia existe y es de la misma
    empresa (tras restaurar una copia, un identificador sin su documento podría reutilizarse para el de otra)."""
    return db.filas(con, """SELECT l.*, (d.id IS NOT NULL) AS evidencia_valida FROM legitimaciones l
                           LEFT JOIN documentos d ON d.id=l.evidencia_documento_id AND d.empresa_id=l.empresa_id
                           WHERE l.empresa_id=? AND l.revocada_en IS NULL ORDER BY l.id""", (empresa_id,))


def evaluar(con, v: dict, empresa: dict) -> dict:
    """Evaluación en el momento del uso: vigencia, titularidad/legitimación y, si es de «Mis certificados», que la
    asignación siga activa, que el archivo exista y que el propietario siga siendo administrador de la empresa."""
    res = evaluar_legitimacion(v, empresa, _legitimaciones(con, empresa["id"]))
    problemas = []
    if v.get("origen") == "usuario":
        prop = db.fila(con, """SELECT u.nombre, u.activo, m.rol FROM usuarios u LEFT JOIN miembros m
                               ON m.usuario_id=u.id AND m.empresa_id=? WHERE u.id=?""", (empresa["id"], v["propietario_id"]))
        res["propietario"] = (prop or {}).get("nombre")
        if v.get("eliminado_en"):
            problemas.append("El propietario eliminó el certificado.")
        elif v.get("blob_cifrado") is None:
            problemas.append("El archivo del certificado debe volver a cargarse en «Mis certificados» (se cargó después "
                             "de la copia de seguridad restaurada).")
        if not v.get("activo"):
            problemas.append("La asignación está retirada.")
        if not prop or not prop["activo"] or prop["rol"] != "admin":
            problemas.append("El propietario del certificado ya no es administrador de esta empresa: la asignación no "
                             "puede usarse.")
    res["utilizable"] = res["vigente"] and not problemas
    if problemas:
        res["detalle"] = " ".join(problemas) + " " + res["detalle"]
    res["ok"] = res["utilizable"] and res["legitimacion_ok"]
    return res


# --- Certificado en uso por empresa y entorno ----------------------------------------------------------------------
_CAMPOS = ("alias", "sujeto", "emisor", "numero_serie", "valido_desde", "valido_hasta", "titular_nif", "representado_nif",
           "titular_nombre", "tipo", "huella_cert")


def _vista_asignacion(a: dict, cu: dict) -> dict:
    v = {k: cu.get(k) for k in _CAMPOS}
    v.update(origen="usuario", id=cu["id"], certificado_usuario_id=cu["id"], asignacion_id=a["id"], empresa_id=a["empresa_id"],
             entorno=a["entorno"], activo=a["activo"], propietario_id=cu["usuario_id"], blob_cifrado=cu["blob_cifrado"],
             eliminado_en=cu["eliminado_en"], asignado_en=a["asignado_en"], asignado_por=a["asignado_por"],
             subido_en=cu["subido_en"])
    return v


def _vista_empresa(c: dict) -> dict:
    return dict(c, origen="empresa", asignacion_id=None, propietario_id=None)


def seleccionado(con, empresa_id: int, entorno: str) -> dict | None:
    """Certificado que se usará para la empresa y entorno, con `legitimacion_dict` evaluado ahora.

    Una asignación activa de «Mis certificados» prevalece: asignar uno desactiva el de la empresa y cargar o reactivar uno
    de la empresa retira la asignación, así que solo coexisten tras restaurar una copia (la asignación es la más reciente)."""
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (empresa_id,))
    if not empresa:
        return None
    v = None
    a = db.fila(con, "SELECT * FROM certificado_asignaciones WHERE empresa_id=? AND entorno=? AND activo=1 ORDER BY id DESC LIMIT 1",
                (empresa_id, entorno))
    if a:
        cu = db.fila(con, "SELECT * FROM certificados_usuario WHERE id=?", (a["certificado_usuario_id"],))
        if cu:
            v = _vista_asignacion(a, cu)
    if v is None and entorno in ENTORNOS_CERT:
        c = db.fila(con, "SELECT * FROM certificados WHERE empresa_id=? AND entorno=? AND activo=1 ORDER BY id DESC LIMIT 1",
                    (empresa_id, entorno))
        if c:
            v = _vista_empresa(c)
    if v is None:
        return None
    v["legitimacion_dict"] = evaluar(con, v, empresa)
    return v


def activo(con, empresa_id: int, entorno: str) -> dict | None:
    """Compatibilidad: certificado en uso (de la empresa o asignado de «Mis certificados»)."""
    return seleccionado(con, empresa_id, entorno)


TEXTO_NIF_NO_ACREDITADO = ("El NIF de la empresa no está acreditado ante la plataforma: el primer envío a la AEAT (pruebas o "
                           "producción) con un certificado que no es de la propia empresa exige que el operador de la plataforma "
                           "verifique el NIF. Pídaselo aportando la documentación que acredite que actúa por la empresa, o use un "
                           "certificado de la propia empresa.")


def nif_acreditado(con, empresa: dict) -> dict:
    """¿Consta que la empresa (su NIF) la gestiona quien puede hacerlo? Cualquier usuario da de alta una empresa con un NIF
    libre y la legitimación de un tercero es una declaración que la aplicación no verifica; por eso el primer envío real
    con el certificado de un tercero exige una de estas pruebas:
    - el operador de la plataforma verificó el NIF (o dio de alta él mismo la empresa, también en la versión anterior);
    - la AEAT ya aceptó un envío de la empresa a pruebas o producción hecho con un certificado PROPIO de la empresa (la
      AEAT comprueba en la conexión que el certificado es auténtico; uno cargado sin más podría ser autofirmado).
    Con un certificado propio no hace falta (lo comprueba la AEAT en cada envío). Devuelve {ok, motivo, en}."""
    e = db.fila(con, "SELECT nif_verificado_en, nif_verificado_motivo FROM empresas WHERE id=?", (empresa["id"],)) or {}
    if e.get("nif_verificado_en"):
        return {"ok": True, "motivo": e.get("nif_verificado_motivo") or "operador", "en": e["nif_verificado_en"]}
    f = db.fila(con, """SELECT e.finalizado_en en FROM actividad a JOIN envios e ON e.id = json_extract(a.detalle, '$.envio_id')
                        WHERE a.empresa_id=? AND a.accion='certificado.uso' AND e.empresa_id=a.empresa_id
                        AND e.entorno IN ('pruebas','produccion') AND json_extract(a.detalle, '$.operacion')='remision'
                        AND json_extract(a.detalle, '$.representante_nif') IS NULL
                        AND e.resultado IN ('Correcto','ParcialmenteCorrecto') ORDER BY e.id LIMIT 1""", (empresa["id"],))
    if f:
        return {"ok": True, "motivo": "envio_certificado_propio", "en": f["en"]}
    return {"ok": False, "motivo": None, "en": None}


def para_envio(con, empresa: dict, entorno: str) -> tuple[dict | None, dict | None]:
    """Certificado con el que se presenta y representante de la cabecera, para la empresa y el entorno.

    Es la regla única para el alta y la anulación (RegFactuSistemaFacturacion: `Cabecera/Representante`) y para las
    consultas (ConsultaFactuSistemaFacturacion), que usan el mismo certificado pero van sin
    `Cabecera/IndicadorRepresentante` aunque haya representante (solo lo llevan al repetirse tras un 4140; ver consulta.py):
    - certificado «propio» → (cert, None);
    - certificado de un tercero con legitimación declarada para su titular → (cert, {"nif", "nombre"});
    - en el simulador, si no hay ninguno asignado → (None, None): el simulador no necesita certificado. Tampoco se
      bloquea por una asignación que ya no puede usarse (propietario que dejó de administrar la empresa, certificado
      eliminado, sin archivo o caducado): se envía sin certificado, como antes de asignarlo. Lo único que se prueba con
      él es la representación, así que un tercero utilizable sin legitimación sí se rechaza.
    Lanza CertificadoNoUtilizable si no hay certificado, no está vigente, la asignación ya no puede usarse o falta la
    legitimación. El certificado devuelto se pasa tal cual a `ficheros_pem` (nunca se reconsulta: TLS y cabecera
    corresponden siempre al mismo certificado)."""
    c = seleccionado(con, empresa["id"], entorno)
    if c is None:
        if entorno == "simulador":
            return None, None
        raise CertificadoNoUtilizable(f"No hay certificado activo para {entorno}")
    leg = c["legitimacion_dict"]
    if entorno == "simulador" and not leg.get("utilizable"):
        return None, None
    if not leg["vigente"]:
        raise CertificadoNoUtilizable(f"El certificado de {entorno} no está vigente")
    if not leg["ok"]:
        raise CertificadoNoUtilizable(f"El certificado de {entorno} no puede usarse: {leg['detalle']}")
    # También aquí (y no solo al expedir): el certificado puede cambiarse entre la expedición y el envío
    if entorno in ENTORNOS_CERT and not leg.get("propio") and not nif_acreditado(con, empresa)["ok"]:
        raise CertificadoNoUtilizable(TEXTO_NIF_NO_ACREDITADO)
    return c, leg.get("representante")


def registrar_uso(con, cert: dict, empresa_id: int, entorno: str, envio_id: int | None, operacion: str = "remision") -> None:
    """Auditoría de cada uso del certificado (sin secretos). Si no puede anotarse, el envío (o la consulta) no se hace."""
    leg = cert.get("legitimacion_dict") or {}
    rep = leg.get("representante")
    db.actividad(con, "certificado.uso", empresa_id=empresa_id,
                 objeto="certificado_usuario" if cert.get("origen") == "usuario" else "certificado", objeto_id=cert["id"],
                 detalle={"entorno": entorno, "operacion": operacion, "envio_id": envio_id,
                          "asignacion_id": cert.get("asignacion_id"), "propietario_id": cert.get("propietario_id"),
                          "titular_nif": (leg.get("titular") or {}).get("nif"),
                          "representante_nif": rep["nif"] if rep else None, "tls": entorno != "simulador"})


def auditar_al_usar(con, cert: dict, empresa_id: int, entorno: str, operacion: str) -> dict:
    """Deja anotado en `cert` que su próximo uso (al descifrarlo en `ficheros_pem`, justo antes de TLS) debe auditarse.
    Lo usan los servicios que obtienen el certificado con remision._preparar_destino (p. ej., la consulta a la AEAT),
    para que cada conexión quede en «Ver usos» sin anotar las consultas que no llegan a usarlo."""
    cert["_auditar_uso"] = (con, empresa_id, entorno, operacion)
    return cert


def resumen(v: dict | None, usuario_id: int | None = None) -> dict | None:
    """Lo que se muestra de un certificado (nunca el archivo ni la contraseña)."""
    if not v:
        return None
    leg = dict(v.get("legitimacion_dict") or {})
    return {"origen": "mis_certificados" if v.get("origen") == "usuario" else "empresa", "certificado_id": v["id"],
            "asignacion_id": v.get("asignacion_id"), "entorno": v.get("entorno"), "alias": v.get("alias"),
            "sujeto": v.get("sujeto"), "emisor": v.get("emisor"), "tipo": v.get("tipo"), "titular_nif": v.get("titular_nif"),
            "representado_nif": v.get("representado_nif"), "valido_desde": v.get("valido_desde"),
            "valido_hasta": v.get("valido_hasta"), "titular": leg.get("titular"),
            "propietario": ({"id": v["propietario_id"], "nombre": leg.get("propietario")} if v.get("propietario_id") else None),
            "es_mio": bool(usuario_id and v.get("propietario_id") == usuario_id), "legitimacion": leg}


def describir(v: dict | None, entorno: str) -> str:
    """Texto del requisito «certificado»: qué certificado se usará y en calidad de qué."""
    if not v:
        return (f"No hay certificado para {entorno}: cárguelo para la empresa o asigne uno de «Mis certificados» "
                "(Configuración → Certificados).")
    leg = v["legitimacion_dict"]
    t = leg.get("titular") or {}
    origen = "de la empresa" if v.get("origen") != "usuario" else f"de «Mis certificados» de {leg.get('propietario') or 'otro usuario'}"
    if leg.get("propio"):
        papel = "propio de la empresa (sin representante)"
    elif leg.get("representante"):
        papel = f"de un tercero: los envíos llevarán Representante {leg['representante']['nombre']} (NIF {leg['representante']['nif']})"
    else:
        papel = "de un tercero: necesita legitimación declarada para su titular"
    # Con un tercero utilizable, el detalle de la legitimación ya figura en el requisito «representacion»
    detalle = "" if leg.get("requiere_legitimacion") and leg.get("utilizable") else leg.get("detalle", "")
    return (f"Se usará el certificado de {t.get('nombre') or '—'} (NIF {t.get('nif') or '—'}), {origen}, válido hasta "
            f"{db.legible(v.get('valido_hasta'), hora=False)}; {papel}. {detalle}").strip()


# --- Carga por empresa -------------------------------------------------------------------------------------------
def _desactivar_empresa(con, empresa_id: int, entorno: str, excepto: int | None = None) -> list[tuple[str, int]]:
    """Deja sin uso los certificados de la empresa en ese entorno (con nueva versión, para anotarlos en el diario)."""
    ids = [r["id"] for r in db.filas(con, "SELECT id FROM certificados WHERE empresa_id=? AND entorno=? AND activo=1 AND id<>?",
                                     (empresa_id, entorno, excepto or 0))]
    for i in ids:
        con.execute("UPDATE certificados SET activo=0, version=version+1 WHERE id=?", (i,))
    return [("certificados", i) for i in ids]


def guardar(con, *, empresa: dict, entorno: str, p12: bytes, clave: str, usuario_id: int, alias: str | None = None,
            retiradas: list | None = None, cambios: list | None = None) -> int:
    """Carga un certificado para la empresa y entorno y lo deja en uso (retira la asignación de «Mis certificados» que
    hubiera; sus identificadores se añaden a `retiradas`, y las filas de `certificados` cambiadas a `cambios`, para
    anotarlos en el diario tras confirmar)."""
    info = analizar(p12, clave)
    leg = evaluar_legitimacion(info, empresa, _legitimaciones(con, empresa["id"]))
    contexto = f"cert:{empresa['id']}:{entorno}".encode()
    blob = cifrar(json.dumps({"p12": base64.b64encode(p12).decode(), "clave": clave}).encode(), contexto)
    desactivados = _desactivar_empresa(con, empresa["id"], entorno)
    ids = retirar(con, usuario_id=usuario_id, motivo="sustituida por un certificado de la empresa", empresa_id=empresa["id"],
                  entorno=entorno)
    if retiradas is not None:
        retiradas.extend(ids)
    cur = con.execute("""INSERT INTO certificados (empresa_id, entorno, alias, sujeto, emisor, numero_serie, valido_desde,
        valido_hasta, titular_nif, representado_nif, tipo, huella_cert, blob_cifrado, legitimacion, activo, subido_por, subido_en,
        titular_nombre, version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,1)""",
                      (empresa["id"], entorno, alias, info["sujeto"], info["emisor"], info["numero_serie"],
                       info["valido_desde"], info["valido_hasta"], info["titular_nif"], info["representado_nif"],
                       info["tipo"], info["huella_cert"], blob, json.dumps(leg, ensure_ascii=False), usuario_id, db.ahora_iso(),
                       info["titular_nombre"]))
    if cambios is not None:
        cambios.extend(desactivados + [("certificados", cur.lastrowid)])
    return cur.lastrowid


def activar_empresa(con, *, empresa_id: int, certificado_id: int, usuario_id: int) -> tuple[list[int], list[tuple[str, int]]]:
    """Vuelve a usar un certificado ya cargado para la empresa. Devuelve las asignaciones retiradas y las filas de
    `certificados` cambiadas (para el diario: la activación debe sobrevivir a una restauración)."""
    c = db.fila(con, "SELECT * FROM certificados WHERE id=? AND empresa_id=?", (certificado_id, empresa_id))
    if not c:
        raise LookupError("No encontrado")
    cambios = _desactivar_empresa(con, empresa_id, c["entorno"], excepto=certificado_id)
    if not c["activo"]:
        con.execute("UPDATE certificados SET activo=1, version=version+1 WHERE id=?", (certificado_id,))
        cambios.append(("certificados", certificado_id))
    retiradas = retirar(con, usuario_id=usuario_id, motivo="sustituida por un certificado de la empresa", empresa_id=empresa_id,
                        entorno=c["entorno"])
    return retiradas, cambios


# --- «Mis certificados» ------------------------------------------------------------------------------------------
def _ctx_usuario(cid: int, usuario_id: int) -> bytes:
    """Contexto AEAD: el archivo cifrado solo se descifra para esa fila y ese propietario."""
    return f"cert-usuario:{cid}:{usuario_id}".encode()


def guardar_usuario(con, *, usuario_id: int, p12: bytes, clave: str, alias: str | None = None) -> tuple[int, bool]:
    """Carga un certificado en «Mis certificados». Devuelve (id, recargado): si el mismo certificado figuraba sin archivo
    (cargado tras la copia restaurada) se le devuelve el archivo y conserva sus asignaciones."""
    info = analizar(p12, clave)
    if not vigente_ahora(info):
        raise CertificadoNoValido("El certificado no está vigente (caducado o aún no válido): no se guarda.")
    alias = limpio(alias, 80)
    previo = db.fila(con, "SELECT * FROM certificados_usuario WHERE usuario_id=? AND huella_cert=? AND eliminado_en IS NULL",
                     (usuario_id, info["huella_cert"]))
    if previo and previo["blob_cifrado"] is not None:
        raise CertificadoDuplicado("Ese certificado ya está en «Mis certificados».")
    if previo:
        cid, recargado = previo["id"], True
    else:
        cid = con.execute("""INSERT INTO certificados_usuario (usuario_id, alias, sujeto, emisor, numero_serie, valido_desde,
            valido_hasta, titular_nif, representado_nif, titular_nombre, tipo, huella_cert, subido_en, version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                          (usuario_id, alias, info["sujeto"], info["emisor"], info["numero_serie"], info["valido_desde"],
                           info["valido_hasta"], info["titular_nif"], info["representado_nif"], info["titular_nombre"],
                           info["tipo"], info["huella_cert"], db.ahora_iso())).lastrowid
        recargado = False
    blob = cifrar(json.dumps({"p12": base64.b64encode(p12).decode(), "clave": clave}).encode(), _ctx_usuario(cid, usuario_id))
    con.execute("UPDATE certificados_usuario SET blob_cifrado=?, alias=COALESCE(?, alias), version=version+? WHERE id=?",
                (blob, alias, 1 if recargado else 0, cid))
    return cid, recargado


def propio_de(con, usuario_id: int, cid: int) -> dict:
    """Certificado de «Mis certificados» del usuario (no eliminado). Otro usuario recibe LookupError (404)."""
    cu = db.fila(con, "SELECT * FROM certificados_usuario WHERE id=? AND usuario_id=? AND eliminado_en IS NULL", (cid, usuario_id))
    if not cu:
        raise LookupError("No encontrado")
    return cu


def eliminar_usuario(con, *, usuario_id: int, cid: int) -> list[tuple[str, int]]:
    """Borra el archivo cifrado (la fila queda como constancia) y retira todas sus asignaciones."""
    propio_de(con, usuario_id, cid)
    con.execute("UPDATE certificados_usuario SET blob_cifrado=NULL, eliminado_en=?, version=version+1 WHERE id=?",
                (db.ahora_iso(), cid))
    ids = retirar(con, usuario_id=usuario_id, motivo="certificado eliminado por su propietario", certificado_usuario_id=cid)
    return [("certificados_usuario", cid)] + [("certificado_asignaciones", i) for i in ids]


def asignar(con, *, usuario_id: int, cid: int, empresa_id: int, entorno: str) -> tuple[int, list[tuple[str, int]]]:
    """Asigna un certificado del usuario a una empresa y entorno (el usuario debe ser su propietario; el rol de
    administrador en la empresa lo comprueba la API). Lo deja en uso."""
    if entorno not in ENTORNOS_ASIGNACION:
        raise ValueError("Entorno no válido")
    cu = propio_de(con, usuario_id, cid)
    if cu["blob_cifrado"] is None:
        raise CertificadoNoUtilizable("Vuelva a cargar el archivo del certificado en «Mis certificados» antes de asignarlo.")
    if not vigente_ahora(cu):
        raise CertificadoNoUtilizable("El certificado no está vigente (caducado o aún no válido).")
    actual = db.fila(con, "SELECT * FROM certificado_asignaciones WHERE empresa_id=? AND entorno=? AND activo=1",
                     (empresa_id, entorno))
    if actual and actual["certificado_usuario_id"] == cid:
        return actual["id"], []
    cambios = [("certificado_asignaciones", i) for i in
               retirar(con, usuario_id=usuario_id, motivo="sustituida por otro certificado", empresa_id=empresa_id, entorno=entorno)]
    if entorno in ENTORNOS_CERT:
        cambios += _desactivar_empresa(con, empresa_id, entorno)
    aid = con.execute("""INSERT INTO certificado_asignaciones (certificado_usuario_id, empresa_id, entorno, activo, asignado_por,
                         asignado_en, version) VALUES (?,?,?,1,?,?,1)""",
                      (cid, empresa_id, entorno, usuario_id, db.ahora_iso())).lastrowid
    return aid, cambios + [("certificado_asignaciones", aid)]


def retirar(con, *, usuario_id: int, motivo: str, empresa_id: int | None = None, entorno: str | None = None,
            certificado_usuario_id: int | None = None, asignacion_id: int | None = None) -> list[int]:
    """Retira las asignaciones activas que cumplan los filtros. Devuelve sus identificadores."""
    sql, p = "SELECT id FROM certificado_asignaciones WHERE activo=1", []
    for col, val in (("empresa_id", empresa_id), ("entorno", entorno), ("certificado_usuario_id", certificado_usuario_id),
                     ("id", asignacion_id)):
        if val is not None:
            sql += f" AND {col}=?"
            p.append(val)
    ids = [r["id"] for r in db.filas(con, sql, p)]
    for i in ids:
        con.execute("""UPDATE certificado_asignaciones SET activo=0, retirado_en=?, retirado_por=?, motivo_retirada=?,
                       version=version+1 WHERE id=?""", (db.ahora_iso(), usuario_id, motivo[:200], i))
    return ids


def mis_certificados(con, usuario_id: int) -> list[dict]:
    """Certificados del usuario con sus asignaciones (sin archivo ni contraseña)."""
    out = []
    for cu in db.filas(con, "SELECT * FROM certificados_usuario WHERE usuario_id=? AND eliminado_en IS NULL ORDER BY id DESC",
                       (usuario_id,)):
        nif_t, nombre_t = titular_presentador(cu)
        asig = []
        for a in db.filas(con, """SELECT a.*, e.nif empresa_nif, e.razon_social empresa_razon FROM certificado_asignaciones a
                                  JOIN empresas e ON e.id=a.empresa_id WHERE a.certificado_usuario_id=? AND a.activo=1
                                  ORDER BY e.razon_social, a.entorno""", (cu["id"],)):
            empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (a["empresa_id"],))
            leg = evaluar(con, _vista_asignacion(a, cu), empresa)
            asig.append({"asignacion_id": a["id"], "empresa": {"id": a["empresa_id"], "nif": a["empresa_nif"],
                                                                "razon_social": a["empresa_razon"]},
                         "entorno": a["entorno"], "asignado_en": a["asignado_en"], "propio": leg["propio"],
                         "utilizable": leg["utilizable"], "ok": leg["ok"], "representante": leg["representante"],
                         "detalle": leg["detalle"]})
        out.append({"id": cu["id"], "alias": cu["alias"], "sujeto": cu["sujeto"], "emisor": cu["emisor"],
                    "numero_serie": cu["numero_serie"], "tipo": cu["tipo"], "titular_nif": cu["titular_nif"],
                    "representado_nif": cu["representado_nif"], "titular": {"nif": nif_t, "nombre": nombre_t},
                    "valido_desde": cu["valido_desde"], "valido_hasta": cu["valido_hasta"], "vigente": vigente_ahora(cu),
                    "subido_en": cu["subido_en"], "requiere_recarga": cu["blob_cifrado"] is None, "asignaciones": asig})
    return out


# --- Archivo para el cliente TLS --------------------------------------------------------------------------------------
def _contexto(cert_row: dict) -> bytes:
    if cert_row.get("origen") == "usuario":
        return _ctx_usuario(cert_row["id"], cert_row["propietario_id"])
    return f"cert:{cert_row['empresa_id']}:{cert_row['entorno']}".encode()


@contextlib.contextmanager
def ficheros_pem(cert_row: dict):
    """Entrega rutas a certificado y clave en PEM para el cliente TLS.

    En Linux se usan ficheros anónimos en memoria (memfd), accesibles como /proc/self/fd/N: la clave privada no
    llega a escribirse en disco. En otros sistemas se usa un directorio temporal 0600 (preferentemente en /dev/shm)
    que se sobrescribe y borra al terminar; los restos se purgan al arrancar.

    Si el certificado lleva una auditoría pendiente (`auditar_al_usar`), se anota ANTES de descifrarlo: si no puede
    anotarse, no se usa."""
    if cert_row.get("blob_cifrado") is None:
        raise CertificadoNoUtilizable("El certificado no tiene archivo: vuelva a cargarlo")
    auditoria = cert_row.pop("_auditar_uso", None)
    if auditoria:
        con_a, empresa_a, entorno_a, operacion_a = auditoria
        registrar_uso(con_a, cert_row, empresa_a, entorno_a, None, operacion_a)
    d = json.loads(descifrar(cert_row["blob_cifrado"], _contexto(cert_row)))
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


# --- Diario: «Mis certificados», asignaciones, legitimaciones y certificado en uso sobreviven a una restauración ------
def anotar(con, cambios: list[tuple[str, int]]) -> None:
    """Anota en el diario la versión actual de cada fila cambiada (tras confirmar la transacción). Nunca incluye el
    archivo cifrado ni la contraseña: solo si la fila tiene archivo. Una legitimación lleva además la fila de su
    documento de evidencia (sin el archivo, que ya tiene su copia espejo junto al diario) para poder recuperarlo. Si el
    diario falla, la conciliación del arranque completará la anotación."""
    for tabla, fid in cambios:
        if tabla not in TABLAS_DIARIO:
            continue
        f = db.fila(con, f"SELECT * FROM {tabla} WHERE id=?", (fid,))
        if not f:
            continue
        if "blob_cifrado" in f:
            f["con_archivo"] = f.pop("blob_cifrado") is not None
        if tabla == "legitimaciones" and f.get("evidencia_documento_id"):
            doc = db.fila(con, "SELECT * FROM documentos WHERE id=? AND empresa_id=?", (f["evidencia_documento_id"], f["empresa_id"]))
            if doc:
                f["_documento"] = doc
        try:
            diario.anotar("certificados_fila", {"tabla": tabla, "fila": f})
        except OSError:
            db.actividad(con, "diario.error", detalle={"anotacion": tabla, "id": fid})


def _inactiva(tabla: str, f: dict) -> bool:
    return bool(f.get("eliminado_en") or f.get("revocada_en")
                or (tabla in ("certificado_asignaciones", "certificados") and not f.get("activo")))


def _recuperar_documento(con, leg: dict) -> bool:
    """Reinserta (con su identificador) la fila del documento de evidencia de una legitimación si falta en la base
    restaurada; el archivo vuelve de la copia espejo del almacén. Solo si es de la misma empresa."""
    doc = leg.get("_documento") or {}
    if not doc or doc.get("id") != leg.get("evidencia_documento_id") or doc.get("empresa_id") != leg.get("empresa_id"):
        return False
    if db.fila(con, "SELECT 1 FROM documentos WHERE id=?", (doc["id"],)):
        return False
    cols = [c[1] for c in con.execute("PRAGMA table_info(documentos)") if c[1] in doc]
    cur = con.execute(f"INSERT OR IGNORE INTO documentos ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      [doc[c] for c in cols])
    return cur.rowcount == 1


def _reproducir_certificado_empresa(con, fid: int, f: dict, por_recargar: list) -> bool:
    """Certificado cargado por empresa: solo se reproduce su estado «en uso». Si es posterior a la copia no puede
    recrearse (la fila exige el archivo cifrado, que el diario nunca guarda): si era el que estaba en uso se informa
    para volver a cargarlo, y NO se vuelve a usar en su lugar el que sustituyó."""
    actual = db.fila(con, "SELECT empresa_id, entorno, huella_cert FROM certificados WHERE id=?", (fid,))
    if not actual:
        if f.get("activo"):
            por_recargar.append({"id": fid, "empresa_id": f.get("empresa_id"), "entorno": f.get("entorno"),
                                 "titular_nif": f.get("titular_nif"), "titular_nombre": f.get("titular_nombre")})
        return False
    if (actual["empresa_id"], actual["entorno"], actual["huella_cert"]) != (f.get("empresa_id"), f.get("entorno"), f.get("huella_cert")):
        raise ValueError("la fila de la copia no corresponde al mismo certificado")
    con.execute("UPDATE certificados SET activo=?, version=? WHERE id=?", (1 if f.get("activo") else 0, f.get("version") or 0, fid))
    return True


def reproducir_diario(con, entradas: list[dict]) -> dict:
    """Aplica sobre la base restaurada la versión más reciente anotada de cada fila, si es posterior a la de la copia.

    Las bajas (eliminado, retirada, revocada, sin uso) van primero para respetar los índices únicos. Un certificado de
    «Mis certificados» posterior a la copia se recrea SIN archivo («debe volver a cargarse»): el diario nunca guarda el
    certificado, ni siquiera cifrado. De los certificados cargados por empresa se reproduce el estado «en uso» (los
    posteriores a la copia se listan en `certificados_empresa_por_recargar`). Una asignación activa deja inactivo el
    certificado de la empresa del mismo entorno (es la elección más reciente). Una legitimación recupera su documento de
    evidencia; si no puede recuperarse, pierde la referencia (deja de contar) en lugar de apuntar a un identificador que
    otro documento podría reutilizar."""
    ultimas: dict[tuple[str, int], dict] = {}
    for e in entradas:
        if e.get("tipo") != "certificados_fila":
            continue
        t, f = (e.get("datos") or {}).get("tabla"), (e.get("datos") or {}).get("fila") or {}
        if t not in TABLAS_DIARIO or "id" not in f:
            continue
        if f.get("version", 0) >= ultimas.get((t, f["id"]), {}).get("version", -1):
            ultimas[(t, f["id"])] = f
    aplicadas, conflictos, por_recargar, documentos = 0, [], [], 0
    orden = sorted(ultimas.items(), key=lambda kv: (0 if _inactiva(kv[0][0], kv[1]) else 1, TABLAS_DIARIO.index(kv[0][0]), kv[0][1]))
    columnas = {t: [c[1] for c in con.execute(f"PRAGMA table_info({t})")] for t in TABLAS_DIARIO}
    for (t, fid), f in orden:
        actual = db.fila(con, f"SELECT version FROM {t} WHERE id=?", (fid,))
        if actual and (actual["version"] or 0) >= (f.get("version") or 0):
            continue
        cols = [c for c in columnas[t] if c in f and c not in ("id", "blob_cifrado")]
        try:
            if t == "certificados":
                aplicadas += _reproducir_certificado_empresa(con, fid, f, por_recargar)
                continue
            if t == "legitimaciones":
                documentos += _recuperar_documento(con, f)
            if t == "certificado_asignaciones" and f.get("activo"):
                con.execute("""UPDATE certificado_asignaciones SET activo=0, retirado_en=?, motivo_retirada=?, version=version+1
                               WHERE empresa_id=? AND entorno=? AND activo=1 AND id<>?""",
                            (db.ahora_iso(), "sustituida (restauración)", f["empresa_id"], f["entorno"], fid))
            if actual:
                con.execute(f"UPDATE {t} SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?", [f[c] for c in cols] + [fid])
            else:
                con.execute(f"INSERT INTO {t} (id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
                            [fid] + [f[c] for c in cols])
            if t == "certificados_usuario" and (f.get("eliminado_en") or not f.get("con_archivo") or not actual):
                con.execute("UPDATE certificados_usuario SET blob_cifrado=NULL WHERE id=?", (fid,))
            aplicadas += 1
        except Exception as e:  # noqa: BLE001 — una fila en conflicto no impide restaurar el resto; se informa
            conflictos.append({"tabla": t, "id": fid, "error": type(e).__name__})
    for a in db.filas(con, "SELECT DISTINCT empresa_id, entorno FROM certificado_asignaciones WHERE activo=1"):
        con.execute("UPDATE certificados SET activo=0, version=version+1 WHERE empresa_id=? AND entorno=? AND activo=1",
                    (a["empresa_id"], a["entorno"]))
    # Evidencia que no está en la base restaurada (ni pudo recuperarse) o es de otra empresa: la legitimación deja de
    # contar y pierde la referencia, para que el identificador libre no acabe apuntando al documento de otra empresa
    sin_evidencia = []
    for l in db.filas(con, """SELECT l.id, l.empresa_id, l.evidencia_documento_id FROM legitimaciones l
                             LEFT JOIN documentos d ON d.id=l.evidencia_documento_id
                             WHERE l.evidencia_documento_id IS NOT NULL AND (d.id IS NULL OR d.empresa_id<>l.empresa_id)"""):
        con.execute("UPDATE legitimaciones SET evidencia_documento_id=NULL, version=version+1 WHERE id=?", (l["id"],))
        db.actividad(con, "legitimacion.evidencia_perdida", empresa_id=l["empresa_id"], objeto="legitimacion", objeto_id=l["id"],
                     detalle={"motivo": "restauración: el documento de evidencia no está en la copia ni en el diario; "
                                        "la legitimación no cuenta hasta volver a declararla"})
        sin_evidencia.append(l["id"])
    return {"filas_aplicadas": aplicadas, "conflictos": conflictos, "documentos_evidencia_recuperados": documentos,
            "legitimaciones_sin_evidencia": sin_evidencia, "certificados_empresa_por_recargar": por_recargar}


def reconciliar_diario(con, entradas: list[dict]) -> int:
    """Al arrancar: anota las filas cuya versión no figure aún en el diario (p. ej. caída antes de anotarlas)."""
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(TABLAS_DIARIO) <= tablas:
        return 0
    vistas: dict[tuple[str, int], int] = {}
    for e in entradas:
        if e.get("tipo") == "certificados_fila":
            d = e.get("datos") or {}
            f = d.get("fila") or {}
            k = (d.get("tabla"), f.get("id"))
            vistas[k] = max(vistas.get(k, -1), f.get("version") or 0)
    faltan = [(t, f["id"]) for t in TABLAS_DIARIO for f in db.filas(con, f"SELECT id, version FROM {t}")
              if vistas.get((t, f["id"]), -1) < (f["version"] or 0)]
    anotar(con, faltan)
    return len(faltan)
