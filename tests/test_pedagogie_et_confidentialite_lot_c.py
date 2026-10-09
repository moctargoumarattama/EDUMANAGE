"""Régressions du lot C sur les routes et services réels, avec SQLite en mémoire."""

import unittest
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire, Bulletin, Classe, Cours, Ecole, Eleve, Inscription, Note,
    PeriodeBulletin, Professeur, Utilisateur,
)
from app.services.bulletins_annuels import verifier_publication_periode
from app.services.notes_annuelles import creer_note
from app.services.passage_annee import evaluer_deliberation_annuelle


class LotCTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-lot-c-pedagogie-confidentialite"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestPedagogieEtConfidentialiteLotC(unittest.TestCase):
    def setUp(self):
        self.app = create_app(LotCTestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.ecole = Ecole(nom="École Lot C", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.flush()

        self.active = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30), statut="active", ecole_id=self.ecole.id,
        )
        self.archivee = AnneeScolaire(
            nom="2024-2025", date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30), statut="archivee", ecole_id=self.ecole.id,
        )
        db.session.add_all([self.active, self.archivee])
        db.session.flush()

        self.admin = Utilisateur(
            nom="Admin", prenom="Lot C", email="admin-lot-c@example.test",
            role="admin", statut="actif", ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("Test1234!"),
        )
        self.prof_user = Utilisateur(
            nom="Prof", prenom="Lot C", email="prof-lot-c@example.test",
            role="professeur", statut="actif", ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("Test1234!"),
        )
        db.session.add_all([self.admin, self.prof_user])
        db.session.flush()

        self.prof = Professeur(
            nom="Prof", prenom="Lot C", email="professeur-lot-c@example.test",
            ecole_id=self.ecole.id, utilisateur_id=self.prof_user.id,
        )
        db.session.add(self.prof)
        db.session.flush()

        self.classe = Classe(
            nom="6e Active", niveau="6e", ecole_id=self.ecole.id,
            annee_scolaire_id=self.active.id,
        )
        self.classe_archivee = Classe(
            nom="6e Archivée", niveau="6e", ecole_id=self.ecole.id,
            annee_scolaire_id=self.archivee.id,
        )
        db.session.add_all([self.classe, self.classe_archivee])
        db.session.flush()

        self.maths = Cours(
            nom="Mathématiques", coefficient=2.0, ecole_id=self.ecole.id,
            classe_id=self.classe.id, professeur_id=self.prof.id,
        )
        self.francais = Cours(
            nom="Français", coefficient=2.0, ecole_id=self.ecole.id,
            classe_id=self.classe.id, professeur_id=self.prof.id,
        )
        self.cours_archive = Cours(
            nom="Histoire archivée", coefficient=3.0, ecole_id=self.ecole.id,
            classe_id=self.classe_archivee.id, professeur_id=self.prof.id,
        )
        db.session.add_all([self.maths, self.francais, self.cours_archive])
        db.session.flush()

        self.eleve = Eleve(
            nom="Élève", prenom="Inscrit", date_naissance=date(2012, 3, 1),
            ecole_id=self.ecole.id, statut="actif", frais_annuels=100000.0,
        )
        db.session.add(self.eleve)
        db.session.flush()
        self.inscription = Inscription(
            eleve_id=self.eleve.id, ecole_id=self.ecole.id,
            annee_scolaire_id=self.active.id, classe_id=self.classe.id,
            statut="inscrit",
        )
        db.session.add(self.inscription)

        self.s1 = PeriodeBulletin(
            nom="Semestre 1", ecole_id=self.ecole.id, annee_id=self.active.id,
            date_debut=date(2025, 9, 1), date_fin=date(2026, 1, 31),
            periode_active=True, publie=False,
        )
        self.s2 = PeriodeBulletin(
            nom="Semestre 2", ecole_id=self.ecole.id, annee_id=self.active.id,
            date_debut=date(2026, 2, 1), date_fin=date(2026, 6, 30),
            periode_active=False, publie=False,
        )
        db.session.add_all([self.s1, self.s2])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _login(self, utilisateur, annee=None):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(utilisateur.id)
            session["_fresh"] = True
            session["role"] = utilisateur.role
            session["ecole_id"] = self.ecole.id
            session["onboarding_complete"] = True
            session[f"onboarding_complete_{self.ecole.id}"] = True
            session["annee_consultee"] = {str(self.ecole.id): (annee or self.active).id}

    def _note(self, cours, periode, valeur=16.0, inscription=None):
        inscription = inscription or self.inscription
        note = Note(
            ecole_id=self.ecole.id, annee_id=self.active.id,
            inscription_id=inscription.id, eleve_id=inscription.eleve_id,
            cours_id=cours.id, valeur=valeur, coefficient=1.0,
            type_evaluation="Devoir", periode=periode,
            date_evaluation=datetime(2025, 11, 10),
        )
        db.session.add(note)
        db.session.commit()
        return note

    def _inscription_annulee(self):
        eleve = Eleve(
            nom="Désisté", prenom="Élève", date_naissance=date(2012, 4, 2),
            ecole_id=self.ecole.id, statut="actif",
        )
        db.session.add(eleve)
        db.session.flush()
        inscription = Inscription(
            eleve_id=eleve.id, ecole_id=self.ecole.id,
            annee_scolaire_id=self.active.id, classe_id=self.classe.id,
            statut="annulee",
        )
        db.session.add(inscription)
        db.session.commit()
        return inscription

    def test_cours_archive_refuse_modification_coefficient(self):
        self._login(self.admin, self.archivee)
        response = self.client.post(
            f"/cours/{self.cours_archive.id}/modifier",
            data={
                "nom": self.cours_archive.nom,
                "description": "Modification interdite",
                "coefficient": "9",
                "professeur_id": str(self.prof.id),
                "classe_id": str(self.classe_archivee.id),
            },
        )
        self.assertEqual(response.status_code, 403)
        db.session.refresh(self.cours_archive)
        self.assertEqual(self.cours_archive.coefficient, 3.0)
        self.assertNotEqual(self.cours_archive.description, "Modification interdite")

    def test_professeur_recoit_fiche_sans_finances(self):
        self._login(self.prof_user)
        with patch(
            "app.services.paiements_annuels.obtenir_synthese_financiere_eleve",
            side_effect=AssertionError("Le professeur ne doit pas lancer de calcul financier"),
        ):
            response = self.client.get(f"/api/eleves/{self.eleve.id}/fiche")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:800])
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertNotIn("comptabilite", data)
        self.assertNotIn("finances", data)
        self.assertNotIn("frais_annuels", data["eleve"])

        self._login(self.admin)
        admin_response = self.client.get(f"/api/eleves/{self.eleve.id}/fiche")
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn("comptabilite", admin_response.get_json())

    def test_deliberation_matiere_manquante_malgre_deux_semestres_notes(self):
        self._note(self.maths, "Semestre 1", 18.0)
        self._note(self.maths, "Semestre 2", 18.0)
        resultat = evaluer_deliberation_annuelle(self.ecole.id, self.active.id, self.eleve.id)
        self.assertTrue(resultat["cursus_incomplet"])
        self.assertEqual(resultat["statut_deliberation"], "Dossier Incomplet")
        self.assertIn("Français", resultat["missing_subjects_names"])
        self.assertEqual(resultat["suggestion"], "Décision réservée au conseil (matières manquantes)")

    def test_bulletins_annuels_ne_remplacent_pas_notes_manquantes_au_s2(self):
        self._note(self.maths, "Semestre 1", 18.0)
        note_sans_periode = self._note(self.maths, "Semestre 1", 17.0)
        note_sans_periode.periode = None
        db.session.commit()
        db.session.refresh(note_sans_periode)
        self.assertIsNone(note_sans_periode.periode)
        db.session.add_all([
            Bulletin(
                ecole_id=self.ecole.id, annee_scolaire_id=self.active.id,
                inscription_id=self.inscription.id, eleve_id=self.eleve.id,
                classe_id=self.classe.id, periode=periode,
                moyenne_generale=18.0, statut="valide",
            )
            for periode in ("Semestre 1", "Semestre 2")
        ])
        db.session.commit()

        resultat = evaluer_deliberation_annuelle(self.ecole.id, self.active.id, self.eleve.id)
        self.assertTrue(resultat["cursus_incomplet"])
        self.assertEqual(resultat["statut_deliberation"], "Dossier Incomplet")
        self.assertIn("Mathématiques", resultat["missing_subjects_by_period"]["Semestre 2"])
        self.assertEqual(resultat["suggestion"], "Décision réservée au conseil (matières manquantes)")

    def test_note_zero_est_enregistree_par_formulaire_individuel(self):
        note = self._note(self.maths, "Semestre 1", 12.0)
        self._login(self.admin)
        response = self.client.post(
            f"/note/{note.id}/modifier",
            data={
                "eleve_id": str(self.eleve.id),
                "cours_id": str(self.maths.id),
                "annee_id": str(self.active.id),
                "valeur": "0",
                "coefficient": "1",
                "type_evaluation": "Devoir",
                "periode": "Semestre 1",
            },
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:800])
        db.session.refresh(note)
        self.assertEqual(note.valeur, 0.0)

    def test_inscription_annulee_ne_bloque_pas_publication(self):
        self._note(self.maths, "Semestre 1", 14.0)
        self._note(self.francais, "Semestre 1", 13.0)
        annulee = self._inscription_annulee()
        autorise, erreur = verifier_publication_periode(self.s1)
        self.assertTrue(autorise, erreur)

        self._login(self.admin)
        page = self.client.get(f"/bulletins?periode_id={self.s1.id}")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        position = html.index("Confirmer la clôture")
        bouton = html[html.rfind("<button", 0, position):position]
        self.assertNotIn("disabled", bouton)
        etat_json = self.client.get(f"/bulletins?periode_id={self.s1.id}&ajax=1")
        self.assertEqual(etat_json.status_code, 200)
        self.assertEqual(etat_json.get_json()["total_eleves_incomplets"], 0)

        response = self.client.post(f"/bulletins/periodes/{self.s1.id}/toggle-publication")
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.s1)
        self.assertTrue(self.s1.publie)
        self.assertEqual(Note.query.filter_by(inscription_id=annulee.id).count(), 0)

    def test_inscription_annulee_refuse_nouvelle_note(self):
        annulee = self._inscription_annulee()
        note, erreur = creer_note(
            ecole_id=self.ecole.id, annee=self.active, user=self.admin,
            eleve_id=annulee.eleve_id, cours_id=self.maths.id,
            valeur=15.0, periode="Semestre 1",
        )
        self.assertIsNone(note)
        self.assertTrue(erreur)
        self.assertEqual(Note.query.filter_by(inscription_id=annulee.id).count(), 0)


if __name__ == "__main__":
    unittest.main()
