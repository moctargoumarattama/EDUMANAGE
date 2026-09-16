"""
tests/test_moyennes_klasora.py
==============================
Tests automatisés validant les 10 exigences relatives aux moyennes et complétude dans KLASORA :
1. un élève avec 1 seule note dans une matière a bien la moyenne de cette note pour la matière
2. un élève avec 2 notes dans une matière a bien la moyenne arithmétique de ces 2 notes (avec ou sans composition)
3. un élève avec plusieurs notes dans plusieurs matières a les bonnes moyennes par matière
4. un élève à qui il manque des matières a un statut provisoire et aucun rang
5. un élève avec toutes les matières évaluées a un statut complet
6. pour un élève provisoire, le rang est None / En attente
7. pour un élève complet, le rang est bien calculé
8. les stats de classe excluent les élèves incomplets ; si aucun élève complet, stats = None / En attente
9. les coefficients continuent d'être appliqués correctement
10. la page Notes n'affiche pas de fausse moyenne générale sur la carte élève
"""

from datetime import date
import unittest

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
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
from app.services.evaluations import (
    calculer_moyenne_matiere,
    calculer_completude_inscription,
    calculer_stats_et_classements_classe,
    verifier_eligibilite_publication_periode,
    preparer_dossier_notes_eleve,
    STATUS_COMPLETE,
    STATUS_PROVISOIRE,
)
from app.services.bulletins_annuels import calculer_bulletin_data
from app.services.notes_annuelles import TYPE_COMPOSITION, TYPE_DEVOIR, TYPE_INTERROGATION


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class TestMoyennesKlasora(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Établissement
        self.ecole = Ecole(nom="École Test Moyennes", statut="actif")
        db.session.add(self.ecole)
        db.session.flush()

        # Année scolaire active
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()

        # Configuration pédagogique (requise par le middleware pour éviter la redirection onboarding)
        self.niveau = NiveauScolaire(nom="3ème", code="3EME_TEST", cycle="college", ordre=3)
        db.session.add(self.niveau)
        db.session.flush()

        self.cfg_niveau = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            niveau_id=self.niveau.id,
            actif=True,
        )
        db.session.add(self.cfg_niveau)
        db.session.flush()

        # Classe
        self.classe = Classe(
            nom="3ème B",
            niveau="3ème",
            niveau_id=self.niveau.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        db.session.add(self.classe)
        db.session.flush()

        # Utilisateur Admin pour tester les rendus de templates
        self.user_admin = Utilisateur(
            email="admin@test.local",
            nom="Admin",
            prenom="User",
            role="admin",
            ecole_id=self.ecole.id,
        )
        self.user_admin.set_mot_de_passe("password123")
        db.session.add(self.user_admin)

        # Période Semestre 1 (active et publiée par défaut pour les tests de calcul canonique)
        self.periode = PeriodeBulletin(
            nom="Semestre 1",
            annee_id=self.annee.id,
            ecole_id=self.ecole.id,
            publie=True,
            periode_active=True,
        )
        db.session.add(self.periode)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_01_une_seule_note_matiere(self):
        """1. Un élève avec 1 seule note dans une matière a bien la moyenne de cette note."""
        n1 = Note(valeur=14.5, type_evaluation=TYPE_DEVOIR, periode="Semestre 1")
        moy = calculer_moyenne_matiere([n1])
        self.assertEqual(moy, 14.5)

    def test_02_deux_notes_matiere_moyenne_arithmetique_et_composition(self):
        """2. Deux notes ordinaires -> moyenne arithmétique. Et si composition -> (ctrl + comp)/2."""
        # 2 notes ordinaires (11 et 15) -> moyenne = 13.0
        n1 = Note(valeur=11.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1")
        n2 = Note(valeur=15.0, type_evaluation=TYPE_INTERROGATION, periode="Semestre 1")
        self.assertEqual(calculer_moyenne_matiere([n1, n2]), 13.0)

        # 3 notes ordinaires (11, 15, 17) -> moyenne = 14.33
        n3 = Note(valeur=17.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1")
        self.assertEqual(calculer_moyenne_matiere([n1, n2, n3]), 14.33)

        # Si composition présente : Contrôle continu = (11 + 15)/2 = 13.0, Composition = 15.0
        # Moyenne semestrielle = (13.0 + 15.0) / 2 = 14.0
        n_comp = Note(valeur=15.0, type_evaluation=TYPE_COMPOSITION, periode="Semestre 1")
        moy_avec_comp = calculer_moyenne_matiere([n1, n2, n_comp])
        self.assertEqual(moy_avec_comp, 14.0)

    def test_03_plusieurs_notes_plusieurs_matieres(self):
        """3. Un élève avec plusieurs notes dans plusieurs matières a les bonnes moyennes par matière."""
        c_pc = Cours(id=1, nom="Physique-Chimie", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c_maths = Cours(id=2, nom="Mathématiques", coefficient=3.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c_svt = Cours(id=3, nom="SVT", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)

        eleve = Eleve(id=1, nom="Diallo", prenom="Amadou", ecole_id=self.ecole.id, date_naissance=date(2011, 5, 10))

        # PC : 11 et 15 -> moy 13.0
        n_pc1 = Note(id=101, eleve_id=eleve.id, eleve=eleve, cours_id=c_pc.id, cours=c_pc, valeur=11.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1", date_evaluation=date(2025, 10, 1))
        n_pc2 = Note(id=102, eleve_id=eleve.id, eleve=eleve, cours_id=c_pc.id, cours=c_pc, valeur=15.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1", date_evaluation=date(2025, 10, 15))

        # Maths : 16 et 18 -> moy 17.0
        n_m1 = Note(id=103, eleve_id=eleve.id, eleve=eleve, cours_id=c_maths.id, cours=c_maths, valeur=16.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1", date_evaluation=date(2025, 10, 5))
        n_m2 = Note(id=104, eleve_id=eleve.id, eleve=eleve, cours_id=c_maths.id, cours=c_maths, valeur=18.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1", date_evaluation=date(2025, 10, 20))

        # SVT : 12, 14, 16 -> moy 14.0
        n_s1 = Note(id=105, eleve_id=eleve.id, eleve=eleve, cours_id=c_svt.id, cours=c_svt, valeur=12.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1", date_evaluation=date(2025, 10, 3))
        n_s2 = Note(id=106, eleve_id=eleve.id, eleve=eleve, cours_id=c_svt.id, cours=c_svt, valeur=14.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1", date_evaluation=date(2025, 10, 12))
        n_s3 = Note(id=107, eleve_id=eleve.id, eleve=eleve, cours_id=c_svt.id, cours=c_svt, valeur=16.0, type_evaluation=TYPE_DEVOIR, periode="Semestre 1", date_evaluation=date(2025, 10, 25))

        all_notes = [n_pc1, n_pc2, n_m1, n_m2, n_s1, n_s2, n_s3]
        dossier = preparer_dossier_notes_eleve(all_notes)
        dossier_dict = {item["cours_nom"]: item for item in dossier}

        self.assertEqual(dossier_dict["Physique-Chimie"]["moyenne_matiere"], 13.0)
        self.assertEqual(dossier_dict["Physique-Chimie"]["notes_count"], 2)

        self.assertEqual(dossier_dict["Mathématiques"]["moyenne_matiere"], 17.0)
        self.assertEqual(dossier_dict["Mathématiques"]["notes_count"], 2)

        self.assertEqual(dossier_dict["SVT"]["moyenne_matiere"], 14.0)
        self.assertEqual(dossier_dict["SVT"]["notes_count"], 3)

    def test_04_eleve_matiere_manquante_statut_provisoire_et_aucun_rang(self):
        """4. Un élève à qui il manque des matières a un statut provisoire et aucun rang."""
        c1 = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Physique", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Provisoire", prenom="Paul", ecole_id=self.ecole.id, date_naissance=date(2011, 1, 1))
        db.session.add_all([c1, c2, eleve])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.flush()

        # Note seulement en Maths (Physique manquante)
        n = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=16.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add(n)
        db.session.commit()

        eval_info = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Semestre 1")
        self.assertEqual(eval_info["status"], STATUS_PROVISOIRE)
        self.assertFalse(eval_info["is_official"])
        self.assertEqual(eval_info["evaluated_subjects"], 1)
        self.assertEqual(eval_info["expected_subjects"], 2)

        # Vérifier également via le service bulletin
        bulletin, err = calculer_bulletin_data(self.ecole.id, self.annee, ins, periode="Semestre 1")
        self.assertIsNone(err)
        self.assertEqual(bulletin["rang"], None)
        self.assertEqual(bulletin["est_provisoire"], True)
        self.assertEqual(bulletin["appreciation"], "En attente")

    def test_05_eleve_toutes_matieres_evaluees_statut_complet(self):
        """5. Un élève avec toutes les matières évaluées a un statut complet."""
        c1 = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Physique", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Complet", prenom="Claire", ecole_id=self.ecole.id, date_naissance=date(2011, 2, 2))
        db.session.add_all([c1, c2, eleve])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.flush()

        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=14.0, type_evaluation="Devoir", periode="Semestre 1")
        n2 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c2.id, inscription_id=ins.id, valeur=16.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n1, n2])
        db.session.commit()

        eval_info = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Semestre 1")
        self.assertEqual(eval_info["status"], STATUS_COMPLETE)
        self.assertTrue(eval_info["is_official"])
        self.assertEqual(eval_info["completion_percent"], 100.0)

    def test_06_eleve_provisoire_rang_est_none(self):
        """6. Pour un élève provisoire, le rang reste strictement None."""
        c1 = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Français", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="Eleve1", prenom="Incomplet", ecole_id=self.ecole.id, date_naissance=date(2011, 3, 3))
        e2 = Eleve(nom="Eleve2", prenom="Complet", ecole_id=self.ecole.id, date_naissance=date(2011, 4, 4))
        db.session.add_all([c1, c2, e1, e2])
        db.session.flush()

        ins1 = Inscription(ecole_id=self.ecole.id, eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        ins2 = Inscription(ecole_id=self.ecole.id, eleve_id=e2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add_all([ins1, ins2])
        db.session.flush()

        # Eleve 1 a seulement 1 matière (note 18)
        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e1.id, cours_id=c1.id, inscription_id=ins1.id, valeur=18.0, type_evaluation="Devoir", periode="Semestre 1")
        # Eleve 2 a les 2 matières (notes 12 et 14)
        n2 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e2.id, cours_id=c1.id, inscription_id=ins2.id, valeur=12.0, type_evaluation="Devoir", periode="Semestre 1")
        n3 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e2.id, cours_id=c2.id, inscription_id=ins2.id, valeur=14.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n1, n2, n3])
        db.session.commit()

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Semestre 1")
        # Eleve 1 provisoire -> rang = None
        self.assertNotIn(ins1.id, stats["rangs_par_inscription"])
        self.assertEqual(stats["rangs_par_inscription"].get(ins1.id), None)

    def test_07_eleve_complet_rang_bien_calcule(self):
        """7. Pour un élève complet, le rang est calculé correctement parmi les complets."""
        c1 = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="Premier", prenom="A", ecole_id=self.ecole.id, date_naissance=date(2011, 5, 5))
        e2 = Eleve(nom="Second", prenom="B", ecole_id=self.ecole.id, date_naissance=date(2011, 6, 6))
        db.session.add_all([c1, e1, e2])
        db.session.flush()

        ins1 = Inscription(ecole_id=self.ecole.id, eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        ins2 = Inscription(ecole_id=self.ecole.id, eleve_id=e2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add_all([ins1, ins2])
        db.session.flush()

        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e1.id, cours_id=c1.id, inscription_id=ins1.id, valeur=17.0, type_evaluation="Devoir", periode="Semestre 1")
        n2 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e2.id, cours_id=c1.id, inscription_id=ins2.id, valeur=13.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n1, n2])
        db.session.commit()

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Semestre 1")
        self.assertEqual(stats["rangs_par_inscription"].get(ins1.id), 1)
        self.assertEqual(stats["rangs_par_inscription"].get(ins2.id), 2)

    def test_08_stats_classe_exclusion_incomplets_et_aucun_complet(self):
        """8. Les stats de classe excluent les incomplets. Si 0 complet -> stats = None / En attente."""
        c1 = Cours(nom="Maths", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Physique", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="Incomplet1", prenom="A", ecole_id=self.ecole.id, date_naissance=date(2011, 7, 7))
        e2 = Eleve(nom="Incomplet2", prenom="B", ecole_id=self.ecole.id, date_naissance=date(2011, 8, 8))
        db.session.add_all([c1, c2, e1, e2])
        db.session.flush()

        ins1 = Inscription(ecole_id=self.ecole.id, eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        ins2 = Inscription(ecole_id=self.ecole.id, eleve_id=e2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add_all([ins1, ins2])
        db.session.flush()

        # Tous deux incomplets (seulement Maths)
        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e1.id, cours_id=c1.id, inscription_id=ins1.id, valeur=19.0, type_evaluation="Devoir", periode="Semestre 1")
        n2 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e2.id, cours_id=c1.id, inscription_id=ins2.id, valeur=18.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n1, n2])
        db.session.commit()

        # Quand 0 complets
        stats_zero = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Semestre 1")
        self.assertEqual(stats_zero["complets_count"], 0)
        self.assertIsNone(stats_zero["moyenne_classe_officielle"])
        self.assertIsNone(stats_zero["plus_forte_moyenne"])
        self.assertIsNone(stats_zero["taux_reussite"])

        # Maintenant, on ajoute un 3ème élève complet avec 12 en Maths et 14 en Physique (moyenne = 13.0)
        e3 = Eleve(nom="Complet", prenom="C", ecole_id=self.ecole.id, date_naissance=date(2011, 9, 9))
        db.session.add(e3)
        db.session.flush()
        ins3 = Inscription(ecole_id=self.ecole.id, eleve_id=e3.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins3)
        db.session.flush()

        n3a = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e3.id, cours_id=c1.id, inscription_id=ins3.id, valeur=12.0, type_evaluation="Devoir", periode="Semestre 1")
        n3b = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e3.id, cours_id=c2.id, inscription_id=ins3.id, valeur=14.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n3a, n3b])
        db.session.commit()

        # Les stats de classe ne doivent contenir QUE l'élève complet (moyenne = 13.0, pas 19 ou 18 des incomplets)
        stats_un = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Semestre 1")
        self.assertEqual(stats_un["complets_count"], 1)
        self.assertEqual(stats_un["moyenne_classe_officielle"], 13.0)
        self.assertEqual(stats_un["plus_forte_moyenne"], 13.0)
        self.assertEqual(stats_un["taux_reussite"], 100.0)

    def test_09_coefficients_ponderation_moyenne_generale(self):
        """9. Les coefficients continuent d'être appliqués fidèlement dans la moyenne générale."""
        c1 = Cours(nom="Matière Coeff 3", coefficient=3.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Matière Coeff 1", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Pondere", prenom="Pierre", ecole_id=self.ecole.id, date_naissance=date(2011, 10, 10))
        db.session.add_all([c1, c2, eleve])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.flush()

        # Note 10 en c1 (coeff 3) -> 10 * 3 = 30
        # Note 18 en c2 (coeff 1) -> 18 * 1 = 18
        # Somme pondérée = 48, Somme coefs = 4 -> Moyenne générale = 48 / 4 = 12.0
        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=10.0, type_evaluation="Devoir", periode="Semestre 1")
        n2 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c2.id, inscription_id=ins.id, valeur=18.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n1, n2])
        db.session.commit()

        eval_info = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Semestre 1")
        self.assertEqual(eval_info["average"], 12.0)
        self.assertEqual(eval_info["status"], STATUS_COMPLETE)

    def test_10_page_notes_aucune_fausse_moyenne_generale(self):
        """10. La page Notes n'affiche pas de fausse moyenne générale sur la carte élève."""
        client = self.app.test_client()

        # Authentifier l'admin
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_admin.id)
            sess["_fresh"] = True

        c1 = Cours(nom="Physique-Chimie", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="OMAR", prenom="Moctar", ecole_id=self.ecole.id, date_naissance=date(2012, 1, 1))
        db.session.add_all([c1, eleve])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.flush()

        # L'élève a seulement 2 notes en PC (11 et 15 -> moyenne PC = 13)
        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=11.0, type_evaluation="Devoir", periode="Semestre 1")
        n2 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=15.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n1, n2])
        db.session.commit()

        response = client.get("/notes")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # 1. La carte ne doit pas contenir de data-moyenne trompeur
        self.assertNotIn('data-moyenne="', html)

        # 2. Le header de l'élève ne doit PAS afficher "Moyenne : 13/20" comme moyenne générale
        self.assertNotIn('Moyenne : 13/20', html)
        self.assertNotIn('Moyenne : 13.0/20', html)
        self.assertNotIn('Moyenne : 13.00/20', html)

        # 3. La moyenne matière DOIT être présente dans le détail dépliable
        self.assertIn("Moyenne matière : 13.00/20", html)

        # 4. Le sélecteur de tri ne doit pas proposer de tri sur une fausse moyenne
        self.assertNotIn('value="moyenne-desc"', html)
        self.assertNotIn('value="moyenne-asc"', html)

    def test_11_toutes_matieres_renseignees_periode_non_publiee_reste_provisoire(self):
        """11. Toutes matières renseignées + période NON publiée -> statut provisoire, rang None, stats classe None."""
        self.periode.publie = False
        db.session.commit()

        c1 = Cours(nom="Histoire", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Géographie", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Provisoire", prenom="Paul", ecole_id=self.ecole.id, date_naissance=date(2011, 1, 1))
        db.session.add_all([c1, c2, eleve])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.flush()

        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=15.0, type_evaluation="Devoir", periode="Semestre 1")
        n2 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c2.id, inscription_id=ins.id, valeur=17.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add_all([n1, n2])
        db.session.commit()

        eval_info = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Semestre 1")
        self.assertEqual(eval_info["status"], STATUS_PROVISOIRE)
        self.assertFalse(eval_info["is_official"])
        self.assertTrue(eval_info["is_pedagogically_complete"])
        self.assertEqual(eval_info["average"], 16.0)

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Semestre 1")
        self.assertIsNone(stats["rangs_par_inscription"].get(ins.id))
        self.assertIsNone(stats["moyenne_classe_officielle"])
        self.assertIsNone(stats["taux_reussite"])

        bulletin, err = calculer_bulletin_data(self.ecole.id, self.annee, ins, periode="Semestre 1")
        self.assertIsNone(err)
        self.assertTrue(bulletin["est_provisoire"])
        self.assertIsNone(bulletin["rang"])
        self.assertEqual(bulletin["appreciation"], "En attente")

    def test_12_periode_publiee_et_eleve_complet_devient_final(self):
        """12. Période publiée + élève complet -> bulletin final, rang officiel, stats de classe calculées."""
        self.periode.publie = True
        db.session.commit()

        c1 = Cours(nom="Anglais", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Officiel", prenom="Alice", ecole_id=self.ecole.id, date_naissance=date(2011, 2, 2))
        db.session.add_all([c1, eleve])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.flush()

        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=16.5, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add(n1)
        db.session.commit()

        eval_info = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Semestre 1")
        self.assertEqual(eval_info["status"], STATUS_COMPLETE)
        self.assertTrue(eval_info["is_official"])

        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Semestre 1")
        self.assertEqual(stats["rangs_par_inscription"].get(ins.id), 1)
        self.assertEqual(stats["moyenne_classe_officielle"], 16.5)
        self.assertEqual(stats["taux_reussite"], 100.0)

        bulletin, err = calculer_bulletin_data(self.ecole.id, self.annee, ins, periode="Semestre 1")
        self.assertIsNone(err)
        self.assertFalse(bulletin["est_provisoire"])
        self.assertEqual(bulletin["rang"], 1)
        self.assertEqual(bulletin["appreciation"], "Excellent")

    def test_13_periode_publiee_mais_eleve_avec_matiere_manquante_reste_provisoire(self):
        """13. Période publiée + élève avec matière manquante -> cet élève reste provisoire."""
        self.periode.publie = True
        db.session.commit()

        c1 = Cours(nom="SVT", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c2 = Cours(nom="Arts", coefficient=1.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        eleve = Eleve(nom="Partiel", prenom="Lucas", ecole_id=self.ecole.id, date_naissance=date(2011, 3, 3))
        db.session.add_all([c1, c2, eleve])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.flush()

        # Note uniquement en SVT, pas en Arts
        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=eleve.id, cours_id=c1.id, inscription_id=ins.id, valeur=14.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add(n1)
        db.session.commit()

        eval_info = calculer_completude_inscription(self.ecole.id, self.annee.id, ins, periode="Semestre 1")
        self.assertEqual(eval_info["status"], STATUS_PROVISOIRE)
        self.assertFalse(eval_info["is_official"])
        self.assertFalse(eval_info["is_pedagogically_complete"])

        bulletin, err = calculer_bulletin_data(self.ecole.id, self.annee, ins, periode="Semestre 1")
        self.assertIsNone(err)
        self.assertTrue(bulletin["est_provisoire"])
        self.assertIsNone(bulletin["rang"])
        self.assertEqual(bulletin["appreciation"], "En attente")

    def test_14_matiere_ayant_notes_dans_classe_mais_un_eleve_sans_note_detecte_incomplet(self):
        """14. Matière ayant des notes dans la classe mais 1 élève sans note -> élève détecté incomplet."""
        c1 = Cours(nom="Mathématiques 6A", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e1 = Eleve(nom="EleveA", prenom="NoteOk", ecole_id=self.ecole.id, date_naissance=date(2011, 4, 4))
        e2 = Eleve(nom="EleveB", prenom="NoteManquante", ecole_id=self.ecole.id, date_naissance=date(2011, 5, 5))
        db.session.add_all([c1, e1, e2])
        db.session.flush()

        ins1 = Inscription(ecole_id=self.ecole.id, eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        ins2 = Inscription(ecole_id=self.ecole.id, eleve_id=e2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add_all([ins1, ins2])
        db.session.flush()

        # e1 a une note en Maths, mais e2 n'en a AUCUNE
        n1 = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e1.id, cours_id=c1.id, inscription_id=ins1.id, valeur=15.0, type_evaluation="Devoir", periode="Semestre 1")
        db.session.add(n1)
        db.session.commit()

        verif = verifier_eligibilite_publication_periode(self.ecole.id, self.annee.id, "Semestre 1")
        self.assertFalse(verif["eligible"])
        self.assertGreaterEqual(verif["total_incomplets"], 1)

        # Vérifier que e2 est bien listé parmi les incomplets avec Mathématiques 6A manquant
        incomplets_classe = verif["details_par_classe"].get(self.classe.nom, [])
        e2_trouve = any(item["inscription_id"] == ins2.id and "Mathématiques 6A" in item["manquants"] for item in incomplets_classe)
        self.assertTrue(e2_trouve)

    def test_15_activer_periode_ne_necessite_pas_completude_notes(self):
        """15. Activer une période ne doit PAS nécessiter la complétude des notes (choix de la période de travail)."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_admin.id)
            sess["_fresh"] = True

        p2 = PeriodeBulletin(
            nom="Semestre 2",
            annee_id=self.annee.id,
            ecole_id=self.ecole.id,
            publie=False,
            periode_active=False,
        )
        db.session.add(p2)
        db.session.commit()

        # Activer Semestre 2 alors qu'il n'y a aucune note
        response = client.get(f"/activer_periode/{p2.id}", follow_redirects=False)
        self.assertEqual(response.status_code, 302)

        db.session.refresh(p2)
        self.assertTrue(p2.periode_active)
        # Ne doit pas avoir été forcée à publie=True
        self.assertFalse(p2.publie)

    def test_16_publication_demande_confirmation_si_bulletins_incomplets(self):
        """16. Tentative de publication avec bulletins incomplets : avertissement et demande de confirmation sans publier."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_admin.id)
            sess["_fresh"] = True

        p_test = PeriodeBulletin(
            nom="Trimestre Test",
            annee_id=self.annee.id,
            ecole_id=self.ecole.id,
            publie=False,
            periode_active=False,
        )
        c_obl = Cours(nom="Latin", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e_inc = Eleve(nom="SansNote", prenom="Marc", ecole_id=self.ecole.id, date_naissance=date(2011, 6, 6))
        db.session.add_all([p_test, c_obl, e_inc])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=e_inc.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.commit()

        # Premier clic sur Publier : doit afficher la page de confirmation sans publier
        response = client.get(f"/toggle_periode/{p_test.id}", follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        db.session.refresh(p_test)
        # La période ne doit PAS être publiée sans confirmation
        self.assertFalse(p_test.publie)

        html = response.get_data(as_text=True)
        self.assertIn("ont encore un bulletin incomplet", html)
        self.assertIn("Latin", html)
        self.assertIn("Confirmer la publication", html)
        self.assertIn("Annuler", html)

    def test_17_publication_possible_apres_confirmation_malgre_incomplets(self):
        """17. Confirmation explicite par l'admin : la période est publiée malgré des bulletins incomplets."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_admin.id)
            sess["_fresh"] = True

        p_test = PeriodeBulletin(
            nom="Trimestre Confirmé",
            annee_id=self.annee.id,
            ecole_id=self.ecole.id,
            publie=False,
            periode_active=False,
        )
        c_obl = Cours(nom="Philosophie", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        e_inc = Eleve(nom="Incomplet2", prenom="Paul", ecole_id=self.ecole.id, date_naissance=date(2011, 7, 7))
        db.session.add_all([p_test, c_obl, e_inc])
        db.session.flush()

        ins = Inscription(ecole_id=self.ecole.id, eleve_id=e_inc.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add(ins)
        db.session.commit()

        # Clic sur Confirmer la publication avec ?confirmer=1
        response = client.get(f"/toggle_periode/{p_test.id}?confirmer=1", follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        db.session.refresh(p_test)
        # La période est maintenant publiée
        self.assertTrue(p_test.publie)

        html = response.get_data(as_text=True)
        self.assertIn("publiée avec avertissement", html)

    def test_18_periode_publiee_avec_complets_et_incomplets_cohabitation(self):
        """18. Période publiée avec complets + incomplets :
        - complets deviennent officiels (avec rang et appréciation)
        - incomplets restent provisoires (sans rang, appréciation en attente)
        - incomplets strictement exclus du rang, du top et du taux de réussite de classe.
        """
        self.periode.publie = True
        db.session.commit()

        c_maths = Cours(nom="Mathématiques", coefficient=3.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        c_phys = Cours(nom="Physique", coefficient=2.0, ecole_id=self.ecole.id, classe_id=self.classe.id)
        db.session.add_all([c_maths, c_phys])
        db.session.flush()

        # Élève 1 : Complet (notes dans Maths et Physique)
        e1 = Eleve(nom="Complet", prenom="Karim", ecole_id=self.ecole.id, date_naissance=date(2011, 8, 8))
        # Élève 2 : Incomplet (note uniquement dans Maths)
        e2 = Eleve(nom="Incomplet", prenom="Nadia", ecole_id=self.ecole.id, date_naissance=date(2011, 9, 9))
        db.session.add_all([e1, e2])
        db.session.flush()

        ins1 = Inscription(ecole_id=self.ecole.id, eleve_id=e1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        ins2 = Inscription(ecole_id=self.ecole.id, eleve_id=e2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, statut="actif")
        db.session.add_all([ins1, ins2])
        db.session.flush()

        # Notes élève 1 : Maths=14, Physique=16 -> Moyenne = (14*3 + 16*2)/5 = 74/5 = 14.8
        n1_m = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e1.id, cours_id=c_maths.id, inscription_id=ins1.id, valeur=14.0, type_evaluation="Devoir", periode="Semestre 1")
        n1_p = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e1.id, cours_id=c_phys.id, inscription_id=ins1.id, valeur=16.0, type_evaluation="Devoir", periode="Semestre 1")

        # Note élève 2 : Maths=18 (Physique manquant)
        n2_m = Note(ecole_id=self.ecole.id, annee_id=self.annee.id, eleve_id=e2.id, cours_id=c_maths.id, inscription_id=ins2.id, valeur=18.0, type_evaluation="Devoir", periode="Semestre 1")

        db.session.add_all([n1_m, n1_p, n2_m])
        db.session.commit()

        # 1. Vérification élève 1 (complet)
        ev1 = calculer_completude_inscription(self.ecole.id, self.annee.id, ins1, periode="Semestre 1")
        self.assertEqual(ev1["status"], STATUS_COMPLETE)
        self.assertTrue(ev1["is_official"])
        self.assertEqual(ev1["average"], 14.8)

        b1, err1 = calculer_bulletin_data(self.ecole.id, self.annee, ins1, periode="Semestre 1")
        self.assertIsNone(err1)
        self.assertFalse(b1["est_provisoire"])
        self.assertEqual(b1["rang"], 1)
        self.assertNotEqual(b1["appreciation"], "En attente")

        # 2. Vérification élève 2 (incomplet)
        ev2 = calculer_completude_inscription(self.ecole.id, self.annee.id, ins2, periode="Semestre 1")
        self.assertEqual(ev2["status"], STATUS_PROVISOIRE)
        self.assertFalse(ev2["is_official"])
        self.assertEqual(ev2["missing_subjects_names"], ["Physique"])

        b2, err2 = calculer_bulletin_data(self.ecole.id, self.annee, ins2, periode="Semestre 1")
        self.assertIsNone(err2)
        self.assertTrue(b2["est_provisoire"])
        self.assertIsNone(b2["rang"])
        self.assertEqual(b2["appreciation"], "En attente")

        # 3. Vérification des statistiques de classe : exclusion de l'élève 2
        stats = calculer_stats_et_classements_classe(self.ecole.id, self.classe.id, self.annee.id, periode="Semestre 1")
        # Élève 1 est classé rang 1
        self.assertEqual(stats["rangs_par_inscription"].get(ins1.id), 1)
        # Élève 2 est exclu du rang
        self.assertIsNone(stats["rangs_par_inscription"].get(ins2.id))

        # Effectifs
        self.assertEqual(stats["effectif_total"], 2)
        self.assertEqual(stats["complets_count"], 1)
        self.assertEqual(stats["provisoires_count"], 1)

        # Statistiques officielles basées uniquement sur l'élève 1 (14.8), sans être polluées par le 18 de l'élève 2
        self.assertEqual(stats["moyenne_classe_officielle"], 14.8)
        self.assertEqual(stats["plus_forte_moyenne"], 14.8)
        self.assertEqual(stats["plus_faible_moyenne"], 14.8)
        self.assertEqual(stats["meilleur_eleve_complet"]["inscription"].id, ins1.id)
        self.assertEqual(stats["taux_reussite"], 100.0)


if __name__ == "__main__":
    unittest.main()
