"""
tests/test_phase2d4_passage_masse.py
=============================================================
KLASORA — Phase 2D-4
Tests du passage d'année en masse avec aperçu avant confirmation.

35 scénarios de test couvrant :
  1-6.   Permissions et sécurité (login, rôles, admin, super_admin)
  7-8.   Isolation multi-école (étanchéité absolue)
  9-11.  Années archivées (source et cible)
  12-17. Validation des entrées (décision, sélection vide, classe cible)
  18-25. Écran d'aperçu (audit sans mutation, statuts, conflits)
  26-30. Exécution en masse (passage, redoublement, sortie, transfert, diplôme)
  31.    Atomicité par élève via savepoint (succès partiels dans lot mixte)
  32.    Idempotence (répétition sans duplication)
  33-34. Cible planifiée vs active (synchronisation Eleve.classe_id)
  35.    Session annee_consultee inchangée
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
from app.services.inscriptions_annuelles import creer_inscription_annuelle, get_inscription
from app.services.niveaux import ensure_standard_niveaux
from app.services.niveaux_annuels import sauvegarder_selection_annuelle
from app.services.passage_annee import preparer_passage_masse, executer_passage_masse
from app.services.annees_scolaires import get_annee_consultee, set_annee_consultee


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class TestPassageAnneeMasse(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        niveaux = ensure_standard_niveaux(commit=False)
        db.session.flush()
        self.niv = {n.code: n for n in niveaux}

        # --- Établissements
        self.ecole_a = Ecole(nom="Collège Alpha")
        self.ecole_b = Ecole(nom="Collège Beta")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # --- Utilisateurs
        self.admin_a = Utilisateur(
            nom="Admin Alpha",
            email="admin.alpha@test.com",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_a.id,
        )
        self.super_admin = Utilisateur(
            nom="Super Admin",
            email="super@test.com",
            mot_de_passe="pass",
            role="super_admin",
            ecole_id=None,
        )
        self.prof_a = Utilisateur(
            nom="Prof Alpha",
            email="prof.alpha@test.com",
            mot_de_passe="pass",
            role="professeur",
            ecole_id=self.ecole_a.id,
        )
        self.parent_a = Utilisateur(
            nom="Parent Alpha",
            email="parent.alpha@test.com",
            mot_de_passe="pass",
            role="parent",
            ecole_id=self.ecole_a.id,
        )

        db.session.add_all([self.admin_a, self.super_admin, self.prof_a, self.parent_a])
        db.session.flush()

        # --- Années scolaires Ecole A
        self.annee_source = AnneeScolaire(
            ecole_id=self.ecole_a.id,
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="active",
        )
        self.annee_cible = AnneeScolaire(
            ecole_id=self.ecole_a.id,
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="planifiee",
        )
        self.annee_archivee = AnneeScolaire(
            ecole_id=self.ecole_a.id,
            nom="2023-2024 (Archivée)",
            date_debut=date(2023, 9, 1),
            date_fin=date(2024, 6, 30),
            statut="archivee",
        )
        db.session.add_all([self.annee_source, self.annee_cible, self.annee_archivee])
        db.session.flush()

        # Config niveaux actifs
        tous_ids = [n.id for n in self.niv.values()]
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_source.id, tous_ids)
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_cible.id, tous_ids)
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_archivee.id, tous_ids)

        # Classes dans source
        self.classe_6e_src = Classe(
            nom="6e A",
            niveau_id=self.niv["6E"].id,
            annee_scolaire_id=self.annee_source.id,
            ecole_id=self.ecole_a.id,
            statut="ouverte",
        )
        self.classe_term_src = Classe(
            nom="Terminale A",
            niveau_id=self.niv["TERMINALE"].id,
            annee_scolaire_id=self.annee_source.id,
            ecole_id=self.ecole_a.id,
            statut="ouverte",
        )
        db.session.add_all([self.classe_6e_src, self.classe_term_src])
        db.session.flush()

        # Classes dans cible
        self.classe_5e_cbl = Classe(
            nom="5e A",
            niveau_id=self.niv["5E"].id,
            annee_scolaire_id=self.annee_cible.id,
            ecole_id=self.ecole_a.id,
            statut="ouverte",
        )
        self.classe_6e_cbl = Classe(
            nom="6e Cible",
            niveau_id=self.niv["6E"].id,
            annee_scolaire_id=self.annee_cible.id,
            ecole_id=self.ecole_a.id,
            statut="ouverte",
        )
        self.classe_4e_cbl = Classe(
            nom="4e A",
            niveau_id=self.niv["4E"].id,
            annee_scolaire_id=self.annee_cible.id,
            ecole_id=self.ecole_a.id,
            statut="ouverte",
        )
        self.classe_fermee_cbl = Classe(
            nom="5e Fermée",
            niveau_id=self.niv["5E"].id,
            annee_scolaire_id=self.annee_cible.id,
            ecole_id=self.ecole_a.id,
            statut="fermee",
        )
        db.session.add_all([self.classe_5e_cbl, self.classe_6e_cbl, self.classe_4e_cbl, self.classe_fermee_cbl])
        db.session.flush()

        # --- Élèves Ecole A
        self.eleve_1 = Eleve(
            nom="Diop",
            prenom="Awa",
            ecole_id=self.ecole_a.id,
            date_naissance=date(2013, 1, 15),
            classe_id=self.classe_6e_src.id,
        )
        self.eleve_2 = Eleve(
            nom="Sow",
            prenom="Mamadou",
            ecole_id=self.ecole_a.id,
            date_naissance=date(2013, 3, 20),
            classe_id=self.classe_6e_src.id,
        )
        self.eleve_3 = Eleve(
            nom="Ndiaye",
            prenom="Fatou",
            ecole_id=self.ecole_a.id,
            date_naissance=date(2013, 5, 10),
            classe_id=self.classe_6e_src.id,
        )
        self.eleve_term = Eleve(
            nom="Ba",
            prenom="Oumar",
            ecole_id=self.ecole_a.id,
            date_naissance=date(2006, 7, 8),
            classe_id=self.classe_term_src.id,
        )
        db.session.add_all([self.eleve_1, self.eleve_2, self.eleve_3, self.eleve_term])
        db.session.flush()

        # Inscriptions dans l'année source
        creer_inscription_annuelle(self.ecole_a.id, self.eleve_1.id, self.annee_source.id, self.classe_6e_src.id, "inscrit")
        creer_inscription_annuelle(self.ecole_a.id, self.eleve_2.id, self.annee_source.id, self.classe_6e_src.id, "inscrit")
        creer_inscription_annuelle(self.ecole_a.id, self.eleve_3.id, self.annee_source.id, self.classe_6e_src.id, "inscrit")
        creer_inscription_annuelle(self.ecole_a.id, self.eleve_term.id, self.annee_source.id, self.classe_term_src.id, "inscrit")

        # Élève et classe Ecole B (pour tests d'isolation)
        self.annee_b = AnneeScolaire(
            ecole_id=self.ecole_b.id,
            nom="2024-2025 B",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="active",
        )
        db.session.add(self.annee_b)
        db.session.flush()
        sauvegarder_selection_annuelle(self.ecole_b.id, self.annee_b.id, tous_ids)
        self.classe_b = Classe(
            nom="6e Beta",
            niveau_id=self.niv["6E"].id,
            annee_scolaire_id=self.annee_b.id,
            ecole_id=self.ecole_b.id,
            statut="ouverte",
        )
        db.session.add(self.classe_b)
        db.session.flush()
        self.eleve_b = Eleve(
            nom="Kone",
            prenom="Ali",
            ecole_id=self.ecole_b.id,
            date_naissance=date(2013, 8, 12),
            classe_id=self.classe_b.id,
        )
        db.session.add(self.eleve_b)
        db.session.flush()
        creer_inscription_annuelle(self.ecole_b.id, self.eleve_b.id, self.annee_b.id, self.classe_b.id, "inscrit")

        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            sess['role'] = user.role
            if user.ecole_id:
                sess['ecole_id'] = user.ecole_id

    # -----------------------------------------------------------------------
    # 1-6. Permissions et rôles
    # -----------------------------------------------------------------------

    def test_01_acces_apercu_non_authentifie(self):
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={"decision": "passage", "eleve_ids": [self.eleve_1.id]})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.location.lower())

    def test_02_acces_confirmer_non_authentifie(self):
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/confirmer"
        resp = self.client.post(url, data={"decision": "passage", "eleve_ids": [self.eleve_1.id]})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.location.lower())

    def test_03_acces_professeur_refuse(self):
        self._login(self.prof_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={"decision": "passage", "eleve_ids": [self.eleve_1.id]})
        self.assertIn(resp.status_code, [302, 403])

    def test_04_acces_parent_refuse(self):
        self._login(self.parent_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={"decision": "passage", "eleve_ids": [self.eleve_1.id]})
        self.assertIn(resp.status_code, [302, 403])

    def test_05_acces_admin_autorise(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Aper\xc3\xa7u avant confirmation", resp.data)

    def test_06_acces_super_admin_autorise(self):
        self._login(self.super_admin)
        with self.client.session_transaction() as sess:
            sess['ecole_id'] = self.ecole_a.id
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        })
        self.assertEqual(resp.status_code, 200)

    # -----------------------------------------------------------------------
    # 7-8. Isolation multi-école
    # -----------------------------------------------------------------------

    def test_07_isolation_multi_ecole_apercu(self):
        self._login(self.admin_a)
        # Tenter d'inclure eleve_b (Ecole B) dans l'aperçu d'Ecole A
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_b.id],
        })
        self.assertEqual(resp.status_code, 200)
        # eleve_b n'appartient pas à ecole_a -> non éligible / introuvable
        self.assertIn(b"Introuvable", resp.data)

    def test_08_isolation_multi_ecole_confirmer(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/confirmer"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_b.id],
        })
        self.assertEqual(resp.status_code, 200)
        # Aucune inscription cible créée pour eleve_b
        insc = get_inscription(self.eleve_b, self.annee_cible)
        self.assertIsNone(insc)

    # -----------------------------------------------------------------------
    # 9-11. Années archivées
    # -----------------------------------------------------------------------

    def test_09_source_archivee_refus_apercu(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_archivee.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("archivée".encode("utf-8"), resp.data)

    def test_10_source_archivee_refus_confirmer(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_archivee.id}/passage/{self.annee_cible.id}/masse/confirmer"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("archivée".encode("utf-8"), resp.data)
        # Vérifier aucune inscription
        insc = get_inscription(self.eleve_1, self.annee_cible)
        self.assertIsNone(insc)

    def test_11_cible_archivee_refusee(self):
        self._login(self.admin_a)
        annee_cible_archivee = AnneeScolaire(
            ecole_id=self.ecole_a.id,
            nom="2027-2028 (Archivée)",
            date_debut=date(2027, 9, 1),
            date_fin=date(2028, 6, 30),
            statut="archivee",
        )
        db.session.add(annee_cible_archivee)
        db.session.commit()

        url = f"/annees/{self.annee_source.id}/passage/{annee_cible_archivee.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Impossible d&#39;inscrire dans une année cible archivée".encode("utf-8"), resp.data)

    # -----------------------------------------------------------------------
    # 12-17. Validation des entrées
    # -----------------------------------------------------------------------

    def test_12_decision_invalide_rejetee(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "inconnue_xyz",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Décision inconnue".encode("utf-8"), resp.data)

    def test_13_selection_vide_rejetee(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("au moins un élève".encode("utf-8"), resp.data)

    def test_14_classe_cible_manquante_pour_passage(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("classe cible explicite".encode("utf-8"), resp.data)

    def test_15_classe_cible_fermee_refusee(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_fermee_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("classe cible est fermée".encode("utf-8"), resp.data)

    def test_16_classe_cible_autre_annee_refusee(self):
        self._login(self.admin_a)
        # Passer classe_6e_src (qui appartient à source, pas à cible)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_6e_src.id,
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("n&#39;appartient pas à l&#39;année cible".encode("utf-8"), resp.data)

    def test_17_classe_cible_autre_ecole_refusee(self):
        self._login(self.admin_a)
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_b.id,
            "eleve_ids": [self.eleve_1.id],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Classe cible introuvable".encode("utf-8"), resp.data)

    # -----------------------------------------------------------------------
    # 18-25. Aperçu et audit sans mutation
    # -----------------------------------------------------------------------

    def test_18_apercu_ne_mute_rien_en_base(self):
        self._login(self.admin_a)
        nb_inscriptions_avant = Inscription.query.count()
        url = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        resp = self.client.post(url, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id, self.eleve_2.id],
        })
        self.assertEqual(resp.status_code, 200)
        nb_inscriptions_apres = Inscription.query.count()
        self.assertEqual(nb_inscriptions_avant, nb_inscriptions_apres)

    def test_19_apercu_detecte_eleves_prets(self):
        prep = preparer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id, self.eleve_2.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )
        self.assertTrue(prep["ok"])
        self.assertEqual(prep["nb_valides"], 2)
        self.assertEqual(prep["nb_conflits"], 0)
        self.assertEqual(prep["nb_deja_traites"], 0)

    def test_20_apercu_detecte_eleves_deja_traites(self):
        # Inscrire eleve_1 en cible dans 5e A
        creer_inscription_annuelle(self.ecole_a.id, self.eleve_1.id, self.annee_cible.id, self.classe_5e_cbl.id, "inscrit")
        db.session.commit()

        prep = preparer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id, self.eleve_2.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )
        self.assertTrue(prep["ok"])
        self.assertEqual(prep["nb_valides"], 1)
        self.assertEqual(prep["nb_deja_traites"], 1)

    def test_21_apercu_detecte_conflit_classe_differente(self):
        # Inscrire eleve_1 en cible dans 6e Cible (autre classe)
        creer_inscription_annuelle(self.ecole_a.id, self.eleve_1.id, self.annee_cible.id, self.classe_6e_cbl.id, "inscrit")
        db.session.commit()

        prep = preparer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )
        self.assertTrue(prep["ok"])
        self.assertEqual(prep["nb_conflits"], 1)
        self.assertEqual(prep["items"][0]["statut"], "conflit")
        self.assertIn("Déjà inscrit dans une autre classe", prep["items"][0]["motif"])

    def test_22_apercu_detecte_conflit_niveau_incompatible(self):
        # eleve_1 est en 6e -> niveau suivant attendu = 5e.
        # Mais on soumet vers classe_4e_cbl (4e) !
        prep = preparer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="passage",
            classe_cible_id=self.classe_4e_cbl.id,
        )
        self.assertTrue(prep["ok"])
        self.assertEqual(prep["nb_conflits"], 1)
        self.assertIn("ne correspond pas au niveau attendu", prep["items"][0]["motif"])

    def test_23_apercu_diplome_valide_sur_terminale(self):
        prep = preparer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_term.id],
            decision="diplome",
        )
        self.assertTrue(prep["ok"])
        self.assertEqual(prep["nb_valides"], 1)
        self.assertEqual(prep["nb_conflits"], 0)

    def test_24_apercu_diplome_refuse_sur_6eme(self):
        prep = preparer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="diplome",
        )
        self.assertTrue(prep["ok"])
        self.assertEqual(prep["nb_conflits"], 1)
        self.assertIn("réservée au cycle terminal", prep["items"][0]["motif"])

    def test_25_apercu_transfert_et_sortie_sans_classe_cible(self):
        prep = preparer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="sortie",
            motif_sortie="Déménagement",
        )
        self.assertTrue(prep["ok"])
        self.assertEqual(prep["nb_valides"], 1)
        self.assertIsNone(prep["classe_cible"])

    # -----------------------------------------------------------------------
    # 26-30. Exécution en masse
    # -----------------------------------------------------------------------

    def test_26_execution_masse_succes_lot_homogene_passage(self):
        rapport, err = executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id, self.eleve_2.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )
        self.assertIsNone(err)
        self.assertEqual(rapport["nb_reussis"], 2)
        self.assertEqual(rapport["nb_echecs"], 0)

        # Vérifier en base
        insc_1 = get_inscription(self.eleve_1, self.annee_cible)
        insc_2 = get_inscription(self.eleve_2, self.annee_cible)
        self.assertIsNotNone(insc_1)
        self.assertIsNotNone(insc_2)
        self.assertEqual(insc_1.classe_id, self.classe_5e_cbl.id)
        self.assertEqual(insc_2.classe_id, self.classe_5e_cbl.id)

        # Vérifier clôture source
        src_1 = get_inscription(self.eleve_1, self.annee_source)
        self.assertEqual(src_1.decision_fin_annee, "passage")
        self.assertEqual(src_1.statut, "termine")

    def test_27_execution_masse_succes_lot_homogene_redoublement(self):
        rapport, err = executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="redoublement",
            classe_cible_id=self.classe_6e_cbl.id,
        )
        self.assertIsNone(err)
        self.assertEqual(rapport["nb_reussis"], 1)

        insc_1 = get_inscription(self.eleve_1, self.annee_cible)
        self.assertIsNotNone(insc_1)
        self.assertEqual(insc_1.classe_id, self.classe_6e_cbl.id)

    def test_28_execution_masse_succes_lot_sortie(self):
        rapport, err = executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id, self.eleve_2.id],
            decision="sortie",
            motif_sortie="Fin de cycle",
        )
        self.assertIsNone(err)
        self.assertEqual(rapport["nb_reussis"], 2)

        # Pas d'inscription cible
        self.assertIsNone(get_inscription(self.eleve_1, self.annee_cible))
        self.assertIsNone(get_inscription(self.eleve_2, self.annee_cible))

        # Statut source = sorti
        src_1 = get_inscription(self.eleve_1, self.annee_source)
        self.assertEqual(src_1.statut, "sorti")
        self.assertEqual(src_1.decision_fin_annee, "sortie")

    def test_29_execution_masse_succes_lot_transfert(self):
        rapport, err = executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_3.id],
            decision="transfert",
            motif_sortie="Changement de ville",
        )
        self.assertIsNone(err)
        self.assertEqual(rapport["nb_reussis"], 1)

        src_3 = get_inscription(self.eleve_3, self.annee_source)
        self.assertEqual(src_3.statut, "transfere")
        self.assertEqual(src_3.decision_fin_annee, "transfert")

    def test_30_execution_masse_succes_lot_diplome_terminale(self):
        rapport, err = executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_term.id],
            decision="diplome",
        )
        self.assertIsNone(err)
        self.assertEqual(rapport["nb_reussis"], 1)

        src_term = get_inscription(self.eleve_term, self.annee_source)
        self.assertEqual(src_term.statut, "diplome")
        self.assertEqual(src_term.decision_fin_annee, "diplome")

    # -----------------------------------------------------------------------
    # 31. Atomicité par savepoint et isolation des échecs
    # -----------------------------------------------------------------------

    def test_31_atomicite_lot_mixte_succes_et_echecs_isoles(self):
        # eleve_2 est déjà inscrit en cible dans une autre classe (conflit)
        creer_inscription_annuelle(self.ecole_a.id, self.eleve_2.id, self.annee_cible.id, self.classe_6e_cbl.id, "inscrit")
        db.session.commit()

        # Lot mixte : eleve_1 (valide), eleve_2 (conflit), eleve_3 (valide)
        rapport, err = executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id, self.eleve_2.id, self.eleve_3.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )
        self.assertIsNone(err)
        self.assertEqual(rapport["nb_reussis"], 2)
        self.assertEqual(rapport["nb_conflits"], 1)

        # eleve_1 et eleve_3 ont réussi et sont bien enregistrés
        self.assertIsNotNone(get_inscription(self.eleve_1, self.annee_cible))
        self.assertIsNotNone(get_inscription(self.eleve_3, self.annee_cible))

        # eleve_2 est resté dans son inscription initiale (classe_6e_cbl)
        insc_2 = get_inscription(self.eleve_2, self.annee_cible)
        self.assertEqual(insc_2.classe_id, self.classe_6e_cbl.id)

    # -----------------------------------------------------------------------
    # 32. Idempotence
    # -----------------------------------------------------------------------

    def test_32_idempotence_rejouer_lot_marque_deja_traite(self):
        # 1er passage
        executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )

        nb_inscriptions_avant = Inscription.query.count()

        # 2e passage identique
        rapport, err = executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )
        self.assertIsNone(err)
        self.assertEqual(rapport["nb_reussis"], 0)
        self.assertEqual(rapport["nb_deja_traites"], 1)

        nb_inscriptions_apres = Inscription.query.count()
        self.assertEqual(nb_inscriptions_avant, nb_inscriptions_apres)

    # -----------------------------------------------------------------------
    # 33-34. Respect du statut cible (planifiée vs active)
    # -----------------------------------------------------------------------

    def test_33_cible_planifiee_ne_modifie_pas_classe_id_courante(self):
        # annee_cible a statut == 'planifiee'
        classe_id_initial = self.eleve_1.classe_id

        executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )

        db.session.refresh(self.eleve_1)
        self.assertEqual(self.eleve_1.classe_id, classe_id_initial)

    def test_34_cible_active_synchronise_classe_id_courante(self):
        # Rendre la cible active
        self.annee_cible.statut = "active"
        db.session.commit()

        executer_passage_masse(
            ecole_id=self.ecole_a.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            eleve_ids=[self.eleve_1.id],
            decision="passage",
            classe_cible_id=self.classe_5e_cbl.id,
        )

        db.session.refresh(self.eleve_1)
        self.assertEqual(self.eleve_1.classe_id, self.classe_5e_cbl.id)

    # -----------------------------------------------------------------------
    # 35. Session annee_consultee inchangée
    # -----------------------------------------------------------------------

    def test_35_session_annee_consultee_reste_inchangee(self):
        self._login(self.admin_a)
        with self.client.session_transaction() as sess:
            sess['annee_scolaire_id'] = self.annee_source.id

        url_apercu = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/apercu"
        self.client.post(url_apercu, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        })

        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('annee_scolaire_id'), self.annee_source.id)

        url_confirmer = f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/masse/confirmer"
        self.client.post(url_confirmer, data={
            "decision": "passage",
            "classe_cible_id": self.classe_5e_cbl.id,
            "eleve_ids": [self.eleve_1.id],
        })

        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('annee_scolaire_id'), self.annee_source.id)


if __name__ == "__main__":
    unittest.main()

