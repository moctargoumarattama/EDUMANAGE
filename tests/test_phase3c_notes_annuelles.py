import os
import sqlite3
import unittest
from datetime import date, datetime

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    Professeur,
    Utilisateur,
)
from app.services.notes_annuelles import (
    MESSAGE_ANNEE_ARCHIVEE,
    MESSAGE_ANNEE_PLANIFIEE,
    calculer_moyennes_eleve_annee,
    calculer_statistiques_notes,
    creer_note,
    get_cours_annee,
    get_cours_choices_notes,
    get_eleves_choices_notes,
    get_inscriptions_notes,
    get_notes_annee,
    modifier_note as service_modifier_note,
    notes_modifiables,
    statut_annee_notes,
    supprimer_note as service_supprimer_note,
    valider_mutation_note,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase3CNotesAnnuellesTestCase(unittest.TestCase):
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
            nom="Admin A", email="admin3c@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id
        )
        self.parent = Utilisateur(
            nom="Parent A", email="parent3c@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id
        )
        self.prof_user_1 = Utilisateur(
            nom="Prof Math", email="profmath@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id
        )
        self.prof_user_2 = Utilisateur(
            nom="Prof Français", email="proffr@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id
        )
        self.admin_b = Utilisateur(
            nom="Admin B", email="adminb3c@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_b.id
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
            nom="Bah",
            prenom="Amadou",
            date_naissance=date(2013, 4, 10),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            parent_id=self.parent.id,
        )
        self.eleve_2 = Eleve(
            nom="Diallo",
            prenom="Fatou",
            date_naissance=date(2013, 8, 15),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            parent_id=self.parent.id,
        )
        self.eleve_autre_parent = Eleve(
            nom="Sow",
            prenom="Moussa",
            date_naissance=date(2013, 2, 20),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
        )
        self.eleve_b = Eleve(
            nom="Barry",
            prenom="Alpha",
            date_naissance=date(2013, 1, 1),
            ecole_id=self.ecole_b.id,
            classe_id=self.classe_b.id,
        )
        db.session.add_all([self.eleve_1, self.eleve_2, self.eleve_autre_parent, self.eleve_b])
        db.session.flush()

        # 7. Inscriptions
        self.insc_archive_1 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_1.id,
            annee_scolaire_id=self.archivee.id,
            classe_id=self.classe_archive.id,
            statut="inscrit",
        )
        self.insc_active_1 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_1.id,
            annee_scolaire_id=self.active.id,
            classe_id=self.classe_active.id,
            statut="inscrit",
        )
        self.insc_active_2 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_2.id,
            annee_scolaire_id=self.active.id,
            classe_id=self.classe_active.id,
            statut="inscrit",
        )
        self.insc_plan_1 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_1.id,
            annee_scolaire_id=self.planifiee.id,
            classe_id=self.classe_plan.id,
            statut="inscrit",
        )
        self.insc_b = Inscription(
            ecole_id=self.ecole_b.id,
            eleve_id=self.eleve_b.id,
            annee_scolaire_id=self.annee_b.id,
            classe_id=self.classe_b.id,
            statut="inscrit",
        )
        db.session.add_all([self.insc_archive_1, self.insc_active_1, self.insc_active_2, self.insc_plan_1, self.insc_b])
        db.session.flush()

        # 8. Note historique de base dans l'année archivée
        self.note_archive = Note(
            valeur=14.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Trimestre 1",
            annee_id=self.archivee.id,
            inscription_id=self.insc_archive_1.id,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_archive.id,
            ecole_id=self.ecole_a.id,
        )
        db.session.add(self.note_archive)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # =========================================================================
    # GROUPE 1 : Modèle Note & Intégrité Inscription
    # =========================================================================

    def test_01_note_foreign_key_inscription_id(self):
        """1. Note possède la colonne inscription_id et la relation vers Inscription."""
        self.assertIsNotNone(self.note_archive.inscription_id)
        self.assertEqual(self.note_archive.inscription_id, self.insc_archive_1.id)

    def test_02_note_relationship_inscription_backref(self):
        """2. La relation Inscription.notes est alimentée par backref."""
        self.assertIn(self.note_archive, self.insc_archive_1.notes)

    def test_03_note_to_dict_includes_inscription_id(self):
        """3. Note.to_dict() inclut bien le champ 'inscription_id'."""
        d = self.note_archive.to_dict()
        self.assertIn("inscription_id", d)
        self.assertEqual(d["inscription_id"], self.insc_archive_1.id)

    # =========================================================================
    # GROUPE 2 : Cycle de vie - Année ACTIVE
    # =========================================================================

    def test_04_creation_note_annee_active_succes(self):
        """4. Création d'une note en année active rattache automatiquement inscription_id et annee_id."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=16.5,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Trimestre 1",
        )
        self.assertIsNone(err)
        self.assertIsNotNone(note)
        self.assertEqual(note.valeur, 16.5)
        self.assertEqual(note.inscription_id, self.insc_active_1.id)
        self.assertEqual(note.annee_id, self.active.id)

    def test_05_modification_note_annee_active_succes(self):
        """5. Modification d'une note en année active met à jour la valeur et sync_version."""
        note, _ = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=12.0,
            coefficient=1.0,
        )
        mod, err = service_modifier_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            note_id=note.id,
            valeur=15.0,
            coefficient=2.0,
            type_evaluation="Composition",
            periode="Trimestre 2",
        )
        self.assertIsNone(err)
        self.assertEqual(mod.valeur, 15.0)
        self.assertEqual(mod.coefficient, 2.0)
        self.assertEqual(mod.periode, "Trimestre 2")
        self.assertGreater(mod.sync_version, 1)

    def test_06_suppression_note_annee_active_succes(self):
        """6. Suppression d'une note en année active par l'administrateur autorisée."""
        note, _ = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=10.0,
        )
        note_id = note.id
        ok, err = service_supprimer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            note_id=note_id,
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIsNone(Note.query.get(note_id))

    # =========================================================================
    # GROUPE 3 : Cycle de vie - Année PLANIFIÉE (Préparation uniquement)
    # =========================================================================

    def test_07_creation_note_annee_planifiee_interdite(self):
        """7. La saisie de notes en année planifiée est strictement rejetée."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_plan.id,
            valeur=15.0,
        )
        self.assertIsNone(note)
        self.assertIsNotNone(err)
        self.assertEqual(err, MESSAGE_ANNEE_PLANIFIEE)

    def test_08_modification_note_annee_planifiee_interdite(self):
        """8. La modification de note dans le contexte planifié est refusée."""
        mod, err = service_modifier_note(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            note_id=self.note_archive.id,
            valeur=18.0,
            coefficient=1.0,
            type_evaluation="Devoir",
            periode="Trimestre 1",
        )
        self.assertIsNone(mod)
        self.assertEqual(err, MESSAGE_ANNEE_PLANIFIEE)

    def test_09_suppression_note_annee_planifiee_interdite(self):
        """9. La suppression de note dans le contexte planifié est refusée."""
        ok, err = service_supprimer_note(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            note_id=self.note_archive.id,
        )
        self.assertFalse(ok)
        self.assertEqual(err, MESSAGE_ANNEE_PLANIFIEE)

    # =========================================================================
    # GROUPE 4 : Cycle de vie - Année ARCHIVÉE (Lecture seule stricte)
    # =========================================================================

    def test_10_creation_note_annee_archivee_interdite(self):
        """10. La création d'une note en année archivée est strictement interdite."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_archive.id,
            valeur=14.0,
        )
        self.assertIsNone(note)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)

    def test_11_modification_note_annee_archivee_interdite(self):
        """11. La modification d'une note d'une année archivée est strictement bloquée."""
        mod, err = service_modifier_note(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            user=self.admin,
            note_id=self.note_archive.id,
            valeur=19.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Trimestre 1",
        )
        self.assertIsNone(mod)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)

    def test_12_suppression_note_annee_archivee_interdite(self):
        """12. La suppression d'une note archivée est strictement bloquée."""
        ok, err = service_supprimer_note(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            user=self.admin,
            note_id=self.note_archive.id,
        )
        self.assertFalse(ok)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)
        self.assertIsNotNone(Note.query.get(self.note_archive.id))

    def test_13_consultation_notes_annee_archivee_autorisee(self):
        """13. La consultation des notes et moyennes de l'année archivée fonctionne parfaitement."""
        notes = get_notes_annee(self.ecole_a.id, self.archivee, user=self.admin)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].id, self.note_archive.id)

    # =========================================================================
    # GROUPE 5 : Cohérence stricte et Inscription Source de Vérité
    # =========================================================================

    def test_14_note_coherence_annee_inscription_classe_cours(self):
        """14. note.inscription.annee_scolaire_id == note.cours.classe.annee_scolaire_id."""
        note, _ = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=14.5,
        )
        self.assertEqual(note.inscription.annee_scolaire_id, note.cours.classe.annee_scolaire_id)
        self.assertEqual(note.inscription.classe_id, note.cours.classe_id)

    def test_15_refus_note_si_eleve_non_inscrit_dans_la_classe_du_cours(self):
        """15. Échec si un élève n'est pas inscrit dans la classe du cours pour cette année."""
        # eleve_non_inscrit dans classe_active
        nouvel_eleve = Eleve(nom="Test", prenom="Paul", date_naissance=date(2010, 1, 1), ecole_id=self.ecole_a.id)
        db.session.add(nouvel_eleve)
        db.session.commit()

        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=nouvel_eleve.id,
            cours_id=self.cours_math_active.id,
            valeur=15.0,
        )
        self.assertIsNone(note)
        self.assertIn("inscrit", err.lower())

    def test_16_refus_note_si_cours_appartient_a_autre_annee(self):
        """16. Refus si le cours appartient à l'année archivée lors d'une saisie en année active."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_archive.id,  # Cours de l'année 2024-2025
            valeur=12.0,
        )
        self.assertIsNone(note)
        self.assertIsNotNone(err)

    def test_17_validation_valeur_note_hors_bornes_refusee(self):
        """17. Note inférieure à 0 ou supérieure à 20 est rejetée."""
        n1, err1 = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=25.0,
        )
        self.assertIsNone(n1)
        self.assertIn("comprise entre 0 et 20", err1)

        n2, err2 = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=-2.0,
        )
        self.assertIsNone(n2)
        self.assertIn("comprise entre 0 et 20", err2)

    def test_18_validation_coefficient_invalide_refusee(self):
        """18. Coefficient nul ou négatif est rejeté."""
        n, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=12.0,
            coefficient=0.0,
        )
        self.assertIsNone(n)
        self.assertIn("supérieur à 0", err)

    # =========================================================================
    # GROUPE 6 : Découplage complet de Eleve.classe_id
    # =========================================================================

    def test_19_notation_possible_meme_si_eleve_classe_id_est_null(self):
        """19. Un élève avec classe_id = None (sorti/diplômé) peut être noté si inscrit dans l'année."""
        self.eleve_1.classe_id = None
        db.session.commit()

        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=17.0,
        )
        self.assertIsNone(err)
        self.assertIsNotNone(note)
        self.assertEqual(note.inscription_id, self.insc_active_1.id)

    def test_20_changement_classe_cache_ne_corrompt_pas_historique_notes(self):
        """20. Changer eleve.classe_id vers une autre classe ne modifie pas les inscriptions des notes existantes."""
        self.eleve_1.classe_id = self.classe_plan.id
        db.session.commit()

        # La note archivée doit toujours pointer vers l'inscription archivée
        note_db = Note.query.get(self.note_archive.id)
        self.assertEqual(note_db.inscription_id, self.insc_archive_1.id)
        self.assertEqual(note_db.inscription.classe_id, self.classe_archive.id)

    def test_21_notes_archivees_affichent_classe_historique_inscription(self):
        """21. L'export ou affichage des notes archivées utilise la classe de l'inscription et non celle du cache élève."""
        self.eleve_1.classe_id = self.classe_plan.id
        db.session.commit()

        notes = get_notes_annee(self.ecole_a.id, self.archivee, user=self.admin)
        note = notes[0]
        classe_affichee = note.inscription.classe.nom if note.inscription and note.inscription.classe else None
        self.assertEqual(classe_affichee, "6ème Archive")

    # =========================================================================
    # GROUPE 7 : Moyennes, Coefficients et Pondérations étanches
    # =========================================================================

    def test_22_calcul_moyenne_generale_ponderee_par_coefficients(self):
        """22. Calcul correct de la moyenne pondérée : (10*2 + 16*3) / (2 + 3) = 68 / 5 = 13.6."""
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_fr_active.id, valeur=10.0, coefficient=2.0)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=16.0, coefficient=3.0)

        stats = calculer_moyennes_eleve_annee(self.insc_active_1.id, self.ecole_a.id, self.active.id)
        self.assertEqual(stats["moyenne"], 13.6)
        self.assertEqual(stats["total_coefficients"], 5.0)

    def test_23_calcul_moyennes_par_matiere(self):
        """23. Calcul des sous-moyennes par matière pour l'inscription."""
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=12.0, coefficient=1.0)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=16.0, coefficient=1.0)

        stats = calculer_moyennes_eleve_annee(self.insc_active_1.id, self.ecole_a.id)
        math_stats = stats["par_matiere"][self.cours_math_active.id]
        self.assertEqual(math_stats["moyenne"], 14.0)
        self.assertEqual(math_stats["nb_notes"], 2)

    def test_24_statistiques_taux_reussite_et_matieres_evaluees(self):
        """24. Statistiques globales : taux de réussite et nombre de matières évaluées."""
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=15.0, coefficient=1.0)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math_active.id, valeur=8.0, coefficient=1.0)

        notes = get_notes_annee(self.ecole_a.id, self.active, user=self.admin)
        stats = calculer_statistiques_notes(notes)
        self.assertEqual(stats["taux_reussite"], 50.0)
        self.assertEqual(stats["matieres_evaluees"], 1)

    def test_25_isolation_totale_moyennes_entre_deux_annees_scolaires(self):
        """25. Les notes de 2024-2025 n'affectent jamais la moyenne de 2025-2026."""
        # note_archive (2024-2025) a une valeur de 14.0
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=18.0, coefficient=1.0)

        stats_archive = calculer_moyennes_eleve_annee(self.insc_archive_1.id, self.ecole_a.id, self.archivee.id)
        stats_active = calculer_moyennes_eleve_annee(self.insc_active_1.id, self.ecole_a.id, self.active.id)

        self.assertEqual(stats_archive["moyenne"], 14.0)
        self.assertEqual(stats_active["moyenne"], 18.0)

    # =========================================================================
    # GROUPE 8 : Isolation Multi-Écoles
    # =========================================================================

    def test_26_refus_creation_note_eleve_autre_ecole(self):
        """26. Impossible d'attribuer une note à un élève d'un autre établissement."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_b.id,
            cours_id=self.cours_math_active.id,
            valeur=12.0,
        )
        self.assertIsNone(note)
        self.assertIn("introuvable", err.lower())

    def test_27_refus_creation_note_cours_autre_ecole(self):
        """27. Impossible d'attribuer une note pour un cours d'un autre établissement."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_b.id,
            valeur=12.0,
        )
        self.assertIsNone(note)
        self.assertIn("introuvable", err.lower())

    def test_28_isolation_consultation_notes_autre_ecole(self):
        """28. Admin École B ne voit aucune note de l'École A."""
        notes_b = get_notes_annee(self.ecole_b.id, self.annee_b, user=self.admin_b)
        self.assertEqual(len(notes_b), 0)

    def test_29_refus_suppression_note_autre_ecole(self):
        """29. Admin École B ne peut pas supprimer une note de l'École A."""
        ok, err = service_supprimer_note(
            ecole_id=self.ecole_b.id,
            annee=self.annee_b,
            user=self.admin_b,
            note_id=self.note_archive.id,
        )
        self.assertFalse(ok)

    # =========================================================================
    # GROUPE 9 : Permissions et Rôles
    # =========================================================================

    def test_30_professeur_peut_noter_ses_propres_cours_uniquement(self):
        """30. Professeur 1 peut noter ses cours de maths en année active."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.prof_user_1,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=15.0,
        )
        self.assertIsNone(err)
        self.assertIsNotNone(note)

    def test_31_professeur_ne_peut_pas_noter_cours_autre_enseignant(self):
        """31. Professeur 1 se voit refuser la saisie de notes pour le cours de français de Professeur 2."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.prof_user_1,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_fr_active.id,
            valeur=15.0,
        )
        self.assertIsNone(note)
        self.assertIn("propres cours", err.lower())

    def test_32_professeur_ne_peut_pas_supprimer_note_autre_enseignant(self):
        """32. Professeur 1 ne peut pas supprimer une note déposée en français."""
        note_fr, _ = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_fr_active.id,
            valeur=11.0,
        )
        ok, err = service_supprimer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.prof_user_1,
            note_id=note_fr.id,
        )
        self.assertFalse(ok)
        self.assertIn("propres cours", err.lower())

    def test_33_parent_ne_voit_que_les_notes_de_ses_propres_enfants(self):
        """33. Un parent connecté ne peut consulter que les notes de ses enfants."""
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=14.0)
        # Note pour un enfant qui n'est pas le sien
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_autre_parent.id, self.cours_math_active.id, valeur=16.0)

        notes_parent = get_notes_annee(self.ecole_a.id, self.active, user=self.parent)
        eleves_vus = {n.eleve_id for n in notes_parent}
        self.assertIn(self.eleve_1.id, eleves_vus)
        self.assertNotIn(self.eleve_autre_parent.id, eleves_vus)

    def test_34_parent_ne_peut_pas_ajouter_de_note(self):
        """34. Un utilisateur parent ne dispose d'aucun droit de création de note."""
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.parent,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math_active.id,
            valeur=18.0,
        )
        self.assertIsNone(note)
        self.assertIn("autorisé", err.lower())

    def test_35_admin_peut_gerer_toutes_les_notes_de_son_ecole(self):
        """35. L'administrateur a le plein pouvoir de notation sur tous les cours de l'école."""
        n1, err1 = creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=13.0)
        n2, err2 = creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_fr_active.id, valeur=17.0)
        self.assertIsNone(err1)
        self.assertIsNone(err2)

    # =========================================================================
    # GROUPE 10 : Routes HTTP, Contexte 2C-5D & Interface
    # =========================================================================

    def test_36_http_get_notes_utilise_annee_consultee_sans_muter_session(self):
        """36. GET /notes utilise l'année consultée en session sans muter session['annee_consultee']."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.archivee.id}

        res = client.get("/notes")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("archiv", html.lower())

        with client.session_transaction() as sess:
            self.assertEqual(sess.get("annee_consultee", {}).get(str(self.ecole_a.id)), self.archivee.id)

    def test_37_http_get_notes_annee_active_affiche_bouton_nouvelle_note(self):
        """37. GET /notes en année active affiche le bouton 'Nouvelle note'."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.active.id}

        res = client.get("/notes")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Nouvelle note", html)

    def test_38_http_get_notes_annee_archivee_affiche_banniere_lecture_seule_et_cache_boutons(self):
        """38. GET /notes en année archivée affiche le bandeau lecture seule et cache 'Nouvelle note'."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.archivee.id}

        res = client.get("/notes")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("lecture seule", html.lower())
        self.assertNotIn("id=\"btnToggleAddNote\"", html)

    def test_39_http_get_notes_annee_planifiee_affiche_banniere_preparation_et_cache_boutons(self):
        """39. GET /notes en année planifiée affiche le bandeau de préparation et masque le bouton d'ajout."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.planifiee.id}

        res = client.get("/notes")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("lorsque cette année sera active", html.lower())
        self.assertNotIn("id=\"btnToggleAddNote\"", html)

    def test_40_http_post_nouvelle_note_annee_active_succes(self):
        """40. POST /notes enregistre la note sur année active et redirige avec succès."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.active.id}

        data = {
            "eleve_id": self.eleve_1.id,
            "cours_id": self.cours_math_active.id,
            "valeur": 18.5,
            "coefficient": 2.0,
            "type_evaluation": "Devoir",
            "periode": "Trimestre 1",
            "annee_id": self.active.id,
        }
        res = client.post("/notes", data=data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        note = Note.query.filter_by(eleve_id=self.eleve_1.id, cours_id=self.cours_math_active.id).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.valeur, 18.5)
        self.assertEqual(note.inscription_id, self.insc_active_1.id)

    def test_41_http_post_nouvelle_note_annee_archivee_rejetee(self):
        """41. POST /notes rejeté si le contexte annuel est archivé."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.archivee.id}

        data = {
            "eleve_id": self.eleve_1.id,
            "cours_id": self.cours_math_archive.id,
            "valeur": 16.0,
            "coefficient": 1.0,
            "type_evaluation": "Devoir",
            "periode": "Trimestre 1",
            "annee_id": self.archivee.id,
        }
        res = client.post("/notes", data=data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("lecture seule", html.lower())

    def test_42_http_post_nouvelle_note_annee_planifiee_rejetee(self):
        """42. POST /notes rejeté si le contexte annuel est planifié."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.planifiee.id}

        data = {
            "eleve_id": self.eleve_1.id,
            "cours_id": self.cours_plan.id,
            "valeur": 14.0,
            "coefficient": 1.0,
            "type_evaluation": "Devoir",
            "periode": "Trimestre 1",
            "annee_id": self.planifiee.id,
        }
        res = client.post("/notes", data=data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("lorsque cette année sera active", html.lower())

    def test_43_http_modifier_note_annee_active_succes(self):
        """43. GET et POST /note/<id>/modifier pour note en année active."""
        note, _ = creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=10.0)
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        # GET
        res_get = client.get(f"/note/{note.id}/modifier")
        self.assertEqual(res_get.status_code, 200)

        # POST
        data = {
            "eleve_id": self.eleve_1.id,
            "cours_id": self.cours_math_active.id,
            "valeur": 17.5,
            "coefficient": 2.0,
            "type_evaluation": "Composition",
            "periode": "Trimestre 2",
            "annee_id": self.active.id,
        }
        res_post = client.post(f"/note/{note.id}/modifier", data=data, follow_redirects=True)
        self.assertEqual(res_post.status_code, 200)
        note_mod = Note.query.get(note.id)
        self.assertEqual(note_mod.valeur, 17.5)

    def test_44_http_modifier_note_annee_archivee_rejetee(self):
        """44. GET et POST /note/<id>/modifier sur note archivée bloqué avec avertissement."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/note/{self.note_archive.id}/modifier", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("lecture seule", html.lower())

    def test_45_http_supprimer_note_annee_active_succes(self):
        """45. POST /notes/supprimer/<id> supprime la note en année active."""
        note, _ = creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math_active.id, valeur=11.0)
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.post(f"/notes/supprimer/{note.id}", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(Note.query.get(note.id))

    def test_46_http_supprimer_note_annee_archivee_rejetee(self):
        """46. POST /notes/supprimer/<id> sur note archivée est rejeté."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.post(f"/notes/supprimer/{self.note_archive.id}", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(Note.query.get(self.note_archive.id))

    def test_47_http_export_excel_filtre_par_annee_consultee(self):
        """47. GET /notes/export_excel génère le fichier Excel pour l'année consultée."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.active.id}

        res = client.get("/notes/export_excel")
        self.assertEqual(res.status_code, 200)
        self.assertIn("spreadsheetml", res.content_type)

    # =========================================================================
    # GROUPE 11 : Synchronisation Hors-Ligne (Offline Sync)
    # =========================================================================

    def test_48_sync_offline_note_liee_a_inscription_sans_dependre_de_eleve_classe_id(self):
        """48. Synchronisation offline attribue inscription_id et annee_id sans dépendre de eleve.classe_id."""
        self.eleve_1.classe_id = None
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        payload = {
            "operations": [
                {
                    "client_op_id": "op-note-sync-01",
                    "type": "note",
                    "data": {
                        "eleve_id": self.eleve_1.id,
                        "cours_id": self.cours_math_active.id,
                        "valeur": 17.0,
                        "coefficient": 2.0,
                        "type_evaluation": "Devoir",
                        "periode": "Trimestre 1",
                        "date_evaluation": "2025-10-15 10:00:00",
                    }
                }
            ]
        }
        res = client.post("/api/sync", json=payload)
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertEqual(json_data["results"][0]["status"], "synced")

        # Vérifier que la note créée est liée à l'inscription active
        synced_note = Note.query.filter_by(eleve_id=self.eleve_1.id, cours_id=self.cours_math_active.id).first()
        self.assertIsNotNone(synced_note)
        self.assertEqual(synced_note.inscription_id, self.insc_active_1.id)
        self.assertEqual(synced_note.annee_id, self.active.id)

    # =========================================================================
    # GROUPE 12 : Base réelle SQLite et intégrité Alembic
    # =========================================================================

    def test_49_base_migree_reelle_colonne_inscription_id_presente(self):
        """49. La vraie base SQLite instance/ecole.db possède la colonne inscription_id sur note."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(note)")
        cols_note = {row[1] for row in cur.fetchall()}
        conn.close()

        self.assertIn("inscription_id", cols_note, "Colonne inscription_id manquante dans la table note")

    def test_50_base_migree_reelle_fk_on_delete_restrict(self):
        """50. La contrainte de clé étrangère sur note.inscription_id est ON DELETE RESTRICT vers inscriptions."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_key_list(note)")
        fks = cur.fetchall()
        conn.close()

        insc_fks = [fk for fk in fks if fk[2] == "inscriptions" and fk[3] == "inscription_id"]
        self.assertTrue(len(insc_fks) > 0, "FK vers inscriptions(id) introuvable dans note")
        on_delete = insc_fks[0][6].upper()
        self.assertEqual(on_delete, "RESTRICT", f"La politique FK doit être RESTRICT, obtenu: {on_delete}")

    def test_51_base_migree_reelle_alembic_head_8ab121cfcb45(self):
        """51. La révision Alembic de la base ecole.db est bien 8ab121cfcb45 (head 3C)."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        rows = cur.fetchall()
        conn.close()

        versions = {row[0] for row in rows}
        self.assertTrue(
            bool(versions & {"8ab121cfcb45", "9bc234dfde56"}),
            f"La révision 8ab121cfcb45 ou ultérieure doit être active. Révisions actuelles: {versions}",
        )


if __name__ == "__main__":
    unittest.main()
