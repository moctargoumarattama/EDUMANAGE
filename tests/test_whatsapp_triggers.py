import unittest
from datetime import date
from unittest.mock import patch

import requests

from app import create_app, db
from app.config import Config
from app.models import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    MessageQueue,
    Paiement,
    Professeur,
    Utilisateur,
)
from app.routes.absences import _notifier_whatsapp_absence
from app.routes.paiements import _notifier_whatsapp_paiement
from app.services.whatsapp_queue import STATUS_SENT, enqueue_message, envoyer_via_baileys, process_queue


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self.payload


class WhatsAppTriggersTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(
            nom="Ecole WhatsApp",
            telephone="90000000",
            whatsapp_enabled=True,
            whatsapp_sender_phone="90000000",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()

        self.classe = Classe(nom="CI A", niveau="CI", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        db.session.add(self.classe)
        db.session.flush()
        self.cours = Cours(nom="Lecture", ecole_id=self.ecole.id, classe_id=self.classe.id)
        self.parent = Utilisateur(
            nom="Parent",
            email=None,
            telephone="90 12 34 56",
            role="parent",
            ecole_id=self.ecole.id,
        )
        self.parent.set_mot_de_passe("1234")
        self.eleve = Eleve(
            nom="Moussa",
            prenom="Amina",
            date_naissance=date(2016, 1, 1),
            genre="F",
            contact_parent="90-12-34-56",
            parent=self.parent,
            ecole_id=self.ecole.id,
            frais_annuels=150000,
        )
        db.session.add_all([self.cours, self.parent, self.eleve])
        db.session.flush()

        self.inscription = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            statut="inscrit",
            frais_annuels=150000,
        )
        db.session.add(self.inscription)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_absence_trigger_enqueues_pending_message(self):
        absence = Absence(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            inscription_id=self.inscription.id,
            date_absence=date(2026, 9, 26),
            justifiee=False,
        )
        db.session.add(absence)
        db.session.commit()

        item = _notifier_whatsapp_absence(absence, ecole=self.ecole, eleve=self.eleve, cours=self.cours)

        self.assertIsNotNone(item)
        self.assertEqual(item.type_message, "absence")
        self.assertEqual(item.destinataire, "+22790123456")
        self.assertIn("Amina Moussa", item.message)
        self.assertIn("Lecture", item.message)
        self.assertIsNotNone(item.expire_le)

    def _login_admin(self):
        admin = Utilisateur(
            nom="Admin",
            email="admin-whatsapp@test.local",
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        admin.set_mot_de_passe("secret")
        db.session.add(admin)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
            sess["annee_consultee"] = {str(self.ecole.id): self.annee.id}
            sess["ecole_id"] = self.ecole.id
        return admin

    def test_absences_form_post_enqueues_pending_message(self):
        self._login_admin()

        response = self.client.post(
            "/absences",
            data={
                "eleve_id": str(self.eleve.id),
                "cours_id": str(self.cours.id),
                "date_absence": "2026-09-26",
                "motif": "Absence saisie manuellement",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        item = MessageQueue.query.one()
        absence = Absence.query.one()
        self.assertFalse(absence.justifiee)
        self.assertEqual(item.type_message, "absence")
        self.assertEqual(item.destinataire, "+22790123456")
        self.assertIn("Lecture", item.message)

    def test_absence_trigger_falls_back_to_student_contact_parent(self):
        self.parent.telephone = "123"
        db.session.commit()
        absence = Absence(ecole_id=self.ecole.id, eleve_id=self.eleve.id, date_absence=date.today(), justifiee=False)

        item = _notifier_whatsapp_absence(absence, ecole=self.ecole, eleve=self.eleve, cours=self.cours)

        self.assertIsNotNone(item)
        self.assertEqual(item.destinataire, "+22790123456")

    def test_absence_trigger_normalises_international_contact_for_baileys(self):
        self.parent.telephone = None
        self.eleve.contact_parent = "+212 6 12 34 56 78"
        db.session.commit()
        absence = Absence(ecole_id=self.ecole.id, eleve_id=self.eleve.id, date_absence=date.today(), justifiee=False)

        item = _notifier_whatsapp_absence(absence, ecole=self.ecole, eleve=self.eleve, cours=self.cours)

        self.assertIsNotNone(item)
        self.assertEqual(item.destinataire, "+212612345678")

    def test_paiement_trigger_enqueues_receipt_message(self):
        paiement = Paiement(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            inscription_id=self.inscription.id,
            montant=50000,
            mois="Octobre",
            annee=2026,
            reference="REC-123",
            statut="paye",
        )
        db.session.add(paiement)
        db.session.commit()
        db.session.refresh(paiement)

        item = _notifier_whatsapp_paiement(paiement, ecole=self.ecole)

        self.assertIsNotNone(item)
        self.assertEqual(item.type_message, "paiement")
        self.assertEqual(item.destinataire, "+22790123456")
        self.assertIn("Versement de 50 000 FCFA", item.message)
        self.assertIn("Reste a payer : 100 000 FCFA", item.message)
        self.assertIn("REC-123", item.message)
        self.assertIsNotNone(item.expire_le)

    def test_student_registration_enqueues_whatsapp_welcome(self):
        self._login_admin()

        response = self.client.post(
            "/ajouter_eleve",
            data={
                "nom": "Garba",
                "prenom": "Salma",
                "genre": "F",
                "date_naissance": "2017-04-03",
                "lieu_naissance": "Niamey",
                "adresse": "Niamey",
                "classe_id": str(self.classe.id),
                "frais_annuels": "150000",
                "parent_id": str(self.parent.id),
                "parent_nom": "",
                "parent_telephone": "",
                "code_parent": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        item = MessageQueue.query.one()
        self.assertEqual(item.type_message, "inscription")
        self.assertEqual(item.destinataire, "+22790123456")
        self.assertIn("Salma Garba", item.message)
        self.assertIn("CI A", item.message)

    def test_professor_creation_enqueues_whatsapp_account_message(self):
        self._login_admin()

        with patch("app.notifications.envoyer_email", return_value=True):
            response = self.client.post(
                "/ajouter_professeur",
                data={
                    "nom": "Issoufou",
                    "prenom": "Mahamadou",
                    "date_naissance": "",
                    "adresse": "Niamey",
                    "telephone": "0770010264",
                    "specialite": "Mathematiques",
                    "matieres_enseignees": "Mathematiques",
                    "code_prof": "24681357",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        professeur = Professeur.query.filter_by(telephone="+212770010264").first()
        self.assertIsNotNone(professeur)
        self.assertEqual(professeur.telephone, "+212770010264")
        item = MessageQueue.query.one()
        self.assertEqual(item.type_message, "compte_professeur")
        self.assertEqual(item.destinataire, "+212770010264")
        self.assertIn("+212770010264", item.message)
        self.assertIn("24681357", item.message)

    def test_envoyer_via_baileys_success_and_failure(self):
        with patch(
            "app.services.whatsapp_queue.requests.post",
            return_value=FakeResponse(200, {"success": True}),
        ) as post_mock:
            self.assertTrue(envoyer_via_baileys("+22790123456", "Bonjour"))

        post_mock.assert_called_once_with(
            "http://127.0.0.1:3001/send",
            json={"to": "+22790123456", "message": "Bonjour"},
            timeout=5,
        )

        with patch(
            "app.services.whatsapp_queue.requests.post",
            return_value=FakeResponse(500, {"error": "telephone deconnecte"}),
        ):
            with self.assertRaises(RuntimeError):
                envoyer_via_baileys("+22790123456", "Bonjour")

        with patch(
            "app.services.whatsapp_queue.requests.post",
            side_effect=requests.exceptions.ConnectionError("offline"),
        ):
            with self.assertRaises(RuntimeError):
                envoyer_via_baileys("+22790123456", "Bonjour")

    def test_cli_process_queue_uses_baileys_sender(self):
        runner = self.app.test_cli_runner()
        fake_result = {
            "processed": [object()],
            "sent": [object()],
            "failed": [],
            "pending": [],
            "expired": [],
        }
        with patch("app.services.whatsapp_queue.process_queue", return_value=fake_result) as process_mock:
            result = runner.invoke(args=["whatsapp", "process-queue", "--limit", "7"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("processed=1", result.output)
        self.assertEqual(process_mock.call_args.kwargs["limit"], 7)

    def test_process_queue_can_call_baileys_sender(self):
        item = enqueue_message(self.ecole.id, "90123456", "Bonjour parent", "general", commit=True)

        with patch(
            "app.services.whatsapp_queue.requests.post",
            return_value=FakeResponse(200, {"success": True}),
        ):
            result = process_queue(envoyer_via_baileys)

        db.session.refresh(item)
        self.assertEqual(item.statut, STATUS_SENT)
        self.assertEqual(result["sent"], [item])


if __name__ == "__main__":
    unittest.main()
