import re
from datetime import datetime, timedelta

import requests

from app import db
from app.models import Ecole, MessageQueue


VALID_MESSAGE_TYPES = {'absence', 'note', 'paiement', 'general'}
STATUS_PENDING = 'en_attente'
STATUS_SENT = 'envoye'
STATUS_FAILED = 'echec'
STATUS_EXPIRED = 'expire'
BAILEYS_SEND_URL = 'http://127.0.0.1:3001/send'


def normaliser_numero_niger(numero):
    """Normalise un numero Niger vers +227XXXXXXXX."""
    if not numero:
        return None

    digits = re.sub(r'\D', '', str(numero))
    if digits.startswith('00227'):
        digits = digits[5:]
    elif digits.startswith('227') and len(digits) > 8:
        digits = digits[3:]

    if len(digits) > 8:
        digits = digits[-8:]

    if len(digits) != 8:
        return None

    return f'+227{digits}'


def verifier_configuration_ecole(ecole_id):
    ecole = db.session.get(Ecole, ecole_id)
    if not ecole:
        return False, "Ecole introuvable."
    if not getattr(ecole, 'whatsapp_enabled', False):
        return False, "WhatsApp n'est pas active pour cette ecole."
    sender = normaliser_numero_niger(getattr(ecole, 'whatsapp_sender_phone', None) or ecole.telephone)
    if not sender:
        return False, "Numero WhatsApp ecole invalide ou manquant."
    return True, sender


def enqueue_message(
    ecole_id,
    destinataire,
    message,
    type_message='general',
    expire_le=None,
    duree_validite_heures=None,
    max_tentatives=3,
    commit=False,
):
    """Ajoute un message dans l'outbox sans tenter de l'envoyer immediatement."""
    numero = normaliser_numero_niger(destinataire)
    if not numero:
        raise ValueError("Numero WhatsApp destinataire invalide.")

    contenu = str(message or '').strip()
    if not contenu:
        raise ValueError("Message WhatsApp vide.")

    if type_message not in VALID_MESSAGE_TYPES:
        type_message = 'general'

    if expire_le is None and duree_validite_heures:
        expire_le = datetime.utcnow() + timedelta(hours=int(duree_validite_heures))

    queue_item = MessageQueue(
        ecole_id=ecole_id,
        destinataire=numero,
        message=contenu,
        type_message=type_message,
        statut=STATUS_PENDING,
        max_tentatives=max(1, int(max_tentatives or 3)),
        expire_le=expire_le,
    )
    db.session.add(queue_item)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return queue_item


def envoyer_via_baileys(destinataire, texte, queue_item=None):
    """Envoie un message WhatsApp via la passerelle locale Baileys."""
    payload = {"to": destinataire, "message": texte}
    try:
        response = requests.post(BAILEYS_SEND_URL, json=payload, timeout=5)
    except requests.RequestException as exc:
        raise RuntimeError(f"Passerelle Baileys indisponible: {exc}") from exc

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code == 200 and data.get("success") is True:
        return True

    error = data.get("error") or data.get("message") or response.text or f"HTTP {response.status_code}"
    raise RuntimeError(f"Echec envoi WhatsApp Baileys: {error}")


def expire_pending_messages(now=None, commit=False):
    now = now or datetime.utcnow()
    expired = (
        MessageQueue.query
        .filter(
            MessageQueue.statut == STATUS_PENDING,
            MessageQueue.expire_le.isnot(None),
            MessageQueue.expire_le <= now,
        )
        .all()
    )
    for item in expired:
        item.statut = STATUS_EXPIRED
        item.erreur_details = "Message expire avant envoi."

    if expired and commit:
        db.session.commit()
    return expired


def get_pending_messages(ecole_id=None, limit=50, now=None):
    now = now or datetime.utcnow()
    query = MessageQueue.query.filter(
        MessageQueue.statut == STATUS_PENDING,
        MessageQueue.tentatives < MessageQueue.max_tentatives,
    ).filter(
        db.or_(MessageQueue.expire_le.is_(None), MessageQueue.expire_le > now)
    )
    if ecole_id is not None:
        query = query.filter(MessageQueue.ecole_id == ecole_id)
    return query.order_by(MessageQueue.date_creation.asc(), MessageQueue.id.asc()).limit(limit).all()


def process_queue(send_func, ecole_id=None, limit=50, now=None, commit=True):
    """
    Depile les messages en attente avec une fonction d'envoi injectable.

    send_func(destinataire, message, queue_item) doit retourner True en cas de succes.
    Toute exception garde le message en file tant que max_tentatives n'est pas atteint.
    """
    now = now or datetime.utcnow()
    expired = expire_pending_messages(now=now, commit=False)
    processed = []

    for item in get_pending_messages(ecole_id=ecole_id, limit=limit, now=now):
        item.tentatives = (item.tentatives or 0) + 1
        try:
            sent = bool(send_func(item.destinataire, item.message, item))
        except Exception as exc:
            sent = False
            item.erreur_details = str(exc)[:2000]

        if sent:
            item.statut = STATUS_SENT
            item.date_envoi = now
            item.erreur_details = None
        elif item.tentatives >= item.max_tentatives:
            item.statut = STATUS_FAILED
            if not item.erreur_details:
                item.erreur_details = "Echec d'envoi WhatsApp apres tentatives maximales."
        else:
            item.statut = STATUS_PENDING
            if not item.erreur_details:
                item.erreur_details = "Envoi WhatsApp echoue, nouvelle tentative prevue."

        processed.append(item)

    if commit and (expired or processed):
        db.session.commit()

    return {
        'processed': processed,
        'expired': expired,
        'sent': [item for item in processed if item.statut == STATUS_SENT],
        'failed': [item for item in processed if item.statut == STATUS_FAILED],
        'pending': [item for item in processed if item.statut == STATUS_PENDING],
    }
