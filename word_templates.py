"""Fill the supplied Word layouts in place, including the original run formatting."""

import io
import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from itertools import count
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
T = f"{{{W}}}t"
P = f"{{{W}}}p"
TR = f"{{{W}}}tr"
TEMPLATES = Path(__file__).with_name("templates")
FIELDS = (
    "lender_name lender_address borrower_intro borrower_name borrower_id_kind borrower_id "
    "borrower_address representation represented_name amount_digits amount_words cents penalty count count_words "
    "place issue_day issue_month issue_year number number_words due_day due_month due_year "
    "beneficiary_name beneficiary_id_kind beneficiary_id payment_address start_day start_month start_year "
    "end_day end_month end_year cheque_number bank short_due symbol beneficiary_waiver body_number "
    "demand_payee demand_consideration demand_words demand_day "
    "lender_id_kind lender_id lender_rep_title lender_rep_name lender_rep_id "
    "account_holder account_id_kind account_id bank_name account_type account_number cbu "
    "guarantor_title guarantor_name guarantor_id_kind guarantor_id guarantor_address bank_prefix street_prefix"
).split()
MARKERS = {name: chr(0xE100 + i) for i, name in enumerate(FIELDS)}
NAMES = {marker: key for key, marker in MARKERS.items()}
TOKEN = re.compile(r"([\ue100-\ue1ff])\1*")


def xml_bytes(node):
    return etree.tostring(node, encoding="UTF-8", xml_declaration=True, standalone=True)


def word_node(name, parent=None, **attributes):
    tag = f"{{{W}}}{name}"
    attrs = {f"{{{W}}}{key}": str(value) for key, value in attributes.items()}
    return etree.Element(tag, attrs) if parent is None else etree.SubElement(parent, tag, attrs)


def overwrite(paragraph, spans):
    """Distribute replacements over the source text runs, leaving all XML styles intact."""
    nodes = list(paragraph.iter(T))
    texts = [node.text or "" for node in nodes]
    spans = sorted(spans)
    for previous, following in zip(spans, spans[1:]):
        if previous[1] > following[0]:
            raise RuntimeError("Overlapping Word template bindings")
    cursor = 0
    for node, original in zip(nodes, texts):
        end = cursor + len(original)
        parts, last = [], cursor
        for start, stop, new in spans:
            left, right = max(start, cursor), min(stop, end)
            if left >= right:
                continue
            parts.append(original[last-cursor:left-cursor])
            # Retain every original formatting boundary, even when a field spans runs.
            size = stop - start
            cut = lambda at: ((at - start) * len(new) + size // 2) // size
            parts.append(new[cut(left):cut(right)])
            last = right
        if parts:
            node.text = "".join(parts) + original[last-cursor:]
            if node.text.startswith(" ") or node.text.endswith(" "):
                node.set(XML_SPACE, "preserve")
        cursor = end
    if spans and spans[-1][1] > cursor:
        raise RuntimeError("Word template binding outside a paragraph")


def fill(node, values):
    for paragraph in node.iter(P):
        text = "".join(t.text or "" for t in paragraph.iter(T))
        spans = [(m.start(), m.end(), values[NAMES[m[1]]]) for m in TOKEN.finditer(text)]
        if spans:
            overwrite(paragraph, spans)


@lru_cache(maxsize=4)
def package(name):
    with ZipFile(TEMPLATES / name) as source:
        return {item: source.read(item) for item in source.namelist()}


def open_template(name):
    parts = package(name)
    return parts, etree.fromstring(parts["word/document.xml"])


def encode(parts, document):
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED, compresslevel=2) as output:
        for name, data in parts.items():
            output.writestr(name, xml_bytes(document) if name == "word/document.xml" else data)
    return stream.getvalue()


@dataclass
class WordPiece:
    parts: dict
    document: etree._Element

    def to_docx(self, *, include_yellow_highlights=False):
        document = self.document
        if not include_yellow_highlights:
            document = deepcopy(document)
            for highlight in document.findall(f".//{{{W}}}highlight[@{{{W}}}val='yellow']"):
                highlight.getparent().remove(highlight)
        return encode(self.parts, document)


def replace_body(document, keep):
    body = document.find(f"{{{W}}}body")
    for index, child in enumerate(list(body)):
        if index not in keep and child.tag != f"{{{W}}}sectPr":
            body.remove(child)
    return body


def clone(node, identifiers):
    result = deepcopy(node)
    for element in result.iter():
        for key in ("paraId", "textId"):
            attribute = f"{{{W14}}}{key}"
            if attribute in element.attrib:
                element.set(attribute, next(identifiers))
    return result


def fill_rows(parent, prototypes, values, empty_lines=()):
    first, second = prototypes
    original = deepcopy(second)
    taken = {v for el in parent.getroottree().getroot().iter() for k,v in el.attrib.items()
             if k in (f"{{{W14}}}paraId", f"{{{W14}}}textId")}
    identifiers = (v for v in (f"{i:08X}" for i in count(1)) if v not in taken)
    for position, value in enumerate(values):
        if position < 2:
            node = (first, second)[position]
        else:
            node = clone(original, identifiers)
            if position - 2 < len(empty_lines):
                parent.replace(empty_lines[position-2], node)
            else:
                previous.addnext(node)
        fill(node, value)
        previous = node
    if len(values) == 1:
        if empty_lines:
            parent.replace(second, clone(empty_lines[0], identifiers))
        else:
            fill(second, dict.fromkeys(FIELDS, ""))


TRANSFER_NOTE_STARTS = (13, 23, 33, 43, 52, 62)


def annex_table(rows):
    """Rebuild only the six printed columns of the source annex image."""
    node = word_node
    table = node("tbl")
    props = node("tblPr", table)
    node("tblW", props, w=5000, type="pct")
    node("tblLayout", props, type="fixed")
    widths = (1000, 1470, 1500, 1500, 1300, 1720)
    grid = node("tblGrid", table)
    for width in widths:
        node("gridCol", grid, w=width)
    labels = ("CUOTA", "FECHA", "CAPITAL", "INTERES", "IVA", "CUOTA")
    records = (("ANEXO I",), labels, *[(str(i), r["annex_due"], r["capital"], r["interest"], r["vat"], r["amount_digits"]) for i, r in enumerate(rows, 1)])
    for index, record in enumerate(records):
        row = node("tr", table)
        if index <= 1:
            node("tblHeader", node("trPr", row))
        for col, text in enumerate(record):
            cell = node("tc", row)
            cp = node("tcPr", cell)
            node("tcW", cp, type="dxa", w=sum(widths) if index == 0 else widths[col])
            if index == 0:
                node("gridSpan", cp, val=6)
            if index == 1:
                node("shd", cp, val="clear", fill="D9D9D9")
                node("bottom", node("tcBorders", cp), val="single", sz=4, color="000000")
            paragraph = node("p", cell)
            pp = node("pPr", paragraph)
            node("spacing", pp, before=0, after=120 if index == 0 else 0, line=240, lineRule="auto")
            node("jc", pp, val="center" if index == 0 else "left" if index == 1 else "right")
            run = node("r", paragraph)
            rp = node("rPr", run)
            node("rFonts", rp, ascii="Calibri", hAnsi="Calibri")
            if index == 0:
                node("b", rp)
            node("sz", rp, val=21)
            node("t", run).text = text
    return table


def loan_document(values, rows, *, bank=False, guarantee=False, detailed=False):
    name = "transfer-and-notes.docx" if bank else "loan-and-notes.docx"
    parts, doc = open_template(name)
    body = replace_body(doc, range(12))
    if bank and not guarantee:
        _, other = open_template("loan-and-notes.docx")
        body.remove(body[8])
        body.replace(body[7], other.find(f"{{{W}}}body")[7])
    elif guarantee and not bank:
        _, other = open_template("transfer-and-notes.docx")
        clauses = other.find(f"{{{W}}}body")
        current = body[7]
        guarantee_clause, jurisdiction = clauses[7], clauses[8]
        current.addprevious(guarantee_clause)
        body.replace(current, jurisdiction)
    if bank:
        # The sanitized source retains the empty paragraph after its page break.
        body[-2].addprevious(annex_table(rows))
    else:
        table = body.find(f"{{{W}}}tbl")
        if detailed:
            body.replace(table, annex_table(rows))
        else:
            original_rows = table.findall(TR)
            fill_rows(table, original_rows[2:4], rows)
    fill(body, values)
    return WordPiece(parts, doc)


def note_document(index, values, *, bank=False):
    parts, doc = open_template("transfer-and-notes.docx" if bank else "loan-and-notes.docx")
    # Word starts both original notes just after an explicit page break, and
    # gives the second one its own paragraph layout. Extra notes reuse that layout.
    if bank:
        start = TRANSFER_NOTE_STARTS[min(index, len(TRANSFER_NOTE_STARTS))-1]
        segment = range(start, start + 8)
    else:
        segment = range(13, 21) if index == 1 else range(22, 30)
    body = replace_body(doc, segment)
    fill(body, values)
    return WordPiece(parts, doc)


def assignment_document(values, rows):
    parts, doc = open_template("assignment.docx")
    body = doc.find(f"{{{W}}}body")
    table = body.find(f"{{{W}}}tbl")
    _, first, second, *writing_lines = table.findall(TR)
    fill_rows(table, (first, second), rows, writing_lines)
    fill(body, values)
    return WordPiece(parts, doc)


def demand_document(values):
    parts, doc = open_template("demand-note.docx")
    fill(doc.find(f"{{{W}}}body"), values)
    return WordPiece(parts, doc)
