"""
Service d'authentification et de vérification par QR code des bulletins KLASORA.
Génération de tokens signés non expirants et stables (sans dépendance à la SECRET_KEY Flask),
utilisant la clé BULLETIN_VERIFICATION_KEY et le sel versionné klasora-bulletin-verification-v1.
"""
import io
import os
import qrcode
from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

SALT_BULLETIN_VERIFICATION = "klasora-bulletin-verification-v1"
SALT_ELEVE_VERIFICATION = "klasora-eleve-verification-v1"
DEFAULT_VERIFICATION_KEY = "klasora_bulletin_verification_secret_key_stable"


def get_bulletin_verification_key() -> str:
    """Récupère la clé dédiée et stable pour la signature des QR codes des bulletins."""
    key = None
    if current_app:
        key = current_app.config.get("BULLETIN_VERIFICATION_KEY")
    if not key:
        key = os.environ.get("BULLETIN_VERIFICATION_KEY")
    if not key and current_app:
        key = current_app.config.get("SECRET_KEY")
    return str(key or DEFAULT_VERIFICATION_KEY)


def get_bulletin_verification_serializer() -> URLSafeSerializer:
    """Retourne un URLSafeSerializer non temporisé avec sel versionné."""
    key = get_bulletin_verification_key()
    return URLSafeSerializer(key, salt=SALT_BULLETIN_VERIFICATION)


def generer_token_bulletin(ecole_id: int, inscription_id: int, periode_id: int) -> str:
    """
    Génère un token de vérification unique et stable pour un bulletin précis.
    La charge utile contient :
    - e: id de l'école
    - i: id de l'inscription annuelle
    - p: id de la période de bulletin (PeriodeBulletin.id)
    """
    serializer = get_bulletin_verification_serializer()
    payload = {
        "e": int(ecole_id),
        "i": int(inscription_id),
        "p": int(periode_id),
    }
    return serializer.dumps(payload)


def decoder_token_bulletin(token: str):
    """
    Décode et valide la signature cryptographique du token de bulletin.
    Retourne un tuple (ecole_id, inscription_id, periode_id) si valide,
    ou (None, None, None) si le token est falsifié, altéré ou corrompu.
    """
    if not token or not isinstance(token, str):
        return None, None, None

    serializer = get_bulletin_verification_serializer()
    try:
        payload = serializer.loads(token)
        if not isinstance(payload, dict):
            return None, None, None
        ecole_id = payload.get("e")
        inscription_id = payload.get("i")
        periode_id = payload.get("p")
        if ecole_id is None or inscription_id is None or periode_id is None:
            return None, None, None
        return int(ecole_id), int(inscription_id), int(periode_id)
    except (BadSignature, Exception):
        return None, None, None


def generer_qr_code_buffer(data_url: str) -> io.BytesIO:
    """
    Génère un flux binaire BytesIO contenant l'image PNG d'un QR code
    haute lisibilité en mémoire (sans écriture disque).
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=1,
    )
    qr.add_data(data_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def get_eleve_verification_serializer() -> URLSafeSerializer:
    """Retourne un URLSafeSerializer avec sel dédié aux élèves pour éviter toute collision avec les bulletins."""
    key = get_bulletin_verification_key()
    return URLSafeSerializer(key, salt=SALT_ELEVE_VERIFICATION)


def generer_token_eleve(ecole_id: int, inscription_id: int) -> str:
    """
    Génère un token de vérification unique pour l'identité scolaire annuelle d'un élève.
    Rattaché à l'inscription annuelle (ecole_id, inscription_id).
    """
    serializer = get_eleve_verification_serializer()
    payload = {
        "e": int(ecole_id),
        "i": int(inscription_id),
    }
    return serializer.dumps(payload)


def decoder_token_eleve(token: str):
    """
    Décode et valide la signature cryptographique du token étudiant.
    Retourne (ecole_id, inscription_id) ou (None, None).
    """
    if not token or not isinstance(token, str):
        return None, None

    serializer = get_eleve_verification_serializer()
    try:
        payload = serializer.loads(token)
        if not isinstance(payload, dict):
            return None, None
        ecole_id = payload.get("e")
        inscription_id = payload.get("i")
        if ecole_id is None or inscription_id is None:
            return None, None
        return int(ecole_id), int(inscription_id)
    except (BadSignature, Exception):
        return None, None


