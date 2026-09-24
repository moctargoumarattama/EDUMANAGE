"""
tests/test_duplication_structure_annee.py
=============================================================
Tests unitaires et d'intégration pour le Chantier C :
Reconduire la structure de l'année en 1 clic (Duplication classes & périodes)

Couverture :
  1. Duplication fidèle et atomique des classes, niveaux et semestres
  2. Vérification que les classes cibles ont effectif = 0 et statut = 'ouverte'
  3. Refus strict si l'année cible possède déjà des classes (anti-doublon)
  4. Refus strict si l'année cible est déjà 'active' ou 'archivee'
  5. Refus si l'année source ne possède aucune classe
  6. Isolation multi-établissements (anti-cross-tenant)
  7. Route POST /annees/<src>/dupliquer_vers/<cbl> avec redirection et flash
  8. Présence du bouton d'action sur l'UI (preparation_annee.html & structure_annee.html)
     et disparition après duplication.
=============================================================
"""
from datetime import date, datetime, timedelta
import unittest

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    NiveauScolaire,
    PeriodeBulletin,
    Utilisateur,
)
from app.services.duplication_structure import dupliquer_structure_annee
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class DuplicationStructureTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-duplication-structure-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestDuplicationStructureAnnee(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DuplicationStructureTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Établissement
        self.ecole = Ecole(
            nom="Complexe Scolaire Excellence",
            adresse="Niamey",
            telephone="90112233",
            email="contact@excellence.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # 2. Utilisateur Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Oumarou",
            email="directeur@excellence.ne",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.admin)
        db.session.flush()

        # 3. Niveaux scolaires
        self.niveau_cm2 = NiveauScolaire(code="CM2", nom="CM2", cycle="primaire", ordre=1)
        self.niveau_6e = NiveauScolaire(code="6E", nom="6ème", cycle="college", ordre=2)
        db.session.add_all([self.niveau_cm2, self.niveau_6e])
        db.session.flush()

        # 4. Année source (active) : 2024-2025
        self.annee_source = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        # Année cible (planifiée) : 2025-2026
        self.annee_cible = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="planifiee",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.annee_source, self.annee_cible])
        db.session.flush()

        # Niveaux configurés sur l'année source
        cfg1 = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            niveau_id=self.niveau_cm2.id,
            actif=True,
        )
        cfg2 = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            niveau_id=self.niveau_6e.id,
            actif=True,
        )
        db.session.add_all([cfg1, cfg2])

        # 3 classes sur l'année source
        self.classe_cm2_a = Classe(
            nom="CM2 A",
            niveau="CM2",
            niveau_id=self.niveau_cm2.id,
            section="A",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            salle="Salle 10",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
        )
        self.classe_cm2_b = Classe(
            nom="CM2 B",
            niveau="CM2",
            niveau_id=self.niveau_cm2.id,
            section="B",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            salle="Salle 11",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
        )
        self.classe_6e_1 = Classe(
            nom="6ème 1",
            niveau="6ème",
            niveau_id=self.niveau_6e.id,
            section="1",
            capacite=40,
            capacite_max=40,
            statut="ouverte",
            salle="Salle 20",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
        )
        db.session.add_all([self.classe_cm2_a, self.classe_cm2_b, self.classe_6e_1])

        # Semestres sur l'année source
        self.p1_src = PeriodeBulletin(
            nom="Semestre 1",
            annee_id=self.annee_source.id,
            ecole_id=self.ecole.id,
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 1, 31),
            publie=False,
            periode_active=False,
        )
        self.p2_src = PeriodeBulletin(
            nom="Semestre 2",
            annee_id=self.annee_source.id,
            ecole_id=self.ecole.id,
            date_debut=date(2025, 2, 1),
            date_fin=date(2025, 6, 30),
            publie=False,
            periode_active=False,
        )
        db.session.add_all([self.p1_src, self.p2_src])
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
            sess['annee_active_id'] = self.annee_source.id
            sess['annee_consultee_id'] = self.annee_source.id

    def test_01_duplication_complete_classes_niveaux_periodes(self):
        """Duplique fidèlement les classes, active les niveaux et projette les périodes vers l'année planifiée."""
        ok, msg = dupliquer_structure_annee(
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertTrue(ok)
        self.assertIn("3 classes et 2 périodes créées avec succès", msg)

        # Vérification des classes créées
        classes_cible = Classe.query.filter_by(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id
        ).all()
        self.assertEqual(len(classes_cible), 3)

        noms_classes = {c.nom for c in classes_cible}
        self.assertEqual(noms_classes, {"CM2 A", "CM2 B", "6ème 1"})

        for c in classes_cible:
            self.assertEqual(c.effectif, 0, "L'effectif initial doit être de 0 élève")
            self.assertEqual(c.statut, "ouverte", "La classe doit être ouverte")
            self.assertIsNone(c.professeur_id, "Professeur non pré-assigné")

        # Vérification des niveaux actifs
        cfgs_cible = AnneeNiveauConfig.query.filter_by(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id, actif=True
        ).all()
        niveaux_actifs_cible = {cfg.niveau_id for cfg in cfgs_cible}
        self.assertEqual(niveaux_actifs_cible, {self.niveau_cm2.id, self.niveau_6e.id})

        # Vérification des périodes projetées
        periodes_cible = PeriodeBulletin.query.filter_by(
            ecole_id=self.ecole.id, annee_id=self.annee_cible.id
        ).order_by(PeriodeBulletin.date_debut.asc()).all()
        self.assertEqual(len(periodes_cible), 2)
        self.assertEqual(periodes_cible[0].nom, "Semestre 1")
        self.assertEqual(periodes_cible[1].nom, "Semestre 2")
        self.assertEqual(periodes_cible[0].date_debut, date(2025, 9, 1))
        self.assertEqual(periodes_cible[1].date_fin, date(2026, 6, 30))

    def test_02_refus_si_classes_deja_existantes(self):
        """Refuse la duplication si l'année cible possède déjà une ou plusieurs classes."""
        # Créer une classe dans l'année cible
        classe_existante = Classe(
            nom="6ème Test",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )
        db.session.add(classe_existante)
        db.session.commit()

        ok, msg = dupliquer_structure_annee(
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("déjà", msg.lower())

    def test_03_refus_si_cible_active_ou_archivee(self):
        """Refuse la duplication si l'année cible n'est pas au statut 'planifiee'."""
        self.annee_cible.statut = "active"
        db.session.commit()

        ok, msg = dupliquer_structure_annee(
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("planifiée", msg.lower())

        self.annee_cible.statut = "archivee"
        db.session.commit()

        ok, msg = dupliquer_structure_annee(
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("planifiée", msg.lower())

    def test_04_refus_si_source_sans_classe(self):
        """Refuse la duplication si l'année source ne possède aucune classe."""
        # Année source vide
        annee_vide = AnneeScolaire(
            nom="2023-2024",
            date_debut=date(2023, 9, 1),
            date_fin=date(2024, 6, 30),
            statut="archivee",
            ecole_id=self.ecole.id,
        )
        db.session.add(annee_vide)
        db.session.commit()

        ok, msg = dupliquer_structure_annee(
            annee_source_id=annee_vide.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("aucune classe", msg.lower())

    def test_05_isolation_multi_ecoles(self):
        """Refuse la duplication si l'école spécifiée ne correspond pas aux années."""
        autre_ecole = Ecole(nom="Autre Établissement", telephone="99887766", statut="actif")
        db.session.add(autre_ecole)
        db.session.commit()

        ok, msg = dupliquer_structure_annee(
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=autre_ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("introuvable", msg.lower())

    def test_06_route_post_dupliquer_structure(self):
        """La route POST dupliquer_vers redirige avec message flash vert et persiste la structure."""
        self._login_admin()

        resp = self.client.post(
            f"/annees/{self.annee_source.id}/dupliquer_vers/{self.annee_cible.id}",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("3 classes et 2 périodes créées avec succès", html)

        classes_creees = Classe.query.filter_by(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id
        ).count()
        self.assertEqual(classes_creees, 3)

    def test_07_ui_bouton_duplication_dans_preparation_annee(self):
        """L'encadré et le bouton de duplication apparaissent sur preparation_annee.html quand l'année est vide."""
        self._login_admin()

        # 1. Avant duplication : le bouton est présent
        resp = self.client.get(f"/annees/{self.annee_cible.id}/preparation")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Gagnez du temps pour la rentrée", html)
        self.assertIn("Reconduire la structure de l'an dernier", html)

        # 2. Après duplication : le bouton disparaît car l'année n'est plus vide
        dupliquer_structure_annee(self.annee_source.id, self.annee_cible.id, self.ecole.id)

        resp = self.client.get(f"/annees/{self.annee_cible.id}/preparation")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertNotIn("Reconduire la structure de l'an dernier", html)

    def test_08_ui_bouton_duplication_dans_structure_annee(self):
        """L'encadré et le bouton de duplication apparaissent sur structure_annee.html quand l'année est vide."""
        self._login_admin()

        # 1. Avant duplication : le bouton est présent
        resp = self.client.get(f"/annees/{self.annee_cible.id}/structure")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Gagnez du temps pour la rentrée", html)
        self.assertIn("Reconduire la structure de l'an dernier", html)

        # 2. Après duplication : le bouton disparaît
        dupliquer_structure_annee(self.annee_source.id, self.annee_cible.id, self.ecole.id)

        resp = self.client.get(f"/annees/{self.annee_cible.id}/structure")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertNotIn("Reconduire la structure de l'an dernier", html)


if __name__ == "__main__":
    unittest.main()

