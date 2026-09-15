"""
tests/test_evaluations_completeness.py
========================================
Suite de tests unitaires et d'intégration pour valider le système canonique
de complétude des évaluations, moyennes provisoires, classements et taux de réussite.

Scénarios couverts (A à L) :
----------------------------
A. 0 notes -> status = "non_evalue", pas de moyenne, pas de rang, pas de top, pas de taux.
B. 1 note sur plusieurs matières -> status = "provisoire", moyenne provisoire calculée, pas de rang, pas de top, pas de taux.
C. Notes partielles -> completion_percent < 100%, status = "provisoire", pas de rang.
D. Coefficients et pondération -> calcul exact de la complétude et de la moyenne selon coefs.
E. 100% complétude -> status = "complete", is_official = True, rang attribué, éligible top & taux.
F. Classement mixte -> SEULS les élèves complets ont un rang officiel (#1, #2, ...).
G. Formule du Taux de réussite -> (count(complete AND average >= 10)) / (total complete) * 100.
H. 0 élève complet -> taux_reussite = None ("Évaluation en cours"), top = None.
I. Isolation année / période -> notes de périodes ou d'années différentes ne se mélangent pas.
J. Bulletin provisoire -> bulletins_annuels retourne status et mentions provisoires.
K. 0 cours attendus -> status = "non_evalue" (JAMAIS complete par liste vide).
L. Ordre et ex-æquo -> classement déterministe préservé.
"""

from datetime import date
import unittest

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
)
from app.services.evaluations import (
    LABEL_COMPLETE,
    LABEL_NON_EVALUE,
    LABEL_PROVISOIRE,
    STATUS_COMPLETE,
    STATUS_NON_EVALUE,
    STATUS_PROVISOIRE,
    calculer_completude_inscription,
    calculer_stats_et_classements_classe,
)
from app.services.bulletins_annuels import calculer_bulletin_data
from app.services.notes_annuelles import TYPE_COMPOSITION, TYPE_DEVOIR


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class EvaluationsCompletenessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Établissement & Année scolaire
        self.ecole = Ecole(nom="École Test Completeness", statut="actif")
        db.session.add(self.ecole)
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()

        # Classe
        self.classe = Classe(
            nom="6ème A",
            niveau="6ème",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        db.session.add(self.classe)
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_scenario_a_zero_notes(self):
        """Scénario A : 0 notes pour l'élève."""
        cours = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Zero", prenom="Note", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([cours, eleve])
        db.session.flush()

        ins = Inscription(eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.commit()

        res = calculer_completude_inscription(self.ecole.id, self.annee.id, ins)
        self.assertEqual(res["status"], STATUS_NON_EVALUE)
        self.assertIsNone(res["average"])
        self.assertFalse(res["is_official"])
        self.assertEqual(res["evaluated_subjects"], 0)
        self.assertEqual(res["expected_subjects"], 1)

    def test_scenario_b_une_seule_note_provisoire(self):
        """Scénario B : 1 seule note sur 3 matières attendues -> Moyenne provisoire, pas de rang."""
        c1 = Cours(nom="Maths", coefficient=3.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Français", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c3 = Cours(nom="Histoire", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Partiel", prenom="Un", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, c2, c3, eleve])
        db.session.flush()

        ins = Inscription(eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.flush()

        note = Note(
            valeur=15.0,
            coefficient=1.0,
            type_evaluation=TYPE_DEVOIR,
            inscription_id=ins.id, eleve_id=ins.eleve_id,
            cours_id=c1.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
            periode="Trimestre 1",
        )
        db.session.add(note)
        db.session.commit()

        res = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Trimestre 1")
        self.assertEqual(res["status"], STATUS_PROVISOIRE)
        self.assertEqual(res["average"], 15.0)
        self.assertFalse(res["is_official"])
        self.assertEqual(res["evaluated_subjects"], 1)
        self.assertEqual(res["expected_subjects"], 3)

        # Vérifier statistiques de classe pour cet élève seul
        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Trimestre 1")
        self.assertNotIn(ins.id, stats["rangs_par_inscription"])
        self.assertIsNone(stats["taux_reussite"])
        self.assertIsNone(stats["meilleur_eleve_complet"])

    def test_scenario_c_notes_partielles(self):
        """Scénario C : 2 matières sur 3 évaluées."""
        c1 = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Français", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c3 = Cours(nom="Anglais", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Deux", prenom="Mat", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, c2, c3, eleve])
        db.session.flush()

        ins = Inscription(eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.flush()

        n1 = Note(valeur=14.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n2 = Note(valeur=12.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c2.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        db.session.add_all([n1, n2])
        db.session.commit()

        res = calculer_completude_inscription(self.ecole.id, self.annee.id, ins)
        self.assertEqual(res["status"], STATUS_PROVISOIRE)
        self.assertEqual(res["evaluated_subjects"], 2)
        self.assertEqual(res["expected_subjects"], 3)
        self.assertEqual(res["average"], 13.0)  # (14*2 + 12*2)/(2+2) = 13.0

    def test_scenario_d_coefficients_ponderation(self):
        """Scénario D : Test des coefficients et du pourcentage de complétude."""
        c1 = Cours(nom="Maths", coefficient=4.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Sport", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Coef", prenom="Test", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, c2, eleve])
        db.session.flush()

        ins = Inscription(eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.flush()

        # Évaluer uniquement Maths (coef 4 sur coef total 5 -> 80% complétude)
        n1 = Note(valeur=16.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        db.session.add(n1)
        db.session.commit()

        res = calculer_completude_inscription(self.ecole.id, self.annee.id, ins)
        self.assertEqual(res["status"], STATUS_PROVISOIRE)
        self.assertEqual(res["completion_percent"], 80.0)
        self.assertEqual(res["average"], 16.0)

    def test_scenario_e_completude_totale(self):
        """Scénario E : 100% des matières évaluées -> Moyenne générale officielle."""
        c1 = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Français", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Complet", prenom="Eleve", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, c2, eleve])
        db.session.flush()

        ins = Inscription(eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.flush()

        n1 = Note(valeur=16.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n2 = Note(valeur=14.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c2.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        db.session.add_all([n1, n2])
        db.session.commit()

        res = calculer_completude_inscription(self.ecole.id, self.annee.id, ins)
        self.assertEqual(res["status"], STATUS_COMPLETE)
        self.assertTrue(res["is_official"])
        self.assertEqual(res["completion_percent"], 100.0)
        self.assertEqual(res["average"], 15.0)

    def test_scenario_f_classement_mixte_complets_et_provisoires(self):
        """Scénario F : Seuls les élèves complets obtiennent un rang officiel."""
        c1 = Cours(nom="Maths", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Français", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        
        # Élève 1 : Complet avec 18/20
        e1 = Eleve(nom="Alpha", prenom="Complet", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        # Élève 2 : Provisoire avec 20/20 (une seule note)
        e2 = Eleve(nom="Beta", prenom="Provisoire", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        # Élève 3 : Complet avec 12/20
        e3 = Eleve(nom="Gamma", prenom="Complet", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, c2, e1, e2, e3])
        db.session.flush()

        ins1 = Inscription(eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        ins2 = Inscription(eleve_id=e2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        ins3 = Inscription(eleve_id=e3.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add_all([ins1, ins2, ins3])
        db.session.flush()

        # Ins1: Maths 18, Fr 18 -> Complet, moy 18.0
        n1 = Note(valeur=18.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins1.id, eleve_id=ins1.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n2 = Note(valeur=18.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins1.id, eleve_id=ins1.eleve_id, cours_id=c2.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        # Ins2: Maths 20, Fr AUCUNE -> Provisoire
        n3 = Note(valeur=20.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins2.id, eleve_id=ins2.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        # Ins3: Maths 12, Fr 12 -> Complet, moy 12.0
        n4 = Note(valeur=12.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins3.id, eleve_id=ins3.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n5 = Note(valeur=12.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins3.id, eleve_id=ins3.eleve_id, cours_id=c2.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        db.session.add_all([n1, n2, n3, n4, n5])
        db.session.commit()

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id)
        rangs = stats["rangs_par_inscription"]

        self.assertEqual(rangs.get(ins1.id), 1)  # Alpha est #1
        self.assertNotIn(ins2.id, rangs)        # Beta provisoire n'a PAS de rang
        self.assertEqual(rangs.get(ins3.id), 2)  # Gamma est #2 (et non #3)

        self.assertEqual(stats["complets_count"], 2)
        self.assertEqual(stats["provisoires_count"], 1)
        self.assertEqual(stats["meilleur_eleve_complet"]["eleve"].id, e1.id)
        self.assertEqual(stats["plus_forte_moyenne"], 18.0)

    def test_scenario_g_formule_exacte_taux_reussite(self):
        """Scénario G : Taux de réussite = (complets avec moyenne >= 10) / total complets * 100."""
        c1 = Cours(nom="Maths", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        db.session.add(c1)
        db.session.flush()

        # Créer 20 élèves complets : 15 avec moyenne >= 10 et 5 avec moyenne < 10
        for i in range(15):
            e = Eleve(nom=f"Admis{i}", prenom="Test", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
            db.session.add(e)
            db.session.flush()
            ins = Inscription(eleve_id=e.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
            db.session.add(ins)
            db.session.flush()
            n = Note(valeur=14.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
            db.session.add(n)

        for i in range(5):
            e = Eleve(nom=f"Echec{i}", prenom="Test", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
            db.session.add(e)
            db.session.flush()
            ins = Inscription(eleve_id=e.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
            db.session.add(ins)
            db.session.flush()
            n = Note(valeur=8.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
            db.session.add(n)

        db.session.commit()

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id)
        self.assertEqual(stats["complets_count"], 20)
        self.assertEqual(stats["taux_reussite"], 75.0)  # 15/20 * 100 = 75.0%

    def test_scenario_h_zero_eleve_complet(self):
        """Scénario H : 0 élève complet -> taux_reussite est None."""
        c1 = Cours(nom="Maths", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Français", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="Seul", prenom="Prov", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, c2, e1])
        db.session.flush()

        ins = Inscription(eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.flush()

        # Une seule note dans Maths -> Provisoire
        n = Note(valeur=18.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        db.session.add(n)
        db.session.commit()

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id)
        self.assertEqual(stats["complets_count"], 0)
        self.assertEqual(stats["provisoires_count"], 1)
        self.assertIsNone(stats["taux_reussite"])
        self.assertIsNone(stats["meilleur_eleve_complet"])
        self.assertIsNone(stats["moyenne_classe_officielle"])

    def test_scenario_i_isolation_annee_et_periode(self):
        """Scénario I : Ne jamais mélanger plusieurs années ou périodes."""
        annee2 = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 6, 30), statut="planifiee", ecole_id=self.ecole.id)
        db.session.add(annee2)
        db.session.flush()

        c1 = Cours(nom="Maths", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="Iso", prenom="Test", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, e1])
        db.session.flush()

        ins = Inscription(eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.flush()

        # Note sur T1
        n_t1 = Note(valeur=16.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id, periode="Trimestre 1")
        # Note sur T2
        n_t2 = Note(valeur=10.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id, periode="Trimestre 2")
        db.session.add_all([n_t1, n_t2])
        db.session.commit()

        res_t1 = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Trimestre 1")
        self.assertEqual(res_t1["average"], 16.0)

        res_t2 = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Trimestre 2")
        self.assertEqual(res_t2["average"], 10.0)

    def test_scenario_j_bulletin_provisoire(self):
        """Scénario J : Le service bulletins_annuels signale l'état provisoire et masque le rang si incomplet."""
        c1 = Cours(nom="Maths", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Français", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="Bull", prenom="Prov", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, c2, e1])
        db.session.flush()

        ins = Inscription(eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.flush()

        n = Note(valeur=15.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins.id, eleve_id=ins.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id, periode="Semestre 1")
        db.session.add(n)
        db.session.commit()

        b_data, err = calculer_bulletin_data(self.ecole.id, self.annee, ins)
        self.assertIsNone(err)
        self.assertTrue(b_data["est_provisoire"])
        self.assertFalse(b_data["est_complet"])
        self.assertIsNone(b_data["rang"])  # Rang None car provisoire

    def test_scenario_k_zero_cours_attendus(self):
        """Scénario K : expected_subjects == 0 -> status est non_evalue (jamais complete)."""
        e1 = Eleve(nom="Zero", prenom="Cours", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add(e1)
        db.session.flush()

        ins = Inscription(eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add(ins)
        db.session.commit()

        # Aucun cours dans la classe
        res = calculer_completude_inscription(self.ecole.id, self.annee.id, ins)
        self.assertEqual(res["expected_subjects"], 0)
        self.assertEqual(res["status"], STATUS_NON_EVALUE)
        self.assertFalse(res["is_official"])

    def test_scenario_l_egalite_ex_aequo(self):
        """Scénario L : Traitement propre des égalités sans plantage."""
        c1 = Cours(nom="Maths", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="Equal1", prenom="A", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        e2 = Eleve(nom="Equal2", prenom="B", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, e1, e2])
        db.session.flush()

        ins1 = Inscription(eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        ins2 = Inscription(eleve_id=e2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id)
        db.session.add_all([ins1, ins2])
        db.session.flush()

        n1 = Note(valeur=15.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins1.id, eleve_id=ins1.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        n2 = Note(valeur=15.0, coefficient=1.0, type_evaluation=TYPE_DEVOIR, inscription_id=ins2.id, eleve_id=ins2.eleve_id, cours_id=c1.id, ecole_id=self.ecole.id, annee_id=self.annee.id)
        db.session.add_all([n1, n2])
        db.session.commit()

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id)
        self.assertEqual(stats["complets_count"], 2)
        self.assertIn(ins1.id, stats["rangs_par_inscription"])
        self.assertIn(ins2.id, stats["rangs_par_inscription"])


if __name__ == "__main__":
    unittest.main()
