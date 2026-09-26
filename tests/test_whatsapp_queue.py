import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.config import Config
from app.models import Ecole, MessageQueue
from app.services.whatsapp_queue import (
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENT,
    enqueue_message,
    get_pending_messages,
    normaliser_numero_niger,
    process_queue,
    verifier_configuration_ecole,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class WhatsAppQueueTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(
            nom="Ecole WhatsApp",
            telephone="90000000",
            statut="actif",
            whatsapp_enabled=True,
            whatsapp_sender_phone="+227 90 00 00 00",
        )
        db.session.add(self.ecole)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_normalise_niger_phone_formats(self):
        self.assertEqual(normaliser_numero_niger("90123456"), "+22790123456")
        self.assertEqual(normaliser_numero_niger("+227 90 12 34 56"), "+22790123456")
        self.assertEqual(normaliser_numero_niger("00227 90-12-34-56"), "+22790123456")
        self.assertIsNone(normaliser_numero_niger("123"))

    def test_enqueue_persists_pending_message(self):
        item = enqueue_message(
            self.ecole.id,
            "90-12-34-56",
            "Votre enfant est absent.",
            type_message="absence",
            commit=True,
        )

        saved = db.session.get(MessageQueue, item.id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.destinataire, "+22790123456")
        self.assertEqual(saved.type_message, "absence")
        self.assertEqual(saved.statut, STATUS_PENDING)
        self.assertEqual(saved.tentatives, 0)

    def test_process_queue_success_marks_sent(self):
        item = enqueue_message(self.ecole.id, "90123456", "Recu disponible", "paiement", commit=True)
        calls = []

        result = process_queue(lambda phone, message, queue_item: calls.append((phone, message, queue_item.id)) or True)

        db.session.refresh(item)
        self.assertEqual(item.statut, STATUS_SENT)
        self.assertEqual(item.tentatives, 1)
        self.assertIsNotNone(item.date_envoi)
        self.assertEqual(len(result["sent"]), 1)
        self.assertEqual(calls, [("+22790123456", "Recu disponible", item.id)])

    def test_failed_send_stays_pending_until_max_attempts_then_failed(self):
        item = enqueue_message(
            self.ecole.id,
            "90123456",
            "Nouvelle note publiee",
            "note",
            max_tentatives=2,
            commit=True,
        )

        process_queue(lambda *_: (_ for _ in ()).throw(ConnectionError("telephone hors ligne")))
        db.session.refresh(item)
        self.assertEqual(item.statut, STATUS_PENDING)
        self.assertEqual(item.tentatives, 1)
        self.assertIn(item, get_pending_messages())

        process_queue(lambda *_: False)
        db.session.refresh(item)
        self.assertEqual(item.statut, STATUS_FAILED)
        self.assertEqual(item.tentatives, 2)
        self.assertNotIn(item, get_pending_messages())

    def test_expired_message_is_not_sent(self):
        item = enqueue_message(
            self.ecole.id,
            "90123456",
            "Absence ancienne",
            "absence",
            expire_le=datetime.utcnow() - timedelta(minutes=1),
            commit=True,
        )
        calls = []

        result = process_queue(lambda *_: calls.append(True) or True)

        db.session.refresh(item)
        self.assertEqual(item.statut, STATUS_EXPIRED)
        self.assertEqual(item.tentatives, 0)
        self.assertEqual(calls, [])
        self.assertEqual(result["expired"], [item])

    def test_school_configuration_check(self):
        ok, sender = verifier_configuration_ecole(self.ecole.id)
        self.assertTrue(ok)
        self.assertEqual(sender, "+22790000000")

        self.ecole.whatsapp_enabled = False
        db.session.commit()
        ok, message = verifier_configuration_ecole(self.ecole.id)
        self.assertFalse(ok)
        self.assertIn("WhatsApp", message)


if __name__ == "__main__":
    unittest.main()
