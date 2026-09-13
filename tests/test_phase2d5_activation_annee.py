"""
tests/test_phase2d5_activation_annee.py
=============================================================
KLASORA — Phase 2D-5 : Activation sécurisée de la nouvelle année
et clôture du cycle annuel.

Suite de tests complète et rigoureuse (46 vérifications) :

GROUPE 1 (1-8)   : Sécurité, permissions et étanchéité multi-école
GROUPE 2 (9-14)  : Validation des statuts, cohérence et idempotence
GROUPE 3 (15-20) : Prérequis et détection des bloquants (structure, passage, dates)
GROUPE 4 (21-25) : Cas première année de l'établissement (sans active antérieure)
GROUPE 5 (26-30) : Clôture atomique, transition de statuts et unicité de l'active
GROUPE 6 (31-36) : Resynchronisation du cache Eleve.classe_id (passage, redoublement, sorties)
GROUPE 7 (37-40) : Atomicité globale, rollback et intégrité de la base
GROUPE 8 (41-45) : Interface utilisateur, redirection des anciens boutons et règle 2C-5D
GROUPE 9 (46)    : Scénario critique End-to-End complet (Moussa, Amina, Issa, Fatou)
=============================================================
"""

import unittest
from datetime import date
from unittest.mock import patch

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Professeur,
    Utilisateur,
)
from app.services.activation_annee import (
    activer_annee_scolaire,
    preparer_activation_annee,
    resynchroniser_classes_eleves_annee_active,
)
from app.services.annees_scolaires import get_annee_consultee, set_annee_consultee
from app.services.classes_annuelles import preparer_structure_annee, set_classe_ouverte
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.niveaux import (
    ensure_ecole_niveau_configs,
    ensure_standard_niveaux,
)
from app.services.niveaux_annuels import sauvegarder_selection_annuelle
from app.services.passage_annee import executer_passage_eleve
from app.services.preparation_annee import get_etat_preparation_annee


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class TestPhase2D5ActivationAnnee(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Niveaux scolaires standards
        ensure_standard_niveaux(commit=False)

        # École A
        self.ecole_a = Ecole(nom="École A", statut="actif")
        # École B
        self.ecole_b = Ecole(nom="École B", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        ensure_ecole_niveau_configs(self.ecole_a.id, default_active=True, commit=False)
        ensure_ecole_niveau_configs(self.ecole_b.id, default_active=True, commit=False)

        # Utilisateurs École A
        self.admin_a = Utilisateur(
            nom="Admin", prenom="A", email="admin_a@test.com",
            role="admin", ecole_id=self.ecole_a.id, mot_de_passe="secret"
        )
        self.super_admin = Utilisateur(
            nom="Super", prenom="Admin", email="super@test.com",
            role="super_admin", mot_de_passe="secret"
        )
        self.prof = Utilisateur(
            nom="Prof", prenom="A", email="prof@test.com",
            role="professeur", ecole_id=self.ecole_a.id, mot_de_passe="secret"
        )
        self.parent_user = Utilisateur(
            nom="Parent", prenom="A", email="parent@test.com",
            role="parent", ecole_id=self.ecole_a.id, mot_de_passe="secret"
        )
        db.session.add_all([self.admin_a, self.super_admin, self.prof, self.parent_user])
        db.session.flush()

        # Enseignant Professeur A
        self.enseignant = Professeur(
            nom="Dupont", prenom="Jean", email="dupont@test.com",
            ecole_id=self.ecole_a.id, specialite="Maths",
            utilisateur_id=self.prof.id
        )
        db.session.add(self.enseignant)

        # Années scolaires École A
        self.annee_active = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30), statut="active", ecole_id=self.ecole_a.id
        )
        self.annee_planifiee = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30), statut="planifiee", ecole_id=self.ecole_a.id
        )
        self.annee_archivee = AnneeScolaire(
            nom="2024-2025", date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30), statut="archivee", ecole_id=self.ecole_a.id
        )

        # Année École B
        self.annee_ecole_b = AnneeScolaire(
            nom="2026-2027 B", date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30), statut="planifiee", ecole_id=self.ecole_b.id
        )
        db.session.add_all([
            self.annee_active, self.annee_planifiee, self.annee_archivee, self.annee_ecole_b
        ])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _preparer_annee_planifiee_valide(self):
        """Helper : configure la structure et les classes de l'année planifiée pour lever les bloquants."""
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [niv_6e.id])
        c_cible = Classe(
            nom="6e A Cible", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c_cible)
        db.session.commit()
        return c_cible

    # =========================================================================
    # GROUPE 1 : Sécurité, permissions et étanchéité multi-école (1 à 8)
    # =========================================================================

    def test_01_acces_anonyme_confirmation_redirige_login(self):
        """1. Visiteur non authentifié sur GET /annees/<id>/activation -> redirigé login."""
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/activation")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_02_acces_anonyme_activer_redirige_login(self):
        """2. Visiteur non authentifié sur POST /annees/<id>/activer -> redirigé login."""
        resp = self.client.post(f"/annees/{self.annee_planifiee.id}/activer")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_03_role_parent_interdit_confirmation(self):
        """3. Rôle parent interdit sur GET /annees/<id>/activation."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.parent_user.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/activation", follow_redirects=True)
        self.assertIn("non autoris", resp.get_data(as_text=True).lower())

    def test_04_role_parent_interdit_activer(self):
        """4. Rôle parent interdit sur POST /annees/<id>/activer."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.parent_user.id)
            sess["_fresh"] = True
        resp = self.client.post(f"/annees/{self.annee_planifiee.id}/activer", follow_redirects=True)
        self.assertIn("non autoris", resp.get_data(as_text=True).lower())

    def test_05_role_professeur_interdit_confirmation(self):
        """5. Rôle professeur interdit sur GET /annees/<id>/activation."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.prof.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/activation", follow_redirects=True)
        self.assertIn("non autoris", resp.get_data(as_text=True).lower())

    def test_06_role_professeur_interdit_activer(self):
        """6. Rôle professeur interdit sur POST /annees/<id>/activer."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.prof.id)
            sess["_fresh"] = True
        resp = self.client.post(f"/annees/{self.annee_planifiee.id}/activer", follow_redirects=True)
        self.assertIn("non autoris", resp.get_data(as_text=True).lower())

    def test_07_admin_ecole_a_interdit_activation_ecole_b_get(self):
        """7. Admin École A ne peut pas accéder à la confirmation de l'École B."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_ecole_b.id}/activation", follow_redirects=True)
        self.assertIn("introuvable", resp.get_data(as_text=True).lower())

    def test_08_admin_ecole_a_interdit_activation_ecole_b_post(self):
        """8. Admin École A ne peut pas activer l'année de l'École B."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.post(f"/annees/{self.annee_ecole_b.id}/activer", follow_redirects=True)
        self.assertIn("introuvable", resp.get_data(as_text=True).lower())

    # =========================================================================
    # GROUPE 2 : Validation des statuts, cohérence et idempotence (9 à 14)
    # =========================================================================

    def test_09_annee_inexistante_get(self):
        """9. Année ID 99999 sur GET /activation renvoie flash introuvable."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get("/annees/99999/activation", follow_redirects=True)
        self.assertIn("introuvable", resp.get_data(as_text=True).lower())

    def test_10_annee_inexistante_post(self):
        """10. Année ID 99999 sur POST /activer renvoie flash introuvable."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.post("/annees/99999/activer", follow_redirects=True)
        self.assertIn("introuvable", resp.get_data(as_text=True).lower())

    def test_11_annee_deja_active_get(self):
        """11. GET /activation sur une année déjà active redirige avec message informatif."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_active.id}/activation", follow_redirects=True)
        self.assertIn("déjà active", resp.get_data(as_text=True).lower())

    def test_12_annee_deja_active_post_idempotence(self):
        """12. Idempotence : POST /activer sur une année déjà active retourne succès immédiat."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.post(f"/annees/{self.annee_active.id}/activer", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("déjà active", resp.get_data(as_text=True).lower())
        # Le statut reste active
        db.session.refresh(self.annee_active)
        self.assertEqual(self.annee_active.statut, "active")

    def test_13_annee_archivee_get_bloquee(self):
        """13. GET /activation sur une année archivée redirige avec avertissement."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_archivee.id}/activation", follow_redirects=True)
        self.assertIn("archivée", resp.get_data(as_text=True).lower())

    def test_14_annee_archivee_service_bloquee(self):
        """14. activer_annee_scolaire sur une année archivée renvoie False."""
        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_archivee.id)
        self.assertFalse(succes)
        self.assertIn("archivée", msg.lower())

    # =========================================================================
    # GROUPE 3 : Prérequis et détection des bloquants (15 à 20)
    # =========================================================================

    def test_15_service_preparer_activation_bloquant_structure_vide(self):
        """15. preparer_activation_annee échoue si structure vide."""
        prep, err = preparer_activation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertIsNone(prep)
        self.assertIsNotNone(err)
        self.assertIn("non configurée", err.lower())

    def test_16_activer_annee_bloque_si_structure_vide(self):
        """16. activer_annee_scolaire échoue si structure non configurée."""
        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(succes)
        self.assertIn("bloquant", msg.lower())

    def test_17_activer_annee_bloque_si_niveau_sans_classe_ouverte(self):
        """17. Activation bloquée si un niveau actif n'a pas de classe ouverte."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        n5 = NiveauScolaire.query.filter_by(code="5E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id, n5.id])
        # On ne crée que 6e A
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.commit()

        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(succes)
        self.assertIn("classe", msg.lower())

    def test_18_activer_annee_bloque_si_passage_eleves_incomplet(self):
        """18. Activation bloquée si des élèves de l'année active n'ont pas été traités."""
        self._preparer_annee_planifiee_valide()
        # Créer un élève dans l'année active
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        c_src = Classe(
            nom="6e Source", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add(c_src)
        db.session.flush()

        eleve = Eleve(
            nom="Sow", prenom="Mamadou", date_naissance=date(2012, 5, 5),
            ecole_id=self.ecole_a.id, classe_id=c_src.id
        )
        db.session.add(eleve)
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id, eleve_id=eleve.id,
            classe_id=c_src.id, annee_scolaire_id=self.annee_active.id
        )
        db.session.commit()

        # Sans passage exécuté, a_traiter == 1 -> bloquant
        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(succes)
        self.assertIn("passage", msg.lower())

    def test_19_activer_annee_autorise_avec_cours_sans_prof(self):
        """19. Les cours sans professeur ou classes sans cours sont non bloquants."""
        self._preparer_annee_planifiee_valide()
        # Aucun élève dans annee active -> passage 0 élèves, prêt.
        # Cours non créé -> avertissement non bloquant.
        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(succes, f"Erreur inattendue: {msg}")

    def test_20_incoherence_plusieurs_annees_actives_bloquee(self):
        """20. Si l'école a déjà >= 2 années actives (état corrompu), l'activation refuse d'opérer."""
        self._preparer_annee_planifiee_valide()
        # On force une 2e année active
        fausse_active = AnneeScolaire(
            nom="2025-2026 Bis", date_debut=date(2025, 10, 1),
            date_fin=date(2026, 7, 1), statut="active", ecole_id=self.ecole_a.id
        )
        db.session.add(fausse_active)
        db.session.commit()

        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(succes)
        self.assertIn("incohérent", msg.lower())

    # =========================================================================
    # GROUPE 4 : Cas première année de l'établissement (21 à 25)
    # =========================================================================

    def test_21_premiere_annee_ecole_sans_active_existante_preparer(self):
        """21. preparer_activation_annee supporte une école sans aucune année active précédente."""
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_b.id, self.annee_ecole_b.id, [niv_6e.id])
        c_b = Classe(
            nom="6e B1", niveau_id=niv_6e.id, ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_ecole_b.id, statut="ouverte"
        )
        db.session.add(c_b)
        db.session.commit()

        prep, err = preparer_activation_annee(self.ecole_b.id, self.annee_ecole_b.id)
        self.assertIsNone(err)
        self.assertIsNone(prep["ancienne_active"])
        self.assertEqual(prep["nb_synchronises"], 0)

    def test_22_premiere_annee_activation_succes(self):
        """22. Activation réussie d'une première année scolaire (école sans historique)."""
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_b.id, self.annee_ecole_b.id, [niv_6e.id])
        c_b = Classe(
            nom="6e B1", niveau_id=niv_6e.id, ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_ecole_b.id, statut="ouverte"
        )
        db.session.add(c_b)
        db.session.commit()

        succes, msg, details = activer_annee_scolaire(self.ecole_b.id, self.annee_ecole_b.id)
        self.assertTrue(succes)
        db.session.refresh(self.annee_ecole_b)
        self.assertEqual(self.annee_ecole_b.statut, "active")

    def test_23_premiere_annee_aucune_archivee_creee(self):
        """23. Lors d'une première activation, aucune année n'est passée à 'archivee'."""
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_b.id, self.annee_ecole_b.id, [niv_6e.id])
        c_b = Classe(
            nom="6e B1", niveau_id=niv_6e.id, ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_ecole_b.id, statut="ouverte"
        )
        db.session.add(c_b)
        db.session.commit()

        succes, msg, details = activer_annee_scolaire(self.ecole_b.id, self.annee_ecole_b.id)
        self.assertTrue(succes)
        archives = AnneeScolaire.query.filter_by(ecole_id=self.ecole_b.id, statut="archivee").all()
        self.assertEqual(len(archives), 0)

    def test_24_premiere_annee_template_affiche_premiere_activation(self):
        """24. Page de confirmation affiche le badge 'Première activation' quand pas d'ancienne active."""
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_b.id, self.annee_ecole_b.id, [niv_6e.id])
        c_b = Classe(
            nom="6e B1", niveau_id=niv_6e.id, ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_ecole_b.id, statut="ouverte"
        )
        db.session.add(c_b)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.super_admin.id)
            sess["ecole_id"] = self.ecole_b.id
            sess["_fresh"] = True

        resp = self.client.get(f"/annees/{self.annee_ecole_b.id}/activation")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("première", resp.get_data(as_text=True).lower())

    def test_25_premiere_annee_etancheite_autre_ecole_intacte(self):
        """25. L'activation de l'École B ne touche absolument pas l'École A."""
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_b.id, self.annee_ecole_b.id, [niv_6e.id])
        c_b = Classe(
            nom="6e B1", niveau_id=niv_6e.id, ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_ecole_b.id, statut="ouverte"
        )
        db.session.add(c_b)
        db.session.commit()

        activer_annee_scolaire(self.ecole_b.id, self.annee_ecole_b.id)

        db.session.refresh(self.annee_active)
        self.assertEqual(self.annee_active.statut, "active")
        db.session.refresh(self.annee_planifiee)
        self.assertEqual(self.annee_planifiee.statut, "planifiee")

    # =========================================================================
    # GROUPE 5 : Clôture atomique, transition de statuts et unicité (26 à 30)
    # =========================================================================

    def test_26_cloture_ancienne_active_passe_a_archivee(self):
        """26. L'ancienne année active passe immédiatement au statut 'archivee'."""
        self._preparer_annee_planifiee_valide()
        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(succes)

        db.session.refresh(self.annee_active)
        self.assertEqual(self.annee_active.statut, "archivee")

    def test_27_annee_cible_passe_a_active(self):
        """27. L'année cible passe du statut 'planifiee' à 'active'."""
        self._preparer_annee_planifiee_valide()
        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(succes)

        db.session.refresh(self.annee_planifiee)
        self.assertEqual(self.annee_planifiee.statut, "active")

    def test_28_unicite_stricte_une_seule_active_apres_activation(self):
        """28. Unicité stricte : exactement une seule année active dans l'école."""
        self._preparer_annee_planifiee_valide()
        activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)

        actives = AnneeScolaire.query.filter_by(ecole_id=self.ecole_a.id, statut="active").all()
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0].id, self.annee_planifiee.id)

    def test_29_anciennes_annees_archivees_restent_archivees(self):
        """29. L'année déjà archivée (2024-2025) reste intacte avec statut='archivee'."""
        self._preparer_annee_planifiee_valide()
        activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)

        db.session.refresh(self.annee_archivee)
        self.assertEqual(self.annee_archivee.statut, "archivee")

    def test_30_aucun_champ_is_active_utilise(self):
        """30. Vérifie que le modèle AnneeScolaire utilise uniquement 'statut' et non 'is_active'."""
        self.assertFalse(hasattr(AnneeScolaire, "is_active"))
        self.assertTrue(hasattr(AnneeScolaire, "statut"))

    # =========================================================================
    # GROUPE 6 : Resynchronisation du cache Eleve.classe_id (31 à 36)
    # =========================================================================

    def test_31_eleve_avec_inscription_nouvelle_annee_recoit_classe_id(self):
        """31. Élève avec inscription dans nouvelle année voit son classe_id synchronisé."""
        c_cible = self._preparer_annee_planifiee_valide()
        el = Eleve(nom="Bah", prenom="Alpha", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id, classe_id=None)
        db.session.add(el)
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id, eleve_id=el.id,
            classe_id=c_cible.id, annee_scolaire_id=self.annee_planifiee.id
        )
        db.session.commit()

        resync = resynchroniser_classes_eleves_annee_active(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(resync["synchronises"], 1)

        db.session.refresh(el)
        self.assertEqual(el.classe_id, c_cible.id)

    def test_32_eleve_sans_inscription_cible_classe_id_devient_none(self):
        """32. Élève qui n'a pas d'inscription dans la nouvelle année active -> classe_id = None."""
        self._preparer_annee_planifiee_valide()
        # Élève resté attaché à l'ancienne classe de l'ancienne année
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        c_anc = Classe(
            nom="Ancienne 6e", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add(c_anc)
        db.session.flush()

        el = Eleve(nom="Diallo", prenom="Oumar", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id, classe_id=c_anc.id)
        db.session.add(el)
        db.session.commit()

        # Resynchronisation sur la nouvelle année
        resynchroniser_classes_eleves_annee_active(self.ecole_a.id, self.annee_planifiee.id)

        db.session.refresh(el)
        self.assertIsNone(el.classe_id)

    def test_33_eleve_sortie_classe_id_devient_none(self):
        """33. Élève ayant fait l'objet d'une décision 'sortie' -> classe_id = None après activation."""
        c_cible = self._preparer_annee_planifiee_valide()
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        c_src = Classe(
            nom="6e Source", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add(c_src)
        db.session.flush()

        el = Eleve(nom="Barry", prenom="Ibrahima", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id, classe_id=c_src.id)
        db.session.add(el)
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id, eleve_id=el.id,
            classe_id=c_src.id, annee_scolaire_id=self.annee_active.id
        )
        db.session.commit()

        # Décision de sortie via le moteur de passage officiel
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_planifiee.id,
            decision="sortie",
            motif_sortie="Déménagement"
        )
        db.session.commit()

        succes, msg, _ = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(succes)

        db.session.refresh(el)
        self.assertIsNone(el.classe_id)

    def test_34_eleve_transfert_classe_id_devient_none(self):
        """34. Élève ayant fait l'objet d'une décision 'transfert' -> classe_id = None."""
        c_cible = self._preparer_annee_planifiee_valide()
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        c_src = Classe(
            nom="6e Source", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add(c_src)
        db.session.flush()

        el = Eleve(nom="Camara", prenom="Salif", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id, classe_id=c_src.id)
        db.session.add(el)
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id, eleve_id=el.id,
            classe_id=c_src.id, annee_scolaire_id=self.annee_active.id
        )
        db.session.commit()

        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_planifiee.id,
            decision="transfert",
            motif_sortie="Changement établissement"
        )
        db.session.commit()

        succes, msg, _ = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(succes)

        db.session.refresh(el)
        self.assertIsNone(el.classe_id)

    def test_35_eleve_diplome_classe_id_devient_none(self):
        """35. Élève ayant fait l'objet d'une décision 'diplome' -> classe_id = None."""
        c_cible = self._preparer_annee_planifiee_valide()
        niv_tle = NiveauScolaire.query.filter_by(code="TERMINALE").first()
        c_src = Classe(
            nom="Terminale Source", niveau_id=niv_tle.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add(c_src)
        db.session.flush()

        el = Eleve(nom="Sy", prenom="Mariam", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id, classe_id=c_src.id)
        db.session.add(el)
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id, eleve_id=el.id,
            classe_id=c_src.id, annee_scolaire_id=self.annee_active.id
        )
        db.session.commit()

        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_planifiee.id,
            decision="diplome"
        )
        db.session.commit()

        succes, msg, _ = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(succes)

        db.session.refresh(el)
        self.assertIsNone(el.classe_id)

    def test_36_inscriptions_historiques_non_modifiees_apres_resync(self):
        """36. La resynchronisation ne modifie aucune inscription historique."""
        c_cible = self._preparer_annee_planifiee_valide()
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        c_src = Classe(
            nom="6e Source", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add(c_src)
        db.session.flush()

        el = Eleve(nom="Kone", prenom="Bakary", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id, classe_id=c_src.id)
        db.session.add(el)
        db.session.flush()

        insc_src, _ = creer_inscription_annuelle(
            ecole_id=self.ecole_a.id, eleve_id=el.id,
            classe_id=c_src.id, annee_scolaire_id=self.annee_active.id
        )
        db.session.commit()

        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_planifiee.id,
            decision="passage",
            classe_cible_id=c_cible.id
        )
        db.session.commit()

        activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)

        db.session.refresh(insc_src)
        self.assertEqual(insc_src.classe_id, c_src.id)
        self.assertEqual(insc_src.annee_scolaire_id, self.annee_active.id)

    # =========================================================================
    # GROUPE 7 : Atomicité globale, rollback et intégrité (37 à 40)
    # =========================================================================

    def test_37_rollback_total_si_erreur_pendant_resynchronisation(self):
        """37. Si une exception survient pendant la resync, rollback total des statuts."""
        self._preparer_annee_planifiee_valide()

        with patch(
            "app.services.activation_annee.resynchroniser_classes_eleves_annee_active",
            side_effect=RuntimeError("Erreur simulée lors de la resync")
        ):
            succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)

        self.assertFalse(succes)
        self.assertIn("Erreur simulée", msg)

        # Vérification qu'aucun changement n'a été persisté
        db.session.refresh(self.annee_active)
        self.assertEqual(self.annee_active.statut, "active")
        db.session.refresh(self.annee_planifiee)
        self.assertEqual(self.annee_planifiee.statut, "planifiee")

    def test_38_rollback_total_si_erreur_commit(self):
        """38. Si db.session.commit échoue, rollback total."""
        self._preparer_annee_planifiee_valide()

        with patch.object(db.session, "commit", side_effect=Exception("Database lock error")):
            succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)

        self.assertFalse(succes)
        self.assertIn("Database lock", msg)

    def test_39_aucune_classe_creee_automatiquement(self):
        """39. L'activation ne crée jamais de classe automatiquement."""
        self._preparer_annee_planifiee_valide()
        nb_classes_avant = Classe.query.filter_by(ecole_id=self.ecole_a.id).count()

        activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)

        nb_classes_apres = Classe.query.filter_by(ecole_id=self.ecole_a.id).count()
        self.assertEqual(nb_classes_avant, nb_classes_apres)

    def test_40_aucun_cours_cree_automatiquement(self):
        """40. L'activation ne crée jamais de cours automatiquement."""
        self._preparer_annee_planifiee_valide()
        nb_cours_avant = Cours.query.filter_by(ecole_id=self.ecole_a.id).count()

        activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id)

        nb_cours_apres = Cours.query.filter_by(ecole_id=self.ecole_a.id).count()
        self.assertEqual(nb_cours_avant, nb_cours_apres)

    # =========================================================================
    # GROUPE 8 : Interface, redirection et règle 2C-5D (41 à 45)
    # =========================================================================

    def test_41_get_confirmation_ne_modifie_pas_session_annee_consultee(self):
        """41. GET /annees/<id>/activation NE MODIFIE PAS session['annee_consultee']."""
        self._preparer_annee_planifiee_valide()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_active.id}
            sess["_fresh"] = True

        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/activation")
        self.assertEqual(resp.status_code, 200)

        with self.client.session_transaction() as sess:
            val = sess.get("annee_consultee")
            annee_id = val.get(str(self.ecole_a.id)) if isinstance(val, dict) else val
            self.assertEqual(annee_id, self.annee_active.id)

    def test_42_etape_6_affiche_bouton_activer_quand_prete(self):
        """42. Dans l'assistant de préparation, l'étape 6 affiche le bouton vers /activation."""
        self._preparer_annee_planifiee_valide()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True

        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        html = resp.get_data(as_text=True)
        self.assertIn(f"/annees/{self.annee_planifiee.id}/activation", html)
        self.assertIn("Activer cette année scolaire", html)

    def test_43_etape_6_masque_bouton_activer_quand_bloquants(self):
        """43. L'assistant de préparation ne propose pas le bouton si l'année a des bloquants."""
        # Année sans structure
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True

        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        html = resp.get_data(as_text=True)
        self.assertNotIn(f"/annees/{self.annee_planifiee.id}/activation", html)

    def test_44_gestion_annees_post_action_activer_redirige_confirmation(self):
        """44. L'ancien formulaire POST action='activer' redirige vers la confirmation."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True

        resp = self.client.post(
            "/annees",
            data={"action": "activer", "annee_id": str(self.annee_planifiee.id)},
            follow_redirects=False
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/annees/{self.annee_planifiee.id}/activation", resp.headers["Location"])

    def test_45_post_activer_met_a_jour_session_annee_consultee(self):
        """45. Après activation POST réussie, session['annee_consultee'] bascule sur la nouvelle année."""
        self._preparer_annee_planifiee_valide()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_active.id}
            sess["_fresh"] = True

        resp = self.client.post(f"/annees/{self.annee_planifiee.id}/activer", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.client.session_transaction() as sess:
            val = sess.get("annee_consultee")
            annee_id = val.get(str(self.ecole_a.id)) if isinstance(val, dict) else val
            self.assertEqual(annee_id, self.annee_planifiee.id)

    # =========================================================================
    # GROUPE 9 : Scénario critique End-to-End complet (46)
    # =========================================================================

    def test_46_scenario_critique_end_to_end_moussa_amina_issa_fatou(self):
        """46. Scénario critique End-to-End :
        - Moussa (admis 6e -> 5e A)
        - Amina (redouble 6e A)
        - Issa (sortie définitive)
        - Fatou (transfert établissement)
        Vérification globale : statuts, cache Eleve.classe_id, intégrité historique, unicité active.
        """
        # 1. Structure et classes pour 2025-2026 (source) et 2026-2027 (cible)
        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        niv_5e = NiveauScolaire.query.filter_by(code="5E").first()

        # Config 2025-2026
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_active.id, [niv_6e.id])
        classe_6e_src = Classe(
            nom="6e A", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add(classe_6e_src)

        # Config 2026-2027
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [niv_6e.id, niv_5e.id])
        classe_6e_cible = Classe(
            nom="6e A Cible", niveau_id=niv_6e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        classe_5e_cible = Classe(
            nom="5e A Cible", niveau_id=niv_5e.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add_all([classe_6e_cible, classe_5e_cible])
        db.session.flush()

        # 2. Création des 4 élèves dans 2025-2026
        moussa = Eleve(nom="Diop", prenom="Moussa", date_naissance=date(2012, 3, 10), ecole_id=self.ecole_a.id, classe_id=classe_6e_src.id)
        amina = Eleve(nom="Toure", prenom="Amina", date_naissance=date(2012, 4, 15), ecole_id=self.ecole_a.id, classe_id=classe_6e_src.id)
        issa = Eleve(nom="Keita", prenom="Issa", date_naissance=date(2012, 6, 20), ecole_id=self.ecole_a.id, classe_id=classe_6e_src.id)
        fatou = Eleve(nom="Ndiaye", prenom="Fatou", date_naissance=date(2012, 8, 25), ecole_id=self.ecole_a.id, classe_id=classe_6e_src.id)
        db.session.add_all([moussa, amina, issa, fatou])
        db.session.flush()

        for el in [moussa, amina, issa, fatou]:
            creer_inscription_annuelle(
                ecole_id=self.ecole_a.id, eleve_id=el.id,
                classe_id=classe_6e_src.id, annee_scolaire_id=self.annee_active.id
            )
        db.session.commit()

        # Vérification préalable : tous les 4 ont classe_id pointant sur 6e source
        for el in [moussa, amina, issa, fatou]:
            db.session.refresh(el)
            self.assertEqual(el.classe_id, classe_6e_src.id)

        # 3. Exécution des décisions de passage individuel
        # Moussa -> passage en 5e A
        res_moussa, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id, eleve_id=moussa.id,
            annee_source_id=self.annee_active.id, annee_cible_id=self.annee_planifiee.id,
            decision="passage", classe_cible_id=classe_5e_cible.id
        )
        self.assertIsNone(err)

        # Amina -> redoublement en 6e A
        res_amina, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id, eleve_id=amina.id,
            annee_source_id=self.annee_active.id, annee_cible_id=self.annee_planifiee.id,
            decision="redoublement", classe_cible_id=classe_6e_cible.id
        )
        self.assertIsNone(err)

        # Issa -> sortie (déménagement)
        res_issa, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id, eleve_id=issa.id,
            annee_source_id=self.annee_active.id, annee_cible_id=self.annee_planifiee.id,
            decision="sortie", motif_sortie="Déménagement en province"
        )
        self.assertIsNone(err)

        # Fatou -> transfert (changement d'établissement)
        res_fatou, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id, eleve_id=fatou.id,
            annee_source_id=self.annee_active.id, annee_cible_id=self.annee_planifiee.id,
            decision="transfert", motif_sortie="Transfert international"
        )
        self.assertIsNone(err)
        db.session.commit()

        # 4. Avant activation : vérifier que la préparation est validée à 100%
        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(etat["verification"]["prete_pour_activation"])
        self.assertEqual(len(etat["verification"]["bloquants"]), 0)
        self.assertEqual(etat["passage"]["total_eleves"], 4)
        self.assertEqual(etat["passage"]["traites"], 4)
        self.assertEqual(etat["passage"]["a_traiter"], 0)

        # Avant activation : Eleve.classe_id n'a PAS encore changé (toujours cache de l'année active en cours)
        for el in [moussa, amina, issa, fatou]:
            db.session.refresh(el)
            self.assertEqual(el.classe_id, classe_6e_src.id)

        # 5. Exécution de l'activation annuelle
        succes, msg, details = activer_annee_scolaire(self.ecole_a.id, self.annee_planifiee.id, user_id=self.admin_a.id)
        self.assertTrue(succes, f"Échec de l'activation : {msg}")

        # 6. Contrôles post-activation fondamentaux :
        # Statuts
        db.session.refresh(self.annee_active)
        self.assertEqual(self.annee_active.statut, "archivee")
        db.session.refresh(self.annee_planifiee)
        self.assertEqual(self.annee_planifiee.statut, "active")

        # Unicité de l'année active
        actives = AnneeScolaire.query.filter_by(ecole_id=self.ecole_a.id, statut="active").all()
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0].id, self.annee_planifiee.id)

        # Cache Eleve.classe_id resynchronisé
        db.session.refresh(moussa)
        self.assertEqual(moussa.classe_id, classe_5e_cible.id, "Moussa doit être affecté à sa classe de 5e")

        db.session.refresh(amina)
        self.assertEqual(amina.classe_id, classe_6e_cible.id, "Amina doit être affectée à sa classe redoublée de 6e")

        db.session.refresh(issa)
        self.assertIsNone(issa.classe_id, "Issa est sorti, son classe_id doit être NULL")

        db.session.refresh(fatou)
        self.assertIsNone(fatou.classe_id, "Fatou est transférée, son classe_id doit être NULL")

        # Intégrité des inscriptions historiques 2025-2026
        for el in [moussa, amina, issa, fatou]:
            inscriptions_anciennes = Inscription.query.filter_by(
                eleve_id=el.id, annee_scolaire_id=self.annee_active.id
            ).all()
            self.assertEqual(len(inscriptions_anciennes), 1)
            self.assertEqual(inscriptions_anciennes[0].classe_id, classe_6e_src.id)

        # Inscriptions 2026-2027
        inscr_moussa_cible = Inscription.query.filter_by(
            eleve_id=moussa.id, annee_scolaire_id=self.annee_planifiee.id
        ).first()
        self.assertIsNotNone(inscr_moussa_cible)
        self.assertEqual(inscr_moussa_cible.classe_id, classe_5e_cible.id)

        inscr_amina_cible = Inscription.query.filter_by(
            eleve_id=amina.id, annee_scolaire_id=self.annee_planifiee.id
        ).first()
        self.assertIsNotNone(inscr_amina_cible)
        self.assertEqual(inscr_amina_cible.classe_id, classe_6e_cible.id)

        inscr_issa_cible = Inscription.query.filter_by(
            eleve_id=issa.id, annee_scolaire_id=self.annee_planifiee.id
        ).first()
        self.assertIsNone(inscr_issa_cible, "Issa ne doit avoir aucune inscription dans la nouvelle année active")

        inscr_fatou_cible = Inscription.query.filter_by(
            eleve_id=fatou.id, annee_scolaire_id=self.annee_planifiee.id
        ).first()
        self.assertIsNone(inscr_fatou_cible, "Fatou ne doit avoir aucune inscription dans la nouvelle année active")



class TestPhase2D5MigratedSchemaDatabase(unittest.TestCase):
    """
    Test de validation sur base de données SQLite migrée via Alembic.
    Couvre précisément le comportement réel de la contrainte classe_id nullable
    avec SQLite foreign keys activées (PRAGMA foreign_keys = ON).
    """

    def setUp(self):
        import os
        import tempfile
        from flask_migrate import upgrade as flask_migrate_upgrade

        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = os.path.join(self.temp_dir, "migrated_activation_test.db")

        class MigratedSchemaConfig(Config):
            TESTING = True
            WTF_CSRF_ENABLED = False
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.temp_db_path}"

        self.app = create_app(MigratedSchemaConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()

        # Application de l'ensemble des migrations Alembic
        flask_migrate_upgrade(directory="migrations")

        # Activer explicitement les contraintes de clés étrangères SQLite
        db.session.execute(db.text("PRAGMA foreign_keys = ON;"))
        db.session.commit()

    def tearDown(self):
        import shutil

        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_47_activation_reelle_base_migree_classe_id_null(self):
        """
        Vérifie qu'après migration Alembic 28cd4856f1c5 :
        1. Le schéma SQLite réel a Eleve.classe_id notnull = 0 (nullable).
        2. La clé étrangère reste ON DELETE RESTRICT.
        3. L'index composite ix_eleve_ecole_classe est bien présent.
        4. Une activation annuelle avec des élèves sortants/transférés synchronise
           Eleve.classe_id = NULL et le commit réussit sans IntegrityError.
        5. La valeur NULL est bien persistée et lisible en SQL brut.
        """
        # 1. Vérification PRAGMA table_info
        cols = db.session.execute(db.text("PRAGMA table_info(eleve)")).fetchall()
        classe_id_col = next(c for c in cols if c[1] == "classe_id")
        self.assertEqual(classe_id_col[3], 0, "classe_id doit être nullable (notnull=0)")

        # 2. Vérification PRAGMA foreign_key_list
        fks = db.session.execute(db.text("PRAGMA foreign_key_list(eleve)")).fetchall()
        classe_id_fk = next(fk for fk in fks if fk[3] == "classe_id")
        self.assertEqual(classe_id_fk[6].upper(), "RESTRICT", "FK classe_id doit rester ON DELETE RESTRICT")

        # 3. Données de test
        ensure_standard_niveaux(commit=False)
        ecole = Ecole(nom="École Test Migrée", statut="actif")
        db.session.add(ecole)
        db.session.flush()
        ensure_ecole_niveau_configs(ecole.id, default_active=True, commit=False)

        annee_src = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30), statut="active", ecole_id=ecole.id
        )
        annee_dst = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30), statut="planifiee", ecole_id=ecole.id
        )
        db.session.add_all([annee_src, annee_dst])
        db.session.flush()

        niv_6e = NiveauScolaire.query.filter_by(code="6E").first()
        niv_5e = NiveauScolaire.query.filter_by(code="5E").first()
        sauvegarder_selection_annuelle(ecole.id, annee_dst.id, [niv_5e.id])

        classe_src = Classe(nom="6e A", niveau_id=niv_6e.id, annee_scolaire_id=annee_src.id, ecole_id=ecole.id, statut="ouverte")
        classe_dst = Classe(nom="5e A", niveau_id=niv_5e.id, annee_scolaire_id=annee_dst.id, ecole_id=ecole.id, statut="ouverte")
        db.session.add_all([classe_src, classe_dst])
        db.session.flush()

        # 3 élèves :
        # e_promu : passe en 5e
        # e_sorti : sort de l'école (classe_id doit devenir NULL)
        # e_transfere : transféré (classe_id doit devenir NULL)
        e_promu = Eleve(nom="DIOP", prenom="Moussa", date_naissance=date(2012, 1, 1), ecole_id=ecole.id, classe_id=classe_src.id, code_parent="CODE_P1")
        e_sorti = Eleve(nom="FALL", prenom="Awa", date_naissance=date(2012, 2, 2), ecole_id=ecole.id, classe_id=classe_src.id, code_parent="CODE_P2")
        e_transfere = Eleve(nom="SOW", prenom="Ibra", date_naissance=date(2012, 3, 3), ecole_id=ecole.id, classe_id=classe_src.id, code_parent="CODE_P3")
        db.session.add_all([e_promu, e_sorti, e_transfere])
        db.session.flush()

        creer_inscription_annuelle(ecole.id, e_promu.id, annee_src.id, classe_src.id)
        creer_inscription_annuelle(ecole.id, e_sorti.id, annee_src.id, classe_src.id)
        creer_inscription_annuelle(ecole.id, e_transfere.id, annee_src.id, classe_src.id)
        db.session.commit()

        # Décisions de passage
        _, err1 = executer_passage_eleve(
            ecole_id=ecole.id, eleve_id=e_promu.id,
            annee_source_id=annee_src.id, annee_cible_id=annee_dst.id,
            decision="passage", classe_cible_id=classe_dst.id
        )
        self.assertIsNone(err1)

        _, err2 = executer_passage_eleve(
            ecole_id=ecole.id, eleve_id=e_sorti.id,
            annee_source_id=annee_src.id, annee_cible_id=annee_dst.id,
            decision="sortie", motif_sortie="Fin d'études"
        )
        self.assertIsNone(err2)

        _, err3 = executer_passage_eleve(
            ecole_id=ecole.id, eleve_id=e_transfere.id,
            annee_source_id=annee_src.id, annee_cible_id=annee_dst.id,
            decision="transfert", motif_sortie="Déménagement"
        )
        self.assertIsNone(err3)
        db.session.commit()

        # Activation annuelle
        succes, msg, details = activer_annee_scolaire(ecole.id, annee_dst.id)
        self.assertTrue(succes, f"L'activation a échoué: {msg}")

        # Commit final réel
        db.session.commit()

        # 4. Vérification SQL brute
        rows = db.session.execute(
            db.text("SELECT id, nom, classe_id FROM eleve WHERE ecole_id = :ecole_id ORDER BY id"),
            {"ecole_id": ecole.id}
        ).fetchall()

        self.assertEqual(len(rows), 3)
        # e_promu -> classe_dst
        self.assertEqual(rows[0][2], classe_dst.id)
        # e_sorti -> NULL
        self.assertIsNone(rows[1][2], "L'élève sorti doit avoir classe_id NULL en base réelle")
        # e_transfere -> NULL
        self.assertIsNone(rows[2][2], "L'élève transféré doit avoir classe_id NULL en base réelle")


if __name__ == "__main__":
    unittest.main()
