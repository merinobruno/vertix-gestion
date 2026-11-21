import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from panel_api import ApiError, service_info, submit, upload_for


STATIC = Path(__file__).with_name("static")
PAGES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/styles.css": ("styles.css", "text/css; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/plantilla-anexo-i.xlsx": ("plantilla-anexo-i.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


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
            self.respond(200, json.dumps(service_info(pdf_export=True)).encode(), "application/json")
        elif path in PAGES:
            name, mime = PAGES[path]
            self.respond(200, (STATIC/name).read_bytes(), mime)
        else:
            self.error_json(404, "La dirección solicitada no existe.")

    do_HEAD = do_GET

    def do_POST(self):
        if not self.local_request() or self.headers.get("Origin") not in (None, "http://"+self.headers.get("Host","")):
            self.error_json(403, "La descarga debe iniciarse desde el panel local.")
            return
        path = urlsplit(self.path).path
        try:
            spec = upload_for(path, self.headers.get_content_type())
            length = self.headers.get("Content-Length", "")
            if not length.isascii() or not length.isdigit() or len(length) > 10 or not 0 < int(length) <= spec.limit:
                raise ApiError(413, spec.size_error)
            data = self.rfile.read(int(length))
            if len(data) != int(length):
                raise ApiError(400, "No se recibieron todos los datos.")
            name, mime, output = submit(path, data, pdf_export=True)
        except ApiError as error:
            self.error_json(error.status, str(error))
            return
        self.respond(200, output, mime, name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Panel documental local")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1",args.port),Panel)
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
