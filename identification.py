import re


TAX_KIND = "CUIT/CUIL"
TAX_TYPES = ("CUIT", "CUIL", TAX_KIND)


def id_group(kind):
    return TAX_KIND if kind in TAX_TYPES else kind


def document_digits(number):
    return re.sub(r"[\s.-]", "", number)


def formatted_number(kind, number):
    digits = document_digits(number)
    if kind in TAX_TYPES:
        if not re.fullmatch(r"[0-9]{11}", digits):
            raise ValueError("El CUIT/CUIL debe tener 11 dígitos.")
        return f"{digits[:2]}-{digits[2:10]}-{digits[10:]}"
    if kind == "DNI":
        if not re.fullmatch(r"[0-9]{1,8}", digits):
            raise ValueError("El DNI debe tener entre 1 y 8 dígitos.")
        return re.sub(r"(?<=\d)(?=(\d{3})+$)", ".", digits)
    raise ValueError("Elegí el tipo de identificación.")
