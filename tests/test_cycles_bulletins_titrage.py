import io
import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.config import Config
from app.models import (
    Utilisateur, Ecole, AnneeScolaire, Classe, Eleve, Inscription,
    Bulletin, NiveauScolaire
)
from app.services import generer_bulletin_pdf, _construire_elements_bulletin, _obtenir_parametres_adaptatifs
from reportlab.lib.styles import getSampleStyleSheet
from app.services.bulletins_annuels import calculer_moyenne_annuelle_reglementaire
from app.services.passage_annee import (
    get_deliberations_annuelles_eleves,
    evaluer_deliberation_annuelle,
)
from app.services.niveaux import (
    determiner_cycle_classe,
    est_cycle_primaire,
    get_periodes_attendues_inscription,
)


class CyclesBulletinsTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-cycles"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestCyclesBulletinsTitrage(unittest.TestCase):
    def setUp(self):
        self.app = create_app(CyclesBulletinsTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Établissement
        self.ecole = Ecole(nom="Complexe Scolaire Excellence", onboarding_complete=True, ville="Niamey")
        db.session.add(self.ecole)
        db.session.commit()

        # Année scolaire active
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        # Niveaux
        self.niveau_cm2 = NiveauScolaire(code="CM2", nom="CM2", cycle="primaire", ordre=6)
        self.niveau_3e = NiveauScolaire(code="3E", nom="3e", cycle="college", ordre=10)
        db.session.add_all([self.niveau_cm2, self.niveau_3e])
        db.session.commit()

        # Classes Primaire & Secondaire
        self.classe_primaire = Classe(
            nom="CM2 A",
            niveau="CM2",
            niveau_id=self.niveau_cm2.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id
        )
        self.classe_secondaire = Classe(
            nom="3ème B",
            niveau="3e",
            niveau_id=self.niveau_3e.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id
        )
        db.session.add_all([self.classe_primaire, self.classe_secondaire])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # =========================================================================
    # 1. TEST DU TITRAGE DYNAMIQUE DU BULLETIN PDF
    # =========================================================================

    def test_titrage_dynamique_bulletin_pdf_compositions(self):
        """Vérifie que '1ère Composition' ne se transforme JAMAIS en '1ER SEMESTRE' et respecte le nom réel."""
        eleve = Eleve(
            nom="Moussa",
            prenom="Amina",
            matricule="25-0001",
            ecole_id=self.ecole.id,
            date_naissance=date(2013, 5, 10)
        )
        db.session.add(eleve)
        db.session.commit()

        styles = getSampleStyleSheet()
        cfg = _obtenir_parametres_adaptatifs(3)

        # 1. Test avec '1ère Composition'
        elements_c1 = _construire_elements_bulletin(
            cfg=cfg,
            content_w=500,
            styles=styles,
            eleve=eleve,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=14.5,
            nom_ecole=self.ecole.nom,
            classe_nom="CM2 A",
            annee_scolaire_nom="2025-2026",
            periode_nom="1ère Composition"
        )
        # Extraire le paragraphe de titre (header table, cellule [0, 2])
        header_table = elements_c1[0]
        title_p = header_table._cellvalues[0][2]
        self.assertIn("1ÈRE COMPOSITION", title_p.text)
        self.assertNotIn("1ER SEMESTRE", title_p.text)

        # 2. Test avec '2ème Composition'
        elements_c2 = _construire_elements_bulletin(
            cfg=cfg,
            content_w=500,
            styles=styles,
            eleve=eleve,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=15.0,
            nom_ecole=self.ecole.nom,
            classe_nom="CM2 A",
            annee_scolaire_nom="2025-2026",
            periode_nom="2ème Composition"
        )
        title_p2 = elements_c2[0]._cellvalues[0][2]
        self.assertIn("2ÈME COMPOSITION", title_p2.text)
        self.assertNotIn("2ÈME SEMESTRE", title_p2.text)

        # 3. Test avec '3ème Composition'
        elements_c3 = _construire_elements_bulletin(
            cfg=cfg,
            content_w=500,
            styles=styles,
            eleve=eleve,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=16.0,
            nom_ecole=self.ecole.nom,
            classe_nom="CM2 A",
            annee_scolaire_nom="2025-2026",
            periode_nom="3ème Composition"
        )
        title_p3 = elements_c3[0]._cellvalues[0][2]
        self.assertIn("3ÈME COMPOSITION", title_p3.text)

        # 4. Test avec '1er Semestre'
        elements_s1 = _construire_elements_bulletin(
            cfg=cfg,
            content_w=500,
            styles=styles,
            eleve=eleve,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=13.0,
            nom_ecole=self.ecole.nom,
            classe_nom="3ème B",
            annee_scolaire_nom="2025-2026",
            periode_nom="1er Semestre"
        )
        title_ps1 = elements_s1[0]._cellvalues[0][2]
        self.assertIn("1ER SEMESTRE", title_ps1.text)

        # 5. Vérifier que la génération complète du PDF fonctionne
        pdf_stream = generer_bulletin_pdf(
            eleve=eleve,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=14.5,
            nom_ecole=self.ecole.nom,
            classe_nom="CM2 A",
            annee_scolaire_nom="2025-2026",
            periode_nom="1ère Composition"
        )
        pdf_bytes = pdf_stream.getvalue()
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    # =========================================================================
    # 2. DÉTECTION DU CYCLE (PRIMAIRE vs SECONDAIRE)
    # =========================================================================

    def test_cycle_detection(self):
        """Vérifie la détection académique des classes et inscriptions."""
        self.assertTrue(est_cycle_primaire(self.classe_primaire))
        self.assertFalse(est_cycle_primaire(self.classe_secondaire))

        # Test sur variations de classes primaires
        for nom in ["CI A", "CP 1", "CE1 B", "CE2-2", "CM1", "CM2 B"]:
            c = Classe(nom=nom, ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
            self.assertTrue(est_cycle_primaire(c), f"Classe {nom} devrait être reconnue comme primaire")

        # Test sur variations de classes secondaires
        for nom in ["6ème A", "5e B", "4ème C", "3e", "2nde A", "1ere S", "Terminale D"]:
            c = Classe(nom=nom, ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
            self.assertFalse(est_cycle_primaire(c), f"Classe {nom} devrait être reconnue comme secondaire")

    # =========================================================================
    # 3. MOYENNE ANNUELLE PRIMAIRE (3 COMPOSITIONS RÉGLEMENTAIRES)
    # =========================================================================

    def test_primaire_moyenne_annuelle_cursus_incomplet_1_composition(self):
        """Primaire: 1 composition évaluée sur 3 -> div par 3, statut incomplet, passage bloqué."""
        eleve = Eleve(
            nom="Abdou", prenom="Salifou", matricule="25-0101",
            ecole_id=self.ecole.id, date_naissance=date(2014, 2, 10)
        )
        db.session.add(eleve)
        db.session.commit()

        insc = Inscription(
            eleve_id=eleve.id, classe_id=self.classe_primaire.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id,
            statut="inscrit"
        )
        db.session.add(insc)
        db.session.commit()

        # 1 seule composition : 15/20
        b1 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="1ère Composition", moyenne_generale=15.0
        )
        db.session.add(b1)
        db.session.commit()

        # Service bulletins_annuels
        res = calculer_moyenne_annuelle_reglementaire(insc.id, self.ecole.id)
        self.assertTrue(res["cursus_incomplet"])
        self.assertEqual(res["statut"], "Dossier Incomplet")
        self.assertEqual(res["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(res["nb_periodes_evaluees"], 1)
        self.assertEqual(res["nb_periodes_attendues"], 3)
        # Règle académique stricte : 15.0 / 3 = 5.0
        self.assertEqual(res["moyenne_annuelle"], 5.0)

        # Service passage_annee (délibération)
        delib = evaluer_deliberation_annuelle(self.ecole.id, self.annee.id, eleve.id)
        self.assertTrue(delib["cursus_incomplet"])
        self.assertEqual(delib["statut_deliberation"], "Dossier Incomplet")
        self.assertEqual(delib["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(delib["periodes_evaluees"], 1)
        self.assertEqual(delib["periodes_attendues"], 3)
        self.assertEqual(delib["moyenne"], 5.0)

    def test_primaire_moyenne_annuelle_cursus_incomplet_2_compositions(self):
        """Primaire: 2 compositions évaluées sur 3 -> div par 3, statut incomplet, passage bloqué."""
        eleve = Eleve(
            nom="Balla", prenom="Nafissatou", matricule="25-0102",
            ecole_id=self.ecole.id, date_naissance=date(2014, 4, 18)
        )
        db.session.add(eleve)
        db.session.commit()

        insc = Inscription(
            eleve_id=eleve.id, classe_id=self.classe_primaire.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id,
            statut="inscrit"
        )
        db.session.add(insc)
        db.session.commit()

        # 2 compositions : 12/20 et 15/20
        b1 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="1ère Composition", moyenne_generale=12.0
        )
        b2 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="2ème Composition", moyenne_generale=15.0
        )
        db.session.add_all([b1, b2])
        db.session.commit()

        # Somme = 27.0 -> Moyenne réglementaire = 27.0 / 3 = 9.0
        res = calculer_moyenne_annuelle_reglementaire(insc.id, self.ecole.id)
        self.assertTrue(res["cursus_incomplet"])
        self.assertEqual(res["statut"], "Dossier Incomplet")
        self.assertEqual(res["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(res["nb_periodes_evaluees"], 2)
        self.assertEqual(res["nb_periodes_attendues"], 3)
        self.assertEqual(res["moyenne_annuelle"], 9.0)

        delib = evaluer_deliberation_annuelle(self.ecole.id, self.annee.id, eleve.id)
        self.assertTrue(delib["cursus_incomplet"])
        self.assertEqual(delib["statut_deliberation"], "Dossier Incomplet")
        self.assertEqual(delib["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(delib["periodes_evaluees"], 2)
        self.assertEqual(delib["periodes_attendues"], 3)
        self.assertEqual(delib["moyenne"], 9.0)

    def test_primaire_moyenne_annuelle_cursus_complet_3_compositions(self):
        """Primaire: 3 compositions complétées (12, 14, 16) -> div par 3 = 14.0, Complet, Passage."""
        eleve = Eleve(
            nom="Oumarou", prenom="Fati", matricule="25-0103",
            ecole_id=self.ecole.id, date_naissance=date(2014, 6, 25)
        )
        db.session.add(eleve)
        db.session.commit()

        insc = Inscription(
            eleve_id=eleve.id, classe_id=self.classe_primaire.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id,
            statut="inscrit"
        )
        db.session.add(insc)
        db.session.commit()

        b1 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="1ère Composition", moyenne_generale=12.0
        )
        b2 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="2ème Composition", moyenne_generale=14.0
        )
        b3 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="3ème Composition", moyenne_generale=16.0
        )
        db.session.add_all([b1, b2, b3])
        db.session.commit()

        # (12 + 14 + 16) / 3 = 14.0
        res = calculer_moyenne_annuelle_reglementaire(insc.id, self.ecole.id)
        self.assertFalse(res["cursus_incomplet"])
        self.assertEqual(res["statut"], "Complet")
        self.assertEqual(res["suggestion"], "Passage")
        self.assertEqual(res["nb_periodes_evaluees"], 3)
        self.assertEqual(res["nb_periodes_attendues"], 3)
        self.assertEqual(res["moyenne_annuelle"], 14.0)

        delib = evaluer_deliberation_annuelle(self.ecole.id, self.annee.id, eleve.id)
        self.assertFalse(delib["cursus_incomplet"])
        self.assertEqual(delib["statut_deliberation"], "Admis")
        self.assertEqual(delib["suggestion"], "passage")
        self.assertEqual(delib["periodes_evaluees"], 3)
        self.assertEqual(delib["periodes_attendues"], 3)
        self.assertEqual(delib["moyenne"], 14.0)

    # =========================================================================
    # 4. MOYENNE ANNUELLE SECONDAIRE (2 SEMESTRES RÉGLEMENTAIRES)
    # =========================================================================

    def test_secondaire_moyenne_annuelle_cursus_incomplet_1_semestre(self):
        """Secondaire: 1 semestre sur 2 (ex: 16/20) -> div par 2 = 8.0, Dossier Incomplet, suggestion bloquée."""
        eleve = Eleve(
            nom="Seydou", prenom="Issoufou", matricule="25-0201",
            ecole_id=self.ecole.id, date_naissance=date(2011, 8, 12)
        )
        db.session.add(eleve)
        db.session.commit()

        insc = Inscription(
            eleve_id=eleve.id, classe_id=self.classe_secondaire.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id,
            statut="inscrit"
        )
        db.session.add(insc)
        db.session.commit()

        b1 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="1er Semestre", moyenne_generale=16.0
        )
        db.session.add(b1)
        db.session.commit()

        # 16.0 / 2 = 8.0
        res = calculer_moyenne_annuelle_reglementaire(insc.id, self.ecole.id)
        self.assertTrue(res["cursus_incomplet"])
        self.assertEqual(res["statut"], "Dossier Incomplet")
        self.assertEqual(res["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(res["nb_periodes_evaluees"], 1)
        self.assertEqual(res["nb_periodes_attendues"], 2)
        self.assertEqual(res["moyenne_annuelle"], 8.0)

        delib = evaluer_deliberation_annuelle(self.ecole.id, self.annee.id, eleve.id)
        self.assertTrue(delib["cursus_incomplet"])
        self.assertEqual(delib["statut_deliberation"], "Dossier Incomplet")
        self.assertEqual(delib["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(delib["periodes_evaluees"], 1)
        self.assertEqual(delib["periodes_attendues"], 2)
        self.assertEqual(delib["moyenne"], 8.0)

    def test_secondaire_moyenne_annuelle_cursus_complet_2_semestres(self):
        """Secondaire: 2 semestres (14.0 et 12.0) -> div par 2 = 13.0, Complet, Passage."""
        eleve = Eleve(
            nom="Hassane", prenom="Mariama", matricule="25-0202",
            ecole_id=self.ecole.id, date_naissance=date(2011, 10, 5)
        )
        db.session.add(eleve)
        db.session.commit()

        insc = Inscription(
            eleve_id=eleve.id, classe_id=self.classe_secondaire.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id,
            statut="inscrit"
        )
        db.session.add(insc)
        db.session.commit()

        b1 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="1er Semestre", moyenne_generale=14.0
        )
        b2 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=insc.id, eleve_id=eleve.id,
            periode="2ème Semestre", moyenne_generale=12.0
        )
        db.session.add_all([b1, b2])
        db.session.commit()

        # (14 + 12) / 2 = 13.0
        res = calculer_moyenne_annuelle_reglementaire(insc.id, self.ecole.id)
        self.assertFalse(res["cursus_incomplet"])
        self.assertEqual(res["statut"], "Complet")
        self.assertEqual(res["suggestion"], "Passage")
        self.assertEqual(res["nb_periodes_evaluees"], 2)
        self.assertEqual(res["nb_periodes_attendues"], 2)
        self.assertEqual(res["moyenne_annuelle"], 13.0)

        delib = evaluer_deliberation_annuelle(self.ecole.id, self.annee.id, eleve.id)
        self.assertFalse(delib["cursus_incomplet"])
        self.assertEqual(delib["statut_deliberation"], "Admis")
        self.assertEqual(delib["suggestion"], "passage")
        self.assertEqual(delib["periodes_evaluees"], 2)
        self.assertEqual(delib["periodes_attendues"], 2)
        self.assertEqual(delib["moyenne"], 13.0)

    # =========================================================================
    # 5. DÉLIBÉRATION GLOBALE MULTI-CYCLES AU SEIN D'UN MÊME ÉTABLISSEMENT
    # =========================================================================

    def test_deliberation_globale_multi_cycles(self):
        """Vérifie qu'un élève primaire avec 2 compositions reste incomplet tandis qu'un élève secondaire avec 2 semestres est complet."""
        # Élève Primaire avec 2 compositions
        el_prim = Eleve(
            nom="Dan Kobo", prenom="Ali", matricule="25-0301",
            ecole_id=self.ecole.id, date_naissance=date(2014, 1, 1)
        )
        # Élève Secondaire avec 2 semestres
        el_sec = Eleve(
            nom="Mainassara", prenom="Rabi", matricule="25-0302",
            ecole_id=self.ecole.id, date_naissance=date(2011, 2, 2)
        )
        db.session.add_all([el_prim, el_sec])
        db.session.commit()

        ins_prim = Inscription(
            eleve_id=el_prim.id, classe_id=self.classe_primaire.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id, statut="inscrit"
        )
        ins_sec = Inscription(
            eleve_id=el_sec.id, classe_id=self.classe_secondaire.id,
            annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id, statut="inscrit"
        )
        db.session.add_all([ins_prim, ins_sec])
        db.session.commit()

        # Bulletins primaire : 2 compositions (incomplet pour le primaire)
        bp1 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=ins_prim.id, eleve_id=el_prim.id,
            periode="1ère Composition", moyenne_generale=14.0
        )
        bp2 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=ins_prim.id, eleve_id=el_prim.id,
            periode="2ème Composition", moyenne_generale=16.0
        )
        # Bulletins secondaire : 2 semestres (complet pour le secondaire)
        bs1 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=ins_sec.id, eleve_id=el_sec.id,
            periode="1er Semestre", moyenne_generale=13.0
        )
        bs2 = Bulletin(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
            inscription_id=ins_sec.id, eleve_id=el_sec.id,
            periode="2ème Semestre", moyenne_generale=15.0
        )
        db.session.add_all([bp1, bp2, bs1, bs2])
        db.session.commit()

        delibs = get_deliberations_annuelles_eleves(self.ecole.id, self.annee.id)

        # Élève Primaire : 2/3 compositions -> Incomplet
        d_prim = delibs[el_prim.id]
        self.assertTrue(d_prim["cursus_incomplet"])
        self.assertEqual(d_prim["periodes_evaluees"], 2)
        self.assertEqual(d_prim["periodes_attendues"], 3)
        self.assertEqual(d_prim["moyenne"], 10.0)  # (14 + 16) / 3
        self.assertEqual(d_prim["suggestion"], "Décision réservée au conseil (cursus incomplet)")

        # Élève Secondaire : 2/2 semestres -> Complet
        d_sec = delibs[el_sec.id]
        self.assertFalse(d_sec["cursus_incomplet"])
        self.assertEqual(d_sec["periodes_evaluees"], 2)
        self.assertEqual(d_sec["periodes_attendues"], 2)
        self.assertEqual(d_sec["moyenne"], 14.0)  # (13 + 15) / 2
        self.assertEqual(d_sec["suggestion"], "passage")


if __name__ == '__main__':
    unittest.main()
