"""Read the saved values of the loan's Anexo I without running Excel formulas."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from unicodedata import normalize
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


CENT = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")
UPLOAD_LIMIT = 4 * 1024 * 1024
HEADERS = ("CUOTA", "FECHA", "CAPITAL", "INTERES", "IVA", "CUOTA")


def label(value):
    if not isinstance(value, str):
        return ""
    return " ".join(normalize("NFKD", value).encode("ascii", "ignore").decode().upper().split())


def monetary(value, column, row, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (float, int, Decimal)):
        raise ValueError(f"Revisá {column} en la fila {row}. Si contiene una fórmula, recalculá el Excel y guardalo.")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0 or amount > MAX_MONEY:
        raise ValueError(f"El importe de {column} en la fila {row} está fuera de rango.")
    amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    if not allow_zero and not amount:
        raise ValueError(f"El importe de {column} en la fila {row} debe ser positivo.")
    return amount


def note_amount(amount, percent):
    result = (amount * (1 + percent / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    if result > MAX_MONEY:
        raise ValueError("El importe del pagaré supera el máximo admitido.")
    return result


def due_date(value, row):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), pattern).date().isoformat()
            except ValueError:
                pass
    raise ValueError(f"Revisá la fecha de vencimiento en la fila {row} del Anexo I.")


def read_section(rows, marker, formulas):
    start, left = marker
    for line in range(start + 1, min(start + 7, len(rows))):
        headers = [label(cell) for cell in rows[line]]
        if tuple(headers[left:left+6]) == HEADERS:
            break
    else:
        raise ValueError("El Anexo I debe incluir CUOTA, FECHA, CAPITAL, INTERES, IVA y CUOTA en ese orden.")
    note_columns = [i for i, title in enumerate(headers) if i >= left+6 and title == "PAGARE"]
    if len(note_columns) > 1:
        raise ValueError("El Anexo I contiene más de una columna PAGARÉ.")
    note_formulas = {}
    if note_columns:
        col = note_columns[0]+1
        for (cell,) in formulas.iter_rows(min_row=line+2, max_row=len(rows), min_col=col, max_col=col):
            if cell.data_type == "f":
                note_formulas[cell.row] = True
    installments = []
    ended = False
    for index in range(line+1, len(rows)):
        record = rows[index]
        number, due, capital, interest, vat, total = record[left:left+6]
        row = index + 1
        # Totals have no installment number or date. Other worksheet blocks
        # may follow the schedule and must never become installments.
        if label(number) in {"TOTAL", "TOTALES"}:
            break
        if number is None:
            if due is not None:
                try:
                    due_date(due, row)
                except ValueError:
                    if not ended:
                        raise ValueError(f"Falta el número de cuota o hay un vencimiento inválido en la fila {row}.") from None
                else:
                    raise ValueError(f"Falta el número de cuota en la fila {row}.")
            ended = True
            continue
        if ended and isinstance(number, str) and not number.strip().isdigit():
            break
        if ended or str(number).strip() != str(len(installments)+1):
            raise ValueError(f"Revisá el número de cuota en la fila {row}. Las cuotas deben comenzar en 1 y ser consecutivas.")
        if len(installments) == 60:
            raise ValueError("El Anexo I supera las 60 cuotas que admite el panel.")
        amount = monetary(total, "CUOTA", row)
        item = {"due": due_date(due, row), "amount": str(amount),
                "capital": str(monetary(capital, "CAPITAL", row, allow_zero=True)),
                "interest": str(monetary(interest, "INTERES", row, allow_zero=True)),
                "vat": str(monetary(vat, "IVA", row, allow_zero=True)), "note_percent": "0"}
        if note_columns and record[note_columns[0]] is not None:
            expected = monetary(record[note_columns[0]], "PAGARÉ", row)
            percent = ((expected / amount - 1)*100).quantize(CENT, rounding=ROUND_HALF_UP)
            if 0 <= percent <= MAX_MONEY and note_amount(amount, percent) == expected:
                item["note_percent"] = format(percent.normalize(), "f")
            else:
                item.update(note_percent="", reference_note=str(expected))
        elif row in note_formulas:
            item.update(note_percent="", note_formula_missing=True)
        installments.append(item)
    if not installments:
        raise ValueError("El Anexo I no tiene cuotas.")
    principal = None
    for row in rows[:start]:
        for pos, value in enumerate(row[:-1]):
            if label(value) == "IMPORTE" and isinstance(row[pos+1], (int, float)):
                principal = str(monetary(row[pos+1], "IMPORTE", 1))
                break
        if principal is not None:
            break
    return {"principal": principal, "installments": installments}


def import_annex(data):
    if not 0 < len(data) <= UPLOAD_LIMIT:
        raise ValueError("Elegí una planilla .xlsx de hasta 4 MB.")
    formulas = None
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(e.file_size for e in entries) > 32*1024*1024:
                raise ValueError("La planilla es demasiado grande. Usá una copia con el Anexo I.")
        book = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except (BadZipFile, InvalidFileException, KeyError, OSError, SyntaxError) as exc:
        raise ValueError("No se pudo leer el archivo. Elegí un libro .xlsx válido y guardado con Excel.") from exc
    try:
        result = None
        for sheet in book.worksheets:
            if sheet.sheet_state != "visible":
                continue
            grid = list(sheet.iter_rows(max_row=min(sheet.max_row or 1000, 1000),
                                        max_col=min(sheet.max_column or 64, 64), values_only=True))
            for row, cells in enumerate(grid):
                for col, value in enumerate(cells):
                    if label(value) != "ANEXO I":
                        continue
                    if result is not None:
                        raise ValueError("Hay más de un Anexo I. Dejá solo el que corresponde a esta operación.")
                    if (sheet.max_row or 0) > len(grid) and row+68 >= len(grid):
                        raise ValueError("El Anexo I supera el área de lectura. Movelo al inicio de la hoja.")
                    if formulas is None:
                        formulas = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
                    result = read_section(grid, (row, col), formulas[sheet.title])
        if result is None:
            raise ValueError("No se encontró el título ANEXO I en la planilla.")
        return result
    except (InvalidOperation, OverflowError, IndexError, TypeError, SyntaxError, BadZipFile) as exc:
        raise ValueError("No se pudo leer el Anexo I. Revisá las celdas y guardá nuevamente la planilla.") from exc
    finally:
        book.close()
        if formulas is not None:
            formulas.close()
