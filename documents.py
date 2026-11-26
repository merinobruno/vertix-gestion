import io
import re
from datetime import date
from decimal import Decimal
from zipfile import ZIP_DEFLATED, ZipFile

from annex import check_installment_total, check_loan_principal, note_amount, printed_amount
from identification import formatted_number
from word_pdf import pdf_batch
from word_templates import assignment_document, demand_document, loan_document, note_document
from combine_word import combine_pieces

MONTHS = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
UNITS = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce "
         "quince dieciséis diecisiete dieciocho diecinueve veinte veintiuno veintidós veintitrés "
         "veinticuatro veinticinco veintiséis veintisiete veintiocho veintinueve").split()
TENS = "cero diez veinte treinta cuarenta cincuenta sesenta setenta ochenta noventa".split()
HUNDREDS = "cero ciento doscientos trescientos cuatrocientos quinientos seiscientos setecientos ochocientos novecientos".split()
TYPES = ("mutuo", "pagares", "cesion")
MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf", "zip": "application/zip"}


def counted(text):
    if text.endswith("veintiuno"):
        return text[:-9] + "veintiún"
    return re.sub(r"\buno$", "un", text)


def number_words(number):
    if number < 30:
        return UNITS[number]
    if number < 100:
        tens, rest = divmod(number, 10)
        return TENS[tens] + (" y " + UNITS[rest] if rest else "")
    if number == 100:
        return "cien"
    if number < 1000:
        leading, rest = divmod(number, 100)
        head = HUNDREDS[leading]
    elif number < 1000000:
        leading, rest = divmod(number, 1000)
        head = "mil" if leading == 1 else counted(number_words(leading)) + " mil"
    else:
        leading, rest = divmod(number, 1000000)
        head = "un millón" if leading == 1 else counted(number_words(leading)) + " millones"
    return head + (" " + number_words(rest) if rest else "")


def money_value(value, label, *, allow_zero=False):
    s = str(value or "").strip() if value != 0 else "0"
    if isinstance(value, str):
        if re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{1,2})?", s):
            s = s.replace(".", "")
        elif re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{1,2}", s):
            s = s.replace(",", "")
        s = s.replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", s):
        raise ValueError(f"Revisá {label}. Usá un número con hasta dos decimales.")
    amount = Decimal(s)
    if not allow_zero and amount <= 0:
        raise ValueError(f"{label.capitalize()} debe ser mayor que cero.")
    return amount


def field(value, label, required=True):
    if not isinstance(value, str):
        value = ""
    value = " ".join(value.split())
    if required and not value:
        raise ValueError(f"Completá {label}.")
    if len(value) > 240 or any(ord(c) < 32 or 0xE100 <= ord(c) <= 0xE1FF
                               or ord(c) in (0xFFFE, 0xFFFF) or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError(f"Revisá {label}.")
    return value


def identification_field(value, kind, label, required=True):
    # Check required fields without flattening spreadsheet cell separators in the identity itself.
    return formatted_number(kind, value) if field(value, label, required) else ""


def calendar_day(value, label):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Completá {label} con una fecha válida.") from None


def address_text(value, label):
    if isinstance(value, str):
        # Earlier panels sent a single line. Only accept it when all four parts can be identified.
        line = field(value, f"el {label}", False)
        match = re.fullmatch(
            r"(?P<street>[^,]+?) (?P<number>(?:\d[\w/-]*(?: bis)?|s/?n))\s*,\s*"
            r"(?:piso\s+(?P<floor>[^,]+),\s*)?(?:(?:depto|dpto)\.?\s+(?P<apartment>[^,]+),\s*)?"
            r"(?P<locality>[^,]+),\s*(?P<province>[^,]+)", line, re.IGNORECASE)
        if not match or any(re.match(r"(?:piso|depto|dpto)\b", part.strip(), re.IGNORECASE)
                            for part in match.group("locality", "province")):
            raise ValueError(f"Completá el {label} con calle, altura, localidad y provincia.")
        value = match.groupdict()
    if not isinstance(value, dict):
        value = {}
    street = field(value.get("street"), f"la calle del {label}")
    number = field(value.get("number"), f"la altura del {label}")
    floor = field(value.get("floor"), f"el piso del {label}", False)
    apartment = field(value.get("apartment"), f"el departamento del {label}", False)
    locality = field(value.get("locality"), f"la localidad del {label}")
    province = field(value.get("province"), f"la provincia del {label}")
    parts = [f"{street} {number}"]
    if floor:
        parts.append(f"piso {floor}")
    if apartment:
        parts.append(f"depto. {apartment}")
    result = ", ".join([*parts, locality, province])
    if len(result) > 240:
        raise ValueError(f"El {label} supera los 240 caracteres.")
    return result


def party(raw, label, *, address_required, id_required=True):
    if not isinstance(raw, dict):
        raw = {}
    kind = raw.get("id_type")
    id_number = identification_field(raw.get("id_number"), kind, f"la identificación de {label}", id_required)
    return {"name": field(raw.get("name"), f"el nombre o la razón social de {label}"),
            "id_type": kind, "id_number": id_number,
            "address": address_text(raw.get("address"), f"domicilio de {label}") if address_required else ""}


def rows(raw, *, cheques, need_bank, notes=False, loan=False, detail=False):
    label = "cheque" if cheques else "cuota"
    if not isinstance(raw, list) or not 1 <= len(raw) <= 60:
        raise ValueError(f"Cargá entre 1 y 60 {'cheques' if cheques else 'cuotas'}.")
    result = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Revisá los datos de {label} {i}.")
        row = {"due": calendar_day(item.get("due"), f"el vencimiento de {label} {i}"),
               "amount": money_value(item.get("amount"), f"el importe de {label} {i}")}
        if cheques:
            row["number"] = field(item.get("number"), f"el número de cheque {i}")
            row["bank"] = field(item.get("bank"), f"el banco del cheque {i}", need_bank)
        elif loan and (detail or any(item.get(k) not in (None, "") for k in ("capital", "interest", "vat"))):
            for key, caption in (("capital", "capital"), ("interest", "interés"), ("vat", "IVA")):
                row[key] = money_value(item.get(key), f"{caption} de la cuota {i}", allow_zero=True)
            check_installment_total(row["amount"], row["capital"], row["interest"], row["vat"], f"la cuota {i}")
        if notes:
            percent = money_value(item.get("note_percent", "0"), f"el porcentaje del pagaré {i}", allow_zero=True)
            row["note_amount"] = note_amount(row["amount"], percent)
        result.append(row)
    return result


def title(value, default):
    value = default if value is None else value
    if value not in ("el Sr.", "la Sra."):
        raise ValueError("Revisá el tratamiento de la parte.")
    return value


def bank_fields(raw, entity):
    cbu = field(raw.get("cbu"), "el CBU")
    if not re.fullmatch(r"[0-9]{22}", cbu):
        raise ValueError("El CBU debe tener 22 dígitos.")
    holder = field(raw.get("account_holder"), "el titular de la cuenta", False)
    holder_id = identification_field(raw.get("account_id"), "CUIT", "el CUIT del titular de la cuenta", False)
    if bool(holder) != bool(holder_id):
        raise ValueError("Completá el nombre y el CUIT del titular de la cuenta, o dejá ambos vacíos para usar los del mutuante.")
    return {"bank_name": field(raw.get("bank_name"), "el banco"), "cbu": cbu,
            "account_type": field(raw.get("account_type"), "el tipo de cuenta"),
            "account_number": field(raw.get("account_number"), "el número de cuenta"),
            "account_holder": holder or company_name(entity, "S.A."),
            "account_id": holder_id or entity["id_number"], "account_id_kind": "CUIT" if holder else entity["id_type"],
            "lender_rep_title": title(raw.get("lender_rep_title"), "el Sr."),
            "lender_rep_name": field(raw.get("lender_rep_name"), "quien representa al mutuante"),
            "lender_rep_id": identification_field(raw.get("lender_rep_id"), "DNI", "el DNI de quien representa al mutuante")}


def parse(raw):
    docs = raw.get("documents")
    if not isinstance(docs, list) or not docs or any(d not in TYPES for d in docs):
        raise ValueError("Elegí al menos un documento.")
    docs = tuple(d for d in TYPES if d in docs)
    loan, wants_notes, transfer = (t in docs for t in TYPES)
    mode = raw.get("note_mode", "installments")
    if mode not in ("installments", "demand"):
        raise ValueError("Elegí una modalidad de pagaré.")
    notes = wants_notes and mode == "installments"
    demand = wants_notes and mode == "demand"
    currency = raw.get("currency")
    output = raw.get("format")
    if currency not in ("ARS", "USD") or output not in ("docx", "pdf", "both"):
        raise ValueError("Elegí una moneda y un formato válidos.")
    if (loan or notes) and currency != "ARS":
        raise ValueError("El modelo de mutuo y los pagarés por cuota corresponden a pesos.")
    if demand and (currency != "USD" or raw.get("guarantor_enabled")):
        raise ValueError("El modelo de pagaré a la vista corresponde a dólares sin garante.")
    combined = raw.get("combined_document", False)
    if not isinstance(combined, bool):
        raise ValueError("Revisá la opción de documento único.")
    method = raw.get("payment_method", "cheques")
    if method not in ("cheques", "bank"):
        raise ValueError("Elegí la forma de pago.")
    bank = method == "bank"
    op = {"documents": docs, "note_mode": mode, "currency": currency, "format": output, "combined_document": combined,
          "bank": bank, "guarantee": bool(raw.get("guarantor_enabled")),
          "issue_date": calendar_day(raw.get("issue_date"), "la fecha de emisión"),
          "place": field(raw.get("place"), "el lugar de emisión")}
    own_entity = loan or (notes and not raw.get("different_beneficiary")) or (transfer and not raw.get("different_assignee"))
    entity = party(raw.get("entity"), "la parte mutuante",
                   address_required=loan or (notes and not raw.get("different_beneficiary") and not raw.get("separate_payment"))) if own_entity else None
    op["entity"] = entity
    if loan:
        represented = bool(raw.get("represented"))
        if bank and represented:
            raise ValueError("La plantilla de transferencia requiere que el mutuario firme en nombre propio.")
        op["client"] = party(raw.get("client"), "la parte mutuaria", address_required=not represented, id_required=not represented)
        op["representative"] = party(raw.get("representative"), "quien representa al mutuario", address_required=True) if represented else None
        op["net"] = money_value(raw.get("net"), "el capital del préstamo" if bank else "el importe neto entregado")
        op["penalty"] = money_value(raw.get("penalty"), "la tasa punitoria mensual", allow_zero=True)
        if bank:
            op["bank_fields"] = bank_fields(raw, entity)
            op["borrower_title"] = title(raw.get("borrower_title"), "el Sr.")
        if op["guarantee"]:
            op["guarantor"] = party(raw.get("guarantor"), "la parte garante", address_required=True)
            op["guarantor_title"] = title(raw.get("guarantor_title"), "la Sra.")
    if notes:
        beneficiary = party(raw.get("beneficiary"), "la parte beneficiaria", address_required=not raw.get("separate_payment")) if raw.get("different_beneficiary") else entity
        op["beneficiary"] = beneficiary
        op["payment_address"] = address_text(raw.get("payment_address"), "domicilio de pago") if raw.get("separate_payment") else beneficiary["address"]
    if transfer:
        op["assignee"] = party(raw.get("assignee"), "la parte cesionaria", address_required=False) if raw.get("different_assignee") else entity
    if demand:
        op["demand_payee"] = field(raw.get("demand_payee"), "el nombre del beneficiario")
        op["demand_id"] = formatted_number("DNI", raw.get("demand_id", ""))
        op["demand_amount"] = money_value(raw.get("demand_amount"), "el importe del pagaré a la vista")
        op["demand_consideration"] = field(raw.get("demand_consideration"), "el concepto del valor recibido")
    op["linked"] = not bank and raw.get("installments_mode", "cheques") == "cheques"
    need_cheques = transfer or ((loan or notes) and op["linked"])
    op["cheques"] = rows(raw.get("cheques"), cheques=True, need_bank=transfer, notes=notes and op["linked"]) if need_cheques else []
    if loan or notes:
        op["installments"] = op["cheques"] if op["linked"] else rows(
            raw.get("installments"), cheques=False, need_bank=False, notes=notes, loan=loan, detail=bank)
        for index, installment in enumerate(op["installments"], 1):
            if installment["due"] < op["issue_date"]:
                raise ValueError(f"El vencimiento de la cuota {index} no puede ser anterior a la fecha de emisión.")
            if index > 1 and installment["due"] < op["installments"][index-2]["due"]:
                raise ValueError(f"El vencimiento de la cuota {index} no puede ser anterior al de la cuota anterior.")
        if loan and any("capital" in r for r in op["installments"]) and not all("capital" in r for r in op["installments"]):
            raise ValueError("Completá capital, interés e IVA para todas las cuotas del Anexo I.")
        if loan and bank:
            check_loan_principal(op["net"], sum(row["capital"] for row in op["installments"]))
    return op


def date_fields(day, prefix):
    return {prefix + "_day": str(day.day), prefix + "_month": MONTHS[day.month-1],
            prefix + "_year": str(day.year)}


def amount_fields(amount):
    return {"amount_digits": printed_amount(amount),
            "amount_words": number_words(int(amount)), "cents": f"{int((amount % 1) * 100):02d}"}


def row_fields(row, currency):
    return {**amount_fields(row["amount"]), "cheque_number": row.get("number", ""),
            "bank": row.get("bank", ""), "short_due": row["due"].strftime("%d/%m/%Y"),
            "annex_due": f"{row['due'].day}/{row['due'].month}/{row['due'].year}",
            **{key: amount_fields(row[key])["amount_digits"] for key in ("capital", "interest", "vat") if key in row},
            "symbol": "$" if currency == "ARS" else "U$S"}


def company_name(person, suffix):
    # These are different written forms of SA in the supplied documents.
    return re.sub(r"(?i)(?<=\s)S\.?A\.?$", suffix, person["name"])


def payee_fields(person, suffix):
    return {"beneficiary_name": company_name(person, suffix),
            "beneficiary_id_kind": person["id_type"], "beneficiary_id": person["id_number"]}


def word_pieces(op):
    selected = op["documents"]
    values = {"place": op["place"], **date_fields(op["issue_date"], "issue")}
    notes = "pagares" in selected and op["note_mode"] == "installments"
    if "mutuo" in selected or notes:
        portions = op["installments"]
        count = len(portions)
        values.update(count=str(count), count_words=number_words(count))
    if "mutuo" in selected:
        client, representative = op["client"], op["representative"]
        signer = representative or client
        penalty = format(op["penalty"], "f")
        if "." in penalty:
            penalty = penalty.rstrip("0").rstrip(".")
        loan = {**values, **amount_fields(op["net"]),
                "lender_name": company_name(op["entity"], "SA"), "lender_address": op["entity"]["address"],
                "lender_id_kind": op["entity"]["id_type"], "lender_id": op["entity"]["id_number"],
                "borrower_intro": "el Sr. " if representative or signer["id_type"] == "DNI" else "",
                "borrower_name": signer["name"], "borrower_id_kind": "D.N.I." if signer["id_type"] == "DNI" else signer["id_type"],
                "borrower_id": signer["id_number"], "borrower_address": signer["address"],
                "representation": ", en representación de " if representative else "",
                "represented_name": client["name"] if representative else "",
                "penalty": penalty.replace(".", ",")}
        if op["bank"]:
            loan.update(op["bank_fields"], lender_name=company_name(op["entity"], "S.A."),
                        borrower_intro=op["borrower_title"] + " ")
        if op["guarantee"]:
            person = op["guarantor"]
            treatment = op["guarantor_title"]
            loan.update(guarantor_title=treatment[0].upper() + treatment[1:],
                        guarantor_name=person["name"], guarantor_address=person["address"],
                        guarantor_id_kind="D. N. I." if person["id_type"] == "DNI" else person["id_type"],
                        guarantor_id=person["id_number"])
        yield "mutuo", loan_document(loan, [row_fields(row, op["currency"]) for row in portions],
                                     bank=op["bank"], guarantee=op["guarantee"], detailed="capital" in portions[0])
    if notes:
        beneficiary = op["beneficiary"]
        for index, row in enumerate(portions, 1):
            suffix = "S.A." if op["bank"] else "SA" if index == 1 else "SA."
            note = {**values, **payee_fields(beneficiary, suffix),
                    "beneficiary_waiver": company_name(beneficiary, "S.A.").rstrip("."),
                    "number": str(index), "body_number": str(index), "number_words": number_words(index),
                    **amount_fields(row["note_amount"]), **date_fields(row["due"], "due"),
                    **date_fields(portions[0]["due"], "start"), **date_fields(portions[-1]["due"], "end"),
                    "payment_address": op["payment_address"].removesuffix(".")}
            if op["bank"]:
                # The transfer model spells this variable without the accent.
                note["amount_words"] = note["amount_words"].replace("millón", "millon")
            yield f"pagare-{index:02d}-de-{count:02d}", note_document(index, note, bank=op["bank"])
    elif "pagares" in selected:
        amount = amount_fields(op["demand_amount"])
        words = amount["amount_words"]
        if amount["cents"] != "00":
            words += " con " + amount["cents"] + "/100"
        demand = {**values, **amount, "demand_words": words, "symbol": "U$S",
                  "demand_day": " " + values["issue_day"], "demand_payee": op["demand_payee"] + " ",
                  "beneficiary_id": op["demand_id"], "demand_consideration": op["demand_consideration"]}
        yield "pagare-a-la-vista", demand_document(demand)
    if "cesion" in selected:
        assignment = {**values, **payee_fields(op["assignee"], "S.A.")}
        yield "cesion", assignment_document(assignment, [row_fields(row, op["currency"]) for row in op["cheques"]])


def word_documents(op):
    pieces = word_pieces(op)
    if op["combined_document"]:
        yield "documento-unico-" + op["issue_date"].isoformat(), combine_pieces(piece for _, piece in pieces).to_docx()
    else:
        for name, piece in pieces:
            yield name, piece.to_docx()


def generate(raw):
    op = parse(raw)
    documents = dict(word_documents(op))
    pdfs = pdf_batch(documents) if op["format"] in ("pdf", "both") else {}
    files = {}
    for name, data in documents.items():
        if op["format"] in ("docx", "both"):
            files[name + ".docx"] = data
        if name in pdfs:
            files[name + ".pdf"] = pdfs[name]
    if len(files) == 1:
        name, data = next(iter(files.items()))
        return name, MIME[name.rsplit(".", 1)[1]], data
    archive = io.BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=2) as output:
        for name, data in files.items():
            output.writestr(name, data)
    return f"documentos-{op['issue_date'].isoformat()}.zip", MIME["zip"], archive.getvalue()


