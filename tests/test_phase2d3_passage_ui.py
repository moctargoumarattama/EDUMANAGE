"""
tests/test_phase2d3_passage_ui.py
=============================================================
KLASORA — Phase 2D-3
Tests de l'interface de passage d'année des élèves (traitement individuel).

Couverture :
  Section 42 : Tests page (1 à 10)
  Section 43 : Tests liste (11 à 17)
  Section 44 : Tests passage (18 à 23)
  Section 45 : Tests redoublement (24 à 26)
  Section 46 : Tests sorties (27 à 31)
  Section 47 : Tests conflit (32 à 34)
  Section 48 : Tests structure (35 à 37)
  Section 49 : Tests transaction (38 à 40)
=============================================================
"""

import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.niveaux import ensure_standard_niveaux
from app.services.niveaux_annuels import sauvegarder_selection_annuelle


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class BasePassageUITestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Niveaux standard
        niveaux = ensure_standard_niveaux(commit=False)
        db.session.flush()
        self.niv = {n.code: n for n in niveaux}

        # Écoles
        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Années école A
        self.annee_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_cible = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        self.annee_archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )

        # Année école B
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_active, self.annee_cible, self.annee_archivee, self.annee_b])
        db.session.flush()

        # Classes école A
        # Source (2025-2026 active)
        self.cls_6a = Classe(
            nom="6e A",
            niveau=self.niv["6E"].nom,
            niveau_id=self.niv["6E"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id,
            statut="ouverte",
        )
        self.cls_term_src = Classe(
            nom="Terminale A",
            niveau=self.niv["TERMINALE"].nom,
            niveau_id=self.niv["TERMINALE"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id,
            statut="ouverte",
        )
        # Cible (2026-2027 planifiée)
        self.cls_5a = Classe(
            nom="5e A",
            niveau=self.niv["5E"].nom,
            niveau_id=self.niv["5E"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )
        self.cls_5b = Classe(
            nom="5e B",
            niveau=self.niv["5E"].nom,
            niveau_id=self.niv["5E"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )
        self.cls_5c_fermee = Classe(
            nom="5e C",
            niveau=self.niv["5E"].nom,
            niveau_id=self.niv["5E"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="fermee",
        )
        self.cls_6b_cible = Classe(
            nom="6e B",
            niveau=self.niv["6E"].nom,
            niveau_id=self.niv["6E"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )
        self.cls_4a = Classe(
            nom="4e A",
            niveau=self.niv["4E"].nom,
            niveau_id=self.niv["4E"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )
        self.cls_term_cible = Classe(
            nom="Terminale Cible",
            niveau=self.niv["TERMINALE"].nom,
            niveau_id=self.niv["TERMINALE"].id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )

        # Classe école B
        self.cls_b = Classe(
            nom="Classe B",
            niveau=self.niv["6E"].nom,
            niveau_id=self.niv["6E"].id,
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_b.id,
            statut="ouverte",
        )

        db.session.add_all([
            self.cls_6a, self.cls_term_src,
            self.cls_5a, self.cls_5b, self.cls_5c_fermee,
            self.cls_6b_cible, self.cls_4a, self.cls_term_cible,
            self.cls_b,
        ])
        db.session.flush()

        # Activer niveaux dans cible
        sauvegarder_selection_annuelle(
            self.ecole_a.id,
            self.annee_cible.id,
            [self.niv["6E"].id, self.niv["5E"].id, self.niv["4E"].id, self.niv["TERMINALE"].id],
        )
        db.session.flush()

        # Élèves
        self.moussa = Eleve(
            nom="Moussa",
            prenom="Abdou",
            date_naissance=date(2013, 2, 3),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_6a.id,
        )
        self.amina = Eleve(
            nom="Amina",
            prenom="Issa",
            date_naissance=date(2013, 5, 6),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_6a.id,
        )
        self.eleve_term = Eleve(
            nom="Diallo",
            prenom="Oumar",
            date_naissance=date(2007, 8, 9),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_term_src.id,
        )
        self.eleve_b = Eleve(
            nom="Alien",
            prenom="B",
            date_naissance=date(2013, 1, 1),
            ecole_id=self.ecole_b.id,
            classe_id=self.cls_b.id,
        )
        db.session.add_all([self.moussa, self.amina, self.eleve_term, self.eleve_b])
        db.session.flush()

        # Inscriptions dans l'année source (2025-2026)
        self.insc_moussa, _ = creer_inscription_annuelle(
            self.ecole_a.id, self.moussa.id, self.annee_active.id, self.cls_6a.id, sync_active=False
        )
        self.insc_amina, _ = creer_inscription_annuelle(
            self.ecole_a.id, self.amina.id, self.annee_active.id, self.cls_6a.id, sync_active=False
        )
        self.insc_term, _ = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve_term.id, self.annee_active.id, self.cls_term_src.id, sync_active=False
        )

        # Utilisateurs
        self.admin = Utilisateur(
            nom="Admin", email="admin@ecole-a.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id
        )
        self.super_admin = Utilisateur(
            nom="SuperAdmin", email="superadmin@klasora.local", mot_de_passe="pass", role="super_admin"
        )
        self.prof = Utilisateur(
            nom="Prof", email="prof@ecole-a.local", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id
        )
        self.parent = Utilisateur(
            nom="Parent", email="parent@ecole-a.local", mot_de_passe="pass", role="parent", ecole_id=self.ecole_a.id
        )
        db.session.add_all([self.admin, self.super_admin, self.prof, self.parent])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user, ecole_id=None):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            if ecole_id:
                sess["ecole_id"] = ecole_id
            elif getattr(user, "ecole_id", None):
                sess["ecole_id"] = user.ecole_id
        return client


# ===========================================================================
# Section 42 : Tests page (1 à 10)
# ===========================================================================

class TestPassagePage(BasePassageUITestCase):
    def test_01_admin_voit_page_passage(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Passage d'année".encode("utf-8"), resp.data)
        self.assertIn(b"Moussa", resp.data)
        self.assertIn(b"2025-2026", resp.data)
        self.assertIn(b"2026-2027", resp.data)

    def test_02_professeur_refuse(self):
        client = self.login_as(self.prof)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}"
        resp = client.get(url)
        self.assertIn(resp.status_code, (302, 403))

    def test_03_parent_refuse(self):
        client = self.login_as(self.parent)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}"
        resp = client.get(url)
        self.assertIn(resp.status_code, (302, 403))

    def test_04_ecole_b_refusee(self):
        # Admin école A tente d'accéder avec année école B
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_b.id}"
        resp = client.get(url, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_05_source_cible_invalides_refusees(self):
        client = self.login_as(self.admin)
        resp = client.get(f"/annees/9999/passage/{self.annee_cible.id}")
        self.assertEqual(resp.status_code, 302)

    def test_06_source_egale_cible_refusee(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_active.id}"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_07_cible_archivee_refusee(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_archivee.id}"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_08_source_apres_cible_refusee(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_cible.id}/passage/{self.annee_active.id}"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_09_get_ne_modifie_aucune_donnee(self):
        client = self.login_as(self.admin)
        inscriptions_avant = Inscription.query.count()
        eleves_avant = Eleve.query.count()
        classes_avant = Classe.query.count()

        client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")

        self.assertEqual(Inscription.query.count(), inscriptions_avant)
        self.assertEqual(Eleve.query.count(), eleves_avant)
        self.assertEqual(Classe.query.count(), classes_avant)

    def test_10_get_ne_change_pas_contexte_annuel_global(self):
        client = self.login_as(self.admin)
        with client.session_transaction() as sess:
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_active.id}

        client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")

        with client.session_transaction() as sess:
            contexte = sess.get("annee_consultee", {}).get(str(self.ecole_a.id))
            self.assertEqual(contexte, self.annee_active.id)


# ===========================================================================
# Section 43 : Tests liste (11 à 17)
# ===========================================================================

class TestPassageListe(BasePassageUITestCase):
    def test_11_eleves_source_affiches_via_inscription(self):
        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertIn(b"Moussa", resp.data)
        self.assertIn(b"Amina", resp.data)

    def test_12_eleve_classe_id_seul_ne_suffit_pas(self):
        # Élève avec Eleve.classe_id dans 6e A mais SANS inscription dans annee_active
        eleve_fantome = Eleve(
            nom="Fantome", prenom="SansInsc", date_naissance=date(2013, 1, 1),
            ecole_id=self.ecole_a.id, classe_id=self.cls_6a.id
        )
        db.session.add(eleve_fantome)
        db.session.commit()

        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertNotIn(b"Fantome", resp.data)

    def test_13_un_eleve_une_ligne(self):
        # Moussa a aussi une inscription historique dans l'année archivée
        creer_inscription_annuelle(
            self.ecole_a.id, self.moussa.id, self.annee_archivee.id, self.cls_6a.id, sync_active=False
        )
        db.session.commit()

        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        # Comptage des occurrences de Moussa dans le tbody
        self.assertEqual(resp.data.count(b"<strong>Moussa Abdou</strong>"), 1)

    def test_14_classe_source_correcte(self):
        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertIn(b"6e A", resp.data)

    def test_15_statut_traite_correct(self):
        # Moussa déjà inscrit dans cible en 5e B
        creer_inscription_annuelle(
            self.ecole_a.id, self.moussa.id, self.annee_cible.id, self.cls_5b.id, sync_active=False
        )
        self.insc_moussa.statut = "termine"
        self.insc_moussa.decision_fin_annee = "passage"
        db.session.commit()

        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertIn("Passage → 5e B".encode("utf-8"), resp.data)
        self.assertIn(b"badge bg-success", resp.data)

    def test_16_statut_non_traite_correct(self):
        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertIn("Non traité".encode("utf-8"), resp.data)

    def test_17_compteurs_corrects(self):
        # Moussa déjà traité (passage)
        creer_inscription_annuelle(
            self.ecole_a.id, self.moussa.id, self.annee_cible.id, self.cls_5b.id, sync_active=False
        )
        self.insc_moussa.statut = "termine"
        self.insc_moussa.decision_fin_annee = "passage"
        db.session.commit()

        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        # Total élèves source: 3 (Moussa, Amina, Diallo)
        # Traités: 1 (Moussa)
        # À traiter: 2 (Amina, Diallo)
        self.assertIn(b'<div class="fs-4 fw-bold text-dark">3</div>', resp.data)
        self.assertIn(b'<div class="fs-4 fw-bold text-success">1</div>', resp.data)
        self.assertIn(b'<div class="fs-4 fw-bold text-warning">2</div>', resp.data)


# ===========================================================================
# Section 44 : Tests passage (18 à 23)
# ===========================================================================

class TestPassagePromotion(BasePassageUITestCase):
    def test_18_6e_affiche_seulement_5e_ouvertes(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"5e A", resp.data)
        self.assertIn(b"5e B", resp.data)

    def test_19_4e_non_proposee(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.get(url)
        # 4e A ne doit pas être proposée pour le passage de 6e
        self.assertNotIn(b'data-decision="passage">4e A', resp.data)

    def test_20_classe_fermee_absente(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.get(url)
        # 5e C est fermée, elle ne doit pas figurer dans les options
        self.assertNotIn(b"5e C", resp.data)

    def test_21_post_valide_cree_nouvelle_inscription(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.cls_5b.id,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Vérifier création Inscription cible
        insc_cible = Inscription.query.filter_by(
            eleve_id=self.moussa.id,
            annee_scolaire_id=self.annee_cible.id,
        ).first()
        self.assertIsNotNone(insc_cible)
        self.assertEqual(insc_cible.classe_id, self.cls_5b.id)
        self.assertEqual(insc_cible.statut, "inscrit")

    def test_22_source_cloturee(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.cls_5b.id,
        })

        src = db.session.get(Inscription, self.insc_moussa.id)
        self.assertEqual(src.statut, "termine")
        self.assertEqual(src.decision_fin_annee, "passage")

    def test_23_cible_planifiee_ne_change_pas_eleve_classe_id(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.cls_5b.id,
        })

        eleve = db.session.get(Eleve, self.moussa.id)
        self.assertEqual(eleve.classe_id, self.cls_6a.id)


# ===========================================================================
# Section 45 : Tests redoublement (24 à 26)
# ===========================================================================

class TestPassageRedoublement(BasePassageUITestCase):
    def test_24_6e_redoublement_propose_seulement_6e(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.get(url)
        self.assertIn(b'data-decision="redoublement">6e B', resp.data)

    def test_25_post_valide_fonctionne(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.post(url, data={
            "decision": "redoublement",
            "classe_cible_id": self.cls_6b_cible.id,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        insc_cible = Inscription.query.filter_by(
            eleve_id=self.moussa.id,
            annee_scolaire_id=self.annee_cible.id,
        ).first()
        self.assertIsNotNone(insc_cible)
        self.assertEqual(insc_cible.classe_id, self.cls_6b_cible.id)

    def test_26_section_differente_possible(self):
        # 6e A source -> 6e B cible autorisé
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        client.post(url, data={
            "decision": "redoublement",
            "classe_cible_id": self.cls_6b_cible.id,
        })
        src = db.session.get(Inscription, self.insc_moussa.id)
        self.assertEqual(src.decision_fin_annee, "redoublement")


# ===========================================================================
# Section 46 : Tests sorties (27 à 31)
# ===========================================================================

class TestPassageSorties(BasePassageUITestCase):
    def test_27_transfert_sans_classe_cible(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.post(url, data={
            "decision": "transfert",
            "motif_sortie": "Changement de ville",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        count_cible = Inscription.query.filter_by(
            eleve_id=self.moussa.id, annee_scolaire_id=self.annee_cible.id
        ).count()
        self.assertEqual(count_cible, 0)

        src = db.session.get(Inscription, self.insc_moussa.id)
        self.assertEqual(src.statut, "transfere")
        self.assertEqual(src.decision_fin_annee, "transfert")

    def test_28_sortie_sans_classe_cible(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        client.post(url, data={"decision": "sortie"}, follow_redirects=True)

        src = db.session.get(Inscription, self.insc_moussa.id)
        self.assertEqual(src.statut, "sorti")
        self.assertEqual(src.decision_fin_annee, "sortie")

    def test_29_diplome_terminale_sans_classe_cible(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_term.id}"
        resp = client.post(url, data={"decision": "diplome"}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        src = db.session.get(Inscription, self.insc_term.id)
        self.assertEqual(src.statut, "diplome")
        self.assertEqual(src.decision_fin_annee, "diplome")

    def test_30_6e_diplome_refuse(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.post(url, data={"decision": "diplome"}, follow_redirects=True)
        self.assertIn("diplome".encode("utf-8"), resp.data.lower())

        src = db.session.get(Inscription, self.insc_moussa.id)
        self.assertNotEqual(src.statut, "diplome")

    def test_31_motif_sortie_transmis(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        client.post(url, data={
            "decision": "transfert",
            "motif_sortie": "Deménagement familial",
        })
        src = db.session.get(Inscription, self.insc_moussa.id)
        self.assertEqual(src.motif_sortie, "Deménagement familial")


# ===========================================================================
# Section 47 : Tests conflit (32 à 34)
# ===========================================================================

class TestPassageConflit(BasePassageUITestCase):
    def test_32_eleve_deja_traite_identique_idempotent(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"

        # 1er passage
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_5b.id})
        # 2e passage identique
        resp = client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_5b.id}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("déjà été traité".encode("utf-8"), resp.data)

    def test_33_eleve_cible_incompatible_erreur(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"

        # Déjà inscrit en 5e A
        creer_inscription_annuelle(
            self.ecole_a.id, self.moussa.id, self.annee_cible.id, self.cls_5a.id, sync_active=False
        )
        db.session.commit()

        # Tentative redoublement vers 6e B -> conflit
        resp = client.post(url, data={"decision": "redoublement", "classe_cible_id": self.cls_6b_cible.id}, follow_redirects=True)
        self.assertIn("conflit".encode("utf-8"), resp.data.lower())

    def test_34_aucune_duplication_inscription(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"

        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_5b.id})
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_5b.id})

        count = Inscription.query.filter_by(
            eleve_id=self.moussa.id,
            annee_scolaire_id=self.annee_cible.id,
        ).count()
        self.assertEqual(count, 1)


# ===========================================================================
# Section 48 : Tests structure (35 à 37)
# ===========================================================================

class TestPassageStructure(BasePassageUITestCase):
    def test_35_niveau_cible_non_utilise_aucune_classe(self):
        # Désactiver 5E dans cible
        config = AnneeNiveauConfig.query.filter_by(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niv["5E"].id,
        ).first()
        if config:
            config.actif = False
            db.session.commit()

        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.get(url)
        self.assertNotIn(b'data-decision="passage">5e A', resp.data)

    def test_36_aucune_classe_ouverte_message_propre(self):
        # Fermer 5e A et 5e B
        self.cls_5a.statut = "fermee"
        self.cls_5b.statut = "fermee"
        db.session.commit()

        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.get(url)
        self.assertIn("Aucune classe ouverte disponible".encode("utf-8"), resp.data)

    def test_37_niveau_source_inconnu_blocage_propre(self):
        self.cls_6a.niveau_id = None
        db.session.commit()

        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        resp = client.get(url)
        self.assertIn("Le niveau de la classe source n'est pas configuré".encode("utf-8"), resp.data)


# ===========================================================================
# Section 49 : Tests transaction (38 à 40)
# ===========================================================================

class TestPassageTransaction(BasePassageUITestCase):
    def test_38_succes_commit(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_5b.id})

        # Nouvelle session pour vérifier la persistance
        db.session.expire_all()
        insc = Inscription.query.filter_by(
            eleve_id=self.moussa.id, annee_scolaire_id=self.annee_cible.id
        ).first()
        self.assertIsNotNone(insc)

    def test_39_erreur_rollback(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"

        # Soumettre une classe fermée -> refusé
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_5c_fermee.id})

        # Vérifier que rien n'a été créé
        db.session.expire_all()
        insc = Inscription.query.filter_by(
            eleve_id=self.moussa.id, annee_scolaire_id=self.annee_cible.id
        ).first()
        self.assertIsNone(insc)

    def test_40_aucune_mutation_partielle(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.moussa.id}"

        # Soumettre une classe fermée
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_5c_fermee.id})

        # La source doit être intacte
        db.session.expire_all()
        src = db.session.get(Inscription, self.insc_moussa.id)
        self.assertEqual(src.statut, "inscrit")
        self.assertIsNone(src.decision_fin_annee)


if __name__ == "__main__":
    unittest.main()
