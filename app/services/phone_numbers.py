import re


def _digits(value):
    if not value:
        return "", ""
    raw = str(value).strip()
    return raw, re.sub(r"\D", "", raw)


def _format_e164(country_and_number):
    if not country_and_number or len(country_and_number) < 8 or len(country_and_number) > 15:
        return None
    if country_and_number.startswith("0"):
        return None
    return f"+{country_and_number}"


def normaliser_telephone_international(value, default_country_code="227"):
    """Normalise un telephone parent sans le limiter au Niger.

    - 8 chiffres sans prefixe restent en local pour compatibilite historique.
    - +XXX... et 00XXX... sont stockes au format international +XXX...
    - 227XXXXXXXX est reconnu comme +227XXXXXXXX.
    """
    if not value:
        return None

    raw, digits = _digits(value)
    if not digits:
        return None

    if raw.lstrip().startswith("+"):
        if default_country_code and digits.startswith(default_country_code) and len(digits) == len(default_country_code) + 8:
            return digits[len(default_country_code):]
        return _format_e164(digits)

    if digits.startswith("00") and len(digits) > 4:
        international = digits[2:]
        if (
            default_country_code
            and international.startswith(default_country_code)
            and len(international) == len(default_country_code) + 8
        ):
            return international[len(default_country_code):]
        return _format_e164(international)

    if default_country_code and digits.startswith(default_country_code) and len(digits) == len(default_country_code) + 8:
        return digits[len(default_country_code):]

    if len(digits) == 10 and digits.startswith(("06", "07")):
        return f"+212{digits[1:]}"

    if len(digits) == 8:
        return digits

    return _format_e164(digits) if 9 <= len(digits) <= 15 else None


def cles_telephone_equivalentes(value):
    numero = normaliser_telephone_international(value)
    if not numero:
        return set()

    keys = {numero}
    if numero.startswith("+227") and len(numero) == 12:
        keys.add(numero[4:])
    elif len(numero) == 8:
        keys.add(f"+227{numero}")
    return keys


def normaliser_numero_whatsapp(value):
    raw, digits = _digits(value)
    if not digits:
        return None

    if raw.lstrip().startswith("+"):
        return _format_e164(digits)

    if digits.startswith("00") and len(digits) > 4:
        return _format_e164(digits[2:])

    if len(digits) == 8:
        return f"+227{digits}"

    if len(digits) == 10 and digits.startswith(("06", "07")):
        return f"+212{digits[1:]}"

    if digits.startswith("227") and len(digits) == 11:
        return f"+{digits}"

    return _format_e164(digits) if 9 <= len(digits) <= 15 else None
