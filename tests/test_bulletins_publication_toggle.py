"""
Tests unitaires et d'intégration pour le chantier P2 :
Action directe de clôture/publication et de réouverture de période
depuis la page des bulletins scolaires (/bulletins/periodes/<id>/toggle-publication).
"""
import unittest
from datetime import date, datetime

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
    JournalCorrection,
    Note,
    PeriodeBulletin,
    Utilisateur,
)


class BulletinsPublicationToggleTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-toggle-publication-bulletins"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class BulletinsPublicationToggleTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(BulletinsPublicationToggleTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Établissement A
        self.ecole_a = Ecole(
            nom="École Principale",
            statut="actif",
            onboarding_complete=True,
        )
        # Établissement B (pour test multi-tenant)
        self.ecole_b = Ecole(
            nom="École Secondaire",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()

        # Année scolaire A
        self.annee_a = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        # Année scolaire B
        self.annee_b = AnneeScolaire(
            nom="2025-2026 B",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_a, self.annee_b])
        db.session.commit()

        # Période pour École A (initialement non publiée)
        self.periode_a = PeriodeBulletin(
            nom="Semestre 1",
            annee_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            publie=False,
            periode_active=True,
        )
        # Période pour École B
        self.periode_b = PeriodeBulletin(
            nom="Semestre 1 B",
            annee_id=self.annee_b.id,
            ecole_id=self.ecole_b.id,
            publie=False,
            periode_active=True,
        )
        db.session.add_all([self.periode_a, self.periode_b])
        db.session.commit()

        # Utilisateur Admin École A
        self.admin_a = Utilisateur(
            nom="Admin",
            prenom="Directeur",
            email="admin@ecole-a.com",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole_a.id,
            statut="actif",
        )
        # Utilisateur Parent École A (non autorisé à clôturer)
        self.parent_a = Utilisateur(
            nom="Parent",
            prenom="Famille",
            email="parent@ecole-a.com",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="parent",
            ecole_id=self.ecole_a.id,
            statut="actif",
        )
        db.session.add_all([self.admin_a, self.parent_a])
        db.session.commit()

        # Classe & Élève
        self.classe = Classe(
            nom="CM2 A",
            niveau="CM2",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a.id,
        )
        db.session.add(self.classe)
        db.session.commit()

        self.cours = Cours(
            nom="Mathématiques",
            coefficient=2.0,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe.id,
        )
        self.eleve = Eleve(
            nom="Kouassi",
            prenom="Jean",
            date_naissance=date(2013, 3, 15),
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([self.cours, self.eleve])
        db.session.commit()

        self.ins = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            statut="inscrit",
        )
        db.session.add(self.ins)
        db.session.commit()

        self.note = Note(
            valeur=17.0,
            coefficient=1.0,
            type_evaluation="Devoir",
            periode="Semestre 1",
            inscription_id=self.ins.id,
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        db.session.add(self.note)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['annee_id'] = user.ecole_id and self.annee_a.id
            sess['annee_consultee'] = {str(user.ecole_id): self.annee_a.id}
            sess['ecole_id'] = user.ecole_id
            sess['role'] = user.role
            sess['onboarding_complete'] = True
            if user.ecole_id:
                sess[f'onboarding_complete_{user.ecole_id}'] = True

    def test_toggle_publication_unauthenticated_or_unauthorized(self):
        """Vérifie le rejet des utilisateurs non connectés ou non administrateurs."""
        url = f"/bulletins/periodes/{self.periode_a.id}/toggle-publication"

        # 1. Non connecté -> Redirection vers login
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers.get("Location", ""))

        # 2. Connecté en tant que parent -> 403 Interdit
        self._login(self.parent_a)
        resp_parent = self.client.post(url)
        self.assertEqual(resp_parent.status_code, 403)

    def test_toggle_publication_multitenant_isolation(self):
        """Vérifie qu'un administrateur ne peut pas clôturer une période d'un autre établissement."""
        self._login(self.admin_a)
        url_autre_ecole = f"/bulletins/periodes/{self.periode_b.id}/toggle-publication"

        resp = self.client.post(url_autre_ecole)
        self.assertEqual(resp.status_code, 404)

    def test_toggle_publication_cloture_et_reouverture_complete(self):
        """Vérifie le cycle complet Clôture -> Rangs officiels -> Réouverture -> Provisoire."""
        self._login(self.admin_a)
        url = f"/bulletins/periodes/{self.periode_a.id}/toggle-publication"

        # 1. État initial : non publiée
        self.assertFalse(self.periode_a.publie)
        self.assertFalse(self.periode_a.est_publie)

        # 2. Action : Clôturer la période via POST
        resp_cloture = self.client.post(url, data={
            "classe_id": str(self.classe.id),
            "search": "Jean"
        })
        self.assertEqual(resp_cloture.status_code, 302)
        location = resp_cloture.headers.get("Location", "")
        self.assertIn("classe_id=" + str(self.classe.id), location)
        self.assertIn("periode_id=" + str(self.periode_a.id), location)
        self.assertIn("search=Jean", location)

        # Vérifier la persistance en BDD
        p_reloaded = db.session.get(PeriodeBulletin, self.periode_a.id)
        self.assertTrue(p_reloaded.publie)
        self.assertTrue(p_reloaded.est_publie)
        self.assertIsNotNone(p_reloaded.date_publication)

        # Vérifier le journal d'audit
        log_cloture = JournalCorrection.query.filter_by(
            action="BULLETIN_CLOTURE_OFFICIEL",
            cible_id=self.periode_a.id
        ).first()
        self.assertIsNotNone(log_cloture)
        self.assertEqual(log_cloture.user_id, self.admin_a.id)

        # Vérifier la page des bulletins rechargée en mode officiel
        resp_page_officielle = self.client.get(location)
        self.assertEqual(resp_page_officielle.status_code, 200)
        html_officiel = resp_page_officielle.get_data(as_text=True)
        # La période doit afficher son badge officiel
        self.assertIn("Période clôturée (Officielle)", html_officiel)
        self.assertIn("Rouvrir la saisie", html_officiel)
        # L'élève étant à 100% complété, son rang officiel #1 doit être présent
        self.assertIn("#1", html_officiel)

        # 3. Action réversible : Rouvrir la période pour modification
        resp_reouvrir = self.client.post(url, data={
            "classe_id": str(self.classe.id)
        })
        self.assertEqual(resp_reouvrir.status_code, 302)

        # Vérifier la réouverture en BDD
        p_reouvert = db.session.get(PeriodeBulletin, self.periode_a.id)
        self.assertFalse(p_reouvert.publie)
        self.assertFalse(p_reouvert.est_publie)

        # Journal d'audit de réouverture
        log_reouverture = JournalCorrection.query.filter_by(
            action="BULLETIN_REOUVERT_SAISIE",
            cible_id=self.periode_a.id
        ).first()
        self.assertIsNotNone(log_reouverture)

        # Vérifier la page des bulletins en mode réouvert / provisoire
        resp_page_provisoire = self.client.get(resp_reouvrir.headers.get("Location", ""))
        self.assertEqual(resp_page_provisoire.status_code, 200)
        html_provisoire = resp_page_provisoire.get_data(as_text=True)
        self.assertIn("Saisie en cours (Notes provisoires)", html_provisoire)
        self.assertIn("Clôturer la période", html_provisoire)
        # Le rang est calculé en temps réel (estimé/provisoire)
        self.assertIn("(Estimé)", html_provisoire)
        self.assertIn("Provisoire", html_provisoire)

    def test_cloture_periode_calcul_et_affichage_officiel_complet(self):
        """
        Vérifie qu'à la clôture d'une période :
        1. Avant clôture : moyennes, rangs estimés (#1 (Estimé)) et statistiques réelles sont calculés en temps réel (Provisoire).
        2. À la clôture : tous les élèves évalués reçoivent leur rang officiel (#1, #2, ...), mentions officielles et statut Officiel scellé.
        """
        self._login(self.admin_a)

        # Ajouter un 2e cours
        cours2 = Cours(
            nom="Français",
            coefficient=2.0,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe.id,
        )
        # Élève 2 : seulement noté en Français (14.0) -> partiel
        eleve2 = Eleve(nom="Diallo", prenom="Awa", date_naissance=date(2013, 5, 20), ecole_id=self.ecole_a.id)
        # Élève 3 : noté en Maths (8.0) et Français (8.0) -> faible
        eleve3 = Eleve(nom="Traore", prenom="Moussa", date_naissance=date(2013, 8, 10), ecole_id=self.ecole_a.id)
        db.session.add_all([cours2, eleve2, eleve3])
        db.session.commit()

        ins2 = Inscription(eleve_id=eleve2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee_a.id, ecole_id=self.ecole_a.id, statut="inscrit")
        ins3 = Inscription(eleve_id=eleve3.id, classe_id=self.classe.id, annee_scolaire_id=self.annee_a.id, ecole_id=self.ecole_a.id, statut="inscrit")
        db.session.add_all([ins2, ins3])
        db.session.commit()

        # Ins1 a déjà Maths 17.0, ajoutons lui Français 15.0 -> Moyenne (17*2 + 15*2)/4 = 16.0 (Excellent)
        n1_bis = Note(valeur=15.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=self.ins.id, eleve_id=self.eleve.id, cours_id=cours2.id, ecole_id=self.ecole_a.id, annee_id=self.annee_a.id)
        # Ins2 a seulement Français 14.0 (partiel) -> Moyenne 14.0 (Très bien)
        n2 = Note(valeur=14.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=ins2.id, eleve_id=eleve2.id, cours_id=cours2.id, ecole_id=self.ecole_a.id, annee_id=self.annee_a.id)
        # Ins3 a Maths 8.0 et Français 8.0 -> Moyenne 8.0 (Insuffisant)
        n3_a = Note(valeur=8.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=ins3.id, eleve_id=eleve3.id, cours_id=self.cours.id, ecole_id=self.ecole_a.id, annee_id=self.annee_a.id)
        n3_b = Note(valeur=8.0, coefficient=1.0, type_evaluation="Devoir", periode="Semestre 1", inscription_id=ins3.id, eleve_id=eleve3.id, cours_id=cours2.id, ecole_id=self.ecole_a.id, annee_id=self.annee_a.id)
        db.session.add_all([n1_bis, n2, n3_a, n3_b])
        db.session.commit()

        # 1. Avant clôture : période non publiée -> calculs en temps réel au fil de l'eau
        resp_before = self.client.get(f"/bulletins?classe_id={self.classe.id}&periode_id={self.periode_a.id}")
        self.assertEqual(resp_before.status_code, 200)
        html_before = resp_before.get_data(as_text=True)
        self.assertIn("Provisoire", html_before)
        self.assertIn("(Estimé)", html_before)
        self.assertIn("Top : 16.00/20", html_before)
        self.assertIn("Réussite : 66.7%", html_before)

        # 2. Clôture de la période via POST
        url_toggle = f"/bulletins/periodes/{self.periode_a.id}/toggle-publication"
        resp_cloture = self.client.post(url_toggle, data={"classe_id": str(self.classe.id)})
        self.assertEqual(resp_cloture.status_code, 302)

        # 3. Vérification de la page après clôture officielle
        resp_after = self.client.get(resp_cloture.headers.get("Location", ""))
        self.assertEqual(resp_after.status_code, 200)
        html_after = resp_after.get_data(as_text=True)

        # Vérifier l'en-tête de période officielle
        self.assertIn("Période clôturée (Officielle)", html_after)

        # Vérifier les badges de statistiques de classe (officiels)
        self.assertIn("Officiel", html_after)
        self.assertNotIn("(Estimé)", html_after)
        self.assertIn("Moyenne :", html_after)
        self.assertIn("Top : 16.00/20", html_after)
        # 2 admis (Ins1: 16, Ins2: 14) sur 3 élèves = 66.7%
        self.assertIn("Réussite : 66.7%", html_after)

        # Vérifier les rangs officiels pour TOUS les élèves évalués (#1, #2, #3)
        self.assertIn("#1", html_after)
        self.assertIn("#2", html_after)
        self.assertIn("#3", html_after)

        # Vérifier les mentions calculées
        self.assertIn("Excellent", html_after)
        self.assertIn("Très bien", html_after)
        self.assertIn("Insuffisant", html_after)

    def test_mobile_class_selector_rendered_in_html(self):
        """Vérifie la présence et les options du composant <select id='mobileSelectClasse'>."""
        self._login(self.admin_a)
        resp = self.client.get(f"/bulletins?classe_id={self.classe.id}&periode_id={self.periode_a.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="mobileSelectClasse"', html)
        self.assertIn("⭐ Toutes les classes (Vue d'ensemble)", html)
        self.assertIn(self.classe.nom, html)


if __name__ == "__main__":
    unittest.main()

