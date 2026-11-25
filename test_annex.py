import io
import unittest
from datetime import date

from openpyxl import Workbook

from annex import import_annex
from documents import parse, word_documents
from test_word_models import body, example_address, operation, paragraph_text
from word_templates import W


def workbook(rows, *, note_header="PAGARÉ"):
    book = Workbook()
    sheet = book.active
    sheet.append(["Importe", 1612000])
    sheet.append(["CUOTA", "FECHA", "SALDO", "CAPITAL", "SALDO CAPITAL", "INTERES", "IVA", "CUOTA"])
    sheet.append([1, date(2035, 3, 11), 400, 200, 200, 20, 4.2, 224.2])
    sheet.append(["ANEXO I"])
    sheet.append(["CUOTA", "FECHA", "CAPITAL", "INTERÉS", "IVA", "CUOTA", None, note_header])
    for row in rows:
        sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def bank_operation():
    data = operation()
    data.update(documents=["mutuo", "pagares"], payment_method="bank", guarantor_enabled=True,
                issue_date="2035-03-12", net="1612000",
                guarantor={"name":"Garantía Ejemplo", "id_type":"DNI", "id_number":"00.000.003", "address":example_address("Calle de Prueba", "3")},
                bank_name="Ejemplo", account_type="Cta. Cte.", account_number="00123", cbu="0"*22,
                lender_rep_name="Representante Ejemplo", lender_rep_id="00.000.009",
                installments=[{"due": "2035-04-12", "amount":"829383.67", "capital":"806000.00",
                               "interest":"19325.35", "vat":"4058.32", "note_percent":"50"},
                              {"due":"2035-05-12", "amount":"806000.00", "capital":"806000.00",
                               "interest":"0", "vat":"0", "note_percent":"0"}])
    return data


class Annex(unittest.TestCase):
    def test_transfer_loan_names_the_lender_and_its_own_cuit_separately_from_the_account_holder(self):
        data = dict(bank_operation(), documents=["mutuo"], account_holder="Tercero Ejemplo", account_id="30-00000002-0")
        contract = body(dict(word_documents(parse(data)))["mutuo"])
        self.assertIn("Entidad Ficticia, CUIT 00-00000001-0, con domicilio", paragraph_text(contract[1]))
        self.assertNotIn("30-00000002-0", paragraph_text(contract[1]))

    def test_imported_due_date_is_checked_against_the_contract(self):
        data = bank_operation()
        data["installments"] = import_annex(workbook([[1, "11/03/2035", 100, 0, 0, 100]]))["installments"]
        with self.assertRaisesRegex(ValueError, "cuota 1.*fecha de emisión"):
            parse(data)
        data["issue_date"] = "2035-03-11"
        self.assertEqual(parse(data)["installments"][0]["due"], date(2035, 3, 11))

    def test_only_the_annex_values_are_imported_to_cents(self):
        data = workbook([
            [1, date(2035,4,12), 806000, 19325.3487, 4058.323227, 829383.671927, None, 1244075.5078905],
            [2, "12/05/2035", 806000, 0, 0, 806000, None, 806000],
            [None, None, 1612000, 19325.35, 4058.32, 1635383.67],
            [None, "NETO A LIQUIDAR", 1580000]])
        result = import_annex(data)
        self.assertEqual(result["principal"], "1612000.00")
        first, second = result["installments"]
        self.assertEqual(first, {"due":"2035-04-12", "amount":"829383.67", "capital":"806000.00",
                                 "interest":"19325.35", "vat":"4058.32", "note_percent":"50"})
        self.assertEqual(second["due"], "2035-05-12")
        self.assertEqual(second["note_percent"], "0")

    def test_optional_blank_missing_cached_and_unrepresentable_note(self):
        rows = [[1,"2035-06-12",1000,0,0,1000], [2,"2035-07-12",1000,0,0,1000,None,"=F7*1.5"],
                [3,"2035-08-12",1000,0,0,1000,None,1099.99]]
        first, second, third = import_annex(workbook(rows))["installments"]
        self.assertEqual(first["note_percent"], "0")
        self.assertTrue(second["note_formula_missing"])
        self.assertEqual(second["note_percent"], "")
        self.assertEqual((third["note_percent"],third["reference_note"]), ("","1099.99"))
        self.assertEqual(import_annex(workbook(rows[:1], note_header=None))["installments"][0]["note_percent"], "0")

    def test_required_cache_and_gaps_are_rejected_without_partial_import(self):
        with self.assertRaisesRegex(ValueError, "CUOTA.*recalculá"):
            import_annex(workbook([[1, "12/6/2035", 100, 0, 0, "=SUM(C6:E6)"]]))
        with self.assertRaisesRegex(ValueError, "consecutivas"):
            import_annex(workbook([[1,"12/6/2035",100,0,0,100], [], [2,"12/7/2035",100,0,0,100]]))
        with self.assertRaisesRegex(ValueError, "xlsx"):
            import_annex(b"not an Excel file")

    def test_payment_amount_changes_only_the_corresponding_note(self):
        data = bank_operation()
        generated = dict(word_documents(parse(data)))
        contract = body(generated["mutuo"])
        contract_text = paragraph_text(contract)
        self.assertIn("por la suma de cada una de las cuotas", contract_text)
        self.assertIn("La Sra. Garantía Ejemplo, D. N. I.", contract_text)
        self.assertIn("las partes y el fiador se someten", contract_text)
        self.assertIn("Banco Ejemplo, Cta. Cte. 00123, CBU " + "0"*22, contract_text)
        table = contract.find(f"{{{W}}}tbl")
        rows = [[paragraph_text(c) for c in row] for row in table.findall(f"{{{W}}}tr")[2:]]
        self.assertEqual(rows[0], ["1","12/4/2035","806.000,00","19.325,35","4.058,32","829.383,67"])
        self.assertNotIn("1.244.075,51", contract_text)
        first = paragraph_text(body(generated["pagare-01-de-02"]))
        second = paragraph_text(body(generated["pagare-02-de-02"]))
        self.assertIn("un millon doscientos cuarenta y cuatro mil setenta y cinco con 51/100, ($1.244.075,51.-)", first)
        self.assertIn("constituye el número dos (2) de dos (2)", second)
        self.assertIn("$806.000,00", second)
        for pct, amount in [("30","1.078.198,77"),("100","1.658.767,34"),("12.5","933.056,63")]:
            with self.subTest(percent=pct):
                data["installments"][0]["note_percent"] = pct
                doc = dict(word_documents(parse(dict(data, documents=["pagares"]))))["pagare-01-de-02"]
                self.assertIn("$"+amount, paragraph_text(body(doc)))

    def test_literal_guarantee_and_jurisdiction_follow_the_switch(self):
        data = bank_operation()
        plain = paragraph_text(body(dict(word_documents(parse(dict(data, guarantor_enabled=False))))["mutuo"]))
        self.assertIn("6) A todos los efectos legales, las partes se someten", plain)
        self.assertNotIn("fiador", plain)
        cheque = dict(data, payment_method="cheques", issue_date=data["cheques"][0]["due"], documents=["mutuo","pagares","cesion"])
        cheque["cheques"][0]["note_percent"] = "50"
        output = dict(word_documents(parse(cheque)))
        self.assertIn("7) A todos los efectos legales, las partes y el fiador", paragraph_text(body(output["mutuo"])))
        self.assertIn("$78.000,00", paragraph_text(body(output["pagare-01-de-02"])))
        self.assertIn("$52.000,00", paragraph_text(body(output["cesion"])))
        self.assertNotIn("$78.000,00", paragraph_text(body(output["cesion"])))


if __name__ == "__main__":
    unittest.main()
