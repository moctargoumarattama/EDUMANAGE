import re
import secrets


ACCESS_CODE_LENGTH = 8
ACCESS_CODE_PATTERN = re.compile(r"^\d{8}$")
_ACCESS_CODE_DIGITS = "0123456789"
_FORBIDDEN_ACCESS_CODES = {
    "00000000",
    "11111111",
    "22222222",
    "33333333",
    "44444444",
    "55555555",
    "66666666",
    "77777777",
    "88888888",
    "99999999",
    "12345678",
    "87654321",
    "01234567",
    "98765432",
}


def generate_access_code():
    while True:
        code = "".join(secrets.choice(_ACCESS_CODE_DIGITS) for _ in range(ACCESS_CODE_LENGTH))
        if code not in _FORBIDDEN_ACCESS_CODES:
            return code


def is_valid_access_code(code):
    return bool(ACCESS_CODE_PATTERN.fullmatch((code or "").strip()))
