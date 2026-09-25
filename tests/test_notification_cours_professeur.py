import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Ecole, Utilisateur, Professeur, Classe, Cours, AnneeScolaire, EcoleGoogleMailConfig
from app.services.cours_notifications import notifier_professeur_cours_assigne


class CoursNotificationTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-secret-key-cours-notification"
    LOGIN_DISABLED = False
    SERVER_NAME = None


class TestNotificationCoursProfesseur(unittest.TestCase):
    def setUp(self):
        self.app = create_app(CoursNotificationTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # 1. École et configuration Gmail
        self.ecole = Ecole(
            nom="Lycée d'Excellence",
            email="contact@lycee-excellence.edu",
            onboarding_complete=True
        )
        db.session.add(self.ecole)
        db.session.commit()

        self.gmail_config = EcoleGoogleMailConfig(
            ecole_id=self.ecole.id,
            google_email="secretariat@lycee-excellence.edu",
            is_connected=True,
            connected_at=datetime.utcnow()
        )
        db.session.add(self.gmail_config)
        db.session.commit()

        # 2. Année scolaire et Classe
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=datetime(2025, 9, 1),
            date_fin=datetime(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        self.classe = Classe(
            nom="Terminale S1",
            statut="ouverte",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id
        )
        db.session.add(self.classe)
        db.session.commit()

        # 3. Administrateur
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Admin",
            email="admin@lycee-excellence.edu",
            role="admin",
            ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("password123")
        )
        db.session.add(self.admin)
        db.session.commit()

        # 4. Professeur
        self.prof_user = Utilisateur(
            nom="Diallo",
            prenom="Mamadou",
            email="mamadou.diallo@lycee.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("password123")
        )
        db.session.add(self.prof_user)
        db.session.commit()

        self.professeur = Professeur(
            nom="Diallo",
            prenom="Mamadou",
            email="mamadou.diallo@lycee.edu",
            code_prof="PROF_DIALLO_01",
            ecole_id=self.ecole.id,
            utilisateur_id=self.prof_user.id
        )
        db.session.add(self.professeur)
        db.session.commit()

        # 5. Cours
        self.cours = Cours(
            nom="Mathématiques Spé",
            coefficient=4.0,
            ecole_id=self.ecole.id,
            classe_id=self.classe.id,
            professeur_id=None
        )
        db.session.add(self.cours)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    @patch("app.services.cours_notifications.send_school_email")
    def test_notifier_professeur_cours_assigne_succes(self, mock_send_email):
        """Vérifie l'envoi d'e-mail au professeur + copie d'archivage à l'école."""
        mock_send_email.return_value = True

        res = notifier_professeur_cours_assigne(
            cours=self.cours,
            professeur=self.professeur,
            async_send=False
        )
        self.assertTrue(res)

        # Doit appeler 2 fois send_school_email (1 au prof, 1 copie école)
        self.assertEqual(mock_send_email.call_count, 2)

        # Vérifier l'envoi au professeur
        first_call = mock_send_email.call_args_list[0]
        self.assertEqual(first_call.kwargs["ecole_id"], self.ecole.id)
        self.assertEqual(first_call.kwargs["to"], "mamadou.diallo@lycee.edu")
        self.assertIn("Attribution d'enseignement", first_call.kwargs["subject"])
        self.assertIn("Mathématiques Spé", first_call.kwargs["html_body"])

        # Vérifier la copie à l'école (Option B)
        second_call = mock_send_email.call_args_list[1]
        self.assertEqual(second_call.kwargs["ecole_id"], self.ecole.id)
        self.assertEqual(second_call.kwargs["to"], "contact@lycee-excellence.edu")
        self.assertIn("[Copie École]", second_call.kwargs["subject"])

    @patch("app.services.cours_notifications.send_school_email")
    def test_notifier_professeur_cours_assigne_gmail_non_connecte(self, mock_send_email):
        """Vérifie que si Gmail n'est pas connecté, l'action ne plante pas et n'envoie rien."""
        self.gmail_config.is_connected = False
        db.session.commit()

        res = notifier_professeur_cours_assigne(
            cours=self.cours,
            professeur=self.professeur,
            async_send=False
        )
        self.assertFalse(res)
        mock_send_email.assert_not_called()

    @patch("app.services.cours_notifications.send_school_email")
    def test_notifier_professeur_cours_assigne_professeur_sans_email(self, mock_send_email):
        """Vérifie que si le professeur n'a pas d'e-mail, aucune exception n'est levée et rien n'est envoyé."""
        self.professeur.email = ""
        self.prof_user.email = ""
        db.session.commit()

        res = notifier_professeur_cours_assigne(
            cours=self.cours,
            professeur=self.professeur,
            async_send=False
        )
        self.assertFalse(res)
        mock_send_email.assert_not_called()

    @patch("app.services.cours_notifications.notifier_professeur_cours_assigne")
    def test_route_affecter_professeur_cours_trigger(self, mock_notifier):
        """Vérifie que la route POST /cours/<id>/professeur déclenche la notification."""
        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['_fresh'] = True
                sess['ecole_id'] = self.ecole.id

            res = self.client.post(
                f"/cours/{self.cours.id}/professeur",
                data={"professeur_id": str(self.professeur.id)}
            )
            self.assertEqual(res.status_code, 302)
            mock_notifier.assert_called_once()
            args, _ = mock_notifier.call_args
            self.assertEqual(args[0].id, self.cours.id)
            self.assertEqual(args[1].id, self.professeur.id)

    @patch("app.services.cours_notifications.notifier_professeur_cours_assigne")
    def test_route_ajouter_cours_trigger(self, mock_notifier):
        """Vérifie que la route POST /ajouter_cours avec professeur déclenche la notification."""
        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['_fresh'] = True
                sess['ecole_id'] = self.ecole.id

            res = self.client.post(
                "/ajouter_cours",
                data={
                    "nom": "Physique-Chimie",
                    "description": "Cours de spécialité",
                    "coefficient": "3.0",
                    "classe_id": str(self.classe.id),
                    "professeur_id": str(self.professeur.id),
                }
            )
            self.assertEqual(res.status_code, 302)
            mock_notifier.assert_called_once()

    @patch("app.services.cours_notifications.notifier_professeur_cours_assigne")
    def test_route_assigner_classes_professeur_trigger(self, mock_notifier):
        """Vérifie que la route POST /professeur/<id>/assigner_classes déclenche la notification."""
        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['_fresh'] = True
                sess['ecole_id'] = self.ecole.id

            res = self.client.post(
                f"/professeur/{self.professeur.id}/assigner_classes",
                data={
                    "action": "assign",
                    "cours_id": str(self.cours.id)
                }
            )
            self.assertEqual(res.status_code, 302)
            mock_notifier.assert_called_once()


if __name__ == '__main__':
    unittest.main()
