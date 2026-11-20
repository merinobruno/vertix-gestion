import io
import json
import unittest
from datetime import date
from importlib.util import find_spec
from zipfile import ZipFile

from test_annex import workbook
from test_word_models import operation


async def request(path, method="GET", *, body=b"", content_type="application/json", origin="https://panel.example"):
    from app import app

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": method, "scheme": "https", "path": path, "query_string": b"",
             "headers": [(b"host", b"panel.example"), (b"origin", origin.encode()), (b"content-type", content_type.encode())]}
    events = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(event):
        events.append(event)

    await app(scope, receive, send)
    start, *response = events
    return start["status"], dict(start["headers"]), [event["body"] for event in response]


@unittest.skipUnless(find_spec("fastapi"), "Install the hosting dependencies from requirements.txt")
class HostedPanel(unittest.IsolatedAsyncioTestCase):
    async def test_large_zip_arrives_in_chunks_and_opens(self):
        status, headers, chunks = await request("/api/generate", "POST", body=json.dumps(operation()).encode())
        self.assertEqual(status, 200)
        self.assertNotIn(b"content-length", headers)
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertIn(b".zip", headers[b"content-disposition"])
        self.assertLessEqual(max(map(len, chunks)), 64 * 1024)
        data = b"".join(chunks)
        self.assertGreater(len(data), 4.5 * 1024 * 1024)
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.namelist(), ["mutuo.docx", "pagare-01-de-02.docx", "pagare-02-de-02.docx", "cesion.docx"])

    async def test_excel_upload_and_hosted_formats(self):
        status, _, chunks = await request("/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(b"".join(chunks))["pdf_export"])
        plan = workbook([[1, date(2034, 2, 3), 300, 20, 4.2, 324.2, None, 356.62]])
        status, _, chunks = await request("/api/anexo", "POST", body=plan, content_type="application/octet-stream")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(b"".join(chunks))["installments"][0]["note_percent"], "10")
        for fmt in ("pdf", "both"):
            status, _, _ = await request("/api/generate", "POST", body=json.dumps({"format": fmt}).encode())
            self.assertEqual(status, 422)

    async def test_checks_origin_content_type_and_body_size_before_generation(self):
        for expected, options in [(403, {"origin": "https://otro.example"}), (415, {"content_type": "text/plain"}),
                                  (413, {"body": b" " * 250001}), (422, {"body": b"[]"})]:
            with self.subTest(status=expected):
                status, _, chunks = await request("/api/generate", "POST", **options)
                self.assertEqual(status, expected)
                self.assertIn("error", json.loads(b"".join(chunks)))
