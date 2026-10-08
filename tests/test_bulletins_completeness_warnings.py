"""
Tests unitaires et d'intégration pour la complétude intelligente des bulletins scolaires :
- Alertes visuelles et infobulles sur matières manquantes (soft warnings)
- Synthèse d'incomplétude dans la modale de clôture (#modalCloturerPeriode)
- Neutralisation des matières non évaluées sur le bulletin PDF sans jamais bloquer l'impression
- Blocage de la clôture en présence d'élèves incomplets
"""
import unittest
from datetime import date

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    PeriodeBulletin,
    Utilisateur,
)
from app.services.bulletins_annuels import calculer_bulletin_data


class BulletinsCompletenessWarningsTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-completeness-warnings-bulletins"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class BulletinsCompletenessWarningsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(BulletinsCompletenessWarningsTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Établissement
        self.ecole = Ecole(
            nom="Académie du Savoir",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.commit()

        # Année scolaire
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.commit()

        # Période
        self.periode = PeriodeBulletin(
            nom="Semestre 1",
            annee_id=self.annee.id,
            ecole_id=self.ecole.id,
            publie=False,
            periode_active=True,
        )
        db.session.add(self.periode)
        db.session.commit()

        # Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Alain",
            email="directeur@academie.com",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.admin)
        db.session.commit()

        # Classe
        self.classe = Classe(
            nom="3ème A",
            niveau="3ème",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        db.session.add(self.classe)
        db.session.commit()

        # 3 Cours officiels de la classe
        self.cours_math = Cours(
            nom="Mathématiques",
            coefficient=2.0,
            ecole_id=self.ecole.id,
            classe_id=self.classe.id,
        )
        self.cours_fr = Cours(
            nom="Français",
            coefficient=2.0,
            ecole_id=self.ecole.id,
            classe_id=self.classe.id,
        )
        self.cours_eps = Cours(
            nom="Éducation Physique (EPS)",
            coefficient=1.0,
            ecole_id=self.ecole.id,
            classe_id=self.classe.id,
        )
        db.session.add_all([self.cours_math, self.cours_fr, self.cours_eps])
        db.session.commit()

        # Élève 1 (Kouassi Jean) : noté en Maths et Français, mais dispensé / non noté en EPS
        self.eleve1 = Eleve(nom="Kouassi", prenom="Jean", date_naissance=date(2011, 4, 10), ecole_id=self.ecole.id)
        # Élève 2 (Diallo Awa) : notée dans les 3 matières (dossier complet 100%)
        self.eleve2 = Eleve(nom="Diallo", prenom="Awa", date_naissance=date(2011, 7, 22), ecole_id=self.ecole.id)
        db.session.add_all([self.eleve1, self.eleve2])
        db.session.commit()

        self.ins1 = Inscription(eleve_id=self.eleve1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id, statut="inscrit")
        self.ins2 = Inscription(eleve_id=self.eleve2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id, statut="inscrit")
        db.session.add_all([self.ins1, self.ins2])
        db.session.commit()

        # Notes Élève 1 (Maths: 16.0, Français: 14.0) -> Pas d'EPS
        n1_math = Note(valeur=16.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=self.ins1.id, eleve_id=self.eleve1.id, cours_id=self.cours_math.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n1_fr = Note(valeur=14.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=self.ins1.id, eleve_id=self.eleve1.id, cours_id=self.cours_fr.id, ecole_id=self.ecole.id, annee_id=self.annee.id)

        # Notes Élève 2 (Maths: 12.0, Français: 14.0, EPS: 15.0) -> Complet
        n2_math = Note(valeur=12.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=self.ins2.id, eleve_id=self.eleve2.id, cours_id=self.cours_math.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n2_fr = Note(valeur=14.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=self.ins2.id, eleve_id=self.eleve2.id, cours_id=self.cours_fr.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n2_eps = Note(valeur=15.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=self.ins2.id, eleve_id=self.eleve2.id, cours_id=self.cours_eps.id, ecole_id=self.ecole.id, annee_id=self.annee.id)

        db.session.add_all([n1_math, n1_fr, n2_math, n2_fr, n2_eps])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['annee_id'] = self.annee.id
            sess['annee_consultee'] = {str(user.ecole_id): self.annee.id}
            sess['ecole_id'] = user.ecole_id
            sess['role'] = user.role
            sess['onboarding_complete'] = True

    def test_student_with_missing_subjects_context_and_soft_warnings(self):
        """
        Vérifie que l'élève avec des matières manquantes reçoit les alertes visuelles,
        l'infobulle Bootstrap descriptive et le blocage de clôture.
        """
        self._login(self.admin)
        resp = self.client.get(f"/bulletins?classe_id={self.classe.id}&periode_id={self.periode.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # 1. Vérifier le badge informatif de l'élève incomplet (Jean Kouassi)
        self.assertIn("1 manquante", html)
        self.assertIn("Éducation Physique (EPS)", html)
        self.assertIn('data-bs-toggle="tooltip"', html)

        # 2. Vérifier le badge complet de l'élève complète (Awa Diallo)
        self.assertIn("3/3 (Complet)", html)

        # 3. Vérifier la synthèse d'incomplétude dans la modale #modalCloturerPeriode
        self.assertIn("modalCloturerPeriode", html)
        self.assertIn("Attention : 1 élève", html)
        self.assertIn("Jean Kouassi", html)
        self.assertIn("La publication est bloquée", html)

    def test_pdf_data_neutralizes_missing_subjects_coefficients(self):
        """
        Vérifie que pour un dossier incomplet :
        - La matière non notée apparaît avec 'Non évalué' (jamais 0/20).
        - Son coefficient est retiré du total_coefficients diviseur.
        - La moyenne générale reflète strictement les matières notées sans pénalisation arbitraire.
        """
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole.id,
            annee=self.annee,
            inscription=self.ins1,
            periode="Semestre 1",
            periode_publiee=False
        )
        self.assertIsNone(err)
        self.assertIsNotNone(data)

        # Vérifier la présence des 3 disciplines officielles
        disciplines = data['disciplines']
        self.assertEqual(len(disciplines), 3)

        disc_eps = next((d for d in disciplines if d['cours_nom'] == "Éducation Physique (EPS)"), None)
        self.assertIsNotNone(disc_eps)
        # La matière manquante doit être explicitement non finalisée avec mention 'Non évalué'
        self.assertFalse(disc_eps['est_finalisee'])
        self.assertIsNone(disc_eps['moyenne_semestre'])
        self.assertEqual(disc_eps['appreciation'], "Non évalué")

        # Diviseur des coefficients : uniquement Maths (2) + Français (2) = 4.0 (EPS coef 1.0 retiré)
        self.assertEqual(data['total_coefficients'], 4.0)

        # Total des points : (16*2) + (14*2) = 32 + 28 = 60.0
        self.assertEqual(data['total_points'], 60.0)

        # Moyenne générale : 60.0 / 4.0 = 15.0 (et non 60/5 = 12.0 si on avait compté 0 en EPS)
        self.assertEqual(data['moyenne_generale'], 15.0)

    def test_individual_and_bulk_pdf_routes_accessible_never_blocked(self):
        """
        Vérifie que les téléchargements PDF (individuel et groupe) restent accessibles (HTTP 200)
        et ne sont JAMAIS bloqués pour les dossiers incomplets.
        """
        self._login(self.admin)

        # 1. Téléchargement individuel de l'élève incomplet
        url_single = f"/bulletin_eleve/{self.eleve1.id}?inscription_id={self.ins1.id}&periode=Semestre 1"
        resp_single = self.client.get(url_single)
        self.assertEqual(resp_single.status_code, 200)
        self.assertEqual(resp_single.mimetype, "application/pdf")
        self.assertTrue(len(resp_single.data) > 1000)

        # 2. Téléchargement groupé pour la classe entière
        url_bulk = f"/bulletins/classes/{self.classe.id}/export-pdf-groupe?periode_id={self.periode.id}"
        resp_bulk = self.client.get(url_bulk)
        self.assertEqual(resp_bulk.status_code, 200)
        self.assertEqual(resp_bulk.mimetype, "application/pdf")
        self.assertTrue(len(resp_bulk.data) > 1000)

    def test_cloture_refusee_avec_eleves_incomplets(self):
        """
        Vérifie que la clôture officielle refuse un dossier pédagogique incomplet.
        """
        self._login(self.admin)
        url_toggle = f"/bulletins/periodes/{self.periode.id}/toggle-publication"

        # Action : clôturer la période
        resp = self.client.post(url_toggle, data={"classe_id": str(self.classe.id)})
        self.assertEqual(resp.status_code, 302)

        # Vérifier en BDD
        p_reloaded = db.session.get(PeriodeBulletin, self.periode.id)
        self.assertFalse(p_reloaded.publie)

        # Le rechargement reste en mode provisoire.
        resp_page = self.client.get(resp.headers.get("Location", ""))
        self.assertEqual(resp_page.status_code, 200)
        html = resp_page.get_data(as_text=True)
        self.assertIn("Saisie en cours (Notes provisoires)", html)


if __name__ == "__main__":
    unittest.main()
