"""Build the sanitized bank-transfer/guarantor layout from the original Word."""

import argparse
import re
from pathlib import Path

from prepare_templates import compile_template, date_fields, letter_date, mark, string
from word_templates import TEMPLATES, TRANSFER_NOTE_STARTS


def capture(text, pattern):
    match = re.search(pattern, text)
    if match is None:
        raise ValueError("The supplied transfer template does not match its bindings")
    return [(match.start(key), match.end(key), key) for key in match.groupdict()]


def bind(body):
    text = string(body[1])
    mark(body[1], capture(text, r"Entre (?P<lender_name>.+?), (?P<lender_id_kind>CUIT) (?P<lender_id>[\d-]+), con domicilio en (?P<lender_address>.+?), representada en este acto por (?P<lender_rep_title>el Sr\.) (?P<lender_rep_name>.+?), DNI N°(?P<lender_rep_id>[\d.]+)") +
         capture(text, r"y (?P<borrower_intro>el Sr\. )(?P<borrower_name>.+?), (?P<borrower_id_kind>D\.N\.I\.) N°(?P<borrower_id>[\d.]+), con domicilio real en (?P<borrower_address>.+?), en adelante"))
    text = string(body[2])
    mark(body[2], capture(text, r"\$(?P<amount_digits>[\d.,]+) en efectivo, \(Pesos (?P<amount_words>.+?) con (?P<cents>\d+)/100") +
         capture(text, r"a nombre de (?P<account_holder>.+?), (?P<account_id_kind>CUIT) (?P<account_id>[\d-]+), (?P<bank_prefix>Banco )(?P<bank_name>.+?), (?P<account_type>Cta\. Cte\.) (?P<account_number>[\d-]+), CBU (?P<cbu>\d+)"))
    mark(body[3], capture(string(body[3]), r"punitorio del (?P<penalty>\d+)%"))
    mark(body[6], capture(string(body[6]), r"Mutuante (?P<count>\d+) \((?P<count_words>\w+)\) pagarés"))
    mark(body[7], capture(string(body[7]), r"6\) (?P<guarantor_title>La Sra\.) (?P<guarantor_name>.+?), (?P<guarantor_id_kind>D\. N\. I\.) N°(?P<guarantor_id>[\d.]+), con domicilio real en (?P<guarantor_address>.+?) se constituye"))
    mark(body[9], capture(string(body[9]), r"efecto en la (?P<place>.+?), a los (?P<issue_day>\d+) días del mes de (?P<issue_month>\w+) del año (?P<issue_year>\d+)"))
    # The photo is a filled example. The generator inserts its editable table here.
    for child in list(body[11]):
        if not child.tag.endswith("}pPr"):
            body[11].remove(child)
    for start in TRANSFER_NOTE_STARTS:
        mark(body[start+1], capture(string(body[start+1]), r"NÚMERO \((?P<number>\d+)\) DE \((?P<count>\d+)\)"))
        letter_date(body[start+2])
        text = string(body[start+3])
        mark(body[start+3], date_fields(text, "due") +
             capture(text, r"ilimitada a (?P<beneficiary_name>.+?), (?P<beneficiary_id_kind>CUIT) (?P<beneficiary_id>[\d-]+), o a su orden") +
             capture(text, r"cantidad de pesos (?P<amount_words>.+?) con (?P<cents>\d\d)/100, \(\$(?P<amount_digits>[\d.,]+)\.\-\)") +
             capture(text, r"pagadero en (?P<street_prefix>calle )(?P<payment_address>.+)\.$"))
        mark(body[start+5], capture(string(body[start+5]), r"emitido por (?P<beneficiary_waiver>.+)\.$"))
        text = string(body[start+7])
        mark(body[start+7], capture(text, r"constituye el número (?P<number_words>\w+) \((?P<body_number>\d+)\) de (?P<count_words>\w+) \((?P<count>\d+)\)") +
             capture(text, r"numerados del 1 al (?P<count>\d+)") +
             date_fields(text, "start") + date_fields(text, "end", text.index(" hasta el ")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    compile_template(parser.parse_args().source, TEMPLATES/"transfer-and-notes.docx",
                     "03cffdcfaa3e4976b2e293ccb03861d277c4a069e4ee1b49935bac9c1b25274f", bind, remove_image=True)
