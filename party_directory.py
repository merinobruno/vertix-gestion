import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

from documents import address_text, field, identification_field, title
from identification import TAX_KIND, document_digits, formatted_number, id_group


class Conflict(Exception):
    pass


class MissingParty(Exception):
    pass


PAGE_SIZE = 25


def default_database():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "Vertix" / "agenda.sqlite3"


def search_key(text):
    return "".join(char for char in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(char))


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS parties (
            id INTEGER PRIMARY KEY, id_type TEXT NOT NULL, document_key TEXT NOT NULL,
            name_key TEXT NOT NULL, profile TEXT NOT NULL, UNIQUE (id_type, document_key)
        )""")
    except sqlite3.Error:
        db.close()
        raise
    return db


def as_record(row_id, encoded):
    revision = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    profile = json.loads(encoded)
    profile.setdefault("address", {})
    return {"id": row_id, "revision": revision, **profile}


def matches(path, query, limit, offset=0, *, count=False):
    name = search_key(field(query, "la búsqueda", False))
    if not count and not name:
        return [], 0
    identifier = re.fullmatch(r"(?:(dni|cuit/cuil|cuit|cuil)[:#]?)?([0-9]+)", document_digits(name))
    number = identifier[2] if identifier else ""
    kind = id_group((identifier[1] or "").upper()) if identifier else ""
    params = {"name":name, "number":number, "kind":kind, "limit":limit, "offset":offset}
    criteria = """FROM parties WHERE instr(name_key, :name) > 0 OR
        (:number != '' AND (:kind = '' OR id_type = :kind OR
         (:kind = 'CUIT/CUIL' AND id_type IN ('CUIT', 'CUIL'))) AND instr(document_key, :number) > 0)"""
    order = """ORDER BY CASE WHEN document_key = :number THEN 0
                             WHEN instr(name_key, :name) = 1 OR (:number != '' AND instr(document_key, :number) = 1) THEN 1
                             ELSE 2 END, name_key, id LIMIT :limit OFFSET :offset"""
    with closing(connect(path)) as db:
        db.execute("BEGIN")
        total = db.execute("SELECT count(*) " + criteria, params).fetchone()[0] if count else 0
        rows = db.execute("SELECT id, profile " + criteria + " " + order, params)
        return [as_record(row_id, encoded) for row_id, encoded in rows], total


def search(path, query):
    return matches(path, query, 8)[0]


def list_parties(path, query, offset=0):
    if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
        raise ValueError("La página de la agenda no es válida.")
    parties, total = matches(path, query, PAGE_SIZE, offset, count=True)
    return {"parties":parties, "total":total, "offset":offset, "page_size":PAGE_SIZE}


def contact_identity(party):
    if not isinstance(party, dict):
        raise ValueError("Completá los datos de la ficha.")
    name = field(party.get("name"), "el nombre o la razón social")
    if len(name) > 180:
        raise ValueError("Usá hasta 180 caracteres en el nombre.")
    kind = party.get("id_type")
    number = identification_field(party.get("id_number"), kind, "el número de identificación")
    return {"name": name, "id_type": kind, "id_number": number}


def directory_address(value, label, *, allow_empty=False):
    if not isinstance(value, dict):
        raise ValueError(f"Completá el {label} por campos.")
    address = {key: field(value.get(key), label, False)
               for key in ("street", "number", "floor", "apartment", "locality", "province")}
    if allow_empty and not any(address.values()):
        return {}
    address_text(address, label)
    return address


def validated_party(party, *, allow_empty_address=False):
    profile = contact_identity(party)
    if "address" in party:
        profile["address"] = directory_address(party["address"], "domicilio de la ficha", allow_empty=allow_empty_address)
    for key in ("borrower_representative", "lender_representative"):
        if key not in party:
            continue
        rep = party[key]
        if rep is None:
            profile[key] = None
        elif not isinstance(rep, dict):
            raise ValueError("Completá los datos del representante.")
        elif key == "borrower_representative":
            profile[key] = {**contact_identity(rep),
                            "address": directory_address(rep.get("address"), "domicilio del representante")}
        else:
            profile[key] = {**contact_identity({**rep, "id_type": "DNI"}), "title": title(rep.get("title"), "el Sr.")}
    return profile


def save(path, party, *, create_only=False, row_id=None, revision=None):
    profile = validated_party(party, allow_empty_address=row_id is not None)
    document = document_digits(profile["id_number"])
    kind = profile["id_type"]

    with closing(connect(path)) as db, db:
        # Serialize lookup and write so a simultaneous save cannot duplicate the document or lose a hidden address.
        db.execute("BEGIN IMMEDIATE")
        same_document = db.execute("""SELECT id, profile FROM parties WHERE document_key = ? AND
            (id_type = ? OR (? = 'CUIT/CUIL' AND id_type IN ('CUIT', 'CUIL'))) ORDER BY id""",
                                   (document, id_group(kind), id_group(kind))).fetchall()
        if row_id is not None:
            existing = db.execute("SELECT id, profile FROM parties WHERE id = ?", (row_id,)).fetchone()
            if not existing:
                raise MissingParty("Ya no se encuentra esta ficha en la agenda.")
            if as_record(*existing)["revision"] != revision:
                raise Conflict("La ficha cambió desde que la abriste. Volvé a abrirla para revisar sus datos actuales.")
            same_identity = any(match[0] == row_id for match in same_document)
            if same_document and not same_identity:
                raise Conflict("Ese tipo y número de identificación ya corresponden a otra ficha.")
        else:
            if len(same_document) > 1:
                raise Conflict("Hay más de una ficha previa con ese CUIT/CUIL. Elegí la ficha en la Agenda.")
            existing = same_document[0] if same_document else None
            same_identity = existing is not None
            if existing and create_only:
                raise Conflict("Ese documento ya está guardado. Seleccioná la ficha para actualizarla.")
        stored = json.loads(existing[1]) if existing else {}
        if same_identity and id_group(kind) == TAX_KIND:
            # Keep historically distinct CUIT/CUIL rows and their known printed labels. A new tax identity stays generic.
            kind = profile["id_type"] = stored["id_type"]
        # A form only sends the fields for its role and payment method. Null explicitly clears a representative.
        profile = {key: value for key, value in {**stored, **profile}.items() if value is not None}
        profile.setdefault("address", {})
        if existing and row_id is None:
            stored.setdefault("address", {})
            stored["id_number"] = formatted_number(stored["id_type"], stored["id_number"])
            if stored == profile:
                return {"party": as_record(*existing), "created": False}
            raise Conflict("Ese documento ya está guardado. Volvé a seleccionar la ficha antes de actualizarla.")
        encoded = json.dumps(profile, ensure_ascii=False)
        if existing:
            row_id = existing[0]
            db.execute("UPDATE parties SET id_type = ?, document_key = ?, name_key = ?, profile = ? WHERE id = ?",
                       (kind, document, search_key(profile["name"]), encoded, row_id))
        else:
            row_id = db.execute("INSERT INTO parties (id_type, document_key, name_key, profile) VALUES (?, ?, ?, ?)",
                                (kind, document, search_key(profile["name"]), encoded)).lastrowid
        return {"party": as_record(row_id, encoded), "created": not existing}


def update(path, party, row_id, revision):
    if type(row_id) is not int or not 0 < row_id <= 2**63 - 1 or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ValueError("Volvé a abrir la ficha antes de editarla.")
    return {"party": save(path, party, row_id=row_id, revision=revision)["party"]}
