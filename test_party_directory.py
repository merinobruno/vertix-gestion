import json
import os
import secrets
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from party_directory import list_parties, save, search, update
from server import HOSTED_AGENDA, Panel
from test_word_models import example_address


class QuietPanel(Panel):
    def log_message(self, *args):
        pass


@contextmanager
def local_panel(path):
    with ThreadingHTTPServer(("127.0.0.1", 0), QuietPanel) as server:
        server.directory_path = path
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval":0.01})
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


def request(port, path, data=None, *, method="POST", origin=True, content_type="application/json", host=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Content-Type":content_type}
    if origin:
        headers["Origin"] = f"http://127.0.0.1:{port}" if origin is True else origin
    if host:
        headers["Host"] = host
    try:
        connection.request(method, path, headers=headers, body=json.dumps(data).encode() if data is not None else None)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


class PartyDirectory(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "agenda.sqlite3"
        self.party = {"name":"Álamo de Prueba", "id_type":"DNI", "id_number":"00.000.111",
                      "address":dict(example_address("Calle de Prueba", "8"), floor="2", apartment="B")}

    def test_local_team_link_uses_only_the_provisioned_destination(self):
        self.enterContext(patch.dict(os.environ, {"LOCALAPPDATA": str(self.path.parent)}))
        shortcut = self.path.parent / "Vertix" / "Acceso Agenda" / "Agenda compartida.url"
        shortcut.parent.mkdir(parents=True)
        team_url = HOSTED_AGENDA + "/" + secrets.token_hex(32)
        with local_panel(self.path) as port:
            def navigate(headers=None):
                with closing(HTTPConnection("127.0.0.1", port, timeout=3)) as connection:
                    connection.request("GET", "/agenda-compartida", headers=headers or {})
                    response = connection.getresponse()
                    self.assertIn("no-store", response.getheader("Cache-Control"))
                    return response.status, response.getheader("Location")

            self.assertEqual(navigate(), (302, HOSTED_AGENDA))
            shortcut.write_text("[InternetShortcut]\nURL=https://otro.example/\n", encoding="utf-8")
            self.assertEqual(navigate(), (302, HOSTED_AGENDA))
            shortcut.write_text("[InternetShortcut]\nURL=" + team_url + "\n", encoding="utf-8-sig")
            self.assertEqual(navigate({"Sec-Fetch-Site":"same-origin"}), (302, team_url))
            for headers in ({"Origin":"https://otro.example"}, {"Sec-Fetch-Site":"cross-site"}, {"Host":"otro.example"}):
                self.assertEqual(navigate(headers), (403, None))

    def test_shared_across_roles_and_server_restarts(self):
        with local_panel(self.path) as port:
            status, health = request(port, "/api/health", method="GET")
            self.assertEqual(status, 200)
            self.assertTrue(health["party_directory"])
            self.assertTrue(health["directory_editor"])
            self.assertTrue(health["identification_formats"])
            status, saved = request(port, "/api/agenda/save", {"role":"client", "party":self.party})
            self.assertEqual(status, 200)
            self.assertTrue(saved["created"])
        with local_panel(self.path) as port:
            status, matches = request(port, "/api/agenda/search", {"role":"entity", "query":"a"})
            self.assertEqual(status, 200)
            self.assertEqual(matches["parties"], [saved["party"]])
            self.assertEqual(matches["parties"][0]["address"], self.party["address"])

    def test_document_identity_preserves_homonyms_and_hidden_domicile(self):
        first = save(self.path, self.party)["party"]
        second = save(self.path, dict(self.party, id_number="00.000.222"))["party"]
        self.assertNotEqual(first["id"], second["id"])
        edited = dict(self.party, name="Álamo Editado", id_number="00000111")
        del edited["address"]
        updated = update(self.path, edited, first["id"], first["revision"])["party"]
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["address"], first["address"])
        self.assertEqual([p["id"] for p in search(self.path, "ALAMO")], [second["id"], first["id"]])
        updated = update(self.path, dict(edited, address=example_address("Otra Calle", "20")), first["id"], updated["revision"])["party"]
        self.assertEqual((updated["address"]["floor"], updated["address"]["apartment"]), ("", ""))
        self.assertEqual(search(self.path, "de prueba"), [second])

    def test_search_requires_text_and_concurrent_retries_keep_one_party(self):
        self.assertEqual(search(self.path, ""), [])
        self.assertFalse(self.path.exists())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: save(self.path, self.party), range(8)))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(len(search(self.path, "Á")), 1)
        self.assertEqual(search(self.path, "%"), [])
        for i in range(10):
            save(self.path, dict(self.party, id_number=str(i + 350)))
        self.assertEqual(len(search(self.path, "álamo")), 8)

    def test_local_origin_and_valid_input_required_before_writing(self):
        with local_panel(self.path) as port:
            for status, options in [(403, {"origin":False}), (403, {"origin":"https://externo.example"}),
                                    (403, {"host":"externo.example"}), (415, {"content_type":"text/plain"})]:
                with self.subTest(options=options):
                    result, _ = request(port, "/api/agenda/save", {"party":self.party}, **options)
                    self.assertEqual(result, status)
            for changes in ({"id_number":"-.-"}, {"address":{"street":"San Jorge"}}, {"name":" "}):
                result, _ = request(port, "/api/agenda/save", {"party":dict(self.party, **changes)})
                self.assertEqual(result, 422)
            self.assertEqual(request(port, "/api/agenda/search", method="GET")[0], 404)
        self.assertFalse(self.path.exists())

    def test_document_search_and_complete_paged_collection(self):
        first = save(self.path, dict(self.party, id_number="20.555.123"))["party"]
        company = save(self.path, dict(self.party, id_type="CUIT", id_number="30-20555123-7"))["party"]
        for index in range(30):
            save(self.path, dict(self.party, name=f"Entidad {index:02}", id_number=str(4000 + index)))
        self.assertEqual(search(self.path, "20.555.123")[0], first)
        self.assertEqual(search(self.path, "c.u.i.t. 30-20555"), [company])
        self.assertEqual(search(self.path, "dni: 555 12"), [first])
        self.assertEqual({p["id"] for p in search(self.path, "555")}, {first["id"], company["id"]})
        self.assertEqual(len(search(self.path, "entidad")), 8)
        page = list_parties(self.path, "")
        rest = list_parties(self.path, "", page["page_size"])
        self.assertEqual((page["total"], rest["total"]), (32, 32))
        self.assertEqual(len({p["id"] for p in page["parties"] + rest["parties"]}), 32)
        self.assertEqual(list_parties(self.path, "entidad 29")["parties"][0]["id_number"], "4.029")

    def test_combined_tax_identity_keeps_legacy_collisions_and_dni_separate(self):
        digits = "00123456789"
        first = save(self.path, dict(self.party, id_type="CUIT", id_number=digits))["party"]
        legacy = dict(self.party, id_type="CUIL", id_number="00-12345678-9", name="Otra ficha histórica")
        with closing(sqlite3.connect(self.path)) as db, db:
            second_id = db.execute("INSERT INTO parties (id_type,document_key,name_key,profile) VALUES (?,?,?,?)",
                                   ("CUIL", digits, "otra ficha historica", json.dumps(legacy))).lastrowid
        with local_panel(self.path) as port:
            for prefix in ("CUIT", "CUIL", "CUIT/CUIL"):
                hits = search(self.path, prefix + " " + digits)
                self.assertEqual({p["id"] for p in hits}, {first["id"], second_id})
            self.assertEqual(search(self.path, "DNI " + digits), [])
            second = next(p for p in hits if p["id"] == second_id)
            candidate = dict(legacy, name="Nombre actualizado", id_type="CUIT/CUIL", id_number=digits)
            status, _ = request(port, "/api/agenda/save", {"party":candidate})
            self.assertEqual(status, 409)
            status, result = request(port, "/api/agenda/update", {"id":second_id, "revision":second["revision"], "party":candidate})
            self.assertEqual(status, 200)
            edited = result["party"]
            self.assertEqual((edited["id"], edited["id_type"], edited["id_number"]), (second_id, "CUIL", "00-12345678-9"))
            self.assertEqual(next(p for p in search(self.path, digits) if p["id"] == first["id"]), first)
            candidate["id_number"] = "30123456782"
            status, result = request(port, "/api/agenda/update", {"id":second_id, "revision":edited["revision"], "party":candidate})
            self.assertEqual(status, 200)
            self.assertEqual(result["party"]["id_type"], "CUIT/CUIL")
            self.assertEqual([p["id"] for p in search(self.path, "CUIL " + digits)], [first["id"]])
            self.assertEqual(search(self.path, "CUIT 30-12345678-2")[0]["id"], second_id)
            status, _ = request(port, "/api/agenda/save", {"create_only":True, "party":dict(candidate, id_type="CUIL")})
            self.assertEqual(status, 409)

    def test_id_length_on_local_save_and_update(self):
        with local_panel(self.path) as port:
            for kind, value in (("CUIT/CUIL", "1234"), ("CUIL", "123456789012"), ("DNI", "00123456789"), ("DNI", "doce")):
                with self.subTest(kind=kind, value=value):
                    status, _ = request(port, "/api/agenda/save", {"party":dict(self.party, id_type=kind, id_number=value)})
                    self.assertEqual(status, 422)
            status, result = request(port, "/api/agenda/save", {"party":dict(self.party, id_type="DNI", id_number="00000001")})
            self.assertEqual((status, result["party"]["id_number"]), (200, "00.000.001"))
            status, _ = request(port, "/api/agenda/update", dict(result["party"], party=dict(self.party, id_number="111111111")))
            self.assertEqual(status, 422)
            self.assertEqual(search(self.path, "00000001"), [result["party"]])

    def test_edits_keep_id_and_conflicts_leave_both_records_intact(self):
        first = save(self.path, self.party)["party"]
        other = save(self.path, dict(self.party, id_number="999"))["party"]
        with local_panel(self.path) as port:
            def edit(party, revision):
                return request(port, "/api/agenda/update", {"id":first["id"], "revision":revision, "party":party})
            status, _ = edit(dict(self.party, id_number="9-9-9"), first["revision"])
            self.assertEqual(status, 409)
            self.assertEqual(search(self.path, "00000111"), [first])
            self.assertEqual(search(self.path, "999"), [other])
            changed = dict(self.party, name="Nueva denominación", id_type="CUIT", id_number="30-00000111-2")
            status, result = edit(changed, first["revision"])
            self.assertEqual(status, 200)
            self.assertEqual(result["party"]["id"], first["id"])
            self.assertNotEqual(result["party"]["revision"], first["revision"])
            status, _ = edit(self.party, first["revision"])
            self.assertEqual(status, 409)
            status, _ = request(port, "/api/agenda/save", {"create_only":True, "party":changed})
            self.assertEqual(status, 409)
            status, retry = request(port, "/api/agenda/save", {"party":changed})
            self.assertEqual((status, retry["created"]), (200, False))
            status, _ = request(port, "/api/agenda/save", {"party":dict(changed, name="Carga anterior")})
            self.assertEqual(status, 409)
            self.assertEqual(search(self.path, "cuit 30-00000111-2"), [result["party"]])
            self.assertEqual(search(self.path, "999"), [other])
            for path in ("/api/agenda/list", "/api/agenda/update"):
                self.assertEqual(request(port, path, {}, origin=False)[0], 403)

    def test_older_profile_without_an_address_is_editable(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE parties (id INTEGER PRIMARY KEY, id_type TEXT NOT NULL, document_key TEXT NOT NULL,
                       name_key TEXT NOT NULL, profile TEXT NOT NULL, UNIQUE (id_type, document_key))""")
            old = {k:v for k, v in self.party.items() if k != "address"}
            db.execute("INSERT INTO parties VALUES (7, 'DNI', '00000111', 'alamo de prueba', ?)", (json.dumps(old),))
        with local_panel(self.path) as port:
            status, page = request(port, "/api/agenda/list", {})
            self.assertEqual(status, 200)
            party, = page["parties"]
            self.assertEqual((party["id"], party["address"]), (7, {}))
            changed = dict(old, name="Nombre corregido", address={})
            status, result = request(port, "/api/agenda/update", {"id":7, "revision":party["revision"], "party":changed})
            self.assertEqual(status, 200)
            self.assertEqual(result["party"]["address"], {})
            self.assertEqual(result["party"]["id"], 7)
