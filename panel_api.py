import json
from typing import NamedTuple

from annex import UPLOAD_LIMIT, import_annex
from documents import generate
from word_pdf import ExportError


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Upload(NamedTuple):
    limit: int
    size_error: str
    types: tuple[str, ...]
    type_error: str


JSON = "application/json; charset=utf-8"
UPLOADS = {
    "/api/anexo": Upload(UPLOAD_LIMIT, "Elegí una planilla .xlsx de hasta 4 MB.",
                         ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"),
                         "Elegí un archivo Excel .xlsx."),
    "/api/generate": Upload(250000, "Los datos de la operación superan el límite.", ("application/json",),
                            "Se esperaba una operación en formato JSON."),
}


def service_info(*, pdf_export):
    return {"status": "ok", "service": "panel-documental", "template_fidelity": True, "demand_note": True,
            "combined_document": True, "annex_import": True, "pdf_export": pdf_export}


def upload_for(path, content_type):
    spec = UPLOADS.get(path)
    if spec is None:
        raise ApiError(404, "La dirección solicitada no existe.")
    if content_type not in spec.types:
        raise ApiError(415, spec.type_error)
    return spec


def submit(path, data, *, pdf_export):
    if path == "/api/anexo":
        try:
            result = import_annex(data)
        except ValueError as exc:
            raise ApiError(422, str(exc)) from exc
        except Exception as exc:
            raise ApiError(422, "No se pudo leer el archivo. Guardá una copia .xlsx con Excel y volvé a intentar.") from exc
        return None, JSON, json.dumps(result, ensure_ascii=False).encode()

    try:
        payload = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise ApiError(422, "Los datos de la operación son inválidos.") from exc
    if not isinstance(payload, dict):
        raise ApiError(422, "Los datos de la operación son inválidos.")
    if not pdf_export and payload.get("format") in ("pdf", "both"):
        raise ApiError(422, "El PDF se genera en el panel local con Microsoft Word.")
    try:
        return generate(payload)
    except (ValueError, UnicodeError) as exc:
        raise ApiError(422, str(exc)) from exc
    except ExportError as exc:
        raise ApiError(503, str(exc)) from exc
    except Exception as exc:
        raise ApiError(500, "No se pudo generar el documento. Revisá los datos y volvé a intentar.") from exc
