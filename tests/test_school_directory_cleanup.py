"""Tests de sécurité et de conformité pour le nettoyage physique du répertoire école.

Vérifie :
1. Suppression physique de app/static/ecoles/<ecole_id>/ et de tous ses sous-fichiers.
2. Idempotence : si le dossier n'existe pas sur le disque, aucune erreur n'est levée.
3. Le dossier parent app/static/ecoles/ reste intact.
4. Les dossiers des autres écoles (ex: app/static/ecoles/<autre_id>/) restent intacts.
5. Intégration dans le flux de suppression définitive d'école.
6. Garde-fous anti-traversal et validation d'arguments.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.config import Config
from app.models import Ecole, Utilisateur
from app.services.school_lifecycle import SCHOOL_DELETE_CONFIRMATION_PHRASE
from app.utils import nettoyer_repertoire_ecole


class TestCleanSchoolConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SERVER_NAME = "klasora.test"


class SchoolDirectoryCleanupTestCase(unittest.TestCase):

    def setUp(self):
        self.temp_static = tempfile.mkdtemp(prefix="klasora_test_static_")
        self.app = create_app(TestCleanSchoolConfig)
        self.app.static_folder = self.temp_static
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Dossier de base static/ecoles
        self.ecoles_dir = os.path.join(self.temp_static, "ecoles")
        os.makedirs(self.ecoles_dir, exist_ok=True)

        self.super_admin = Utilisateur(
            nom="SuperAdmin",
            prenom="Root",
            email="superadmin@clean.test",
            role="super_admin",
            statut="actif",
        )
        self.super_admin.set_mot_de_passe("secret123")

        self.ecole_a = Ecole(nom="Ecole Alpha", statut="actif", onboarding_complete=True)
        self.ecole_b = Ecole(nom="Ecole Beta", statut="actif", onboarding_complete=True)

        db.session.add_all([self.super_admin, self.ecole_a, self.ecole_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if os.path.exists(self.temp_static):
            shutil.rmtree(self.temp_static, ignore_errors=True)

    def _client_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            if user.ecole_id:
                sess["ecole_id"] = user.ecole_id
        return client

    def test_nettoyer_repertoire_ecole_physical_deletion(self):
        """Vérifie que le dossier de l'école et ses fichiers internes sont supprimés."""
        school_a_dir = os.path.join(self.ecoles_dir, str(self.ecole_a.id))
        sub_dir = os.path.join(school_a_dir, "documents")
        os.makedirs(sub_dir, exist_ok=True)

        file_logo = os.path.join(school_a_dir, "logo_abc123.png")
        file_doc = os.path.join(sub_dir, "document.pdf")

        with open(file_logo, "w", encoding="utf-8") as f:
            f.write("dummy-logo-data")
        with open(file_doc, "w", encoding="utf-8") as f:
            f.write("dummy-pdf-data")

        self.assertTrue(os.path.exists(school_a_dir))
        self.assertTrue(os.path.isfile(file_logo))
        self.assertTrue(os.path.isfile(file_doc))

        res = nettoyer_repertoire_ecole(self.ecole_a.id, static_folder=self.temp_static)
        self.assertTrue(res)

        # Le dossier de l'école A et son contenu doivent être supprimés
        self.assertFalse(os.path.exists(school_a_dir))
        self.assertFalse(os.path.exists(file_logo))
        self.assertFalse(os.path.exists(file_doc))

    def test_nettoyer_repertoire_ecole_idempotent_when_missing(self):
        """Vérifie l'idempotence : aucune erreur si le dossier n'existe pas sur le disque."""
        school_a_dir = os.path.join(self.ecoles_dir, str(self.ecole_a.id))
        if os.path.exists(school_a_dir):
            shutil.rmtree(school_a_dir)

        self.assertFalse(os.path.exists(school_a_dir))

        # Doit renvoyer True sans exception
        res = nettoyer_repertoire_ecole(self.ecole_a.id, static_folder=self.temp_static)
        self.assertTrue(res)

    def test_nettoyer_repertoire_ecole_preserves_parent_directory(self):
        """Vérifie que le dossier parent static/ecoles/ reste intact."""
        school_a_dir = os.path.join(self.ecoles_dir, str(self.ecole_a.id))
        os.makedirs(school_a_dir, exist_ok=True)

        res = nettoyer_repertoire_ecole(self.ecole_a.id, static_folder=self.temp_static)
        self.assertTrue(res)

        # Le dossier parent existe toujours
        self.assertTrue(os.path.exists(self.ecoles_dir))
        self.assertTrue(os.path.isdir(self.ecoles_dir))

    def test_nettoyer_repertoire_ecole_preserves_other_schools(self):
        """Vérifie que les répertoires d'autres écoles ne sont pas touchés."""
        school_a_dir = os.path.join(self.ecoles_dir, str(self.ecole_a.id))
        school_b_dir = os.path.join(self.ecoles_dir, str(self.ecole_b.id))

        os.makedirs(school_a_dir, exist_ok=True)
        os.makedirs(school_b_dir, exist_ok=True)

        file_b = os.path.join(school_b_dir, "logo_beta.png")
        with open(file_b, "w", encoding="utf-8") as f:
            f.write("beta-logo-content")

        res = nettoyer_repertoire_ecole(self.ecole_a.id, static_folder=self.temp_static)
        self.assertTrue(res)

        # Ecole A est supprimée, Ecole B est préservée avec ses fichiers
        self.assertFalse(os.path.exists(school_a_dir))
        self.assertTrue(os.path.exists(school_b_dir))
        self.assertTrue(os.path.isfile(file_b))

    def test_nettoyer_repertoire_ecole_path_traversal_guards(self):
        """Vérifie que les tentatives d'évasion de chemin ou ID invalides sont bloquées."""
        # 1. ecole_id négatif ou zéro
        self.assertFalse(nettoyer_repertoire_ecole(0, static_folder=self.temp_static))
        self.assertFalse(nettoyer_repertoire_ecole(-1, static_folder=self.temp_static))
        # 2. ecole_id type non-int
        self.assertFalse(nettoyer_repertoire_ecole("1", static_folder=self.temp_static))  # type: ignore
        self.assertFalse(nettoyer_repertoire_ecole(None, static_folder=self.temp_static))  # type: ignore

        # Le dossier parent static/ecoles reste intact
        self.assertTrue(os.path.exists(self.ecoles_dir))

    def test_definitive_school_deletion_flow_triggers_cleanup(self):
        """Vérifie l'intégration de bout en bout : la suppression définitive d'une école purge son dossier."""
        # Configurer l'école A pour qu'elle soit éligible à la suppression (désactivée depuis > 30 jours)
        self.ecole_a.statut = "inactive"
        self.ecole_a.disabled_at = datetime.utcnow() - timedelta(days=31)
        db.session.commit()

        # Créer des fichiers dans static/ecoles/<ecole_a.id>/
        school_a_dir = os.path.join(self.ecoles_dir, str(self.ecole_a.id))
        os.makedirs(school_a_dir, exist_ok=True)
        logo_path = os.path.join(school_a_dir, "logo_alpha.png")
        with open(logo_path, "w", encoding="utf-8") as f:
            f.write("school-alpha-data")

        # Créer aussi un fichier pour l'école B
        school_b_dir = os.path.join(self.ecoles_dir, str(self.ecole_b.id))
        os.makedirs(school_b_dir, exist_ok=True)
        logo_b_path = os.path.join(school_b_dir, "logo_beta.png")
        with open(logo_b_path, "w", encoding="utf-8") as f:
            f.write("school-beta-data")

        client = self._client_as(self.super_admin)
        response = client.post(
            f"/admin/ecoles/{self.ecole_a.id}/supprimer",
            data={
                "confirmation_nom": self.ecole_a.nom,
                "confirmation_phrase": SCHOOL_DELETE_CONFIRMATION_PHRASE,
            },
        )
        self.assertEqual(response.status_code, 302)

        # L'école A n'existe plus en base
        self.assertIsNone(Ecole.query.get(self.ecole_a.id))

        # Le dossier sur disque de l'école A a été physiquement nettoyé
        self.assertFalse(os.path.exists(school_a_dir))

        # L'école B et ses fichiers existent toujours
        self.assertIsNotNone(Ecole.query.get(self.ecole_b.id))
        self.assertTrue(os.path.exists(school_b_dir))
        self.assertTrue(os.path.isfile(logo_b_path))
        self.assertTrue(os.path.exists(self.ecoles_dir))


if __name__ == "__main__":
    unittest.main()

