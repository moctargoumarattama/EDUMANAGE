"""
Tests unitaires pour l'Interface Utilisateur (UI/UX) du Droit à l'Erreur & Réintégration (KLASORA):
1. Bandeau et modal de réintégration sur la fiche élève (voir_eleve.html) pour un élève non inscrit.
2. Absence du bandeau pour un élève déjà inscrit.
3. Bouton et modal de recherche rapide sur l'annuaire des élèves (eleves.html).
4. Soumission de la réintégration (statut 'inscrit' direct et statut 'preinscrit').
5. Disparition du bandeau dès que l'élève est réintégré.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class UIDroitErreurTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-ui-droit-erreur"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestUIDroitErreur(unittest.TestCase):
    def setUp(self):
        self.app = create_app(UIDroitErreurTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Établissement
        self.ecole = Ecole(
            nom="École Pilote Klasora",
            adresse="Niamey",
            telephone="90000000",
            email="contact@ecolepilote.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # 2. Utilisateur Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Test",
            email="directeur@ecolepilote.ne",
            mot_de_passe=generate_password_hash("secret123"),
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.admin)

        # 3. Année Scolaire Active
        self.annee_active = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
        )
        db.session.add(self.annee_active)
        db.session.flush()

        # 4. Niveau et Classe
        self.niveau = NiveauScolaire(
            nom="6ème", code="6E", ordre=1, cycle="college"
        )
        db.session.add(self.niveau)
        db.session.flush()

        self.classe = Classe(
            nom="6ème A",
            niveau="6ème",
            niveau_id=self.niveau.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id,
            statut="ouverte",
        )
        db.session.add(self.classe)
        db.session.flush()

        # 5. Élève Déjà Inscrit
        self.eleve_inscrit = Eleve(
            nom="Diallo",
            prenom="Amadou",
            genre="M",
            date_naissance=date(2013, 3, 15),
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.eleve_inscrit)
        db.session.flush()

        self.insc_active = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_inscrit.id,
            annee_scolaire_id=self.annee_active.id,
            classe_id=self.classe.id,
            statut="inscrit",
            date_inscription=datetime.utcnow(),
            frais_annuels=120000.0,
        )
        db.session.add(self.insc_active)

        # 6. Ancien Élève Non Inscrit
        self.eleve_non_inscrit = Eleve(
            nom="Kaboré",
            prenom="Aïcha",
            genre="F",
            date_naissance=date(2013, 7, 22),
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.eleve_non_inscrit)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['user_id'] = self.admin.id
            sess['ecole_id'] = self.ecole.id
            sess['annee_active_id'] = self.annee_active.id
            sess['annee_consultee_id'] = self.annee_active.id

    def test_01_fiche_eleve_non_inscrit_affiche_bandeau_et_modal(self):
        """La fiche d'un élève non inscrit affiche le bandeau d'alerte et le modal de réintégration."""
        self._login_admin()

        resp = self.client.get(f"/eleve/{self.eleve_non_inscrit.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Vérification du bandeau
        self.assertIn("Élève non inscrit pour l'année scolaire en cours", html)
        self.assertIn("Droit à l'erreur", html)
        self.assertIn("Réintégrer pour l'année en cours", html)
        self.assertIn("#modalReinscrireEleve", html)

        # Vérification du modal
        self.assertIn('id="modalReinscrireEleve"', html)
        self.assertIn("Classe de destination", html)
        self.assertIn("6ème A", html)
        self.assertIn('value="inscrit"', html)
        self.assertIn('value="preinscrit"', html)
        self.assertIn("Inscrit direct", html)
        self.assertIn("Préinscrit", html)

    def test_02_fiche_eleve_inscrit_masque_bandeau_et_modal(self):
        """La fiche d'un élève déjà inscrit ne doit pas afficher le bandeau de réintégration."""
        self._login_admin()

        resp = self.client.get(f"/eleve/{self.eleve_inscrit.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertNotIn("Élève non inscrit pour l'année scolaire en cours", html)
        self.assertNotIn('id="modalReinscrireEleve"', html)

    def test_03_annuaire_eleves_affiche_bouton_et_modal_recherche_ancien(self):
        """L'annuaire des élèves propose le bouton 'Réintégrer un ancien élève' et son modal de recherche."""
        self._login_admin()

        resp = self.client.get("/eleves")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Bouton dans la barre d'outils
        self.assertIn("Réintégrer un ancien élève", html)
        self.assertIn("#modalRechercheAncienEleve", html)

        # Modal et son contenu
        self.assertIn('id="modalRechercheAncienEleve"', html)
        self.assertIn('id="searchAncienEleveInput"', html)
        self.assertIn('id="anciensElevesListContainer"', html)
        self.assertIn("Kaboré", html)
        self.assertIn("Aïcha", html)
        self.assertIn("Choisir", html)

    def test_04_post_reinscription_inscrit_met_a_jour_fiche_et_supprime_bandeau(self):
        """POST /annees/<id>/reinscrire_eleve réinscrit l'élève avec succès et supprime le bandeau."""
        self._login_admin()

        # Soumission de la réintégration
        resp = self.client.post(
            f"/annees/{self.annee_active.id}/reinscrire_eleve",
            data={
                "eleve_id": self.eleve_non_inscrit.id,
                "classe_cible_id": self.classe.id,
                "statut": "inscrit",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        # Vérification en base de données
        insc = Inscription.query.filter_by(
            eleve_id=self.eleve_non_inscrit.id,
            annee_scolaire_id=self.annee_active.id,
        ).first()
        self.assertIsNotNone(insc)
        self.assertEqual(insc.statut, "inscrit")
        self.assertEqual(insc.classe_id, self.classe.id)

        # Re-consultation de la fiche élève : le bandeau a disparu
        resp_fiche = self.client.get(f"/eleve/{self.eleve_non_inscrit.id}")
        self.assertEqual(resp_fiche.status_code, 200)
        html_fiche = resp_fiche.data.decode("utf-8")
        self.assertNotIn("Élève non inscrit pour l'année scolaire en cours", html_fiche)
        self.assertIn("6ème A", html_fiche)

    def test_05_post_reinscription_statut_preinscrit(self):
        """Réintégration avec statut 'preinscrit' en attente d'acompte."""
        self._login_admin()

        autre_ancien = Eleve(
            nom="Traoré",
            prenom="Moussa",
            genre="M",
            date_naissance=date(2013, 8, 10),
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(autre_ancien)
        db.session.commit()

        resp = self.client.post(
            f"/annees/{self.annee_active.id}/reinscrire_eleve",
            data={
                "eleve_id": autre_ancien.id,
                "classe_cible_id": self.classe.id,
                "statut": "preinscrit",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        insc = Inscription.query.filter_by(
            eleve_id=autre_ancien.id,
            annee_scolaire_id=self.annee_active.id,
        ).first()
        self.assertIsNotNone(insc)
        self.assertEqual(insc.statut, "preinscrit")


if __name__ == "__main__":
    unittest.main()
