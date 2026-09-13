import unittest
from datetime import date

from flask import template_rendered

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
    Note,
    Utilisateur,
)
from app.services.activation_annee import resynchroniser_classes_eleves_annee_active
from app.services.statistiques_annuelles import get_rapports_annuels


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase4ANettoyageLegacyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole 4A", statut="actif")
        self.ecole_b = Ecole(nom="Ecole B", statut="actif")
        db.session.add_all([self.ecole, self.ecole_b])
        db.session.flush()

        self.annee_a = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=self.ecole.id)
        self.annee_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole.id)
        self.annee_autre = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=self.ecole_b.id)
        db.session.add_all([self.annee_a, self.annee_b, self.annee_autre])
        db.session.flush()

        self.classe_a = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole.id, annee_scolaire_id=self.annee_a.id)
        self.classe_b = Classe(nom="5e B", niveau="5e", ecole_id=self.ecole.id, annee_scolaire_id=self.annee_b.id)
        self.classe_autre = Classe(nom="Autre", niveau="6e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_autre.id)
        db.session.add_all([self.classe_a, self.classe_b, self.classe_autre])
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="admin4a@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole.id)
        self.parent = Utilisateur(nom="Parent", email="parent4a@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole.id)
        db.session.add_all([self.admin, self.parent])
        db.session.flush()

        self.eleve = Eleve(nom="Diallo", prenom="Moussa", date_naissance=date(2013, 1, 1), ecole_id=self.ecole.id, parent_id=self.parent.id, classe_id=self.classe_a.id)
        self.eleve_cache_sans_inscription = Eleve(nom="Cache", prenom="Sans", date_naissance=date(2014, 1, 1), ecole_id=self.ecole.id, classe_id=self.classe_a.id)
        self.eleve_autre = Eleve(nom="Autre", prenom="Eleve", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_b.id, classe_id=self.classe_autre.id)
        db.session.add_all([self.eleve, self.eleve_cache_sans_inscription, self.eleve_autre])
        db.session.flush()

        self.insc_a = Inscription(ecole_id=self.ecole.id, eleve_id=self.eleve.id, classe_id=self.classe_a.id, annee_scolaire_id=self.annee_a.id, frais_annuels=1000)
        self.insc_b = Inscription(ecole_id=self.ecole.id, eleve_id=self.eleve.id, classe_id=self.classe_b.id, annee_scolaire_id=self.annee_b.id, frais_annuels=2000)
        self.insc_autre = Inscription(ecole_id=self.ecole_b.id, eleve_id=self.eleve_autre.id, classe_id=self.classe_autre.id, annee_scolaire_id=self.annee_autre.id)
        db.session.add_all([self.insc_a, self.insc_b, self.insc_autre])
        db.session.flush()

        self.cours_a = Cours(nom="Math", ecole_id=self.ecole.id, classe_id=self.classe_a.id)
        self.cours_b = Cours(nom="Math", ecole_id=self.ecole.id, classe_id=self.classe_b.id)
        db.session.add_all([self.cours_a, self.cours_b])
        db.session.flush()

        db.session.add_all([
            Note(valeur=11, coefficient=1, eleve_id=self.eleve.id, cours_id=self.cours_a.id, ecole_id=self.ecole.id, inscription_id=self.insc_a.id, annee_id=None),
            Note(valeur=17, coefficient=1, eleve_id=self.eleve.id, cours_id=self.cours_b.id, ecole_id=self.ecole.id, inscription_id=self.insc_b.id, annee_id=None),
            Absence(date_absence=date(2026, 10, 1), eleve_id=self.eleve.id, cours_id=self.cours_b.id, ecole_id=self.ecole.id, inscription_id=self.insc_b.id),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _client_as(self, user, annee=None):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["role"] = user.role
            session["ecole_id"] = user.ecole_id
            if annee:
                session["annee_consultee"] = {str(user.ecole_id): annee.id}
        return client

    def test_01_cache_actif_reste_jusqu_activation(self):
        self.assertEqual(self.eleve.classe_id, self.classe_a.id)
        self.annee_a.statut = "archivee"
        self.annee_b.statut = "active"
        db.session.commit()
        resynchroniser_classes_eleves_annee_active(self.ecole.id, self.annee_b.id)
        db.session.refresh(self.eleve)
        self.assertEqual(self.eleve.classe_id, self.classe_b.id)
        self.assertEqual(self.insc_a.classe_id, self.classe_a.id)
        self.assertEqual(self.insc_b.classe_id, self.classe_b.id)

    def test_02_rapports_ignorent_cache_eleve_classe_id(self):
        self.eleve.classe_id = None
        db.session.commit()
        data = get_rapports_annuels(self.ecole.id, self.annee_b)
        self.assertEqual(data["statistiques"]["total_eleves"], 1)
        self.assertEqual(data["statistiques"]["total_absences"], 1)
        self.assertEqual(data["classes_data"][0]["moyenne"], 17)

    def test_03_api_eleves_classe_utilise_inscription_active(self):
        self.eleve.classe_id = None
        db.session.commit()
        client = self._client_as(self.admin, self.annee_a)
        response = client.get(f"/api/eleves/classe/{self.classe_a.id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["eleves"]), 1)
        self.assertEqual(data["eleves"][0]["id"], self.eleve.id)

    def test_04_export_notes_pdf_utilise_inscription_pas_note_annee_id(self):
        client = self._client_as(self.admin, self.annee_a)
        response = client.get(f"/eleve/{self.eleve.id}/export_notes_pdf")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/pdf", response.content_type)

    def test_05_rapports_multi_ecole(self):
        data = get_rapports_annuels(self.ecole.id, self.annee_a)
        self.assertEqual(data["statistiques"]["total_eleves"], 1)
        self.assertNotIn("Autre", data["chart_data"]["labels"])

    def test_06_get_rapports_ne_mute_pas_session(self):
        client = self._client_as(self.admin, self.annee_a)
        with client.session_transaction() as session:
            avant = dict(session["annee_consultee"])
        response = client.get("/rapports")
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as session:
            self.assertEqual(session["annee_consultee"], avant)

    def test_07_qr_population_depend_de_inscription_active_pas_cache(self):
        client = self._client_as(self.admin, self.annee_a)
        response = client.get("/qrcodes_etudiants")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Moussa", response.data)
        self.assertNotIn(b"Sans Cache", response.data)

        db.session.add(Inscription(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_cache_sans_inscription.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id,
        ))
        db.session.commit()

        response = client.get("/qrcodes_etudiants")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sans Cache", response.data)


if __name__ == "__main__":
    unittest.main()
