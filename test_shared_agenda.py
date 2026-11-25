import hashlib
import json
import os
import secrets
import time
import unittest
from importlib.util import find_spec
from unittest.mock import patch

from test_hosted import request


@unittest.skipUnless(find_spec("fastapi"), "Install the hosting dependencies from requirements.txt")
class SharedAgenda(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.code = secrets.token_hex(12).upper()
        self.machine = secrets.token_hex(32)
        self.enterContext(patch.dict(os.environ, {
            "VERTIX_AGENDA_URL": "  https://fixture-testing.convex.site/  ",
            "VERTIX_AGENDA_SERVICE_KEY": self.machine + "\n",
            "VERTIX_AGENDA_KEY_HASH": "\n" + hashlib.sha256(self.code.encode()).hexdigest(),
        }))

    async def post(self, action, payload=None, **kwargs):
        return await request("/api/agenda/" + action, "POST", body=json.dumps(payload or {}).encode(), **kwargs)

    async def login(self):
        status, headers, parts = await self.post("login", {"code": " " + self.code + " "})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(b"".join(parts))["authenticated"])
        cookie = headers[b"set-cookie"].decode()
        for attr in ("__Host-vertix-agenda=", "HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"):
            self.assertIn(attr, cookie)
        self.assertNotIn(self.machine, cookie)
        self.assertNotIn(self.code, cookie)
        return cookie.partition(";")[0]

    async def test_lock_origin_rotation_expiry_and_logout(self):
        import shared_agenda
        forward = self.enterContext(patch.object(shared_agenda, "forward"))
        for action in shared_agenda.DATA_ACTIONS:
            status, headers, _ = await self.post(action)
            self.assertEqual(status, 401)
            self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual((await self.post("login", {"code": "incorrect"}))[0], 401)
        for origin in (None, "null", "https://other.example"):
            self.assertEqual((await self.post("login", {"code": self.code}, origin=origin))[0], 403)
        first = await self.login()
        second = await self.login()
        self.assertNotEqual(first, second)
        self.assertEqual((await self.post("list", cookie=first[:-1]+"!"))[0], 401)
        self.assertEqual((await self.post("list", cookie=first, origin=None))[0], 403)
        with patch.object(shared_agenda.time, "time", return_value=time.time()+shared_agenda.LIFETIME+1):
            self.assertEqual((await self.post("list", cookie=first))[0], 401)
        with patch.dict(os.environ, {"VERTIX_AGENDA_KEY_HASH": secrets.token_hex(32)}):
            self.assertEqual((await self.post("search", cookie=first))[0], 401)
        with patch.dict(os.environ, {"VERTIX_AGENDA_SERVICE_KEY": secrets.token_hex(32)}):
            self.assertEqual((await self.post("list", cookie=second))[0], 401)
        status, headers, _ = await self.post("logout", cookie=first)
        self.assertEqual(status, 200)
        self.assertIn(b"Max-Age=0", headers[b"set-cookie"])
        forward.assert_not_called()

    async def test_validated_requests_use_one_gateway_for_two_sessions(self):
        import shared_agenda
        forward = self.enterContext(patch.object(shared_agenda, "forward", return_value={"parties": []}))
        a, b = await self.login(), await self.login()
        for cookie in (a, b):
            status, _, _ = await self.post("search", {"query":"CUIL 00.123.456"}, cookie=cookie)
            self.assertEqual(status, 200)
            settings, action, data = forward.call_args.args
            self.assertEqual(settings.service_key, self.machine)
            self.assertEqual(action, "search")
            self.assertEqual((data["digits"], data["kind"]), ("00123456", "CUIT/CUIL"))
        party = {"name":"Ficción", "id_type":"DNI", "id_number":"00123"}
        self.assertEqual((await self.post("save", {"party":party}, cookie=a))[0], 200)
        record = forward.call_args.args[2]["record"]
        self.assertEqual(record["nameKey"], "ficcion")
        self.assertEqual(record["profile"]["id_number"], "00.123")
        self.assertNotIn("address", record["profile"])
        self.assertEqual((await self.post("save", {"party":{**party,"address":{"street":"Calle"}}}, cookie=b))[0], 422)
        self.assertEqual((await self.post("list", {"offset":-1}, cookie=b))[0], 422)
        self.assertEqual((await self.post("import", {"parties":[party]*26}, cookie=b))[0], 422)
        forward.return_value = {"created":1,"existing":0,"conflicts":0}
        status, _, parts = await self.post("import", {"parties":[party,{}]}, cookie=b)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(b"".join(parts))["invalid"], 1)
        records = forward.call_args.args[2]["records"]
        self.assertEqual(len(records), 1)
        self.assertRegex(records[0]["fingerprint"], r"^[a-f0-9]{64}$")

    async def test_invalid_configuration_fails_closed_without_sending_data(self):
        import shared_agenda
        with patch.dict(os.environ, {"VERTIX_AGENDA_URL":"https://bad.example/path"}), patch.object(shared_agenda, "forward") as forward:
            status, _, parts = await self.post("session")
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(b"".join(parts))["configured"])
            self.assertEqual((await self.post("login", {"code":self.code}))[0], 503)
            self.assertEqual((await self.post("search"))[0], 401)
            forward.assert_not_called()
