import json
import os
import secrets
import unittest
from importlib.util import find_spec
from unittest.mock import patch

from test_hosted import request


@unittest.skipUnless(find_spec("fastapi"), "Install the hosting dependencies from requirements.txt")
class SharedAgenda(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.machine = secrets.token_hex(32)
        self.enterContext(patch.dict(os.environ, {
            "VERTIX_AGENDA_URL": "  https://fixture-testing.convex.site/  ",
            "VERTIX_AGENDA_SERVICE_KEY": self.machine + "\n",
        }))

    async def post(self, action, payload=None, **kwargs):
        return await request("/api/agenda/" + action, "POST", body=json.dumps(payload or {}).encode(), **kwargs)

    async def test_same_origin_panels_use_the_agenda_without_a_login(self):
        import shared_agenda
        page = {"parties":[], "total":0, "page_size":25}
        forward = self.enterContext(patch.object(shared_agenda, "forward", return_value=page))
        for action in ("access", "login", "session", "logout"):
            self.assertEqual((await self.post(action))[0], 404)
        for origin in (None, "null", "https://other.example"):
            self.assertEqual((await self.post("list", origin=origin))[0], 403)
        forward.assert_not_called()
        status, headers, parts = await self.post("list")
        self.assertEqual(status, 200)
        self.assertNotIn(b"set-cookie", headers)
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual(json.loads(b"".join(parts)), page)
        self.assertEqual(forward.call_args.args[0].service_key, self.machine)

    async def test_independent_requests_use_the_same_directory(self):
        import shared_agenda
        forward = self.enterContext(patch.object(shared_agenda, "forward", return_value={"parties": []}))
        status, _, _ = await self.post("search", {"query":"CUIL 00.123.456"})
        self.assertEqual(status, 200)
        settings, action, data = forward.call_args.args
        self.assertEqual(settings.service_key, self.machine)
        self.assertEqual(action, "search")
        self.assertEqual((data["digits"], data["kind"]), ("00123456", "CUIT/CUIL"))
        party = {"name":"Ficción", "id_type":"DNI", "id_number":"00123"}
        self.assertEqual((await self.post("save", {"party":party}))[0], 200)
        record = forward.call_args.args[2]["record"]
        self.assertEqual(record["nameKey"], "ficcion")
        self.assertEqual(record["profile"]["id_number"], "00.123")
        self.assertNotIn("address", record["profile"])
        self.assertEqual((await self.post("save", {"party":{**party,"address":{"street":"Calle"}}}))[0], 422)
        self.assertEqual((await self.post("list", {"offset":-1}))[0], 422)
        self.assertEqual((await self.post("import", {"parties":[party]*26}))[0], 422)
        forward.return_value = {"created":1,"existing":0,"conflicts":0}
        status, _, parts = await self.post("import", {"parties":[party,{}]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(b"".join(parts))["invalid"], 1)
        records = forward.call_args.args[2]["records"]
        self.assertEqual(len(records), 1)
        self.assertRegex(records[0]["fingerprint"], r"^[a-f0-9]{64}$")

    async def test_invalid_configuration_fails_closed_without_sending_data(self):
        import shared_agenda
        forward = self.enterContext(patch.object(shared_agenda, "forward"))
        for variable in ("VERTIX_AGENDA_URL", "VERTIX_AGENDA_SERVICE_KEY"):
            with patch.dict(os.environ, {variable:"invalid"}):
                self.assertEqual((await self.post("list"))[0], 503)
                self.assertEqual((await self.post("search"))[0], 503)
        forward.assert_not_called()
