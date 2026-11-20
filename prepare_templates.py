"""Rebuild anonymized layout masters from the supplied Word documents.

The legacy assignment must first be saved as DOCX by Word itself. Character
offsets are bound only to the two specific sources, before any input from the
form is available. The exported files never include a copy of the filled example.
"""

import argparse
import hashlib
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from word_templates import MARKERS, T, TEMPLATES, TR, W, overwrite, xml_bytes


def string(node):
    return "".join(t.text or "" for t in node.iter(T))


def mark(node, spans):
    overwrite(node, [(a, b, MARKERS[name] * (b-a)) for a, b, name in spans])


def date_fields(text, prefix, start=0):
    match = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", text[start:])
    if not match:
        raise ValueError("Unexpected source date format")
    return [(start + match.start(i), start + match.end(i), prefix + key)
            for i, key in enumerate(("_day", "_month", "_year"), 1)]


def letter_date(p):
    source = string(p)
    mark(p, [(0, source.index(", "), "place"), *date_fields(source, "issue")])


def loan_master(body):
    mark(body[1], [(6, 13, "lender_name"), (31, 92, "lender_address"),
                  (146, 153, "borrower_intro"), (153, 170, "borrower_name"),
                  (172, 178, "borrower_id_kind"), (181, 191, "borrower_id"),
                  (215, 256, "borrower_address"), (256, 279, "representation"),
                  (279, 290, "represented_name")])
    mark(body[2], [(54, 67, "amount_digits"), (88, 155, "amount_words"), (160, 162, "cents")])
    mark(body[3], [(90, 91, "penalty")])
    mark(body[6], [(86, 87, "count"), (89, 92, "count_words")])
    mark(body[8], [(93, 124, "place"), (132, 134, "issue_day"),
                  (151, 160, "issue_month"), (169, 173, "issue_year")])
    table = body[10]
    for row in table.findall(TR)[2:4]:
        for index, cell in enumerate(row.findall(f"{{{W}}}tc")):
            p = cell.find(f"{{{W}}}p")
            if index == 2:
                mark(p, [(0, 1, "symbol"), (1, len(string(p)), "amount_digits")])
            else:
                mark(p, [(0, len(string(p)), ("cheque_number", "short_due")[index])])
    for label, issue, payment, waiver, closing in [(14, 15, 16, 17, 19), (23, 24, 25, 27, 29)]:
        mark(body[label], [(8, 9, "number"), (15, 16, "count")])
        letter_date(body[issue])
        p = body[payment]
        text = string(p)
        payee = re.search(r"ilimitada a (.+?), (CUIT) ([\d-]+), o a su orden", text)
        amount = re.search(r"cantidad de pesos (.+?) con (\d\d)/100, \(\$([\d.,]+)\.\-\)", text)
        address = re.search(r"pagadero en calle (.+)\.$", text)
        mark(p, [*date_fields(text, "due"),
                 (payee.start(1), payee.end(1), "beneficiary_name"),
                 (payee.start(2), payee.end(2), "beneficiary_id_kind"),
                 (payee.start(3), payee.end(3), "beneficiary_id"),
                 (amount.start(1), amount.end(1), "amount_words"),
                 (amount.start(2), amount.end(2), "cents"),
                 (amount.start(3), amount.end(3), "amount_digits"),
                 (address.start(1), address.end(1), "payment_address")])
        # The final period belongs to the source's abbreviated company name;
        # when the submitted name lacks punctuation, its sentence still ends here.
        mark(body[waiver], [(415, 423, "beneficiary_waiver")])
        p = body[closing]
        text = string(p)
        mark(p, [(45, 48, "number_words"), (50, 51, "body_number"), (56, 59, "count_words"),
                 (61, 62, "count"), (92, 93, "count"), *date_fields(text, "start"),
                 *date_fields(text, "end", text.index(" hasta el "))])


def assignment_master(body):
    letter_date(body[0])
    mark(body[3], [(71, len(string(body[3]))-1, "beneficiary_name")])
    mark(body[4], [(0, 4, "beneficiary_id_kind"), (5, 18, "beneficiary_id")])
    for row in list(body)[7:9]:
        texts = [t for t in row.iter(T) if t.text]
        if len(texts) != 4:
            raise ValueError("Unexpected assignment row")
        lengths = [len(t.text) for t in texts]
        n, b, d, a = lengths
        mark(row, [(0, n, "cheque_number"), (n, n+b, "bank"), (n+b, n+b+d, "short_due"),
                   (n+b+d, n+b+d+1, "symbol"), (n+b+d+1, n+b+d+a, "amount_digits")])


def demand_master(body):
    mark(body[0], [(4, 14, "symbol"), (15, 22, "amount_digits")])
    mark(body[6], [(18, 30, "place"), (31, 36, "demand_day"), (40, 51, "issue_month"), (55, 60, "issue_year")])
    # Keep the literal underscores around the wording of the amount and the
    # actual underlined tab stops at the signature. Only the blanks are filled.
    mark(body[7], [(61, 88, "demand_payee"), (98, 119, "beneficiary_id"), (175, 189, "demand_words"),
                  (226, 260, "demand_consideration")])


def clean_part(name, data):
    if name in ("[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels"):
        root = etree.fromstring(data)
        for item in list(root):
            target = item.get("PartName", item.get("Target", "")).lstrip("./")
            if target.startswith(("docProps/", "customXml/")) or item.get("Type", "").endswith("/customXml"):
                root.remove(item)
        return xml_bytes(root)
    if name == "word/settings.xml":
        root = etree.fromstring(data)
        for node in list(root):
            if etree.QName(node).localname in {"docId", "docVars", "attachedTemplate"}:
                root.remove(node)
        return xml_bytes(root)
    return data


def compile_template(source, dest, expected_body, prepare, *, remove_image=False):
    with ZipFile(source) as original, ZipFile(dest, "w", ZIP_DEFLATED) as output:
        for name in original.namelist():
            if name.startswith(("docProps/", "customXml/")) or (remove_image and name.startswith("word/media/")):
                continue
            data = original.read(name)
            if name == "word/document.xml":
                if hashlib.sha256(data).hexdigest() != expected_body:
                    raise ValueError("This source differs from the reviewed Word layout")
                root = etree.fromstring(data)
                body = root.find(f"{{{W}}}body")
                prepare(body)
                for node in body.iter(f"{{{W}}}smartTagPr"):
                    node.getparent().remove(node)
                data = xml_bytes(root)
            data = clean_part(name, data)
            if remove_image and name == "word/_rels/document.xml.rels":
                rels = etree.fromstring(data)
                for rel in list(rels):
                    if rel.get("Type", "").endswith("/image"):
                        rels.remove(rel)
                data = xml_bytes(rels)
            output.writestr(name, data)


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("loan", type=Path)
    args.add_argument("assignment", type=Path)
    args.add_argument("demand_note", type=Path)
    paths = args.parse_args()
    TEMPLATES.mkdir(exist_ok=True)
    compile_template(paths.loan, TEMPLATES/"loan-and-notes.docx",
                     "36e4e62372f241b5c677d6d94e351b3c6d114bdf0589959fe0de2f6986ac6a01", loan_master)
    compile_template(paths.assignment, TEMPLATES/"assignment.docx",
                     "27be782a4249f4e02c93ddab3db449eeec5a07486e66a3b56508bff422133c16", assignment_master)
    compile_template(paths.demand_note, TEMPLATES/"demand-note.docx",
                     "92560706edcb11e28d68fe07ec9f053db0f7520a061210e1708954220fe70f7b", demand_master)
