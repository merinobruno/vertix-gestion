import io
import re
import unittest
from copy import deepcopy
from zipfile import ZipFile

from lxml import etree

from documents import parse, word_documents
from word_templates import P, T, TOKEN, W, XML_SPACE, package


def body(data):
    with ZipFile(io.BytesIO(data)) as file:
        return etree.fromstring(file.read("word/document.xml")).find(f"{{{W}}}body")


def paragraph_text(p):
    return "".join(el.text or "" for el in p.iter(T))


def layout(element):
    element = deepcopy(element)
    for t in element.iter(T):
        t.text = None
        t.attrib.pop(XML_SPACE, None)
    return etree.tostring(element, method="c14n")


def legal_pattern(source):
    parts, position = [], 0
    for match in TOKEN.finditer(source):
        parts.extend((re.escape(source[position:match.start()]), ".*?"))
        position = match.end()
    return "".join(parts) + re.escape(source[position:])


def operation():
    return {
        "documents": ["mutuo", "pagares", "cesion"], "format": "docx", "currency": "ARS",
        "place": "Ciudad de Prueba", "issue_date": "2026-11-19", "net": "90000", "penalty": "3",
        "client": {"name": "Comercial de Ejemplo", "id_type": "CUIT",
                   "id_number": "00-00000000-0", "address": "Calle de Prueba 120"},
        "entity": {"name": "Entidad Ficticia", "id_type": "CUIT",
                   "id_number": "00-00000001-0", "address": "Avenida de Prueba 40"},
        "cheques": [{"number": "PRUEBA-1", "bank": "Banco Ficticio", "due": "2026-12-19", "amount": "52000"},
                    {"number": "PRUEBA-2", "bank": "Banco Ficticio", "due": "2027-01-19", "amount": "54000.23"}],
    }


class WordModels(unittest.TestCase):
    def assert_source_layout(self, output, template, indexes=None):
        doc = body(output)
        original = etree.fromstring(package(template)["word/document.xml"]).find(f"{{{W}}}body")
        for index, node in enumerate(list(original)):
            if indexes is not None and index not in indexes and node.tag != f"{{{W}}}sectPr":
                original.remove(node)
        self.assertEqual(layout(doc), layout(original))
        for reference, generated in zip(original.iter(P), doc.iter(P), strict=True):
            source, text = paragraph_text(reference), paragraph_text(generated)
            self.assertIsNotNone(re.fullmatch(legal_pattern(source), text))
            self.assertIsNone(TOKEN.search(text))
        with ZipFile(io.BytesIO(output)) as file:
            self.assertFalse(any(p.startswith(("customXml/", "docProps/")) for p in file.namelist()))

    def test_split_pages_and_assignment_keep_every_fixed_run_and_clause(self):
        documents = dict(word_documents(parse(operation())))
        self.assert_source_layout(documents["mutuo"], "loan-and-notes.docx", range(12))
        self.assert_source_layout(documents["pagare-01-de-02"], "loan-and-notes.docx", range(13, 21))
        self.assert_source_layout(documents["pagare-02-de-02"], "loan-and-notes.docx", range(22, 30))
        self.assert_source_layout(documents["cesion"], "assignment.docx")
        second = paragraph_text(body(documents["pagare-02-de-02"]))
        self.assertIn("NÚMERO (2) DE (2)", second)
        self.assertIn("constituye el número uno (1) de dos (2)", second)
        self.assertIn("emitidos el 19 de diciembre de 2026 hasta el 19 de enero de 2027", second)

    def test_extra_cheques_and_manual_installments_use_original_rows(self):
        data = operation()
        data["cheques"].append(dict(data["cheques"][1], number="PRUEBA-3"))
        data.update(installments_mode="manual", installments=[{"due": "2026-12-12", "amount": "120.34"}])
        docs = dict(word_documents(parse(data)))
        table = body(docs["mutuo"]).find(f"{{{W}}}tbl")
        cells = table.findall(f"{{{W}}}tr")[2].findall(f"{{{W}}}tc")
        self.assertEqual([paragraph_text(c) for c in cells], ["", "12/12/2026", "$120,34"])
        assignment = body(docs["cesion"])
        cheque_rows = assignment.find(f"{{{W}}}tbl").findall(f"{{{W}}}tr")
        self.assertEqual(len(cheque_rows), 16)  # header and fifteen printable slots
        self.assertEqual([paragraph_text(c) for c in cheque_rows[3].findall(f"{{{W}}}tc")],
                         ["PRUEBA-3", "Banco Ficticio", "19/01/2027", "$54.000,23"])

    def test_assignment_fixed_columns_and_handwriting_slots(self):
        ns = {"w": W}
        for count in (1, 15, 60):
            with self.subTest(count=count):
                data = dict(operation(), documents=["cesion"], currency="USD")
                bank = "Banco de Prueba con una denominación extensa para el formulario"
                data["cheques"] = [dict(data["cheques"][0], number=f"CH-{i}", bank=bank,
                                        amount="123456789012.34") for i in range(count)]
                table = body(dict(word_documents(parse(data)))["cesion"]).find("w:tbl", ns)
                rows = table.findall("w:tr", ns)
                self.assertEqual(len(rows), 1 + max(count, 15))
                self.assertEqual(table.xpath("w:tblPr/w:tblLayout/@w:type", namespaces=ns), ["fixed"])
                self.assertEqual(table.xpath("w:tblPr/w:tblBorders/*/@w:val", namespaces=ns), ["nil"] * 6)
                self.assertIsNotNone(rows[0].find("w:trPr/w:tblHeader", ns))
                widths = table.xpath("w:tblGrid/w:gridCol/@w:w", namespaces=ns)
                for i, row in enumerate(rows[:count+1]):
                    cells = row.findall("w:tc", ns)
                    expected = (["Cheque Nro.", "Banco", "Vto.", "Importe"] if i == 0 else
                                [f"CH-{i-1}", bank, "19/12/2026", "U$S123.456.789.012,34"])
                    self.assertEqual([paragraph_text(c) for c in cells], expected)
                    self.assertEqual(row.xpath("w:tc/w:tcPr/w:tcW/@w:w", namespaces=ns), widths)
                    self.assertEqual(row.xpath("w:tc/w:p/w:pPr/w:jc/@w:val", namespaces=ns),
                                     ["left", "left", "center", "right"])
                    self.assertIsNone(row.find(".//w:tab", ns))
                for row in rows[count+1:]:
                    self.assertEqual(paragraph_text(row), "")
                    self.assertIsNotNone(row.find(".//w:pBdr/w:bottom", ns))

    def test_demand_note_uses_its_own_complete_form_and_no_installments(self):
        data = {"documents": ["pagares"], "note_mode": "demand", "currency": "USD", "format": "docx",
                "place": "Ciudad de Prueba", "issue_date": "2026-11-19", "demand_amount": "4225.23",
                "demand_payee": "Ada Ejemplo", "demand_id": "00.000.001", "demand_consideration": "una operación de prueba"}
        docs = dict(word_documents(parse(data)))
        self.assertEqual(list(docs), ["pagare-a-la-vista"])
        self.assert_source_layout(docs["pagare-a-la-vista"], "demand-note.docx")
        text = paragraph_text(body(docs["pagare-a-la-vista"]))
        self.assertIn("Por U$S 4.225,23", text)
        self.assertIn("Ada Ejemplo D.N.I. N° 00.000.001", text)
        self.assertIn("dólares estadounidenses cuatro mil doscientos veinticinco con 23/100", text)
        self.assertIn("Pagadero en la Ciudad de Buenos Aires.", text)
        with self.assertRaises(ValueError):
            parse(dict(data, currency="ARS"))
        with self.assertRaises(ValueError):
            parse(dict(data, note_mode="installments"))
        with self.assertRaises(ValueError):
            parse(dict(operation(), guarantor_enabled=True))


if __name__ == "__main__":
    unittest.main()
