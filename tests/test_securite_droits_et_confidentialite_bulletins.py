"""Régressions des droits d'appel, de publication et des archives."""

import unittest
from datetime import date

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import (
    Absence, AnneeScolaire, Bulletin, Classe, Cours, Ecole, Eleve,
    Inscription, PeriodeBulletin, Professeur, Utilisateur,
)
from app.services.bulletins_annuels import (
    generer_ou_recuperer_bulletin, modifier_appreciation_bulletin,
    supprimer_bulletin,
)
from app.services.notes_annuelles import erreur_verrou_notes


class ConfigSecuriteBulletins:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-securite-bulletins"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class SecuriteDroitsBulletinsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ConfigSecuriteBulletins)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = self.app.test_client()
        db.create_all()

        self.ecole = Ecole(nom="École sécurité", statut="actif", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.flush()
        self.ancienne = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30), statut="archivee", ecole_id=self.ecole.id,
        )
        self.annee = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30), statut="active", ecole_id=self.ecole.id,
        )
        db.session.add_all([self.ancienne, self.annee])
        db.session.flush()
        self.passee_publiee = PeriodeBulletin(
            nom="Semestre 1", annee_id=self.ancienne.id,
            ecole_id=self.ecole.id, publie=True,
        )
        self.periode_t1 = PeriodeBulletin(
            nom="Semestre 1", annee_id=self.annee.id,
            ecole_id=self.ecole.id, publie=False,
        )
        self.periode_t2 = PeriodeBulletin(
            nom="Semestre 2", annee_id=self.annee.id,
            ecole_id=self.ecole.id, publie=False, periode_active=True,
        )
        db.session.add_all([self.passee_publiee, self.periode_t1, self.periode_t2])

        def user(role, email):
            return Utilisateur(
                nom=role, prenom="Test", email=email,
                mot_de_passe=generate_password_hash("Secret123!"),
                role=role, ecole_id=self.ecole.id, statut="actif",
            )

        self.admin = user("admin", "admin@securite.test")
        self.parent = user("parent", "parent@securite.test")
        self.user_a = user("professeur", "prof-a@securite.test")
        self.user_b = user("professeur", "prof-b@securite.test")
        db.session.add_all([self.admin, self.parent, self.user_a, self.user_b])
        db.session.flush()
        self.prof_a = Professeur(
            nom="A", prenom="Prof", utilisateur_id=self.user_a.id,
            ecole_id=self.ecole.id,
        )
        self.prof_b = Professeur(
            nom="B", prenom="Prof", utilisateur_id=self.user_b.id,
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.prof_a, self.prof_b])
        db.session.flush()
        self.classe = Classe(
            nom="CM2", ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        self.classe_passee = Classe(
            nom="CM2", ecole_id=self.ecole.id,
            annee_scolaire_id=self.ancienne.id,
        )
        db.session.add_all([self.classe, self.classe_passee])
        db.session.flush()
        self.cours_a = Cours(
            nom="Maths", classe_id=self.classe.id,
            professeur_id=self.prof_a.id, ecole_id=self.ecole.id,
        )
        self.cours_b = Cours(
            nom="Français", classe_id=self.classe.id,
            professeur_id=self.prof_b.id, ecole_id=self.ecole.id,
        )
        self.eleve = Eleve(
            nom="Élève", prenom="Test", date_naissance=date(2013, 1, 1),
            parent_id=self.parent.id, ecole_id=self.ecole.id,
        )
        db.session.add_all([self.cours_a, self.cours_b, self.eleve])
        db.session.flush()
        self.inscription = Inscription(
            eleve_id=self.eleve.id, classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id,
            statut="inscrit",
        )
        self.inscription_passee = Inscription(
            eleve_id=self.eleve.id, classe_id=self.classe_passee.id,
            annee_scolaire_id=self.ancienne.id, ecole_id=self.ecole.id,
            statut="inscrit",
        )
        db.session.add_all([self.inscription, self.inscription_passee])
        db.session.flush()
        self.absence_a = Absence(
            eleve_id=self.eleve.id, inscription_id=self.inscription.id,
            cours_id=self.cours_a.id, ecole_id=self.ecole.id,
            date_absence=date(2026, 10, 10), motif="Saisie A",
        )
        db.session.add(self.absence_a)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["annee_id"] = self.annee.id
            session["annee_consultee"] = {str(self.ecole.id): self.annee.id}
            session["ecole_id"] = self.ecole.id
            session["role"] = user.role
            session["onboarding_complete"] = True
            session[f"onboarding_complete_{self.ecole.id}"] = True

    def test_professeur_b_ne_modifie_pas_absence_du_professeur_a(self):
        self._login(self.user_b)
        response = self.client.post("/api/sync", json=[{
            "type": "absence", "client_op_id": "absence-usurpation-1",
            "absence_id": self.absence_a.id,
            "eleve_id": self.eleve.id, "cours_id": self.cours_b.id,
            "date_absence": "2026-10-10", "base_version": 1,
            "motif": "Écrasé par B",
        }])
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json["success"])
        self.assertEqual(response.json["results"][0]["reason"], "CONFLIT_DROITS")
        self.assertEqual(db.session.get(Absence, self.absence_a.id).motif, "Saisie A")
        self.assertEqual(self.client.get(f"/absences/edit/{self.absence_a.id}").status_code, 403)
        self.assertEqual(self.client.post(f"/absences/delete/{self.absence_a.id}").status_code, 403)
        self.assertIsNotNone(db.session.get(Absence, self.absence_a.id))

    def test_parent_ne_voit_pas_t2_si_seul_t1_ancien_est_publie(self):
        self._login(self.parent)
        url = f"/bulletin/inscription/{self.inscription.id}?periode=Semestre%202"
        self.assertEqual(self.client.get(url).status_code, 403)
        page = self.client.get(f"/bulletins?periode_id={self.periode_t2.id}")
        self.assertIn(page.status_code, (200, 302, 403))
        if page.status_code == 200:
            self.assertNotIn("Test Élève", page.get_data(as_text=True))
        self.assertFalse(self.periode_t2.publie)

    def test_bulletin_archive_rejette_mutations_et_reouverture(self):
        bulletin = Bulletin(
            inscription_id=self.inscription.id, eleve_id=self.eleve.id,
            ecole_id=self.ecole.id, classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id, periode="Semestre 1",
            moyenne_generale=14.0, appreciation_generale="Scellé", statut="archive",
        )
        db.session.add(bulletin)
        db.session.commit()
        self._login(self.admin)
        result, error = generer_ou_recuperer_bulletin(
            self.ecole.id, self.annee, self.admin, self.inscription.id,
            periode="Semestre 1", appreciation_generale="Altéré",
        )
        self.assertIsNone(error)
        self.assertEqual(result.id, bulletin.id)
        self.assertEqual(result.appreciation_generale, "Scellé")
        self.assertFalse(supprimer_bulletin(self.ecole.id, self.annee, self.admin, bulletin.id)[0])
        self.assertIsNotNone(modifier_appreciation_bulletin(
            self.ecole.id, self.annee, self.admin, bulletin.id, "Altéré"
        )[1])
        self.assertEqual(self.client.post(f"/bulletin/{bulletin.id}/supprimer").status_code, 403)
        self.assertEqual(self.client.post(
            f"/bulletin/{bulletin.id}/appreciation", data={"appreciation": "Altéré"}
        ).status_code, 403)
        self.assertEqual(self.client.post(
            f"/bulletins/periodes/{self.periode_t1.id}/toggle-publication"
        ).status_code, 403)
        self.assertIsNotNone(erreur_verrou_notes(
            self.ecole.id, self.annee.id, "Semestre 1", self.inscription
        ))
        gestion = self.client.get("/periodes").get_data(as_text=True)
        self.assertNotIn(f"/toggle_periode/{self.periode_t1.id}", gestion)
        self.assertFalse(self.periode_t1.publie)
        self.assertEqual(db.session.get(Bulletin, bulletin.id).appreciation_generale, "Scellé")

    def test_publication_sans_notes_est_bloquee(self):
        self._login(self.admin)
        response = self.client.post(
            f"/bulletins/periodes/{self.periode_t2.id}/toggle-publication"
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(db.session.get(PeriodeBulletin, self.periode_t2.id).publie)
        with self.client.session_transaction() as session:
            messages = [message for _category, message in session.get("_flashes", [])]
        self.assertTrue(any("aucune note" in message for message in messages))
        self.assertEqual(self.client.post(
            f"/toggle_periode/{self.periode_t2.id}"
        ).status_code, 302)
        self.assertFalse(db.session.get(PeriodeBulletin, self.periode_t2.id).publie)


if __name__ == "__main__":
    unittest.main()
