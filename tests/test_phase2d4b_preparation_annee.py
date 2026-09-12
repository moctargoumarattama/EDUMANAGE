"""
tests/test_phase2d4b_preparation_annee.py
=============================================================
KLASORA — Phase 2D-4B : Assistant central « Préparer l'année »
Suite de tests complète et rigoureuse (40 vérifications) :

1-10:   Sécurité et permissions (anonyme, rôles, multi-école, statuts)
11-15:  Règle absolue 2C-5D (immuabilité de session, boutons consulter)
16-20:  Étape 1 : Structure pédagogique
21-26:  Étape 2 : Classes (ouvertes, fermées, niveaux sans classe)
27-30:  Étape 3 : Cours et professeurs (informatif / non bloquant)
31-35:  Étape 4 : Passage des élèves (applicable vs non applicable, décompte)
36-40:  Étape 5 & 6 : Synthèse, progression, éligibilité activation & UI
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
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Professeur,
    Utilisateur,
)
from app.services.annees_scolaires import get_annee_consultee, set_annee_consultee
from app.services.classes_annuelles import set_classe_ouverte
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.niveaux import (
    ensure_standard_niveaux,
    ensure_ecole_niveau_configs,
)
from app.services.niveaux_annuels import sauvegarder_selection_annuelle
from app.services.passage_annee import executer_passage_eleve
from app.services.preparation_annee import (
    determiner_source_passage_pour_cible,
    get_etat_preparation_annee,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class TestPhase2D4BPreparationAnnee(unittest.TestCase):
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

        # Utilisateurs
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

        # Années École A
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

    # =========================================================================
    # GROUPE 1 : Sécurité et permissions (1 à 10)
    # =========================================================================

    def test_01_acces_anonyme_redirige_vers_login(self):
        """1. Utilisateur non connecté redirigé vers /login."""
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_02_role_parent_interdit(self):
        """2. Rôle parent -> accès non autorisé (redirection 302)."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.parent_user.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation", follow_redirects=True)
        self.assertIn("non autoris", resp.get_data(as_text=True).lower())

    def test_03_role_professeur_interdit(self):
        """3. Rôle professeur -> accès non autorisé (redirection 302)."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.prof.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation", follow_redirects=True)
        self.assertIn("non autoris", resp.get_data(as_text=True).lower())

    def test_04_admin_ecole_a_vers_annee_ecole_b_bloque(self):
        """4. Admin école A ne peut pas accéder à l'année de l'école B."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_ecole_b.id}/preparation", follow_redirects=True)
        self.assertIn("introuvable", resp.get_data(as_text=True).lower())

    def test_05_annee_inexistante_bloquee(self):
        """5. Année inexistante -> message flash et redirection."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get("/annees/99999/preparation", follow_redirects=True)
        self.assertIn("introuvable", resp.get_data(as_text=True).lower())

    def test_06_annee_archivee_bloquee(self):
        """6. Année archivée -> message flash et redirection."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_archivee.id}/preparation", follow_redirects=True)
        self.assertIn("archivée", resp.get_data(as_text=True).lower())

    def test_07_annee_active_bloquee(self):
        """7. Année déjà active -> message flash et redirection."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_active.id}/preparation", follow_redirects=True)
        self.assertIn("déjà active", resp.get_data(as_text=True).lower())

    def test_08_annee_planifiee_acces_succes(self):
        """8. Admin école A accède avec succès à l'année planifiée de son école (200 OK)."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("2026-2027", resp.get_data(as_text=True))

    def test_09_super_admin_acces_succes(self):
        """9. Super admin avec ecole_id en session accède à l'assistant (200 OK)."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.super_admin.id)
            sess["ecole_id"] = self.ecole_a.id
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        self.assertEqual(resp.status_code, 200)

    def test_10_super_admin_sans_ecole_redirige(self):
        """10. Super admin sans ecole_id en session est invité à choisir une école."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.super_admin.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation", follow_redirects=True)
        self.assertIn("sélectionner un établissement", resp.get_data(as_text=True).lower())

    # =========================================================================
    # GROUPE 2 : Règle absolue 2C-5D (11 à 15)
    # =========================================================================

    def test_11_get_preparation_ne_modifie_pas_session_annee_consultee(self):
        """11. GET /annees/<planifiee>/preparation NE modifie PAS session['annee_consultee']."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["annee_consultee"] = self.annee_active.id
            sess["_fresh"] = True

        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        self.assertEqual(resp.status_code, 200)

        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("annee_consultee"), self.annee_active.id)

    def test_12_get_preparation_sans_session_reste_vide(self):
        """12. Si aucune annee_consultee n'était en session, elle ne l'est pas après GET."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True

        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        self.assertEqual(resp.status_code, 200)

        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("annee_consultee"))

    def test_13_bouton_gerer_classes_est_form_consulter_avec_next(self):
        """13. Le bouton 'Gérer les classes' est un POST /consulter avec next=/classes."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        html = resp.get_data(as_text=True)
        self.assertIn(f'/annees/{self.annee_planifiee.id}/consulter', html)
        self.assertIn('name="next"', html)
        self.assertIn('value="/classes"', html)

    def test_14_bouton_gerer_cours_est_form_consulter_avec_next(self):
        """14. Le bouton 'Gérer les cours' est un POST /consulter avec next=/cours."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        html = resp.get_data(as_text=True)
        self.assertIn(f'/annees/{self.annee_planifiee.id}/consulter', html)
        self.assertIn('value="/cours"', html)

    def test_15_bouton_revenir_annee_active_fonctionne(self):
        """15. Revenir à l'année active bascule la session via POST /consulter."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_planifiee.id}
            sess["_fresh"] = True

        resp = self.client.post(
            f"/annees/{self.annee_active.id}/consulter",
            data={"next": f"/annees/{self.annee_planifiee.id}/preparation"},
            follow_redirects=False
        )
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as sess:
            val = sess.get("annee_consultee")
            annee_id = val.get(str(self.ecole_a.id)) if isinstance(val, dict) else val
            self.assertEqual(annee_id, self.annee_active.id)

    # =========================================================================
    # GROUPE 3 : Étape 1 - Structure pédagogique (16 à 20)
    # =========================================================================

    def test_16_structure_non_configuree_au_depart(self):
        """16. Sans AnneeNiveauConfig, structure.prete est False et total_niveaux == 0."""
        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(etat["structure"]["prete"])
        self.assertEqual(etat["structure"]["total_niveaux"], 0)

    def test_17_structure_bloquant_present_si_vide(self):
        """17. Bloquant explicite présent si structure non configurée."""
        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(any("Structure pédagogique non configurée" in b for b in etat["verification"]["bloquants"]))

    def test_18_structure_prete_apres_configuration(self):
        """18. Après enregistrement des niveaux actifs, structure.prete devient True."""
        niveaux = NiveauScolaire.query.filter_by(cycle="college").all()
        sauvegarder_selection_annuelle(
            self.ecole_a.id, self.annee_planifiee.id, [n.id for n in niveaux]
        )
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(etat["structure"]["prete"])
        self.assertEqual(etat["structure"]["total_niveaux"], len(niveaux))

    def test_19_structure_niveaux_contient_les_noms(self):
        """19. structure.niveaux contient les instances NiveauScolaire configurées."""
        niveaux = NiveauScolaire.query.filter_by(cycle="college").all()
        sauvegarder_selection_annuelle(
            self.ecole_a.id, self.annee_planifiee.id, [niveaux[0].id]
        )
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat["structure"]["niveaux"][0].id, niveaux[0].id)

    def test_20_lien_structure_pointe_vers_route_structure(self):
        """20. Le bouton pointe vers /annees/<id>/structure."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        html = resp.get_data(as_text=True)
        self.assertIn(f'/annees/{self.annee_planifiee.id}/structure', html)

    # =========================================================================
    # GROUPE 4 : Étape 2 - Classes (21 à 26)
    # =========================================================================

    def test_21_classes_bloquant_si_aucune_classe(self):
        """21. Structure configurée mais 0 classe -> bloquant 'Aucune classe'."""
        niv = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [niv.id])
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(etat["classes"]["prete"])
        self.assertTrue(any("Aucune classe n'a été créée" in b for b in etat["verification"]["bloquants"]))

    def test_22_classes_bloquant_si_classes_uniquement_fermees(self):
        """22. Classe créée mais fermée -> bloquant 'Aucune classe n'est ouverte'."""
        niv = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [niv.id])
        c = Classe(
            nom="6e A", niveau_id=niv.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="fermee"
        )
        db.session.add(c)
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(etat["classes"]["prete"])
        self.assertEqual(etat["classes"]["ouvertes"], 0)
        self.assertEqual(etat["classes"]["fermees"], 1)
        self.assertTrue(any("Aucune classe n'est ouverte" in b for b in etat["verification"]["bloquants"]))

    def test_23_classes_bloquant_si_niveau_sans_classe(self):
        """23. Niveaux 6e et 5e actifs, mais classe uniquement en 6e -> 5e sans classe."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        n5 = NiveauScolaire.query.filter_by(code="5E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id, n5.id])
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertFalse(etat["classes"]["prete"])
        self.assertEqual(len(etat["classes"]["niveaux_sans_classe"]), 1)
        self.assertEqual(etat["classes"]["niveaux_sans_classe"][0].id, n5.id)
        self.assertTrue(any("n'ont aucune classe ouverte" in b for b in etat["verification"]["bloquants"]))

    def test_24_classes_pretes_quand_chaque_niveau_a_classe_ouverte(self):
        """24. Chaque niveau actif a au moins une classe ouverte -> classes.prete est True."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        n5 = NiveauScolaire.query.filter_by(code="5E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id, n5.id])
        c6 = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        c5 = Classe(
            nom="5e A", niveau_id=n5.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add_all([c6, c5])
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(etat["classes"]["prete"])
        self.assertEqual(len(etat["classes"]["niveaux_sans_classe"]), 0)

    def test_25_classes_compteurs_ouvertes_et_fermees(self):
        """25. Décompte exact des classes ouvertes vs fermées."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        c1 = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        c2 = Classe(
            nom="6e B", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="fermee"
        )
        db.session.add_all([c1, c2])
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat["classes"]["total_classes"], 2)
        self.assertEqual(etat["classes"]["ouvertes"], 1)
        self.assertEqual(etat["classes"]["fermees"], 1)

    def test_26_classes_statut_label(self):
        """26. Statut label affiche 'Prête' ou 'Incomplète'."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat["classes"]["statut_label"], "Prête")

    # =========================================================================
    # GROUPE 5 : Étape 3 - Cours et professeurs (27 à 30)
    # =========================================================================

    def test_27_cours_aucun_cours_genere_avertissement(self):
        """27. Aucun cours -> avertissement non bloquant."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(any("Aucun cours n'a été créé" in a for a in etat["verification"]["avertissements"]))
        # Ce n'est PAS un bloquant !
        self.assertFalse(any("cours" in b.lower() for b in etat["verification"]["bloquants"]))

    def test_28_cours_sans_prof_genere_avertissement(self):
        """28. Cours créé sans professeur -> avertissement sans bloquant."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.flush()

        cours = Cours(nom="Mathématiques", ecole_id=self.ecole_a.id, classe_id=c.id, professeur_id=None)
        db.session.add(cours)
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat["cours"]["total_cours"], 1)
        self.assertEqual(etat["cours"]["sans_prof"], 1)
        self.assertTrue(any("aucun enseignant assigné" in a for a in etat["verification"]["avertissements"]))

    def test_29_cours_complet_leve_avertissements(self):
        """29. Cours avec professeur assigné -> avertissements de cours levés."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.flush()

        cours = Cours(nom="Mathématiques", ecole_id=self.ecole_a.id, classe_id=c.id, professeur_id=self.enseignant.id)
        db.session.add(cours)
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat["cours"]["avec_prof"], 1)
        self.assertEqual(etat["cours"]["sans_prof"], 0)
        self.assertEqual(len(etat["cours"]["classes_sans_cours"]), 0)

    def test_30_cours_non_bloquant_pour_activation(self):
        """30. Même avec 0 cours, prete_pour_activation peut être True si les bloquants sont levés."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.commit()
        # Aucun élève dans annee_active -> passage 0 élèves, a_traiter = 0

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(etat["verification"]["prete_pour_activation"])
        self.assertEqual(len(etat["verification"]["bloquants"]), 0)
        self.assertTrue(len(etat["verification"]["avertissements"]) > 0)

    # =========================================================================
    # GROUPE 6 : Étape 4 - Passage des élèves (31 à 35)
    # =========================================================================

    def test_31_passage_non_applicable_sans_source(self):
        """31. École B sans année antérieure -> passage.applicable est False, non bloquant."""
        etat = get_etat_preparation_annee(self.ecole_b.id, self.annee_ecole_b.id)
        self.assertFalse(etat["passage"]["applicable"])
        self.assertTrue(etat["passage"]["prete"])
        self.assertIsNone(etat["passage"]["source"])

    def test_32_passage_applicable_avec_source_et_eleves_non_traites(self):
        """32. Élève dans année active sans décision -> a_traiter > 0 et bloquant présent."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        c_src = Classe(
            nom="6e Source", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        el = Eleve(
            nom="Kane", prenom="Awa", date_naissance=date(2012, 1, 1),
            ecole_id=self.ecole_a.id, classe=c_src
        )
        db.session.add_all([c_src, el])
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            classe_id=c_src.id,
            annee_scolaire_id=self.annee_active.id
        )
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertTrue(etat["passage"]["applicable"])
        self.assertEqual(etat["passage"]["total_eleves"], 1)
        self.assertEqual(etat["passage"]["traites"], 0)
        self.assertEqual(etat["passage"]["a_traiter"], 1)
        self.assertFalse(etat["passage"]["prete"])
        self.assertTrue(any("n'ont pas encore été traités" in b for b in etat["verification"]["bloquants"]))

    def test_33_passage_eleve_traite_par_inscription_cible(self):
        """33. Élève ayant une inscription dans l'année cible est compté comme traité."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        n5 = NiveauScolaire.query.filter_by(code="5E").first()
        c_src = Classe(
            nom="6e Source", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        c_dst = Classe(
            nom="5e Cible", niveau_id=n5.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        el = Eleve(
            nom="Kane", prenom="Awa", date_naissance=date(2012, 1, 1),
            ecole_id=self.ecole_a.id, classe=c_src
        )
        db.session.add_all([c_src, c_dst, el])
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            classe_id=c_src.id,
            annee_scolaire_id=self.annee_active.id
        )
        creer_inscription_annuelle(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            classe_id=c_dst.id,
            annee_scolaire_id=self.annee_planifiee.id
        )
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat["passage"]["total_eleves"], 1)
        self.assertEqual(etat["passage"]["traites"], 1)
        self.assertEqual(etat["passage"]["a_traiter"], 0)
        self.assertTrue(etat["passage"]["prete"])

    def test_34_passage_eleve_traite_par_sortie_ou_transfert(self):
        """34. Élève clôturé en sortie/transfert dans la source est compté comme traité."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        c_src = Classe(
            nom="6e Source", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        el = Eleve(
            nom="Sow", prenom="Ali", date_naissance=date(2012, 5, 5),
            ecole_id=self.ecole_a.id, classe=c_src
        )
        db.session.add_all([c_src, el])
        db.session.flush()

        insc, _ = creer_inscription_annuelle(
            ecole_id=self.ecole_a.id,
            eleve_id=el.id,
            classe_id=c_src.id,
            annee_scolaire_id=self.annee_active.id
        )
        insc.statut = "transfere"
        insc.decision_fin_annee = "transfert"
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat["passage"]["traites"], 1)
        self.assertEqual(etat["passage"]["a_traiter"], 0)
        self.assertTrue(etat["passage"]["prete"])

    def test_35_lien_passage_eleves_affiche_quand_applicable(self):
        """35. Lien vers /passage/<source>/<cible> présent dans le template quand applicable."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        html = resp.get_data(as_text=True)
        self.assertIn(f'/annees/{self.annee_active.id}/passage/{self.annee_planifiee.id}', html)

    # =========================================================================
    # GROUPE 7 : Synthèse, progression et activation (36 à 40)
    # =========================================================================

    def test_36_progression_augmente_par_etape(self):
        """36. Score de progression augmente au fur et à mesure des étapes."""
        # 1. Init : 0%
        etat0 = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat0["progression"], 30)  # 0 struct, 0 class, passage 30 car 0 eleves

        # 2. Structure configurée : +30%
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        db.session.commit()
        etat1 = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat1["progression"], 60)

        # 3. Classe créée : 100% car tous bloquants levés !
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.commit()
        etat2 = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(etat2["progression"], 100)
        self.assertTrue(etat2["verification"]["prete_pour_activation"])

    def test_37_prete_pour_activation_quand_zero_bloquant(self):
        """37. prete_pour_activation est True si len(bloquants) == 0."""
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_selection_annuelle(self.ecole_a.id, self.annee_planifiee.id, [n6.id])
        c = Classe(
            nom="6e A", niveau_id=n6.id, ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id, statut="ouverte"
        )
        db.session.add(c)
        db.session.commit()

        etat = get_etat_preparation_annee(self.ecole_a.id, self.annee_planifiee.id)
        self.assertEqual(len(etat["verification"]["bloquants"]), 0)
        self.assertTrue(etat["verification"]["prete_pour_activation"])

    def test_38_template_affiche_les_6_etapes(self):
        """38. Le HTML contient les titres des 6 étapes."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get(f"/annees/{self.annee_planifiee.id}/preparation")
        html = resp.get_data(as_text=True)
        self.assertIn("1. Structure", html)
        self.assertIn("2. Classes", html)
        self.assertIn("3. Cours et professeurs", html)
        self.assertIn("4. Passage des", html)
        self.assertIn("5. Contr", html)
        self.assertIn("6. Statut pour", html)

    def test_39_bouton_preparer_annee_present_sur_gestion_annees_pour_planifiee(self):
        """39. Bouton 'Préparer l'année' présent sur /annees pour annee.statut == 'planifiee'."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get("/annees")
        html = resp.get_data(as_text=True)
        self.assertIn(f"/annees/{self.annee_planifiee.id}/preparation", html)
        self.assertIn("Préparer", html)

    def test_40_bouton_preparer_annee_absent_pour_active_et_archivee(self):
        """40. Bouton 'Préparer l'année' absent pour années actives ou archivées."""
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
            sess["_fresh"] = True
        resp = self.client.get("/annees")
        html = resp.get_data(as_text=True)
        self.assertNotIn(f"/annees/{self.annee_active.id}/preparation", html)
        self.assertNotIn(f"/annees/{self.annee_archivee.id}/preparation", html)


if __name__ == "__main__":
    unittest.main()
