"""
Tests unitaires pour les Quick Wins ergonomiques du Passage d'Année (KLASORA):
1. Dédramatisation de l'écran d'activation d'année (communication rassurante, 3 cartes d'indicateurs).
2. Calcul et affichage de la moyenne annuelle (par bulletin ou notes en fallback, sans N+1).
3. Affichage du badge de décision suggérée (vert/orange/gris) et boutons de sélection rapide.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    PeriodeBulletin,
    Utilisateur,
)
from app.services.activation_annee import preparer_activation_annee
from app.services.passage_annee import get_moyennes_annuelles_eleves
from app.services.semestres import configurer_semestres_annee
from app.services.niveaux_annuels import sauvegarder_selection_annuelle
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class QuickWinsPassageTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-quickwins-passage"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestQuickWinsPassageAnnee(unittest.TestCase):
    def setUp(self):
        self.app = create_app(QuickWinsPassageTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Établissement
        self.ecole = Ecole(
            nom="École Pilote Test",
            adresse="Niamey",
            telephone="90000000",
            email="contact@ecole-pilote.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # 2. Utilisateur Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Test",
            email="directeur@ecole-pilote.ne",
            mot_de_passe=generate_password_hash("password123"),
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.admin)

        # 3. Année Source (Active) et Année Cible (Planifiée)
        self.annee_source = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
        )
        self.annee_cible = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="planifiee",
        )
        db.session.add_all([self.annee_source, self.annee_cible])
        db.session.flush()

        # Semestres pour l'année cible (pour validation preparer_activation_annee)
        configurer_semestres_annee(self.ecole.id, self.annee_cible.id, date(2027, 1, 31))

        # 4. Niveaux & Classes
        self.niveau_cp = NiveauScolaire(
            nom="CP",
            code="CP",
            ordre=1,
            cycle="primaire",
        )
        self.niveau_ce1 = NiveauScolaire(
            nom="CE1",
            code="CE1",
            ordre=2,
            cycle="primaire",
        )
        db.session.add_all([self.niveau_cp, self.niveau_ce1])
        db.session.flush()

        self.niveau_cp.niveau_suivant_id = self.niveau_ce1.id

        self.classe_source = Classe(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            nom="CP-A",
            niveau_id=self.niveau_cp.id,
            statut="ouverte",
        )
        self.classe_cible = Classe(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            nom="CE1-A",
            niveau_id=self.niveau_ce1.id,
            statut="ouverte",
        )
        self.classe_cible_cp = Classe(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            nom="CP-Cible",
            niveau_id=self.niveau_cp.id,
            statut="ouverte",
        )
        db.session.add_all([self.classe_source, self.classe_cible, self.classe_cible_cp])
        db.session.flush()

        # Structure annuelle active
        sauvegarder_selection_annuelle(self.ecole.id, self.annee_source.id, [self.niveau_cp.id, self.niveau_ce1.id])
        sauvegarder_selection_annuelle(self.ecole.id, self.annee_cible.id, [self.niveau_cp.id, self.niveau_ce1.id])

        # 5. Élèves
        self.eleve_admis = Eleve(
            ecole_id=self.ecole.id,
            nom="Traoré",
            prenom="Amina",
            date_naissance=date(2018, 5, 12),
            genre="F",
            statut="actif",
        )
        self.eleve_redoublant = Eleve(
            ecole_id=self.ecole.id,
            nom="Oumarou",
            prenom="Moussa",
            date_naissance=date(2018, 3, 20),
            genre="M",
            statut="actif",
        )
        self.eleve_nouveau = Eleve(
            ecole_id=self.ecole.id,
            nom="Ibrahim",
            prenom="Fatima",
            date_naissance=date(2018, 7, 10),
            genre="F",
            statut="actif",
        )
        db.session.add_all([self.eleve_admis, self.eleve_redoublant, self.eleve_nouveau])
        db.session.flush()

        # Inscriptions année source
        self.insc_src_admis = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=self.eleve_admis.id,
            classe_id=self.classe_source.id,
            statut="actif",
        )
        self.insc_src_redoub = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=self.eleve_redoublant.id,
            classe_id=self.classe_source.id,
            statut="actif",
        )
        db.session.add_all([self.insc_src_admis, self.insc_src_redoub])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["user_id"] = self.admin.id
            sess["role"] = self.admin.role
            sess["ecole_id"] = self.ecole.id

    def test_get_moyennes_annuelles_eleves_avec_bulletins(self):
        """Vérifie le calcul des moyennes annuelles à partir des bulletins."""
        bulletin_admis = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=self.eleve_admis.id,
            classe_id=self.classe_source.id,
            moyenne_generale=14.50,
            statut="valide",
        )
        bulletin_redoub = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=self.eleve_redoublant.id,
            classe_id=self.classe_source.id,
            moyenne_generale=8.75,
            statut="valide",
        )
        db.session.add_all([bulletin_admis, bulletin_redoub])
        db.session.commit()

        moyennes = get_moyennes_annuelles_eleves(self.ecole.id, self.annee_source.id)
        self.assertEqual(moyennes[self.eleve_admis.id], 14.50)
        self.assertEqual(moyennes[self.eleve_redoublant.id], 8.75)

    def test_get_moyennes_annuelles_eleves_avec_notes_fallback(self):
        """Vérifie le calcul pondéré des moyennes via les Notes brutes en absence de Bulletins."""
        cours = Cours(
            ecole_id=self.ecole.id,
            classe_id=self.classe_source.id,
            nom="Mathématiques",
            coefficient=2.0,
        )
        db.session.add(cours)
        db.session.commit()

        # Pour eleve_admis : 14/20 (coeff 1) + 16/20 (coeff 2) -> (14*1 + 16*2)/3 = 46/3 = 15.33
        n1 = Note(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_admis.id,
            cours_id=cours.id,
            annee_id=self.annee_source.id,
            valeur=14.0,
            coefficient=1.0,
        )
        n2 = Note(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_admis.id,
            cours_id=cours.id,
            annee_id=self.annee_source.id,
            valeur=16.0,
            coefficient=2.0,
        )
        # Pour eleve_redoublant : 6/20 (coeff 1) + 8/20 (coeff 2) -> (6*1 + 8*2)/3 = 22/3 = 7.33
        n3 = Note(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_redoublant.id,
            cours_id=cours.id,
            annee_id=self.annee_source.id,
            valeur=6.0,
            coefficient=1.0,
        )
        n4 = Note(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_redoublant.id,
            cours_id=cours.id,
            annee_id=self.annee_source.id,
            valeur=8.0,
            coefficient=2.0,
        )
        db.session.add_all([n1, n2, n3, n4])
        db.session.commit()

        moyennes = get_moyennes_annuelles_eleves(self.ecole.id, self.annee_source.id)
        self.assertAlmostEqual(moyennes[self.eleve_admis.id], 15.33, places=2)
        self.assertAlmostEqual(moyennes[self.eleve_redoublant.id], 7.33, places=2)

        moyennes = get_moyennes_annuelles_eleves(self.ecole.id, self.annee_source.id)
        self.assertAlmostEqual(moyennes[self.eleve_admis.id], 15.33, places=2)
        self.assertAlmostEqual(moyennes[self.eleve_redoublant.id], 7.33, places=2)

    def test_preparer_activation_indicateurs_cartes(self):
        """Vérifie le calcul des indicateurs admis, redoublants et nouveaux pour l'écran d'activation."""
        # Configurer les inscriptions cible :
        # Admis
        self.insc_src_admis.decision_fin_annee = "passage"
        insc_cible_admis = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=self.eleve_admis.id,
            classe_id=self.classe_cible.id,
            statut="actif",
        )
        # Redoublant
        self.insc_src_redoub.decision_fin_annee = "redoublement"
        insc_cible_redoub = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=self.eleve_redoublant.id,
            classe_id=self.classe_cible_cp.id,
            statut="actif",
        )
        # Nouveau
        insc_cible_nouveau = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=self.eleve_nouveau.id,
            classe_id=self.classe_cible_cp.id,
            statut="actif",
        )
        db.session.add_all([insc_cible_admis, insc_cible_redoub, insc_cible_nouveau])
        db.session.commit()

        stats, err = preparer_activation_annee(self.ecole.id, self.annee_cible.id)
        self.assertIsNone(err)
        self.assertEqual(stats["nb_promus"], 1)
        self.assertEqual(stats["nb_redoublants"], 1)
        self.assertEqual(stats["nb_nouveaux"], 1)

    def test_ecran_activation_confirmation_dedramatise(self):
        """Vérifie que l'écran d'activation utilise des termes rassurants et affiche les 3 cartes clés."""
        # Traitement préalable des passages pour lever le bloquant a_traiter == 0
        self.insc_src_admis.decision_fin_annee = "passage"
        insc_cible_admis = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=self.eleve_admis.id,
            classe_id=self.classe_cible.id,
            statut="inscrit",
        )
        self.insc_src_redoub.decision_fin_annee = "redoublement"
        insc_cible_redoub = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=self.eleve_redoublant.id,
            classe_id=self.classe_cible_cp.id,
            statut="inscrit",
        )
        db.session.add_all([insc_cible_admis, insc_cible_redoub])
        db.session.commit()

        self._login()
        res = self.client.get(f"/annees/{self.annee_cible.id}/activation")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Vérification des termes rassurants et bienveillants
        self.assertIn("Prêt pour la rentrée scolaire ?", html)
        self.assertIn("archivée en toute sécurité", html)
        self.assertIn("Élèves admis (Passage)", html)
        self.assertIn("Élèves redoublants", html)
        self.assertIn("Nouveaux inscrits", html)

        # Vérification de l'absence des termes anxiogènes
        self.assertNotIn("Avertissement solennel", html)
        self.assertNotIn("Opération immédiate et irréversible", html)

    def test_tableau_passage_annee_resultat_annuel_et_selections(self):
        """Vérifie l'affichage de la colonne résultat annuel, badges suggérés et boutons rapides."""
        # Créer des bulletins pour afficher les moyennes
        b1 = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=self.eleve_admis.id,
            classe_id=self.classe_source.id,
            moyenne_generale=14.50,
            statut="valide",
        )
        b2 = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=self.eleve_redoublant.id,
            classe_id=self.classe_source.id,
            moyenne_generale=8.75,
            statut="valide",
        )
        db.session.add_all([b1, b2])
        db.session.commit()

        self._login()
        res = self.client.get(f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Colonne Résultat annuel
        self.assertIn("Résultat annuel", html)
        self.assertIn("14.50/20", html)
        self.assertIn("Passage suggéré", html)
        self.assertIn("8.75/20", html)
        self.assertIn("Redoublement suggéré", html)

        # Attributs data-moyenne et boutons rapides
        self.assertIn('data-moyenne="14.5"', html)
        self.assertIn('data-moyenne="8.75"', html)
        self.assertIn('id="btn-select-admis"', html)
        self.assertIn('id="btn-select-redoublants"', html)

    def test_vue_passage_eleve_suggestion_preselectionnee(self):
        """Vérifie que sur passage_eleve, la suggestion est bien pré-cochée selon la moyenne."""
        # Eleve redoublant avec 8.75/20
        b_redoub = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=self.eleve_redoublant.id,
            classe_id=self.classe_source.id,
            moyenne_generale=8.75,
            statut="valide",
        )
        db.session.add(b_redoub)
        db.session.commit()

        self._login()
        res = self.client.get(
            f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_redoublant.id}"
        )
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn("8.75/20", html)
        self.assertIn("Redoublement suggéré", html)
        # Vérifier que le radio de redoublement est checked
        self.assertIn('id="decision_redoublement" value="redoublement" checked', html)


if __name__ == "__main__":
    unittest.main()
