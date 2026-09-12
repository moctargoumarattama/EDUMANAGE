"""
tests/test_phase2d3c_acces_permanent_passage.py
=============================================================
KLASORA — Phase 2D-3C
Tests d'accès permanent au passage d'année des élèves.

Vérifie les 10 critères requis :
1. bouton Passage des élèves visible dans /annees ;
2. bouton toujours visible après traitement d’un élève ;
3. accès toujours possible avec élèves déjà traités ;
4. page accessible avec 0 élève à traiter ;
5. admin autorisé ;
6. professeur/parent refusés ;
7. autre école refusée ;
8. année cible archivée non modifiable ;
9. bouton Retour aux passages fonctionne ;
10. aucun changement de session annee_consultee par simple GET.
=============================================================
"""

import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
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
from app.services.passage_annee import executer_passage_eleve
from app.services.annees_scolaires import get_annee_consultee, set_annee_consultee


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class TestAccesPermanentPassage(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        niveaux = ensure_standard_niveaux(commit=False)
        db.session.flush()
        self.niv = {n.code: n for n in niveaux}

        # Écoles
        self.ecole_a = Ecole(nom="Ecole Alpha")
        self.ecole_b = Ecole(nom="Ecole Beta")
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

        # Classes
        self.cls_6a = Classe(
            nom="6e A",
            niveau=self.niv["6E"].nom,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id,
            niveau_id=self.niv["6E"].id,
        )
        self.cls_5a = Classe(
            nom="5e A",
            niveau=self.niv["5E"].nom,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niv["5E"].id,
        )
        db.session.add_all([self.cls_6a, self.cls_5a])
        db.session.flush()

        # Niveaux annuels
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_active.id, [self.niv["6E"].id])
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_cible.id, [self.niv["6E"].id, self.niv["5E"].id])

        # Élèves
        self.eleve1 = Eleve(
            nom="DIALLO",
            prenom="Amadou",
            date_naissance=date(2013, 1, 1),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_6a.id,
        )
        self.eleve2 = Eleve(
            nom="BAH",
            prenom="Mariam",
            date_naissance=date(2013, 2, 2),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_6a.id,
        )
        db.session.add_all([self.eleve1, self.eleve2])
        db.session.flush()

        # Inscriptions source
        self.insc1, _ = creer_inscription_annuelle(
            self.ecole_a.id,
            self.eleve1.id,
            self.annee_active.id,
            self.cls_6a.id,
            sync_active=False,
        )
        self.insc2, _ = creer_inscription_annuelle(
            self.ecole_a.id,
            self.eleve2.id,
            self.annee_active.id,
            self.cls_6a.id,
            sync_active=False,
        )

        # Utilisateurs
        self.admin = Utilisateur(
            nom="Admin",
            email="admin@alpha.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_a.id,
        )
        self.prof = Utilisateur(
            nom="Prof",
            email="prof@alpha.local",
            mot_de_passe="pass",
            role="professeur",
            ecole_id=self.ecole_a.id,
        )
        self.parent = Utilisateur(
            nom="Parent",
            email="parent@alpha.local",
            mot_de_passe="pass",
            role="parent",
            ecole_id=self.ecole_a.id,
        )
        self.admin_b = Utilisateur(
            nom="AdminB",
            email="admin@beta.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_b.id,
        )

        db.session.add_all([self.admin, self.prof, self.parent, self.admin_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            if getattr(user, "ecole_id", None):
                sess["ecole_id"] = user.ecole_id
        return client

    # -------------------------------------------------------------
    # 1. Bouton Passage des élèves visible dans /annees
    # -------------------------------------------------------------
    def test_01_bouton_passage_eleves_visible_dans_annees(self):
        client = self.login_as(self.admin)
        resp = client.get("/annees")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        expected_url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}"
        self.assertIn(expected_url, html)
        self.assertIn("Passage des élèves", html)

    # -------------------------------------------------------------
    # 2. Bouton toujours visible après traitement d'un élève
    # -------------------------------------------------------------
    def test_02_bouton_toujours_visible_apres_traitement_un_eleve(self):
        res, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve1.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5a.id,
        )
        self.assertIsNone(err)
        db.session.commit()

        client = self.login_as(self.admin)
        resp = client.get("/annees")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        expected_url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}"
        self.assertIn(expected_url, html)
        self.assertIn("Passage des élèves", html)

    # -------------------------------------------------------------
    # 3. Accès toujours possible avec élèves déjà traités
    # -------------------------------------------------------------
    def test_03_acces_toujours_possible_avec_eleves_deja_traites(self):
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve1.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5a.id,
        )
        db.session.commit()

        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Élèves traités", html)
        self.assertIn("Élèves à traiter", html)
        self.assertIn("DIALLO", html)
        self.assertIn("BAH", html)

    # -------------------------------------------------------------
    # 4. Page accessible avec 0 élève à traiter (tous traités)
    # -------------------------------------------------------------
    def test_04_page_accessible_avec_zero_eleve_a_traiter(self):
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve1.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5a.id,
        )
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve2.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_cible.id,
            decision="redoublement",
            classe_cible_id=self.cls_6a.id,
        )
        db.session.commit()

        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("100%", html)
        self.assertIn("Traité", html)

        # Dans /annees le bouton reste également visible
        resp_annees = client.get("/annees")
        self.assertIn(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}", resp_annees.get_data(as_text=True))

    # -------------------------------------------------------------
    # 5. Admin autorisé
    # -------------------------------------------------------------
    def test_05_admin_autorise(self):
        client = self.login_as(self.admin)
        resp = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertEqual(resp.status_code, 200)

    # -------------------------------------------------------------
    # 6. Professeur et parent refusés
    # -------------------------------------------------------------
    def test_06_professeur_et_parent_refuses(self):
        # Professeur
        client_prof = self.login_as(self.prof)
        resp_prof = client_prof.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertIn(resp_prof.status_code, [302, 403])

        # Parent
        client_parent = self.login_as(self.parent)
        resp_parent = client_parent.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertIn(resp_parent.status_code, [302, 403])

    # -------------------------------------------------------------
    # 7. Autre école refusée
    # -------------------------------------------------------------
    def test_07_autre_ecole_refusee(self):
        client_b = self.login_as(self.admin_b)
        resp = client_b.get(
            f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}",
            follow_redirects=False,
        )
        # Redirection vers /annees (302)
        self.assertEqual(resp.status_code, 302)

    # -------------------------------------------------------------
    # 8. Année cible archivée non modifiable
    # -------------------------------------------------------------
    def test_08_annee_cible_archivee_non_modifiable(self):
        client = self.login_as(self.admin)
        resp = client.get(
            f"/annees/{self.annee_active.id}/passage/{self.annee_archivee.id}",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        resp_annees = client.get("/annees")
        html_annees = resp_annees.get_data(as_text=True)
        self.assertNotIn(f"/passage/{self.annee_archivee.id}", html_annees)

    # -------------------------------------------------------------
    # 9. Bouton Retour aux passages fonctionne
    # -------------------------------------------------------------
    def test_09_bouton_retour_aux_passages_fonctionne(self):
        client = self.login_as(self.admin)
        resp = client.get(
            f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.eleve1.id}"
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        expected_url = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}"
        self.assertIn(expected_url, html)
        self.assertIn("Retour aux passages", html)

    # -------------------------------------------------------------
    # 10. Aucun changement de session annee_consultee par simple GET
    # -------------------------------------------------------------
    def test_10_aucun_changement_session_annee_consultee_par_simple_get(self):
        client = self.login_as(self.admin)

        with client.session_transaction() as sess:
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_active.id}

        # GET passage
        client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")

        # GET élève
        client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.eleve1.id}")

        with client.session_transaction() as sess:
            contexte = sess.get("annee_consultee", {}).get(str(self.ecole_a.id))
            self.assertEqual(contexte, self.annee_active.id)


class TestProtectionSourceArchivee(unittest.TestCase):
    """
    Vérifie la règle absolue :
    Une année source archivée est historique et immuable (ARCHIVE = LECTURE SEULE).
    """
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        niveaux = ensure_standard_niveaux(commit=False)
        db.session.flush()
        self.niv = {n.code: n for n in niveaux}

        # Écoles
        self.ecole_a = Ecole(nom="Ecole Alpha")
        self.ecole_b = Ecole(nom="Ecole Beta")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Année source archivée
        self.annee_src_archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        # Année source active
        self.annee_src_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        # Année cible planifiée
        self.annee_cible = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([self.annee_src_archivee, self.annee_src_active, self.annee_cible])
        db.session.flush()

        # Classes
        self.cls_src_arch = Classe(
            nom="6e 2024",
            niveau=self.niv["6E"].nom,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_src_archivee.id,
            niveau_id=self.niv["6E"].id,
        )
        self.cls_src_act = Classe(
            nom="6e 2025",
            niveau=self.niv["6E"].nom,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_src_active.id,
            niveau_id=self.niv["6E"].id,
        )
        self.cls_cible = Classe(
            nom="5e 2026",
            niveau=self.niv["5E"].nom,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niv["5E"].id,
        )
        db.session.add_all([self.cls_src_arch, self.cls_src_act, self.cls_cible])
        db.session.flush()

        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_src_archivee.id, [self.niv["6E"].id])
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_src_active.id, [self.niv["6E"].id])
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_cible.id, [self.niv["6E"].id, self.niv["5E"].id])

        # Élève dans l'année archivée
        self.eleve_arch = Eleve(
            nom="TRAORE",
            prenom="Oumar",
            date_naissance=date(2013, 3, 3),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_src_arch.id,
        )
        # Élève dans l'année active
        self.eleve_act = Eleve(
            nom="KONE",
            prenom="Fatou",
            date_naissance=date(2013, 4, 4),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_src_act.id,
        )
        db.session.add_all([self.eleve_arch, self.eleve_act])
        db.session.flush()

        self.insc_arch = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_arch.id,
            annee_scolaire_id=self.annee_src_archivee.id,
            classe_id=self.cls_src_arch.id,
            statut="inscrit",
        )
        db.session.add(self.insc_arch)
        db.session.flush()

        self.insc_act, _ = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve_act.id, self.annee_src_active.id, self.cls_src_act.id, sync_active=False
        )

        self.admin = Utilisateur(
            nom="Admin", email="admin@alpha.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id
        )
        self.admin_b = Utilisateur(
            nom="AdminB", email="admin@beta.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.admin, self.admin_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            if getattr(user, "ecole_id", None):
                sess["ecole_id"] = user.ecole_id
        return client

    # 1. source active -> formulaire individuel accessible
    def test_prot_01_source_active_formulaire_accessible(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_src_active.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_act.id}"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Confirmer la décision", html)
        self.assertIn("Passage", html)

    # 2. source archivee -> formulaire individuel inaccessible (redirection)
    def test_prot_02_source_archivee_formulaire_inaccessible(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_arch.id}"
        resp = client.get(url, follow_redirects=False)
        # Doit être une redirection sûre vers la page de passage
        self.assertEqual(resp.status_code, 302)
        expected_dest = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}"
        self.assertIn(expected_dest, resp.location)

        # Avec follow_redirects : message d'avertissement lecture seule
        resp_follow = client.get(url, follow_redirects=True)
        html = resp_follow.get_data(as_text=True)
        self.assertIn("Cette année scolaire est archivée", html)
        self.assertIn("lecture seule", html)

    # 3. source archivee -> aucun bouton Traiter sur la page passage_annee
    def test_prot_03_source_archivee_aucun_bouton_traiter(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}"
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # Doit afficher l'état lecture seule et aucun bouton Traiter
        self.assertIn("Mode lecture seule", html)
        self.assertIn("Lecture seule", html)
        self.assertNotIn(">Traiter<", html)
        self.assertNotIn(f"/eleves/{self.eleve_arch.id}", html)

    # 4. POST forgé sur source archivee -> aucune mutation
    def test_prot_04_post_forge_source_archivee_aucune_mutation(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_arch.id}"
        post_data = {
            "decision": "passage",
            "classe_cible_id": self.cls_cible.id,
        }
        resp = client.post(url, data=post_data, follow_redirects=False)
        # Rejet immédiat avec redirection 302
        self.assertEqual(resp.status_code, 302)

    # 5. inscription source inchangée après tentative POST
    def test_prot_05_inscription_source_inchangee(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_arch.id}"
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_cible.id})

        db.session.expire_all()
        insc = Inscription.query.get(self.insc_arch.id)
        self.assertEqual(insc.statut, "inscrit")
        self.assertIsNone(insc.decision_fin_annee)

    # 6. inscription cible non créée après tentative POST
    def test_prot_06_inscription_cible_non_creee(self):
        client = self.login_as(self.admin)
        url = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_arch.id}"
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_cible.id})

        db.session.expire_all()
        insc_cible = Inscription.query.filter_by(
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=self.eleve_arch.id,
        ).first()
        self.assertIsNone(insc_cible)

    # 7. autre école toujours refusée
    def test_prot_07_autre_ecole_refusee(self):
        client_b = self.login_as(self.admin_b)
        url = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_arch.id}"
        resp = client_b.get(url, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    # 8. session annee_consultee non modifiée
    def test_prot_08_session_annee_consultee_non_modifiee(self):
        client = self.login_as(self.admin)
        with client.session_transaction() as sess:
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_src_active.id}

        url = f"/annees/{self.annee_src_archivee.id}/passage/{self.annee_cible.id}/eleves/{self.eleve_arch.id}"
        client.get(url)
        client.post(url, data={"decision": "passage", "classe_cible_id": self.cls_cible.id})

        with client.session_transaction() as sess:
            contexte = sess.get("annee_consultee", {}).get(str(self.ecole_a.id))
            self.assertEqual(contexte, self.annee_src_active.id)

