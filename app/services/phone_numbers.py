import re


def normaliser_telephone_international(value, default_country_code="227"):
    """Normalise un telephone parent sans le limiter au Niger.

    - 8 chiffres sans prefixe restent en local pour compatibilite historique.
    - +XXX... et 00XXX... sont stockes au format international +XXX...
    - 227XXXXXXXX est reconnu comme +227XXXXXXXX.
    """
    if not value:
        return None

    raw = str(value).strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    if raw.lstrip().startswith("+"):
        if default_country_code and digits.startswith(default_country_code) and len(digits) == len(default_country_code) + 8:
            return digits[len(default_country_code):]
        return f"+{digits}" if 8 <= len(digits) <= 15 else None

    if digits.startswith("00") and len(digits) > 4:
        international = digits[2:]
        if (
            default_country_code
            and international.startswith(default_country_code)
            and len(international) == len(default_country_code) + 8
        ):
            return international[len(default_country_code):]
        return f"+{international}" if 8 <= len(international) <= 15 else None

    if default_country_code and digits.startswith(default_country_code) and len(digits) == len(default_country_code) + 8:
        return digits[len(default_country_code):]

    if len(digits) == 8:
        return digits

    return f"+{digits}" if 9 <= len(digits) <= 15 else None


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
    numero = normaliser_telephone_international(value)
    if not numero:
        return None
    if numero.startswith("+"):
        return numero
    return f"+227{numero}"
