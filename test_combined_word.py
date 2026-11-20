import io
import unittest
from zipfile import ZipFile

from documents import generate, parse, word_documents
from test_word_models import body, operation, paragraph_text
from word_templates import P, W, W14


def combined(data):
    files = dict(word_documents(parse(dict(data, combined_document=True))))
    filename, data = next(iter(files.items()))
    assert len(files) == 1 and filename.startswith("documento-unico-")
    return data


class CombinedWord(unittest.TestCase):
    def test_mixed_preserves_text_order_and_each_original_page_setup(self):
        for choice in (["mutuo", "pagares", "cesion"], ["mutuo", "cesion"], ["pagares", "cesion"], ["mutuo", "pagares"]):
            with self.subTest(documents=choice):
                data = dict(operation(), documents=choice)
                individual = list(dict(word_documents(parse(data))).values())
                result = body(combined(data))
                lines = [paragraph_text(p) for source in individual for p in body(source).iter(P)]
                self.assertEqual([paragraph_text(p) for p in result.iter(P)], lines)
                sizes = [(s.get(f"{{{W}}}w"),s.get(f"{{{W}}}h")) for s in result.iter(f"{{{W}}}pgSz")]
                expected = [body(doc).find(f"{{{W}}}sectPr/{{{W}}}pgSz") for doc in individual]
                self.assertEqual(sizes, [(s.get(f"{{{W}}}w"),s.get(f"{{{W}}}h")) for s in expected])
                self.assertFalse(list(result.iter(f"{{{W}}}altChunk")))

    def test_one_selected_piece_retains_its_original_package(self):
        data = dict(operation(), documents=["cesion"])
        name, mime, merged = generate(dict(data, combined_document=True))
        self.assertTrue(name.endswith(".docx"))
        self.assertIn("wordprocessingml", mime)
        standalone = dict(word_documents(parse(data)))["cesion"]
        with ZipFile(io.BytesIO(standalone)) as a, ZipFile(io.BytesIO(merged)) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for n in a.namelist():
                self.assertEqual(a.read(n), b.read(n))

    def test_sixty_notes_have_unique_ids_and_one_set_of_fonts(self):
        data = dict(operation(), documents=["pagares"], installments_mode="manual",
                    installments=[{"due":"2027-01-15","amount":str(i+1)} for i in range(60)])
        result = combined(data)
        doc = body(result)
        self.assertEqual(len(list(doc.iter(f"{{{W}}}sectPr"))), 60)
        self.assertIn("NÚMERO (60) DE (60)", paragraph_text(doc))
        ids = [p.get(f"{{{W14}}}paraId") for p in doc.iter(P) if p.get(f"{{{W14}}}paraId")]
        self.assertEqual(len(ids), len(set(ids)))
        with ZipFile(io.BytesIO(result)) as package:
            self.assertEqual(sum(p.startswith("word/fonts/") for p in package.namelist()), 8)
            self.assertLess(len(result), 3_000_000)

    def test_demand_and_assignment_remain_two_a4_sections(self):
        data = dict(operation(), documents=["pagares","cesion"], note_mode="demand", currency="USD",
                    demand_payee="Ada Ejemplo", demand_id="00.000.001", demand_amount="4225.23",
                    demand_consideration="una operación de prueba")
        doc = body(combined(data))
        self.assertEqual([p.get(f"{{{W}}}w") for p in doc.iter(f"{{{W}}}pgSz")], ["11906"] * 2)
        self.assertIn("Pagadero en la Ciudad de Buenos Aires.", paragraph_text(doc))
        self.assertIn("Cesionario", paragraph_text(doc))


if __name__ == "__main__":
    unittest.main()
