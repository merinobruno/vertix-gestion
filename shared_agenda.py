import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from documents import field
from identification import document_digits, id_group
from panel_api import ApiError
from party_directory import search_key, validated_party


router = APIRouter(prefix="/api/agenda")
COOKIE = "__Host-vertix-agenda"
LIFETIME = 12 * 60 * 60
PRIVATE = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
UNAVAILABLE = "No se pudo conectar con la agenda compartida. Volvé a intentar."
LOCKED = "Ingresá la clave del equipo para acceder a la agenda."
DATA_ACTIONS = {"search", "list", "save", "update", "import"}


@dataclass(frozen=True, repr=False)
class Settings:
    host: str
    service_key: str
    code_hash: str

    @classmethod
    def configured(cls):
        url = os.environ.get("VERTIX_AGENDA_URL", "").strip()
        host = re.fullmatch(r"https://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.convex\.site)/?", url)
        key = os.environ.get("VERTIX_AGENDA_SERVICE_KEY", "").strip()
        digest = os.environ.get("VERTIX_AGENDA_KEY_HASH", "").strip()
        if not host or not re.fullmatch(r"[!-~]{32,512}", key) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None
        return cls(host[1], key, digest)

    def sign(self, payload):
        signing_key = hmac.digest(self.service_key.encode("ascii"), b"vertix:agenda:session:v1\0" + self.code_hash.encode("ascii"), "sha256")
        return hmac.new(signing_key, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def expiry(self, cookie):
        match = re.fullmatch(r"(v1\.([0-9]{10,11})\.[A-Za-z0-9_-]{22})\.([a-f0-9]{64})", cookie or "")
        if not match or not hmac.compare_digest(self.sign(match[1]), match[3]):
            return 0
        now, expiry = int(time.time()), int(match[2])
        return expiry if now < expiry <= now + LIFETIME else 0


def canonical_party(party, allow_empty_address):
    profile, digits, address = validated_party(party, allow_empty_address=allow_empty_address)
    if address is not None:
        profile["address"] = address
    return {"profile": profile, "kind": id_group(profile["id_type"]), "digits": digits, "nameKey": search_key(profile["name"])}


def prepared(action, body):
    if action in ("list", "search"):
        query = body.get("query", "")
        if not isinstance(query, str) or len(query) > 240:
            raise ValueError("La búsqueda admite hasta 240 caracteres.")
        name = search_key(field(query, "la búsqueda", False))
        match = re.fullmatch(r"(?:(dni|cuit/cuil|cuit|cuil)[:#]?)?([0-9]+)", document_digits(name))
        offset = body.get("offset", 0) if action == "list" else 0
        if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
            raise ValueError("La página de la agenda no es válida.")
        return {"name": name, "digits": match[2] if match else "", "kind": id_group((match[1] or "").upper()) if match else "", "offset": offset}
    if action == "import":
        parties = body.get("parties")
        if not isinstance(parties, list) or not 1 <= len(parties) <= 25:
            raise ValueError("Importá hasta 25 fichas por vez.")
        records, invalid = [], 0
        for party in parties:
            try:
                record = canonical_party(party, True)
            except ValueError:
                invalid += 1
                continue
            record["profile"].setdefault("address", {})
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            record["fingerprint"] = hashlib.sha256(encoded).hexdigest()
            records.append(record)
        return {"records": records, "invalid": invalid}
    data = {"record": canonical_party(body.get("party"), action == "update")}
    if action == "update":
        row_id, revision = body.get("id"), body.get("revision")
        if not isinstance(row_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row_id) or type(revision) is not int or not 1 <= revision < 2**53:
            raise ValueError("Volvé a abrir la ficha antes de editarla.")
        data.update(id=row_id, revision=revision)
    return data


def forward(settings, action, data):
    # Connect directly to the configured TLS host. In particular, never forward the bearer through redirects.
    connection = http.client.HTTPSConnection(settings.host, timeout=12)
    packed = json.dumps({"action": action, "data": data}, ensure_ascii=False, separators=(",", ":")).encode()
    try:
        connection.request("POST", "/agenda", body=packed, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + settings.service_key,
        })
        response = connection.getresponse()
        raw = response.read(196609)
        if len(raw) > 196608 or response.status not in (200, 404, 409, 422):
            raise ApiError(503, UNAVAILABLE)
        reply = json.loads(raw)
        if not isinstance(reply, dict):
            raise ApiError(503, UNAVAILABLE)
        if response.status != 200:
            # Only expose business errors issued by our authenticated endpoint.
            message = reply.get("error")
            if not isinstance(message, str) or not 0 < len(message) <= 220:
                raise ApiError(503, UNAVAILABLE)
            raise ApiError(response.status, message)
        return reply
    except (OSError, http.client.HTTPException, ValueError, RecursionError) as exc:
        raise ApiError(503, UNAVAILABLE) from exc
    finally:
        connection.close()


async def body_for(request, limit):
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        raise ApiError(415, "Se esperaba una solicitud en formato JSON.")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise ApiError(413, "La solicitud supera el tamaño permitido.")
        raw.extend(chunk)
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ApiError(422, "Los datos de la solicitud son inválidos.") from exc
    if not isinstance(data, dict):
        raise ApiError(422, "Los datos de la solicitud son inválidos.")
    return data


@router.post("/{action}")
async def agenda(action: str, request: Request):
    host = request.headers.get("host", "")
    local = request.url.hostname in ("127.0.0.1", "localhost", "::1")
    origin = request.headers.get("origin")
    if not host or (origin != "https://" + host and not (local and origin == "http://" + host)):
        raise ApiError(403, "Abrí la agenda desde el panel.")
    if action not in DATA_ACTIONS and action not in ("session", "login", "logout"):
        raise ApiError(404, "La dirección solicitada no existe.")
    settings = Settings.configured()
    expiry = settings.expiry(request.cookies.get(COOKIE)) if settings else 0
    if action in DATA_ACTIONS and not expiry:
        raise ApiError(401, LOCKED)
    body = await body_for(request, 98304 if action == "import" else 16384 if action in DATA_ACTIONS else 1024)
    if action == "session":
        return JSONResponse({"configured": bool(settings), "authenticated": bool(expiry), "expires_at": expiry}, headers=PRIVATE)
    if action == "logout":
        response = JSONResponse({"authenticated": False}, headers=PRIVATE)
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response
    if action == "login":
        if not settings:
            raise ApiError(503, UNAVAILABLE)
        code = body.get("code")
        if not isinstance(code, str) or not 1 <= len(code.strip()) <= 128 or not code.strip().isascii():
            raise ApiError(401, "La clave del equipo es incorrecta.")
        if not hmac.compare_digest(hashlib.sha256(code.strip().encode("ascii")).hexdigest(), settings.code_hash):
            raise ApiError(401, "La clave del equipo es incorrecta.")
        expiry = int(time.time()) + LIFETIME
        payload = f"v1.{expiry}.{secrets.token_urlsafe(16)}"
        response = JSONResponse({"authenticated": True, "expires_at": expiry}, headers=PRIVATE)
        response.set_cookie(COOKIE, payload + "." + settings.sign(payload), max_age=LIFETIME, path="/", secure=True, httponly=True, samesite="strict")
        return response
    try:
        data = prepared(action, body)
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    invalid = data.pop("invalid", 0)
    result = await run_in_threadpool(forward, settings, action, data)
    if action == "import":
        result["invalid"] = invalid
    return JSONResponse(result, headers=PRIVATE)
