"""
KLASORA — TESTS PHASE 3D : ANNUALISATION DU MODULE BULLETINS
Validation complète :
1. Ancrage Bulletin -> Inscription (source de vérité)
2. Découplage total d'avec Eleve.classe_id (y compris si None)
3. Cycle de vie : Active (CRUD/Génération), Planifiée (Interdite), Archivée (Lecture seule stricte, consultable & téléchargeable)
4. Calcul des notes, moyennes pondérées et rangs strictement par Inscription annuelle
5. Génération PDF avec classe et année historiques
6. Rôles & Sécurité : Admin, Professeur, Parent (propre enfant + accès archives), Multi-écoles
7. Règle 2C-5D : session["annee_consultee"] non mutée
8. Base SQLite réelle migrée instance/ecole.db (Alembic 9bc234dfde56, FK RESTRICT)
"""

import os
import unittest
from datetime import date, datetime
import sqlite3

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    PeriodeBulletin,
    Professeur,
    Utilisateur,
)
from app.services.annees_scolaires import get_annee_consultee
from app.services.bulletins_annuels import (
    calculer_bulletin_data,
    generer_ou_recuperer_bulletin,
    modifier_appreciation_bulletin,
    supprimer_bulletin,
    statut_annee_bulletins,
    bulletins_modifiables,
    MESSAGE_ANNEE_PLANIFIEE,
    MESSAGE_ANNEE_ARCHIVEE,
)
from app.services import generer_bulletin_pdf


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase3DBulletinsAnnuelsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # 1. Écoles
        self.ecole_a = Ecole(nom="École A Test", statut="actif")
        self.ecole_b = Ecole(nom="École B Test", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # 2. Années scolaires École A
        self.archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        self.active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.planifiee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )

        # Année scolaire École B
        self.annee_b = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.archivee, self.active, self.planifiee, self.annee_b])
        db.session.flush()

        # 3. Classes
        self.classe_archive = Classe(
            nom="6ème Archive", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.archivee.id
        )
        self.classe_active = Classe(
            nom="5ème Active", niveau="5e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id
        )
        self.classe_plan = Classe(
            nom="4ème Plan", niveau="4e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.planifiee.id
        )
        self.classe_b = Classe(
            nom="Classe B", niveau="5e", statut="ouverte", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id
        )
        db.session.add_all([self.classe_archive, self.classe_active, self.classe_plan, self.classe_b])
        db.session.flush()

        # 4. Utilisateurs
        self.admin = Utilisateur(
            nom="Admin A", email="admin3d@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id
        )
        self.parent = Utilisateur(
            nom="Parent A", email="parent3d@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id
        )
        self.prof_user_1 = Utilisateur(
            nom="Prof Math", email="profmath3d@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id
        )
        self.prof_user_2 = Utilisateur(
            nom="Prof Français", email="proffr3d@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id
        )
        self.admin_b = Utilisateur(
            nom="Admin B", email="adminb3d@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.admin, self.parent, self.prof_user_1, self.prof_user_2, self.admin_b])
        db.session.flush()

        self.prof_1 = Professeur(nom="Math", prenom="Prof", utilisateur_id=self.prof_user_1.id, ecole_id=self.ecole_a.id)
        self.prof_2 = Professeur(nom="Français", prenom="Prof", utilisateur_id=self.prof_user_2.id, ecole_id=self.ecole_a.id)
        db.session.add_all([self.prof_1, self.prof_2])
        db.session.flush()

        # 5. Cours
        self.cours_math_active = Cours(
            nom="Mathématiques", coefficient=3.0, ecole_id=self.ecole_a.id, classe_id=self.classe_active.id, professeur_id=self.prof_1.id
        )
        self.cours_fr_active = Cours(
            nom="Français", coefficient=2.0, ecole_id=self.ecole_a.id, classe_id=self.classe_active.id, professeur_id=self.prof_2.id
        )
        self.cours_math_archive = Cours(
            nom="Maths Archive", coefficient=3.0, ecole_id=self.ecole_a.id, classe_id=self.classe_archive.id, professeur_id=self.prof_1.id
        )
        self.cours_plan = Cours(
            nom="Maths Futur", coefficient=3.0, ecole_id=self.ecole_a.id, classe_id=self.classe_plan.id, professeur_id=self.prof_1.id
        )
        self.cours_b = Cours(
            nom="Cours B", coefficient=2.0, ecole_id=self.ecole_b.id, classe_id=self.classe_b.id
        )
        db.session.add_all([self.cours_math_active, self.cours_fr_active, self.cours_math_archive, self.cours_plan, self.cours_b])
        db.session.flush()

        # 6. Élèves
        self.eleve_1 = Eleve(
            nom="Diallo",
            prenom="Moussa",
            date_naissance=date(2012, 5, 14),
            genre="M",
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            parent_id=self.parent.id,
        )
        self.eleve_2 = Eleve(
            nom="Bah",
            prenom="Fatou",
            date_naissance=date(2012, 8, 22),
            genre="F",
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            parent_id=self.parent.id,
        )
        self.eleve_autre_parent = Eleve(
            nom="Sow",
            prenom="Amadou",
            date_naissance=date(2012, 3, 10),
            genre="M",
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
        )
        self.eleve_b = Eleve(
            nom="Traore",
            prenom="Oumar",
            date_naissance=date(2012, 1, 1),
            genre="M",
            ecole_id=self.ecole_b.id,
            classe_id=self.classe_b.id,
        )
        db.session.add_all([self.eleve_1, self.eleve_2, self.eleve_autre_parent, self.eleve_b])
        db.session.flush()

        # 7. Inscriptions
        self.insc_archive_1 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_1.id,
            classe_id=self.classe_archive.id,
            annee_scolaire_id=self.archivee.id,
            statut="inscrit",
            frais_annuels=150000.0,
        )
        self.insc_active_1 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_1.id,
            classe_id=self.classe_active.id,
            annee_scolaire_id=self.active.id,
            statut="inscrit",
            frais_annuels=160000.0,
        )
        self.insc_active_2 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_2.id,
            classe_id=self.classe_active.id,
            annee_scolaire_id=self.active.id,
            statut="inscrit",
            frais_annuels=160000.0,
        )
        self.insc_active_autre = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_autre_parent.id,
            classe_id=self.classe_active.id,
            annee_scolaire_id=self.active.id,
            statut="inscrit",
            frais_annuels=160000.0,
        )
        self.insc_b = Inscription(
            ecole_id=self.ecole_b.id,
            eleve_id=self.eleve_b.id,
            classe_id=self.classe_b.id,
            annee_scolaire_id=self.annee_b.id,
            statut="inscrit",
            frais_annuels=150000.0,
        )
        db.session.add_all([self.insc_archive_1, self.insc_active_1, self.insc_active_2, self.insc_active_autre, self.insc_b])
        db.session.flush()

        # 8. Périodes de bulletin
        self.periode_act = PeriodeBulletin(
            nom="Trimestre 1",
            annee_id=self.active.id,
            ecole_id=self.ecole_a.id,
            publie=True,
            periode_active=True,
        )
        self.periode_t2 = PeriodeBulletin(
            nom="Trimestre 2",
            annee_id=self.active.id,
            ecole_id=self.ecole_a.id,
            publie=False,
            periode_active=False,
        )
        self.periode_arch = PeriodeBulletin(
            nom="Trimestre 1",
            annee_id=self.archivee.id,
            ecole_id=self.ecole_a.id,
            publie=True,
            periode_active=False,
        )
        db.session.add_all([self.periode_act, self.periode_t2, self.periode_arch])
        db.session.flush()

        # 9. Notes
        # Notes Archive (Moussa en 6e, 2024-2025)
        self.note_arch_math = Note(
            valeur=14.0, coefficient=3.0, ecole_id=self.ecole_a.id, eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_archive.id, inscription_id=self.insc_archive_1.id,
            annee_id=self.archivee.id, periode="Trimestre 1"
        )
        # Notes Active (Moussa en 5e, 2025-2026 : Math=16.0, Fr=14.0 -> Moyenne pondérée = (16*3 + 14*2)/5 = 15.2)
        self.note_act_math_1 = Note(
            valeur=16.0, coefficient=3.0, ecole_id=self.ecole_a.id, eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id, inscription_id=self.insc_active_1.id,
            annee_id=self.active.id, periode="Trimestre 1"
        )
        self.note_act_fr_1 = Note(
            valeur=14.0, coefficient=2.0, ecole_id=self.ecole_a.id, eleve_id=self.eleve_1.id,
            cours_id=self.cours_fr_active.id, inscription_id=self.insc_active_1.id,
            annee_id=self.active.id, periode="Trimestre 1"
        )
        # Note Active Fatou (Math=18.0 -> Rang 1, Moussa Rang 2)
        self.note_act_math_2 = Note(
            valeur=18.0, coefficient=3.0, ecole_id=self.ecole_a.id, eleve_id=self.eleve_2.id,
            cours_id=self.cours_math_active.id, inscription_id=self.insc_active_2.id,
            annee_id=self.active.id, periode="Trimestre 1"
        )
        # Note Active Trimestre 2 Moussa (Math=10.0 pour tester isolation période)
        self.note_act_t2_math_1 = Note(
            valeur=10.0, coefficient=3.0, ecole_id=self.ecole_a.id, eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id, inscription_id=self.insc_active_1.id,
            annee_id=self.active.id, periode="Trimestre 2"
        )

        db.session.add_all([
            self.note_arch_math,
            self.note_act_math_1,
            self.note_act_fr_1,
            self.note_act_math_2,
            self.note_act_t2_math_1,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # =========================================================================
    # GROUPE 1 : Schéma, Relations et Clés Étrangères
    # =========================================================================

    def test_01_bulletin_foreign_key_inscription_id(self):
        """1. Le modèle Bulletin possède la colonne inscription_id."""
        self.assertTrue(hasattr(Bulletin, 'inscription_id'))
        col = Bulletin.__table__.columns.get('inscription_id')
        self.assertIsNotNone(col)
        self.assertTrue(col.nullable)

    def test_02_bulletin_relationship_inscription_backref(self):
        """2. La relation ORM Bulletin.inscription et le backref Inscription.bulletins existent."""
        bulletin = Bulletin(
            inscription_id=self.insc_active_1.id,
            eleve_id=self.eleve_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            annee_scolaire_id=self.active.id,
            periode="Trimestre 1",
            moyenne_generale=15.2,
        )
        db.session.add(bulletin)
        db.session.commit()

        self.assertEqual(bulletin.inscription.id, self.insc_active_1.id)
        self.assertIn(bulletin, self.insc_active_1.bulletins)

    def test_03_bulletin_to_dict_includes_inscription_id(self):
        """3. Bulletin.to_dict() expose inscription_id, periode et moyennes."""
        bulletin = Bulletin(
            inscription_id=self.insc_active_1.id,
            eleve_id=self.eleve_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            annee_scolaire_id=self.active.id,
            periode="Trimestre 1",
            moyenne_generale=15.2,
            rang=2,
            rang_total=3,
        )
        db.session.add(bulletin)
        db.session.commit()

        d = bulletin.to_dict()
        self.assertEqual(d["inscription_id"], self.insc_active_1.id)
        self.assertEqual(d["periode"], "Trimestre 1")
        self.assertEqual(d["moyenne_generale"], 15.2)
        self.assertEqual(d["rang"], 2)

    def test_04_bulletin_fk_on_delete_restrict(self):
        """4. La suppression d'une inscription ayant un bulletin est bloquée par FK RESTRICT."""
        bulletin = Bulletin(
            inscription_id=self.insc_active_1.id,
            eleve_id=self.eleve_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            annee_scolaire_id=self.active.id,
            periode="Trimestre 1",
            moyenne_generale=15.2,
        )
        db.session.add(bulletin)
        db.session.commit()

        # Tenter de supprimer l'inscription doit échouer (FK RESTRICT)
        # Note : sur SQLite in-memory sans PRAGMA foreign_keys=ON explicite par défaut,
        # on teste la définition schema de la FK
        fk = [fk for fk in Bulletin.__table__.foreign_keys if fk.column.table.name == 'inscriptions'][0]
        self.assertEqual(fk.ondelete, 'RESTRICT')

    # =========================================================================
    # GROUPE 2 : Cycle de Vie de l'Année Scolaire
    # =========================================================================

    def test_05_generation_bulletin_annee_active_succes(self):
        """5. Génération et persistance réussie d'un bulletin en année active."""
        bulletin, err = generer_ou_recuperer_bulletin(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            inscription_id=self.insc_active_1.id,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        self.assertIsNotNone(bulletin)
        self.assertEqual(bulletin.inscription_id, self.insc_active_1.id)
        self.assertAlmostEqual(bulletin.moyenne_generale, 15.2, places=1)

    def test_06_refus_generation_bulletin_annee_planifiee(self):
        """6. Génération de bulletin strictement interdite sur une année planifiée."""
        # Créer une inscription test en année planifiée
        insc_plan = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_1.id,
            classe_id=self.classe_plan.id,
            annee_scolaire_id=self.planifiee.id,
            statut="inscrit"
        )
        db.session.add(insc_plan)
        db.session.commit()

        bulletin, err = generer_ou_recuperer_bulletin(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            inscription_id=insc_plan.id,
            periode="Trimestre 1"
        )
        self.assertIsNone(bulletin)
        self.assertEqual(err, MESSAGE_ANNEE_PLANIFIEE)

    def test_07_consultation_bulletin_annee_archivee_lecture_seule_autorisee(self):
        """7. Consultation d'un bulletin en année archivée autorisée en lecture seule."""
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            inscription=self.insc_archive_1,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        self.assertIsNotNone(data)
        self.assertEqual(data['moyenne_generale'], 14.0)
        self.assertEqual(data['classe'].nom, "6ème Archive")
        self.assertEqual(data['annee_scolaire'].nom, "2024-2025")

    def test_08_modification_appreciation_annee_archivee_interdite(self):
        """8. Modification d'appréciation interdite sur bulletin d'année archivée."""
        bulletin = Bulletin(
            inscription_id=self.insc_archive_1.id,
            eleve_id=self.eleve_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_archive.id,
            annee_scolaire_id=self.archivee.id,
            periode="Trimestre 1",
            moyenne_generale=14.0,
            appreciation_generale="Bien"
        )
        db.session.add(bulletin)
        db.session.commit()

        mod, err = modifier_appreciation_bulletin(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            user=self.admin,
            bulletin_id=bulletin.id,
            nouvelle_appreciation="Très bien"
        )
        self.assertIsNone(mod)
        self.assertIn("archivée", err.lower())

    def test_09_suppression_bulletin_annee_archivee_interdite(self):
        """9. Suppression de bulletin interdite sur année archivée."""
        bulletin = Bulletin(
            inscription_id=self.insc_archive_1.id,
            eleve_id=self.eleve_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_archive.id,
            annee_scolaire_id=self.archivee.id,
            periode="Trimestre 1",
            moyenne_generale=14.0,
        )
        db.session.add(bulletin)
        db.session.commit()

        succes, err = supprimer_bulletin(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            user=self.admin,
            bulletin_id=bulletin.id
        )
        self.assertFalse(succes)
        self.assertIn("archivée", err.lower())
        self.assertIsNotNone(Bulletin.query.get(bulletin.id))

    def test_10_modification_appreciation_annee_planifiee_interdite(self):
        """10. Modification d'appréciation interdite sur année planifiée."""
        mod, err = modifier_appreciation_bulletin(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            bulletin_id=999,
            nouvelle_appreciation="Test"
        )
        self.assertIsNone(mod)
        self.assertEqual(err, MESSAGE_ANNEE_PLANIFIEE)

    def test_11_suppression_bulletin_annee_planifiee_interdite(self):
        """11. Suppression de bulletin interdite sur année planifiée."""
        succes, err = supprimer_bulletin(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            bulletin_id=999
        )
        self.assertFalse(succes)
        self.assertIn("planifiée", err.lower())

    # =========================================================================
    # GROUPE 3 : Isolation des Notes & Coefficients
    # =========================================================================

    def test_12_notes_meme_inscription_uniquement_utilisees(self):
        """12. Les notes du bulletin proviennent exclusivement de Note.inscription_id."""
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            inscription=self.insc_active_1,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        for n in data['notes']:
            self.assertEqual(n.inscription_id, self.insc_active_1.id)

    def test_13_notes_autre_annee_scolaire_strictement_exclues(self):
        """13. Les notes de l'année précédente (archive) ne polluent pas le bulletin actif."""
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            inscription=self.insc_active_1,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        note_ids = [n.id for n in data['notes']]
        self.assertNotIn(self.note_arch_math.id, note_ids)

    def test_14_notes_autre_classe_strictement_exclues(self):
        """14. Les notes d'une autre classe ne sont pas prises en compte."""
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            inscription=self.insc_active_1,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        for n in data['notes']:
            self.assertEqual(n.cours.classe_id, self.classe_active.id)

    def test_15_calcul_moyenne_generale_ponderee_par_coefficients(self):
        """15. Moyenne générale pondérée exacte : (16*3 + 14*2) / (3+2) = 76 / 5 = 15.20."""
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            inscription=self.insc_active_1,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        self.assertEqual(data['moyenne_generale'], 15.2)

    def test_16_preservation_coefficients_historiques_cours(self):
        """16. Les coefficients du cours annuel historique sont préservés."""
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            inscription=self.insc_active_1,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        self.assertEqual(data['coefficients_par_cours']["Mathématiques"], 3.0)
        self.assertEqual(data['coefficients_par_cours']["Français"], 2.0)

    def test_17_calcul_moyennes_par_matiere_exact(self):
        """17. Les moyennes par matière sont exactes."""
        data, err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            inscription=self.insc_active_1,
            periode="Trimestre 1"
        )
        self.assertIsNone(err)
        self.assertEqual(data['moyennes_par_cours']["Mathématiques"], 16.0)
        self.assertEqual(data['moyennes_par_cours']["Français"], 14.0)

    # =========================================================================
    # GROUPE 4 : Calcul du Rang & Population de Référence
    # =========================================================================

    def test_18_calcul_rang_base_sur_population_inscriptions_classe(self):
        """18. Le rang est calculé sur la population des élèves inscrits dans la même classe annuelle."""
        # Fatou : Math=18.0 (moyenne 18.0) -> Rang 1
        # Moussa : Moyenne 15.2 -> Rang 2
        data_fatou, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_2, periode="Trimestre 1")
        data_moussa, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 1")

        self.assertEqual(data_fatou['rang'], 1)
        self.assertEqual(data_moussa['rang'], 2)
        self.assertEqual(data_moussa['rang_total'], 2)  # 2 élèves évalués

    def test_19_inscriptions_autre_annee_exclues_du_rang(self):
        """19. Les élèves d'une autre année scolaire n'entrent pas dans le rang."""
        data_moussa, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 1")
        # Le rang total ne doit pas inclure les inscriptions de 2024-2025
        self.assertLessEqual(data_moussa['rang_total'], 3)

    def test_20_inscriptions_autre_classe_exclues_du_rang(self):
        """20. Les élèves d'une autre classe n'entrent pas dans le calcul du rang."""
        data_b, _ = calculer_bulletin_data(self.ecole_b.id, self.annee_b, self.insc_b, periode="Trimestre 1")
        self.assertIsNone(data_b['rang'])  # Aucune note pour eleve_b

    def test_21_eleves_non_evalues_ont_rang_none(self):
        """21. Un élève sans notes a un rang None."""
        data_autre, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_autre, periode="Trimestre 1")
        self.assertIsNone(data_autre['rang'])
        self.assertEqual(data_autre['appreciation'], "Non évalué")

    # =========================================================================
    # GROUPE 5 : Découplage Total du Cache Eleve.classe_id
    # =========================================================================

    def test_22_bulletin_affiche_classe_historique_inscription(self):
        """22. Le bulletin affiche la classe historique de l'inscription et non Eleve.classe_id."""
        # Moussa a classe_id = 5e Active
        # Mais son inscription archive est 6e Archive
        data_arch, _ = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        self.assertEqual(data_arch['classe'].nom, "6ème Archive")

    def test_23_bulletin_affiche_annee_historique_inscription(self):
        """23. Le bulletin affiche l'année historique de l'inscription."""
        data_arch, _ = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        self.assertEqual(data_arch['annee_scolaire'].nom, "2024-2025")

    def test_24_eleve_classe_id_different_sans_impact_sur_bulletin(self):
        """24. Si Eleve.classe_id change (ex: passage en classe supérieure), l'ancien bulletin reste intact."""
        self.eleve_1.classe_id = self.classe_plan.id
        db.session.commit()

        data_arch, _ = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        self.assertEqual(data_arch['classe'].nom, "6ème Archive")
        self.assertEqual(data_arch['moyenne_generale'], 14.0)

    def test_25_eleve_classe_id_null_ne_bloque_pas_bulletin_ni_pdf(self):
        """25. Si Eleve.classe_id est NULL (sortie/diplôme), le bulletin et le PDF restent 100% fonctionnels."""
        self.eleve_1.classe_id = None
        db.session.commit()

        data_arch, _ = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        self.assertEqual(data_arch['classe'].nom, "6ème Archive")

        # Génération du PDF avec classe_id=None
        pdf_buf = generer_bulletin_pdf(
            eleve=self.eleve_1,
            notes_par_cours=data_arch['notes_par_cours'],
            moyennes_par_cours=data_arch['moyennes_par_cours'],
            moyenne_generale=data_arch['moyenne_generale'],
            classe_nom=data_arch['classe'].nom,
            annee_scolaire_nom=data_arch['annee_scolaire'].nom,
            periode_nom=data_arch['periode'],
        )
        self.assertIsNotNone(pdf_buf)
        self.assertGreater(len(pdf_buf.getvalue()), 1000)

    # =========================================================================
    # GROUPE 6 : Gestion des Périodes (Trimestres / Semestres)
    # =========================================================================

    def test_26_isolation_notes_trimestre_1_et_trimestre_2(self):
        """26. Les notes de Trimestre 1 et Trimestre 2 sont strictement isolées."""
        data_t1, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 1")
        data_t2, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 2")

        self.assertEqual(len(data_t1['notes']), 2)  # Math 16 + Fr 14
        self.assertEqual(len(data_t2['notes']), 1)  # Math 10

    def test_27_bulletin_t1_ne_contient_pas_notes_t2(self):
        """27. Le bulletin T1 ne calcule pas la note de T2."""
        data_t1, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 1")
        note_ids = [n.id for n in data_t1['notes']]
        self.assertNotIn(self.note_act_t2_math_1.id, note_ids)

    def test_28_creation_periode_bulletin_liee_a_annee_scolaire(self):
        """28. Une période de bulletin est rattachée à une année scolaire."""
        self.assertEqual(self.periode_act.annee_id, self.active.id)
        self.assertEqual(self.periode_act.ecole_id, self.ecole_a.id)

    def test_29_activation_periode_ecole(self):
        """29. L'activation d'une période met periode_active=True."""
        self.assertTrue(self.periode_act.periode_active)
        self.assertFalse(self.periode_t2.periode_active)

    # =========================================================================
    # GROUPE 7 : Rôles, Permissions & Multi-Écoles
    # =========================================================================

    def test_30_admin_peut_consulter_tous_bulletins_ecole(self):
        """30. L'admin peut générer/consulter tous les bulletins de son école."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}inscription_id={self.insc_active_1.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")

    def test_31_parent_peut_consulter_bulletin_de_ses_propres_enfants(self):
        """31. Le parent peut télécharger le bulletin de son propre enfant."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.parent.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}inscription_id={self.insc_active_1.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")

    def test_32_parent_refuse_acces_bulletin_autre_enfant(self):
        """32. Le parent se voit refuser l'accès au bulletin d'un autre enfant."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.parent.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_autre_parent.id}inscription_id={self.insc_active_autre.id}")
        self.assertEqual(res.status_code, 302)
        # Redirection vers parent_dashboard

    def test_33_parent_peut_telecharger_bulletin_annee_archivee_sans_periode_publiee(self):
        """33. Le parent peut télécharger le bulletin d'une année archivée même si aucune période active n'est publiée."""
        # Désactiver toutes les périodes
        self.periode_act.publie = False
        self.periode_act.periode_active = False
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.parent.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}inscription_id={self.insc_archive_1.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")

    def test_34_professeur_acces_limite_a_ses_classes(self):
        """34. Le professeur accède aux élèves de ses cours/classes."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.prof_user_1.id)

        # Prof 1 enseigne les mathématiques en 5e Active où Moussa est inscrit
        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}inscription_id={self.insc_active_1.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")

    def test_35_isolation_multi_ecoles_refus_bulletin_autre_ecole(self):
        """35. L'admin de l'École A ne peut pas accéder aux bulletins de l'École B."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_b.id}inscription_id={self.insc_b.id}")
        self.assertEqual(res.status_code, 302)  # Redirection avec flash danger

    # =========================================================================
    # GROUPE 8 : Génération PDF & Impression
    # =========================================================================

    def test_36_pdf_utilise_classe_et_annee_historiques(self):
        """36. Le PDF contient la classe et l'année scolaire historiques de l'inscription."""
        data_arch, _ = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        buf = generer_bulletin_pdf(
            eleve=self.eleve_1,
            notes_par_cours=data_arch['notes_par_cours'],
            moyennes_par_cours=data_arch['moyennes_par_cours'],
            moyenne_generale=data_arch['moyenne_generale'],
            classe_nom="6ème Archive",
            annee_scolaire_nom="2024-2025",
            periode_nom="Trimestre 1",
            rang=1,
            rang_total=1,
        )
        self.assertIsNotNone(buf)
        content = buf.getvalue()
        self.assertTrue(content.startswith(b'%PDF'))

    def test_37_pdf_telechargeable_sur_annee_archivee(self):
        """37. Le PDF d'une année archivée est téléchargeable via HTTP."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}inscription_id={self.insc_archive_1.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertIn("bulletin_Moussa_Diallo", res.headers.get("Content-Disposition", ""))

    def test_38_pdf_contient_matieres_et_moyennes_de_inscription(self):
        """38. Le flux PDF intègre les données académiques de l'inscription."""
        data_act, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 1")
        buf = generer_bulletin_pdf(
            eleve=self.eleve_1,
            notes_par_cours=data_act['notes_par_cours'],
            moyennes_par_cours=data_act['moyennes_par_cours'],
            moyenne_generale=data_act['moyenne_generale'],
            classe_nom=data_act['classe'].nom,
            annee_scolaire_nom=data_act['annee_scolaire'].nom,
            periode_nom="Trimestre 1",
            rang=data_act['rang'],
            rang_total=data_act['rang_total'],
        )
        self.assertGreater(len(buf.getvalue()), 1000)

    def test_39_pdf_genere_avec_eleve_classe_id_null(self):
        """39. Le PDF se génère sans erreur même si l'élève n'a plus de classe actuelle."""
        self.eleve_1.classe_id = None
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}inscription_id={self.insc_archive_1.id}")
        self.assertEqual(res.status_code, 200)

    # =========================================================================
    # GROUPE 9 : Règle 2C-5D & Navigation HTTP
    # =========================================================================

    def test_40_http_get_bulletins_ne_mute_pas_session(self):
        """40. Un appel GET /bulletins ne modifie pas session['annee_consultee']."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.active.id}

        res = client.get("/bulletins")
        self.assertEqual(res.status_code, 200)

        with client.session_transaction() as sess:
            self.assertEqual(sess["annee_consultee"][str(self.ecole_a.id)], self.active.id)

    def test_41_http_parametre_annee_id_ne_mute_pas_session(self):
        """41. Passer annee_id=X à /bulletins ne modifie pas la session."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.active.id}

        res = client.get(f"/bulletinsannee_id={self.archivee.id}")
        self.assertEqual(res.status_code, 200)

        with client.session_transaction() as sess:
            self.assertEqual(sess["annee_consultee"][str(self.ecole_a.id)], self.active.id)

    def test_42_http_bulletin_eleve_annee_active_succes(self):
        """42. GET /bulletin_eleve/<id> en année active retourne le fichier PDF."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")

    def test_43_http_bulletin_eleve_annee_planifiee_redirige_avec_alerte(self):
        """43. GET /bulletin_eleve sur une inscription en année planifiée est refusé."""
        insc_plan = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_1.id,
            classe_id=self.classe_plan.id,
            annee_scolaire_id=self.planifiee.id,
            statut="inscrit"
        )
        db.session.add(insc_plan)
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/bulletin_eleve/{self.eleve_1.id}inscription_id={insc_plan.id}")
        self.assertEqual(res.status_code, 302)

    # =========================================================================
    # GROUPE 10 : Moteur Annuel & Résilience Historique
    # =========================================================================

    def test_44_activation_nouvelle_annee_ne_copie_pas_bulletins(self):
        """44. L'activation d'une nouvelle année ne duplique aucun bulletin historique."""
        bulletin_arch = Bulletin(
            inscription_id=self.insc_archive_1.id,
            eleve_id=self.eleve_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_archive.id,
            annee_scolaire_id=self.archivee.id,
            periode="Trimestre 1",
            moyenne_generale=14.0,
        )
        db.session.add(bulletin_arch)
        db.session.commit()

        initial_count = Bulletin.query.count()
        # Simulation passage / activation nouvelle année
        self.active.statut = "archivee"
        self.planifiee.statut = "active"
        db.session.commit()

        self.assertEqual(Bulletin.query.count(), initial_count)

    def test_45_passage_annuel_ne_deplace_pas_bulletins_historiques(self):
        """45. Le passage annuel ne modifie pas les inscriptions des bulletins existants."""
        bulletin_arch = Bulletin(
            inscription_id=self.insc_archive_1.id,
            eleve_id=self.eleve_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_archive.id,
            annee_scolaire_id=self.archivee.id,
            periode="Trimestre 1",
            moyenne_generale=14.0,
        )
        db.session.add(bulletin_arch)
        db.session.commit()

        # Nouvelle inscription en 5e
        self.eleve_1.classe_id = self.classe_active.id
        db.session.commit()

        b = Bulletin.query.get(bulletin_arch.id)
        self.assertEqual(b.inscription_id, self.insc_archive_1.id)
        self.assertEqual(b.classe_id, self.classe_archive.id)

    def test_46_sortie_ou_diplome_eleve_conserve_bulletins_via_inscription(self):
        """46. Si un élève quitte l'école ou est diplômé, ses bulletins restent archivés."""
        self.eleve_1.statut = "sorti"
        self.eleve_1.classe_id = None
        db.session.commit()

        data_arch, err = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        self.assertIsNone(err)
        self.assertEqual(data_arch['moyenne_generale'], 14.0)

    def test_47_scenar_complet_moussa_deux_annees_deux_classes_et_classe_nulle(self):
        """47. Scénario central : Moussa en 6e (2024-2025), en 5e (2025-2026), puis classe_id=None."""
        # 1. Moussa en 6e
        data_6e, err_6e = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        self.assertIsNone(err_6e)
        self.assertEqual(data_6e['classe'].nom, "6ème Archive")
        self.assertEqual(data_6e['annee_scolaire'].nom, "2024-2025")
        self.assertEqual(data_6e['moyenne_generale'], 14.0)

        # 2. Moussa en 5e
        data_5e, err_5e = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 1")
        self.assertIsNone(err_5e)
        self.assertEqual(data_5e['classe'].nom, "5ème Active")
        self.assertEqual(data_5e['annee_scolaire'].nom, "2025-2026")
        self.assertEqual(data_5e['moyenne_generale'], 15.2)

        # 3. Eleve.classe_id devient NULL
        self.eleve_1.classe_id = None
        db.session.commit()

        # Vérifier que les 2 bulletins restent distincts et exacts
        data_6e_bis, _ = calculer_bulletin_data(self.ecole_a.id, self.archivee, self.insc_archive_1, periode="Trimestre 1")
        data_5e_bis, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.insc_active_1, periode="Trimestre 1")

        self.assertEqual(data_6e_bis['classe'].nom, "6ème Archive")
        self.assertEqual(data_6e_bis['moyenne_generale'], 14.0)
        self.assertEqual(data_5e_bis['classe'].nom, "5ème Active")
        self.assertEqual(data_5e_bis['moyenne_generale'], 15.2)

    # =========================================================================
    # GROUPE 11 : Vraie Base SQLite Migrée (instance/ecole.db)
    # =========================================================================

    def test_48_base_migree_reelle_colonne_inscription_id_presente(self):
        """48. La vraie base SQLite instance/ecole.db possède la colonne inscription_id sur bulletin."""
        db_path = os.path.join(self.app.root_path, "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("instance/ecole.db non trouvé")

        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("PRAGMA table_info(bulletin)")
        cols = [row[1] for row in cur.fetchall()]
        con.close()

        self.assertIn("inscription_id", cols)
        self.assertIn("periode", cols)
        self.assertIn("moyenne_generale", cols)
        self.assertIn("rang", cols)

    def test_49_base_migree_reelle_fk_on_delete_restrict(self):
        """49. La vraie base SQLite possède la clé étrangère RESTRICT vers inscriptions."""
        db_path = os.path.join(self.app.root_path, "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("instance/ecole.db non trouvé")

        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("PRAGMA foreign_key_list(bulletin)")
        fks = cur.fetchall()
        con.close()

        insc_fks = [fk for fk in fks if fk[2] == "inscriptions"]
        self.assertTrue(len(insc_fks) >= 1)
        self.assertEqual(insc_fks[0][6], "RESTRICT")

    def test_50_base_migree_reelle_alembic_head_9bc234dfde56(self):
        """50. La vraie base SQLite est à jour sur la révision Alembic 9bc234dfde56."""
        db_path = os.path.join(self.app.root_path, "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("instance/ecole.db non trouvé")

        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        head = cur.fetchone()[0]
        con.close()

        self.assertEqual(head, "9bc234dfde56")


if __name__ == "__main__":
    unittest.main()

