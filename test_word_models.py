import io
import re
import unittest
from copy import deepcopy
from zipfile import ZipFile

from lxml import etree

from documents import parse, word_documents, word_pieces
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


def example_address(street, number):
    return {"street": street, "number": number, "locality": "Ciudad de Prueba", "province": "Neuquén"}


def operation():
    return {
        "documents": ["mutuo", "pagares", "cesion"], "format": "docx", "currency": "ARS",
        "place": "Ciudad de Prueba", "issue_date": "2026-11-19", "net": "90000", "penalty": "3",
        "client": {"name": "Comercial de Ejemplo", "id_type": "CUIT",
                   "id_number": "00-00000000-0", "address": example_address("Calle de Prueba", "120")},
        "entity": {"name": "Entidad Ficticia", "id_type": "CUIT",
                   "id_number": "00-00000001-0", "address": example_address("Avenida de Prueba", "40")},
        "cheques": [{"number": "PRUEBA-1", "bank": "Banco Ficticio", "due": "2026-12-19", "amount": "52000"},
                    {"number": "PRUEBA-2", "bank": "Banco Ficticio", "due": "2027-01-19", "amount": "54000.23"}],
    }


class WordModels(unittest.TestCase):
    def test_cheque_loan_alone_prints_and_requires_the_lenders_own_id(self):
        data = dict(operation(), documents=["mutuo"], net="1.234,56", penalty="2,50")
        data["entity"].update(id_number="30712345678")
        contract = body(dict(word_documents(parse(data)))["mutuo"])
        self.assertIn("Entidad Ficticia, CUIT 30-71234567-8, con domicilio", paragraph_text(contract[1]))
        self.assertIn("1.234,56", paragraph_text(contract))
        for invalid in ("12.34,56", "1,234", "1.23.456"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "dos decimales"):
                parse(dict(data, net=invalid))
        del data["entity"]["id_number"]
        with self.assertRaisesRegex(ValueError, "identificación de la parte mutuante"):
            parse(data)

    def test_combined_and_known_tax_labels_preserve_all_digits_in_the_generated_model(self):
        for kind in ("CUIT", "CUIL", "CUIT/CUIL"):
            with self.subTest(kind=kind):
                data = operation()
                data["entity"].update(id_type=kind, id_number="00123456789")
                docs = dict(word_documents(parse(data)))
                self.assertIn(kind + " 00-12345678-9", paragraph_text(body(docs["pagare-01-de-02"])))
        data = operation()
        data["client"].update(id_type="DNI", id_number="00123456")
        self.assertEqual(parse(data)["client"]["id_number"], "00.123.456")
        for kind, number in (("CUIT/CUIL", "12345678"), ("CUIT", "201234567890"), ("DNI", "20123456789")):
            with self.subTest(kind=kind, number=number), self.assertRaisesRegex(ValueError, "dígitos"):
                data["client"].update(id_type=kind, id_number=number)
                parse(data)

    def test_composed_addresses_in_contract_and_separate_payment_note(self):
        data = operation()
        data["client"]["address"].update(floor=" 3 ", apartment="B")
        data.update(separate_payment=True, payment_address=example_address("Calle de Pago", "S/N"))
        docs = dict(word_documents(parse(data)))
        contract = paragraph_text(body(docs["mutuo"]))
        self.assertIn("Calle de Prueba 120, piso 3, depto. B, Ciudad de Prueba, Neuquén", contract)
        self.assertIn("Avenida de Prueba 40, Ciudad de Prueba, Neuquén", contract)
        self.assertIn("Calle de Pago S/N, Ciudad de Prueba, Neuquén", paragraph_text(body(docs["pagare-01-de-02"])))

    def test_required_and_legacy_addresses(self):
        data = operation()
        for key in ("street", "number", "locality", "province"):
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError, "domicilio de la parte mutuaria"):
                data["client"]["address"] = dict(example_address("San Jorge", "111"), **{key:" "})
                parse(data)
        for line in ("San Jorge 111", "San Jorge 111, Neuquén", "San Jorge 111, piso 3, Neuquén"):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, "calle, altura, localidad y provincia"):
                data["client"]["address"] = line
                parse(data)
        complete = "San Jorge 111, piso 2, depto. A, Villa Ejemplo, Neuquén"
        data["client"]["address"] = complete
        self.assertEqual(parse(data)["client"]["address"], complete)
        data["client"]["address"] = example_address("x"*230, "10")
        with self.assertRaisesRegex(ValueError, "240 caracteres"):
            parse(data)

    def test_only_the_address_used_by_the_model_is_required(self):
        data = operation()
        data.update(represented=True, representative=data["client"].copy())
        del data["client"]["address"]
        self.assertIn("Calle de Prueba 120", parse(data)["representative"]["address"])
        beneficiary = dict(data["entity"], address={})
        notes = dict(data, documents=["pagares"], different_beneficiary=True, beneficiary=beneficiary)
        with self.assertRaisesRegex(ValueError, "domicilio de la parte beneficiaria"):
            parse(notes)
        notes.update(separate_payment=True, payment_address=data["entity"]["address"])
        self.assertIn("Avenida de Prueba 40", parse(notes)["payment_address"])
        del notes["payment_address"]["locality"]
        with self.assertRaisesRegex(ValueError, "localidad del domicilio de pago"):
            parse(notes)

    def test_each_installment_can_mature_on_or_after_issue(self):
        for mode in ("cheques", "manual"):
            with self.subTest(mode=mode):
                data = operation()
                data.update(installments_mode=mode, issue_date="2026-12-17")
                rows = data["cheques"] if mode == "cheques" else [dict(row) for row in data["cheques"]]
                data["installments"] = rows
                rows[1]["due"] = "2026-12-15"
                with self.assertRaisesRegex(ValueError, "cuota 2.*fecha de emisión"):
                    parse(data)
                rows[1]["due"] = data["issue_date"]
                self.assertEqual(parse(data)["installments"][1]["due"].isoformat(), data["issue_date"])
        data = dict(operation(), documents=["cesion"])
        data["cheques"][0]["due"] = "2020-01-01"
        self.assertNotIn("installments", parse(data))

    def assert_source_layout(self, output, template, indexes=None):
        doc = body(output)
        original = etree.fromstring(package(template)["word/document.xml"]).find(f"{{{W}}}body")
        for index, node in enumerate(list(original)):
            if indexes is not None and index not in indexes and node.tag != f"{{{W}}}sectPr":
                original.remove(node)
        for highlight in original.xpath(".//w:highlight[@w:val='yellow']", namespaces={"w": W}):
            highlight.getparent().remove(highlight)
        self.assertEqual(layout(doc), layout(original))
        for reference, generated in zip(original.iter(P), doc.iter(P), strict=True):
            source, text = paragraph_text(reference), paragraph_text(generated)
            self.assertIsNotNone(re.fullmatch(legal_pattern(source), text))
            self.assertIsNone(TOKEN.search(text))
        with ZipFile(io.BytesIO(output)) as file:
            self.assertFalse(any(p.startswith(("customXml/", "docProps/")) for p in file.namelist()))

    def test_source_highlights_can_be_restored_after_a_clean_export(self):
        _, piece = next(word_pieces(parse(operation())))
        source = piece.document.find(f"{{{W}}}body")
        expected = etree.tostring(source, method="c14n")
        yellow = ".//w:highlight[@w:val='yellow']"
        self.assertTrue(source.xpath(yellow, namespaces={"w": W}))
        self.assertFalse(body(piece.to_docx()).xpath(yellow, namespaces={"w": W}))
        restored = body(piece.to_docx(include_yellow_highlights=True))
        self.assertEqual(etree.tostring(restored, method="c14n"), expected)

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
