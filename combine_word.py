"""Join the panel's editable Word bodies and preserve each piece's page setup."""

from copy import deepcopy
from itertools import count

from lxml import etree

from word_templates import W, W14, P, T, XML_SPACE, WordPiece, xml_bytes


# Word 2003 and Word 2013 wrap fully justified paragraphs differently. These
# are the actual line endings of the assignment's three fixed legal paragraphs
# in the original Word printout, in order. Every source character stays intact.
ASSIGNMENT_LINE_ENDS = (
    ("cesión, declarando ", "garantizando además "),
    ("principales pagadores ", "los firmantes, de ", "Decreto 5965/ 63 y ", "las previsiones del Art. "),
    ("Art. 523 Inc. 2 ", "la competencia de los "),
)


def tag(name):
    return f"{{{W}}}{name}"


def element(name, **attributes):
    return etree.Element(tag(name), {tag(key): str(value) for key, value in attributes.items()})


def paragraph_properties(paragraph):
    props = paragraph.find(tag("pPr"))
    if props is None:
        props = element("pPr")
        paragraph.insert(0, props)
    return props


def finish_section(body):
    section = body[-1]
    paragraph = body[-2]
    if section.tag != tag("sectPr") or paragraph.tag != P:
        raise ValueError("La plantilla no tiene el cierre de sección esperado.")
    # A section break belongs to the previous printed paragraph. Adding a new
    # paragraph here can shift text or even create a blank page.
    paragraph_properties(paragraph).append(section)


def legacy_style(parts, source):
    """Keep the imported Word 2003 Normal style distinct from modern Normal."""
    name = "NormalCesion"
    styles = etree.fromstring(parts["word/styles.xml"])
    legacy_styles = etree.fromstring(source["word/styles.xml"])
    normal = legacy_styles.find(f"{tag('style')}[@{tag('styleId')}='Normal']")
    style = element("style", type="paragraph", customStyle=1, styleId=name)
    style.append(element("name", val="Normal (cesión)"))
    properties = element("pPr")
    # The old .doc starts from zero interparagraph space and single leading.
    # The modern document's defaults add eight points and use 1.08 lines.
    properties.append(element("spacing", before=0, after=0, line=240, lineRule="auto"))
    style.append(properties)
    defaults = legacy_styles.find(f"{tag('docDefaults')}/{tag('rPrDefault')}/{tag('rPr')}")
    combined_runs = deepcopy(defaults)
    for node in normal.find(tag("rPr")):
        inherited = combined_runs.find(node.tag)
        if inherited is None:
            combined_runs.append(deepcopy(node))
        else:
            inherited.attrib.update(node.attrib)
    style.append(combined_runs)
    styles.append(style)
    parts["word/styles.xml"] = xml_bytes(styles)

    fonts = etree.fromstring(parts["word/fontTable.xml"])
    existing = {font.get(tag("name")) for font in fonts}
    for font in etree.fromstring(source["word/fontTable.xml"]):
        if font.get(tag("name")) not in existing:
            fonts.append(deepcopy(font))
    parts["word/fontTable.xml"] = xml_bytes(fonts)
    # Only the legacy models use literal tabs. Word rounds explicit stops
    # slightly differently from its native default, so retain the source's
    # default tab interval when appending that model to the Letter documents.
    settings = etree.fromstring(parts["word/settings.xml"])
    original_settings = etree.fromstring(source["word/settings.xml"])
    settings.find(tag("defaultTabStop")).set(tag("val"), original_settings.find(tag("defaultTabStop")).get(tag("val")))
    parts["word/settings.xml"] = xml_bytes(settings)
    return name


def retain_printed_lines(paragraph, endings):
    full_text = "".join(t.text or "" for t in paragraph.iter(T))
    positions = []
    cursor = 0
    for ending in endings:
        cursor = full_text.index(ending, cursor) + len(ending)
        positions.append(cursor)
    for position in reversed(positions):
        start = 0
        for node in paragraph.iter(T):
            original = node.text or ""
            if start < position <= start + len(original):
                split = position - start
                node.text = original[:split]
                node.set(XML_SPACE, "preserve")
                line = element("br")
                node.addnext(line)
                if split < len(original):
                    tail = element("t")
                    tail.text = original[split:]
                    tail.set(XML_SPACE, "preserve")
                    line.addnext(tail)
                break
            start += len(original)


def apply_legacy_style(body, name):
    for paragraph in body.iter(P):
        properties = paragraph_properties(paragraph)
        properties.insert(0, element("pStyle", val=name))
    for paragraph, endings in zip(list(body)[-11:-8], ASSIGNMENT_LINE_ENDS, strict=True):
        retain_printed_lines(paragraph, endings)


def combine_pieces(pieces):
    iterator = iter(pieces)
    first = next(iterator)
    parts = dict(first.parts)
    body = first.document.find(tag("body"))
    styles = {}
    para_id = f"{{{W14}}}paraId"
    used = {p.get(para_id) for p in body.iter(P) if p.get(para_id)}
    identifiers = (f"{i:08X}" for i in count(1))
    for piece in iterator:
        following = piece.document.find(tag("body"))
        if first.parts["word/styles.xml"] != piece.parts["word/styles.xml"]:
            key = piece.parts["word/styles.xml"]
            if key not in styles:
                styles[key] = legacy_style(parts, piece.parts)
            apply_legacy_style(following, styles[key])
        for paragraph in following.iter(P):
            current = paragraph.get(para_id)
            if current:
                if current in used:
                    current = next(value for value in identifiers if value not in used)
                    paragraph.set(para_id, current)
                used.add(current)
        finish_section(body)
        for node in list(following):
            body.append(node)
    return WordPiece(parts, first.document)
