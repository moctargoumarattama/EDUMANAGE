"""tests/test_phase5d_semestres.py
==================================
Tests complets pour la Phase 5D :
Système de notation semestriel (Modèle à 2 semestres).

30 Scénarios couverts :
1. test_01_moyenne_controles_simple_arithmetique
2. test_02_moyenne_controles_ignore_coefficient_note
3. test_03_composition_unique_par_matiere_semestre
4. test_04_doublon_composition_rejete_en_creation
5. test_05_doublon_composition_rejete_en_modification
6. test_06_saisie_classe_composition_met_a_jour_sans_doublon
7. test_07_moyenne_matiere_semestre_calcul_exact
8. test_08_moyenne_matiere_incomplete_si_manque_composition
9. test_09_moyenne_matiere_incomplete_si_manque_controles
10. test_10_coefficient_matiere_provient_du_cours
11. test_11_points_matiere_est_moyenne_fois_coef_cours
12. test_12_moyenne_generale_semestre_ponderee_par_coef_cours
13. test_13_moyenne_generale_exclut_matieres_incompletes
14. test_14_moyenne_annuelle_moyenne_s1_s2
15. test_15_moyenne_annuelle_non_calculee_si_s2_manquant
16. test_16_independance_notes_s1_et_s2
17. test_17_independance_rangs_s1_et_s2
18. test_18_classement_eleves_semestre_ordre_decroissant
19. test_19_statistiques_classe_semestre_moyenne_min_max
20. test_20_bulletin_data_structure_semestrielle
21. test_21_bulletin_pdf_generation_semestre_1
22. test_22_bulletin_pdf_generation_semestre_2
23. test_23_validation_periode_autorisee_uniquement_semestres
24. test_24_validation_type_evaluation_autorise
25. test_25_annee_planifiee_bloque_saisie_semestre
26. test_26_annee_archivee_bloque_saisie_semestre
27. test_27_professeur_limite_a_ses_cours_semestre
28. test_28_parent_voit_uniquement_ses_enfants_semestre
29. test_29_isolation_ecoles_semestres
30. test_30_journalisation_audit_note_semestre
"""

import unittest
from datetime import date, datetime
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
    NiveauScolaire,
    Note,
    Professeur,
    Utilisateur,
)
from app.services.niveaux import ensure_ecole_niveau_configs
from app.services.structure_annuelle import sauvegarder_structure_annee
from app.services.notes_annuelles import (
    SEMESTRE_1,
    SEMESTRE_2,
    PERIODES_SEMESTRES,
    TYPE_DEVOIR,
    TYPE_INTERROGATION,
    TYPE_COMPOSITION,
    TYPES_CONTROLE_CONTINU,
    TYPES_EVALUATION,
    MESSAGE_ANNEE_ARCHIVEE,
    MESSAGE_ANNEE_PLANIFIEE,
    calculer_moyenne_controles,
    calculer_moyenne_matiere_semestre,
    calculer_points_matiere,
    calculer_moyenne_generale_semestre,
    calculer_moyenne_annuelle,
    calculer_moyennes_eleve_annee,
    creer_note,
    modifier_note,
    supprimer_note,
    valider_mutation_note,
    saisir_notes_classe,
)
from app.services.bulletins_annuels import (
    calculer_bulletin_data,
    _calculer_rang_et_stats_classe,
    generer_ou_recuperer_bulletin,
)
from app.services import generer_bulletin_pdf


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase5DSemestresTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Écoles
        self.ecole_a = Ecole(nom="École A Test", statut="actif")
        self.ecole_b = Ecole(nom="École B Test", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        ensure_ecole_niveau_configs(self.ecole_a.id, commit=True)
        ensure_ecole_niveau_configs(self.ecole_b.id, commit=True)
        self.n6 = NiveauScolaire.query.filter_by(code="6E").first()
        self.n5 = NiveauScolaire.query.filter_by(code="5E").first()

        # Années scolaires École A
        self.archivee = AnneeScolaire(
            nom="2024-2025", date_debut=date(2024, 9, 1), date_fin=date(2025, 7, 31),
            statut="archivee", ecole_id=self.ecole_a.id,
        )
        self.active = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31),
            statut="active", ecole_id=self.ecole_a.id,
        )
        self.planifiee = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31),
            statut="planifiee", ecole_id=self.ecole_a.id,
        )
        # Année scolaire École B
        self.annee_b = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31),
            statut="active", ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.archivee, self.active, self.planifiee, self.annee_b])
        db.session.flush()

        sauvegarder_structure_annee(self.ecole_a.id, self.active.id, [self.n6.id, self.n5.id])
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b.id, [self.n6.id, self.n5.id])
        db.session.flush()

        # Classes
        self.classe_6a = Classe(nom="6ème A", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id)
        self.classe_archive = Classe(nom="6ème Arch", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.archivee.id)
        self.classe_plan = Classe(nom="6ème Plan", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.planifiee.id)
        self.classe_b = Classe(nom="6ème B-Ecole", niveau="6e", statut="ouverte", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_6a, self.classe_archive, self.classe_plan, self.classe_b])
        db.session.flush()

        # Utilisateurs
        self.admin = Utilisateur(nom="Admin", prenom="User", email="admin@ecole.com", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id)
        self.user_prof1 = Utilisateur(nom="Prof", prenom="Un", email="prof1@ecole.com", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id)
        self.user_prof2 = Utilisateur(nom="Prof", prenom="Deux", email="prof2@ecole.com", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id)
        self.user_parent = Utilisateur(nom="Parent", prenom="Test", email="parent@ecole.com", mot_de_passe="pass", role="parent", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="AdminB", prenom="User", email="admin@ecoleb.com", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin, self.user_prof1, self.user_prof2, self.user_parent, self.admin_b])
        db.session.flush()

        self.prof1 = Professeur(nom="Prof", prenom="Un", email="prof1@ecole.com", ecole_id=self.ecole_a.id, utilisateur_id=self.user_prof1.id)
        self.prof2 = Professeur(nom="Prof", prenom="Deux", email="prof2@ecole.com", ecole_id=self.ecole_a.id, utilisateur_id=self.user_prof2.id)
        db.session.add_all([self.prof1, self.prof2])
        db.session.flush()

        # Cours dans 6e A avec coefficients distincts
        self.cours_math = Cours(nom="Mathématiques", coefficient=3.0, classe_id=self.classe_6a.id, professeur_id=self.prof1.id, ecole_id=self.ecole_a.id)
        self.cours_francais = Cours(nom="Français", coefficient=2.0, classe_id=self.classe_6a.id, professeur_id=self.prof2.id, ecole_id=self.ecole_a.id)
        self.cours_svt = Cours(nom="SVT", coefficient=1.0, classe_id=self.classe_6a.id, professeur_id=self.prof2.id, ecole_id=self.ecole_a.id)
        self.cours_b = Cours(nom="Maths B", coefficient=2.0, classe_id=self.classe_b.id, ecole_id=self.ecole_b.id)
        db.session.add_all([self.cours_math, self.cours_francais, self.cours_svt, self.cours_b])
        db.session.flush()

        # Élèves et Inscriptions
        self.eleve_1 = Eleve(nom="DIALLO", prenom="Amadou", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_a.id, parent_id=self.user_parent.id)
        self.eleve_2 = Eleve(nom="BAH", prenom="Fatou", date_naissance=date(2013, 2, 2), ecole_id=self.ecole_a.id)
        self.eleve_3 = Eleve(nom="CAMARA", prenom="Ibrahim", date_naissance=date(2013, 3, 3), ecole_id=self.ecole_a.id)
        self.eleve_b = Eleve(nom="TOURE", prenom="Moussa", date_naissance=date(2013, 5, 5), ecole_id=self.ecole_b.id)
        db.session.add_all([self.eleve_1, self.eleve_2, self.eleve_3, self.eleve_b])
        db.session.flush()

        self.ins_1 = Inscription(eleve_id=self.eleve_1.id, classe_id=self.classe_6a.id, annee_scolaire_id=self.active.id, ecole_id=self.ecole_a.id)
        self.ins_2 = Inscription(eleve_id=self.eleve_2.id, classe_id=self.classe_6a.id, annee_scolaire_id=self.active.id, ecole_id=self.ecole_a.id)
        self.ins_3 = Inscription(eleve_id=self.eleve_3.id, classe_id=self.classe_6a.id, annee_scolaire_id=self.active.id, ecole_id=self.ecole_a.id)
        self.ins_b = Inscription(eleve_id=self.eleve_b.id, classe_id=self.classe_b.id, annee_scolaire_id=self.annee_b.id, ecole_id=self.ecole_b.id)
        db.session.add_all([self.ins_1, self.ins_2, self.ins_3, self.ins_b])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user, annee_id=None):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            target_annee = annee_id or self.active.id
            if user.ecole_id:
                sess['annee_consultee'] = {str(user.ecole_id): target_annee}

    # 1. Moyenne des contrôles continus = moyenne arithmétique simple
    def test_01_moyenne_controles_simple_arithmetique(self):
        notes = [12.0, 14.0, 16.0]
        moy = calculer_moyenne_controles(notes)
        self.assertEqual(moy, 14.0)

    # 2. Moyenne des contrôles ignore le coefficient individuel de chaque note
    def test_02_moyenne_controles_ignore_coefficient_note(self):
        n1 = Note(valeur=10.0, coefficient=5.0, type_evaluation=TYPE_DEVOIR)
        n2 = Note(valeur=20.0, coefficient=1.0, type_evaluation=TYPE_INTERROGATION)
        moy = calculer_moyenne_controles([n1, n2])
        # Simple arithmétique : (10 + 20) / 2 = 15.0, PAS (10*5 + 20*1)/6 = 11.67
        self.assertEqual(moy, 15.0)

    # 3. Composition unique par matière et semestre : création initiale autorisée
    def test_03_composition_unique_par_matiere_semestre(self):
        note, err = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math.id,
            valeur=15.0,
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        self.assertIsNone(err)
        self.assertIsNotNone(note)
        self.assertEqual(note.type_evaluation, TYPE_COMPOSITION)
        self.assertEqual(note.periode, SEMESTRE_1)

    # 4. Doublon de composition rejeté en création unitaire
    def test_04_doublon_composition_rejete_en_creation(self):
        creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math.id,
            valeur=14.0,
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        # Deuxième composition pour le même élève / cours / semestre
        note2, err2 = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math.id,
            valeur=16.0,
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        self.assertIsNone(note2)
        self.assertIn("existe déjà", err2.lower())

    # 5. Doublon de composition rejeté en modification
    def test_05_doublon_composition_rejete_en_modification(self):
        comp, _ = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math.id,
            valeur=14.0,
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        devoir, _ = creer_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_1.id,
            cours_id=self.cours_math.id,
            valeur=12.0,
            type_evaluation=TYPE_DEVOIR,
            periode=SEMESTRE_1,
        )
        # Tenter de changer le devoir en Composition -> doit être rejeté
        mod, err = modifier_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            note_id=devoir.id,
            valeur=13.0,
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        self.assertIsNone(mod)
        self.assertIn("existe déjà", err.lower())

        # En revanche, modifier la valeur de la composition existante est autorisé
        mod_comp, err_comp = modifier_note(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            note_id=comp.id,
            valeur=17.0,
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        self.assertIsNotNone(mod_comp)
        self.assertEqual(mod_comp.valeur, 17.0)

    # 6. Saisie par classe pour composition met à jour sans créer de doublon
    def test_06_saisie_classe_composition_met_a_jour_sans_doublon(self):
        # 1ère saisie par classe : composition
        nb1, err1 = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            classe_id=self.classe_6a.id,
            cours_id=self.cours_math.id,
            notes_dict={self.eleve_1.id: 12.0},
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        self.assertEqual(nb1, 1)
        self.assertIsNone(err1)

        # 2ème saisie par classe : mise à jour de la composition
        nb2, err2 = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            classe_id=self.classe_6a.id,
            cours_id=self.cours_math.id,
            notes_dict={self.eleve_1.id: 15.0},
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        )
        self.assertEqual(nb2, 1)
        self.assertIsNone(err2)

        # Vérifier qu'il n'y a qu'une seule note de composition pour l'élève 1 en Math S1
        notes = Note.query.filter_by(
            inscription_id=self.ins_1.id,
            cours_id=self.cours_math.id,
            type_evaluation=TYPE_COMPOSITION,
            periode=SEMESTRE_1,
        ).all()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].valeur, 15.0)

    # 7. Moyenne matière semestre = (moyenne_controles + note_composition) / 2
    def test_07_moyenne_matiere_semestre_calcul_exact(self):
        moy_ctrl = 14.0
        note_comp = 16.0
        moy_mat = calculer_moyenne_matiere_semestre(moy_ctrl, note_comp)
        self.assertEqual(moy_mat, 15.0)

    # 8. Moyenne matière incomplète si manque composition -> None
    def test_08_moyenne_matiere_incomplete_si_manque_composition(self):
        moy_ctrl = 14.0
        moy_mat = calculer_moyenne_matiere_semestre(moy_ctrl, None)
        self.assertIsNone(moy_mat)

    # 9. Moyenne matière incomplète si manque contrôles -> None
    def test_09_moyenne_matiere_incomplete_si_manque_controles(self):
        note_comp = 15.0
        moy_mat = calculer_moyenne_matiere_semestre(None, note_comp)
        self.assertIsNone(moy_mat)

    # 10. Coefficient matière provient strictement de Cours.coefficient
    def test_10_coefficient_matiere_provient_du_cours(self):
        self.assertEqual(self.cours_math.coefficient, 3.0)
        # Note avec un coefficient différent
        note = Note(valeur=15.0, coefficient=1.0, type_evaluation=TYPE_COMPOSITION)
        # Le coefficient officiel doit être celui du cours
        points = calculer_points_matiere(15.0, self.cours_math.coefficient)
        self.assertEqual(points, 45.0)

    # 11. Points matière = moyenne_matiere * Cours.coefficient
    def test_11_points_matiere_est_moyenne_fois_coef_cours(self):
        pts = calculer_points_matiere(14.5, 3.0)
        self.assertEqual(pts, 43.5)
        # Si moyenne est None -> points est None
        self.assertIsNone(calculer_points_matiere(None, 3.0))

    # 12. Moyenne générale semestre pondérée par les coefficients des cours
    def test_12_moyenne_generale_semestre_ponderee_par_coef_cours(self):
        # Math (coef 3, moyenne 15.0 -> pts 45)
        # Français (coef 2, moyenne 10.0 -> pts 20)
        # Total points = 65, Total coef = 5 -> moyenne générale = 13.0
        matieres = [
            {'moyenne': 15.0, 'coefficient': 3.0, 'points': 45.0},
            {'moyenne': 10.0, 'coefficient': 2.0, 'points': 20.0},
        ]
        moy_gen = calculer_moyenne_generale_semestre(matieres)
        self.assertEqual(moy_gen, 13.0)

    # 13. Moyenne générale exclut les matières incomplètes
    def test_13_moyenne_generale_exclut_matieres_incompletes(self):
        # Math (coef 3, moyenne 15.0 -> pts 45)
        # Français (incomplète : moyenne None)
        matieres = [
            {'moyenne': 15.0, 'coefficient': 3.0, 'points': 45.0},
            {'moyenne': None, 'coefficient': 2.0, 'points': None},
        ]
        moy_gen = calculer_moyenne_generale_semestre(matieres)
        self.assertEqual(moy_gen, 15.0)

    # 14. Moyenne annuelle = (moyenne_s1 + moyenne_s2) / 2
    def test_14_moyenne_annuelle_moyenne_s1_s2(self):
        moy_ann = calculer_moyenne_annuelle(14.0, 16.0)
        self.assertEqual(moy_ann, 15.0)

    # 15. Moyenne annuelle non calculée si S2 est manquant
    def test_15_moyenne_annuelle_non_calculee_si_s2_manquant(self):
        self.assertIsNone(calculer_moyenne_annuelle(14.0, None))
        self.assertIsNone(calculer_moyenne_annuelle(None, 16.0))

    # 16. Indépendance totale des notes S1 et S2
    def test_16_independance_notes_s1_et_s2(self):
        # Saisir des notes en S1
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 12.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 14.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        # Calculer le bulletin de S2 -> l'élève n'a aucune note en S2
        data_s2, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_1, periode=SEMESTRE_2)
        self.assertEqual(data_s2["notes_count"], 0)
        self.assertIsNone(data_s2["moyenne_generale"])

    # 17. Indépendance totale des rangs S1 et S2
    def test_17_independance_rangs_s1_et_s2(self):
        # En S1 : Eleve 1 a 16.0, Eleve 2 a 12.0 -> Eleve 1 est 1er
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 16.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 16.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 12.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 12.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        data_s1_e1, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_1, periode=SEMESTRE_1)
        data_s1_e2, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_2, periode=SEMESTRE_1)
        self.assertEqual(data_s1_e1["rang"], 1)
        self.assertEqual(data_s1_e2["rang"], 2)

        # En S2 : Eleve 2 a 18.0, Eleve 1 a 10.0 -> Eleve 2 est 1er, Eleve 1 est 2e
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 10.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_2)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 10.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_2)

        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 18.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_2)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 18.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_2)

        data_s2_e1, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_1, periode=SEMESTRE_2)
        data_s2_e2, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_2, periode=SEMESTRE_2)
        self.assertEqual(data_s2_e1["rang"], 2)
        self.assertEqual(data_s2_e2["rang"], 1)

    # 18. Classement des élèves d'un semestre par ordre décroissant
    def test_18_classement_eleves_semestre_ordre_decroissant(self):
        # Eleve 1 : 18.0 (1er)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 18.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 18.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        # Eleve 2 : 14.0 (2e)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 14.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 14.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        # Eleve 3 : 10.0 (3e)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_3.id, self.cours_math.id, 10.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_3.id, self.cours_math.id, 10.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        d1, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_1, periode=SEMESTRE_1)
        d2, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_2, periode=SEMESTRE_1)
        d3, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_3, periode=SEMESTRE_1)

        self.assertEqual(d1["rang"], 1)
        self.assertEqual(d2["rang"], 2)
        self.assertEqual(d3["rang"], 3)
        self.assertEqual(d1["rang_total"], 3)

    # 19. Statistiques de classe pour un semestre (moyenne, min, max, effectif)
    def test_19_statistiques_classe_semestre_moyenne_min_max(self):
        # Eleve 1 : 18.0, Eleve 2 : 12.0
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 18.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 18.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 12.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_2.id, self.cours_math.id, 12.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        # Eleve 3 : non évalué
        target_rank, evalues_count, stats = _calculer_rang_et_stats_classe(
            self.ecole_a.id, self.classe_6a.id, self.active.id, self.ins_1.id, periode=SEMESTRE_1
        )
        self.assertEqual(target_rank, 1)
        self.assertEqual(evalues_count, 2)
        self.assertEqual(stats["effectif_classe"], 3)
        self.assertEqual(stats["moyenne_classe"], 15.0)
        self.assertEqual(stats["plus_forte_moyenne"], 18.0)
        self.assertEqual(stats["plus_faible_moyenne"], 12.0)

    # 20. Structure des données semestrielles du bulletin
    def test_20_bulletin_data_structure_semestrielle(self):
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 14.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1)
        creer_note(self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 16.0, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1)

        data, err = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_1, periode=SEMESTRE_1)
        self.assertIsNone(err)
        self.assertIn("disciplines", data)
        self.assertEqual(len(data["disciplines"]), 1)

        disc = data["disciplines"][0]
        self.assertEqual(disc["cours_nom"], "Mathématiques")
        self.assertEqual(disc["moyenne_controles"], 14.0)
        self.assertEqual(disc["note_composition"], 16.0)
        self.assertEqual(disc["moyenne_semestre"], 15.0)
        self.assertEqual(disc["coefficient"], 3.0)
        self.assertEqual(disc["points"], 45.0)
        self.assertTrue(disc["est_finalisee"])
        self.assertEqual(data["total_coefficients"], 3.0)
        self.assertEqual(data["total_points"], 45.0)
        self.assertEqual(data["moyenne_generale"], 15.0)

    # 21. Génération PDF Bulletin Semestre 1 avec titre dynamique
    def test_21_bulletin_pdf_generation_semestre_1(self):
        data, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_1, periode=SEMESTRE_1)
        buffer = generer_bulletin_pdf(
            eleve=self.eleve_1,
            notes_par_cours=data['notes_par_cours'],
            moyennes_par_cours=data['moyennes_par_cours'],
            moyenne_generale=data['moyenne_generale'],
            nom_ecole=self.ecole_a.nom,
            classe_nom=self.classe_6a.nom,
            annee_scolaire_nom=self.active.nom,
            periode_nom=SEMESTRE_1,
            rang=1,
            rang_total=3,
            disciplines=data.get('disciplines'),
            total_coefficients=data.get('total_coefficients'),
            total_points=data.get('total_points'),
            stats_classe=data.get('stats_classe'),
        )
        self.assertIsNotNone(buffer)
        pdf_bytes = buffer.getvalue()
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    # 22. Génération PDF Bulletin Semestre 2 avec titre dynamique
    def test_22_bulletin_pdf_generation_semestre_2(self):
        data, _ = calculer_bulletin_data(self.ecole_a.id, self.active, self.ins_1, periode=SEMESTRE_2)
        buffer = generer_bulletin_pdf(
            eleve=self.eleve_1,
            notes_par_cours=data['notes_par_cours'],
            moyennes_par_cours=data['moyennes_par_cours'],
            moyenne_generale=data['moyenne_generale'],
            nom_ecole=self.ecole_a.nom,
            classe_nom=self.classe_6a.nom,
            annee_scolaire_nom=self.active.nom,
            periode_nom=SEMESTRE_2,
            rang=1,
            rang_total=3,
            disciplines=data.get('disciplines'),
            total_coefficients=data.get('total_coefficients'),
            total_points=data.get('total_points'),
            stats_classe=data.get('stats_classe'),
        )
        self.assertIsNotNone(buffer)
        pdf_bytes = buffer.getvalue()
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    # 23. Validation de période : seuls Semestre 1 et Semestre 2 sont acceptés
    def test_23_validation_periode_autorisee_uniquement_semestres(self):
        valide_trimestre, err, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, periode="Trimestre 1"
        )
        self.assertFalse(valide_trimestre)
        self.assertIn("Période non autorisée", err)

        valide_s1, _, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, periode=SEMESTRE_1
        )
        self.assertTrue(valide_s1)

        valide_s2, _, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, periode=SEMESTRE_2
        )
        self.assertTrue(valide_s2)

    # 24. Validation du type d'évaluation : seuls Devoir, Interrogation, Composition sont acceptés
    def test_24_validation_type_evaluation_autorise(self):
        valide_faux, err, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, type_evaluation="Examen"
        )
        self.assertFalse(valide_faux)
        self.assertIn("Type d'évaluation non autorisé", err)

        valide_dev, _, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, type_evaluation=TYPE_DEVOIR
        )
        self.assertTrue(valide_dev)

        valide_int, _, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, type_evaluation=TYPE_INTERROGATION
        )
        self.assertTrue(valide_int)

        valide_comp, _, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, type_evaluation=TYPE_COMPOSITION
        )
        self.assertTrue(valide_comp)

    # 25. Année planifiée bloque la saisie pour un semestre
    def test_25_annee_planifiee_bloque_saisie_semestre(self):
        valide, err, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.planifiee, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, periode=SEMESTRE_1
        )
        self.assertFalse(valide)
        self.assertEqual(err, MESSAGE_ANNEE_PLANIFIEE)

    # 26. Année archivée bloque la saisie pour un semestre
    def test_26_annee_archivee_bloque_saisie_semestre(self):
        valide, err, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.archivee, self.admin, self.eleve_1.id, self.cours_math.id, 15.0, periode=SEMESTRE_1
        )
        self.assertFalse(valide)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)

    # 27. Professeur limité à ses propres cours pour la notation semestrielle
    def test_27_professeur_limite_a_ses_cours_semestre(self):
        # prof1 enseigne Math, pas Français
        valide_math, _, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.user_prof1, self.eleve_1.id, self.cours_math.id, 15.0, periode=SEMESTRE_1
        )
        self.assertTrue(valide_math)

        valide_francais, err, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.user_prof1, self.eleve_1.id, self.cours_francais.id, 15.0, periode=SEMESTRE_1
        )
        self.assertFalse(valide_francais)
        self.assertIn("vos propres cours", err)

    # 28. Parent ne voit que les bulletins / notes de ses propres enfants
    def test_28_parent_voit_uniquement_ses_enfants_semestre(self):
        from app.models import PeriodeBulletin
        periode_pub = PeriodeBulletin(
            nom=SEMESTRE_1,
            annee_id=self.active.id,
            ecole_id=self.ecole_a.id,
            publie=True,
            periode_active=True,
        )
        db.session.add(periode_pub)
        db.session.commit()

        self._login(self.user_parent)
        # Accès au bulletin de son enfant (eleve_1)
        resp_own = self.client.get(f"/bulletin_eleve/{self.eleve_1.id}periode=Semestre+1")
        self.assertEqual(resp_own.status_code, 200)

        # Tentative d'accès à l'enfant d'un autre (eleve_2) -> redirection avec message d'erreur
        resp_other = self.client.get(f"/bulletin_eleve/{self.eleve_2.id}periode=Semestre+1", follow_redirects=True)
        self.assertEqual(resp_other.status_code, 200)
        html = resp_other.get_data(as_text=True)
        self.assertIn("non autorisé", html.lower())

    # 29. Isolation inter-écoles pour les semestres
    def test_29_isolation_ecoles_semestres(self):
        # Tentative d'ajouter une note pour l'élève de l'école B depuis l'école A
        valide, err, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_b.id, self.cours_math.id, 15.0, periode=SEMESTRE_1
        )
        self.assertFalse(valide)
        self.assertIn("introuvable", err.lower())

    # 30. Journalisation d'audit lors des opérations de notation semestrielle
    def test_30_journalisation_audit_note_semestre(self):
        logs = []
        def mock_log(**kwargs):
            logs.append(kwargs)

        self.app.log_correction = mock_log

        # 1. Création
        note, _ = creer_note(
            self.ecole_a.id, self.active, self.admin, self.eleve_1.id, self.cours_math.id, 14.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1
        )
        self.assertGreater(len(logs), 0)
        self.assertEqual(logs[-1]["action"], "ajout")
        self.assertEqual(logs[-1]["cible_id"], note.id)

        # 2. Modification
        modifier_note(
            self.ecole_a.id, self.active, self.admin, note.id, 16.0, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1
        )
        self.assertEqual(logs[-1]["action"], "modification")

        # 3. Suppression
        supprimer_note(self.ecole_a.id, self.active, self.admin, note.id)
        self.assertEqual(logs[-1]["action"], "suppression")
