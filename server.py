import argparse
import http.client
import json
import sqlite3
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import party_directory
from panel_api import ApiError, JSON, Upload, service_info, submit, upload_for


STATIC = Path(__file__).with_name("static")
HOSTED_PANEL = "vertix-gestion.vercel.app"
DIRECTORY_ROUTES = {"/api/agenda/save", "/api/agenda/search", "/api/agenda/list", "/api/agenda/update", "/api/agenda/import"}
DIRECTORY_UPLOAD = Upload(16384, "La ficha supera el tamaño permitido.", ("application/json",), "Se esperaba una ficha en formato JSON.")
IMPORT_UPLOAD = Upload(98304, "La importación supera el tamaño permitido.", DIRECTORY_UPLOAD.types, DIRECTORY_UPLOAD.type_error)
PAGES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/styles.css": ("styles.css", "text/css; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/plantilla-anexo-i.xlsx": ("plantilla-anexo-i.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def needs_representative_support(path, data):
    if path not in ("/api/agenda/save", "/api/agenda/update", "/api/agenda/import"):
        return False
    try:
        body = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise ApiError(422, "Los datos de la ficha son inválidos.") from exc
    if not isinstance(body, dict):
        return False
    parties = body.get("parties") if path == "/api/agenda/import" else [body.get("party")]
    return isinstance(parties, list) and any(isinstance(party, dict) and
        ("borrower_representative" in party or "lender_representative" in party) for party in parties)


def relay_directory(path, data):
    unavailable = "No se pudo conectar con la agenda compartida. Volvé a intentar."
    connection = http.client.HTTPSConnection(HOSTED_PANEL, timeout=20)
    try:
        if needs_representative_support(path, data):
            # An older hosted panel accepts a contact but silently drops its representatives.
            connection.request("GET", "/api/health")
            health = connection.getresponse()
            raw = health.read(4097)
            if health.status != 200 or len(raw) > 4096:
                raise ApiError(503, unavailable)
            capabilities = json.loads(raw)
            if not isinstance(capabilities, dict) or capabilities.get("agenda_representatives") is not True:
                raise ApiError(503, "La agenda compartida todavía necesita actualizarse para guardar representantes.")
        connection.request("POST", path, body=data, headers={"Origin": "https://" + HOSTED_PANEL, "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(196609)
        if len(raw) > 196608 or response.status not in (200, 404, 409, 413, 415, 422):
            raise ApiError(503, unavailable)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ApiError(503, unavailable)
        if response.status != 200:
            error = result.get("error")
            if not isinstance(error, str) or not 0 < len(error) <= 220:
                raise ApiError(503, unavailable)
            raise ApiError(response.status, error)
        return None, JSON, raw
    except (OSError, http.client.HTTPException, ValueError, RecursionError) as exc:
        raise ApiError(503, unavailable) from exc
    finally:
        connection.close()


class Panel(BaseHTTPRequestHandler):
    def local_request(self):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        return self.headers.get("Host") in hosts

    def respond(self, status, data, content_type, name=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if name:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def error_json(self, status, message):
        self.respond(status, json.dumps({"error":message},ensure_ascii=False).encode(),"application/json; charset=utf-8")

    def do_GET(self):
        if not self.local_request():
            self.error_json(403, "Usá la dirección local del panel.")
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            local = self.server.directory_path is not None
            self.respond(200, json.dumps(service_info(pdf_export=True, party_directory=local, shared_directory=not local)).encode(), "application/json")
        elif path in PAGES:
            name, mime = PAGES[path]
            self.respond(200, (STATIC/name).read_bytes(), mime)
        else:
            self.error_json(404, "La dirección solicitada no existe.")

    do_HEAD = do_GET

    def directory_response(self, path, data):
        try:
            request = json.loads(data)
            if not isinstance(request, dict):
                raise ValueError("Los datos de la ficha son inválidos.")
            if path == "/api/agenda/search":
                result = {"parties": party_directory.search(self.server.directory_path, request.get("query"),
                                                            dni_only=request.get("dni_only") is True)}
            elif path == "/api/agenda/list":
                result = party_directory.list_parties(self.server.directory_path, request.get("query"), request.get("offset", 0))
            elif path == "/api/agenda/update":
                result = party_directory.update(self.server.directory_path, request.get("party"), request.get("id"), request.get("revision"))
            else:
                result = party_directory.save(self.server.directory_path, request.get("party"), create_only=request.get("create_only") is True)
            return None, JSON, json.dumps(result, ensure_ascii=False).encode()
        except party_directory.Conflict as exc:
            raise ApiError(409, str(exc)) from exc
        except party_directory.MissingParty as exc:
            raise ApiError(404, str(exc)) from exc
        except (ValueError, RecursionError) as exc:
            message = "Los datos de la ficha son inválidos." if isinstance(exc, (json.JSONDecodeError, UnicodeError, RecursionError)) else str(exc)
            raise ApiError(422, message) from exc
        except (OSError, sqlite3.Error) as exc:
            raise ApiError(503, "No se pudo acceder a la agenda local. Volvé a intentar.") from exc

    def do_POST(self):
        if not self.local_request() or self.headers.get("Origin") not in (None, "http://"+self.headers.get("Host","")):
            self.error_json(403, "La descarga debe iniciarse desde el panel local.")
            return
        path = urlsplit(self.path).path
        try:
            if path in DIRECTORY_ROUTES:
                if self.headers.get("Origin") != "http://" + self.headers["Host"]:
                    raise ApiError(403, "Abrí la agenda desde el panel local.")
                if path == "/api/agenda/import" and self.server.directory_path is not None:
                    raise ApiError(404, "La dirección solicitada no existe.")
                spec = IMPORT_UPLOAD if path == "/api/agenda/import" else DIRECTORY_UPLOAD
                if self.headers.get_content_type() not in spec.types:
                    raise ApiError(415, spec.type_error)
            else:
                spec = upload_for(path, self.headers.get_content_type())
            length = self.headers.get("Content-Length", "")
            if not length.isascii() or not length.isdigit() or len(length) > 10 or not 0 < int(length) <= spec.limit:
                raise ApiError(413, spec.size_error)
            data = self.rfile.read(int(length))
            if len(data) != int(length):
                raise ApiError(400, "No se recibieron todos los datos.")
            if path in DIRECTORY_ROUTES:
                handler = self.directory_response if self.server.directory_path is not None else relay_directory
                name, mime, output = handler(path, data)
            else:
                name, mime, output = submit(path, data, pdf_export=True)
        except ApiError as error:
            self.error_json(error.status, str(error))
            return
        self.respond(200, output, mime, name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Panel documental local")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--directory-db", type=Path, nargs="?", const=party_directory.default_database(),
                    help="Use an offline SQLite directory instead of the shared agenda.")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1",args.port),Panel)
    server.directory_path = args.directory_db
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Panel documental: {url}",flush=True)
    if args.open:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
