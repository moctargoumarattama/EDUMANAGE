"""Tests unitaires et d'intégration pour l'accélération ergonomique et le guidage de démarrage d'école.

Couvre :
1. La génération par lot de classes (service et route) avec détection et évitement strict des doublons.
2. L'injection des matières et coefficients standard (service et route) avec idempotence.
3. L'étanchéité multi-tenant stricte (refus HTTP 403 en cas d'action sur l'école d'un tiers).
4. Le calcul dynamique de l'état d'avancement (guidance state) et les transitions Étape 1 -> 2 -> 3 -> Complet.
5. Le rendu conditionnel de la carte pas-à-pas sur le tableau de bord (index.html).
"""

from datetime import date
import io
import unittest
from PIL import Image

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from app.services.niveaux import ensure_standard_niveaux
from app.services.pedagogie_standard import (
    generer_classes_batch,
    get_school_guidance_state,
    injecter_matieres_onboarding,
    injecter_matieres_standard,
)
from app.services.structure_annuelle import sauvegarder_structure_annee


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-fast-setup-guidance"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class FastSetupAndGuidanceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Niveaux standards
        ensure_standard_niveaux(commit=True)
        self.niveau_6e = NiveauScolaire.query.filter_by(code="6E").first()
        self.niveau_5e = NiveauScolaire.query.filter_by(code="5E").first()
        self.niveau_cm2 = NiveauScolaire.query.filter_by(code="CM2").first()

        # Établissement A
        self.ecole_a = Ecole(
            nom="Complexe Scolaire A",
            adresse="Niamey",
            telephone="90000001",
            onboarding_complete=True,
        )
        # Établissement B (Tenant distinct)
        self.ecole_b = Ecole(
            nom="Groupe Scolaire B",
            adresse="Maradi",
            telephone="90000002",
            onboarding_complete=True,
        )
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Années actives
        self.annee_a = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_a, self.annee_b])
        db.session.flush()

        # Structure annuelle pour l'école A et B (activation des niveaux)
        tous_niveaux = NiveauScolaire.query.all()
        ids_niveaux = [n.id for n in tous_niveaux]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a.id, ids_niveaux)
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b.id, ids_niveaux)
        db.session.commit()

        # Administrateurs
        self.admin_a = Utilisateur(
            nom="Admin",
            prenom="Alpha",
            email="admin@alpha.test",
            mot_de_passe="secret123",
            role="admin",
            ecole_id=self.ecole_a.id,
        )
        self.admin_b = Utilisateur(
            nom="Admin",
            prenom="Beta",
            email="admin@beta.test",
            mot_de_passe="secret123",
            role="admin",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.admin_a, self.admin_b])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["ecole_id"] = user.ecole_id
            sess["user_role"] = user.role

    # -------------------------------------------------------------------------
    # 1. Tests Génération de classes par lot (Service & Route)
    # -------------------------------------------------------------------------

    def test_generer_classes_batch_service_et_idempotence(self):
        """Vérifie la création par lot de classes et l'absence totale de doublons."""
        configs = [
            {"niveau_id": self.niveau_6e.id, "section": "A"},
            {"niveau_id": self.niveau_6e.id, "section": "B"},
            {"niveau_id": self.niveau_cm2.id, "section": "A"},
        ]

        creees, existantes, err = generer_classes_batch(self.ecole_a.id, self.annee_a.id, configs)
        self.assertIsNone(err)
        self.assertEqual(len(creees), 3)
        self.assertEqual(len(existantes), 0)

        # Vérification en base
        noms = {c.nom for c in Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id).all()}
        self.assertIn("6e A", noms)
        self.assertIn("6e B", noms)
        self.assertIn("CM2 A", noms)

        # Relance avec les mêmes configurations -> 0 créée, 3 existantes
        creees_2, existantes_2, err_2 = generer_classes_batch(self.ecole_a.id, self.annee_a.id, configs)
        self.assertIsNone(err_2)
        self.assertEqual(len(creees_2), 0)
        self.assertEqual(len(existantes_2), 3)
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id).count(), 3)

    def test_route_generation_rapide_classes(self):
        """Vérifie la route GET et POST /classes/generation-rapide pour un admin."""
        self._login(self.admin_a)

        # GET
        res_get = self.client.get("/classes/generation-rapide")
        self.assertEqual(res_get.status_code, 200)
        self.assertIn("Génération rapide des classes", res_get.get_data(as_text=True))

        # POST
        data = {
            "classes_selected": [
                f"{self.niveau_5e.id}:A",
                f"{self.niveau_5e.id}:B",
            ]
        }
        res_post = self.client.post("/classes/generation-rapide", data=data, follow_redirects=True)
        self.assertEqual(res_post.status_code, 200)

        c5a = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id, nom="5e A").first()
        c5b = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id, nom="5e B").first()
        self.assertIsNotNone(c5a)
        self.assertIsNotNone(c5b)

    # -------------------------------------------------------------------------
    # 2. Tests Injection Matières Standard (Service & Route)
    # -------------------------------------------------------------------------

    def test_injecter_matieres_standard_service_et_idempotence(self):
        """Vérifie l'injection de cours/matières standard et coefficients recommandés sans doublons."""
        classe = Classe(
            nom="6e A",
            niveau=self.niveau_6e.nom,
            niveau_id=self.niveau_6e.id,
            section="A",
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
        )
        db.session.add(classe)
        db.session.commit()

        # Première injection
        cours_crees, err = injecter_matieres_standard(self.ecole_a.id, self.annee_a.id, classe.id)
        self.assertIsNone(err)
        self.assertGreaterEqual(len(cours_crees), 6)

        noms_matieres = {c.nom: c.coefficient for c in cours_crees}
        self.assertIn("Français", noms_matieres)
        self.assertIn("Mathématiques", noms_matieres)
        self.assertEqual(noms_matieres["Français"], 4.0)
        self.assertEqual(noms_matieres["Mathématiques"], 4.0)

        # Deuxième injection -> aucun nouveau cours créé
        cours_crees_2, err_2 = injecter_matieres_standard(self.ecole_a.id, self.annee_a.id, classe.id)
        self.assertIsNone(err_2)
        self.assertEqual(len(cours_crees_2), 0)
        self.assertEqual(Cours.query.filter_by(classe_id=classe.id).count(), len(cours_crees))

    def test_route_charger_matieres_standard_ajax_et_form(self):
        """Vérifie l'endpoint /classe/<id>/charger-matieres-standard en AJAX et standard."""
        self._login(self.admin_a)

        classe = Classe(
            nom="CM2 A",
            niveau=self.niveau_cm2.nom,
            niveau_id=self.niveau_cm2.id,
            section="A",
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
        )
        db.session.add(classe)
        db.session.commit()

        # Appel AJAX JSON
        res_ajax = self.client.post(
            f"/classe/{classe.id}/charger-matieres-standard",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res_ajax.status_code, 200)
        json_data = res_ajax.get_json()
        self.assertTrue(json_data["success"])
        self.assertGreater(json_data["count"], 0)

        # Appel Form standard (déjà configurées)
        res_form = self.client.post(
            f"/classe/{classe.id}/charger-matieres-standard",
            follow_redirects=True,
        )
        self.assertEqual(res_form.status_code, 200)

    # -------------------------------------------------------------------------
    # 3. Tests Étanchéité Multi-tenant
    # -------------------------------------------------------------------------

    def test_multi_tenant_protection_charger_matieres(self):
        """Un administrateur de l'école A ne peut pas injecter de matières dans une classe de l'école B (HTTP 403)."""
        self._login(self.admin_a)

        classe_b = Classe(
            nom="6e B Tenant",
            niveau=self.niveau_6e.nom,
            niveau_id=self.niveau_6e.id,
            section="B",
            annee_scolaire_id=self.annee_b.id,
            ecole_id=self.ecole_b.id,
        )
        db.session.add(classe_b)
        db.session.commit()

        # Tentative cross-tenant
        res = self.client.post(
            f"/classe/{classe_b.id}/charger-matieres-standard",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 403)

        res_form = self.client.post(f"/classe/{classe_b.id}/charger-matieres-standard")
        self.assertEqual(res_form.status_code, 403)

    # -------------------------------------------------------------------------
    # 4. Tests Calcul Dynamique du Guidance State
    # -------------------------------------------------------------------------

    def test_guidance_state_transitions(self):
        """Valide la transition étape par étape : 1 (classes) -> 2 (cours) -> 3 (élèves) -> complet."""
        # Étape 1 : Aucune classe
        state_1 = get_school_guidance_state(self.ecole_a.id, self.annee_a.id)
        self.assertFalse(state_1["has_classes"])
        self.assertFalse(state_1["has_cours"])
        self.assertFalse(state_1["has_eleves"])
        self.assertFalse(state_1["setup_complet"])
        self.assertEqual(state_1["current_step"], 1)

        # Ajout d'une classe -> Étape 2
        classe = Classe(
            nom="6e A",
            niveau=self.niveau_6e.nom,
            niveau_id=self.niveau_6e.id,
            section="A",
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
        )
        db.session.add(classe)
        db.session.commit()

        state_2 = get_school_guidance_state(self.ecole_a.id, self.annee_a.id)
        self.assertTrue(state_2["has_classes"])
        self.assertFalse(state_2["has_cours"])
        self.assertFalse(state_2["has_eleves"])
        self.assertFalse(state_2["setup_complet"])
        self.assertEqual(state_2["current_step"], 2)

        # Ajout d'un cours -> Étape 3
        cours = Cours(nom="Français", coefficient=4.0, ecole_id=self.ecole_a.id, classe_id=classe.id)
        db.session.add(cours)
        db.session.commit()

        state_3 = get_school_guidance_state(self.ecole_a.id, self.annee_a.id)
        self.assertTrue(state_3["has_classes"])
        self.assertTrue(state_3["has_cours"])
        self.assertFalse(state_3["has_eleves"])
        self.assertFalse(state_3["setup_complet"])
        self.assertEqual(state_3["current_step"], 3)

        # Inscription d'un élève -> Setup complet
        eleve = Eleve(nom="Zarma", prenom="Amina", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_a.id)
        db.session.add(eleve)
        db.session.flush()

        ins = Inscription(
            eleve_id=eleve.id,
            classe_id=classe.id,
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            statut="inscrit",
        )
        db.session.add(ins)
        db.session.commit()

        state_complete = get_school_guidance_state(self.ecole_a.id, self.annee_a.id)
        self.assertTrue(state_complete["has_classes"])
        self.assertTrue(state_complete["has_cours"])
        self.assertTrue(state_complete["has_eleves"])
        self.assertTrue(state_complete["setup_complet"])
        self.assertEqual(state_complete["current_step"], "complete")

    # -------------------------------------------------------------------------
    # 5. Tests Étape Classes dans le Tunnel d'Onboarding (/onboarding)
    # -------------------------------------------------------------------------

    def test_dashboard_clean_without_guidance_card(self):
        """Vérifie que le dashboard reste épuré sans carte pas-à-pas."""
        self._login(self.admin_a)
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertNotIn("guidance-card", body)
        self.assertNotIn("Étape 1 : Créez vos classes", body)

    def test_injecter_matieres_onboarding_service_multi_class_and_custom_coeffs(self):
        """Vérifie le service injecter_matieres_onboarding : propagation sur plusieurs classes du même niveau et respect des coefficients."""
        # Création de 2 classes de 6ème et 1 classe de 5ème
        c6a = Classe(
            nom="6e A",
            niveau=self.niveau_6e.nom,
            niveau_id=self.niveau_6e.id,
            section="A",
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
        )
        c6b = Classe(
            nom="6e B",
            niveau=self.niveau_6e.nom,
            niveau_id=self.niveau_6e.id,
            section="B",
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
        )
        c5a = Classe(
            nom="5e A",
            niveau=self.niveau_5e.nom,
            niveau_id=self.niveau_5e.id,
            section="A",
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([c6a, c6b, c5a])
        db.session.commit()

        matieres_config = {
            self.niveau_6e.id: [
                {"nom": "Français", "coefficient": 5.0},
                {"nom": "Mathématiques", "coefficient": 4.5},
            ],
            self.niveau_5e.id: [
                {"nom": "Français", "coefficient": 4.0},
                {"nom": "Informatique Avancée", "coefficient": 2.0},
            ],
        }

        cours_crees, err = injecter_matieres_onboarding(self.ecole_a.id, self.annee_a.id, matieres_config)
        self.assertIsNone(err)
        # 6e A reçoit 2 cours, 6e B reçoit 2 cours, 5e A reçoit 2 cours -> total 6
        self.assertEqual(len(cours_crees), 6)

        # Vérification sur 6e A et 6e B
        c6a_cours = {c.nom: c.coefficient for c in Cours.query.filter_by(classe_id=c6a.id).all()}
        c6b_cours = {c.nom: c.coefficient for c in Cours.query.filter_by(classe_id=c6b.id).all()}
        self.assertEqual(c6a_cours["Français"], 5.0)
        self.assertEqual(c6a_cours["Mathématiques"], 4.5)
        self.assertEqual(c6b_cours["Français"], 5.0)
        self.assertEqual(c6b_cours["Mathématiques"], 4.5)

        # Vérification sur 5e A
        c5a_cours = {c.nom: c.coefficient for c in Cours.query.filter_by(classe_id=c5a.id).all()}
        self.assertEqual(c5a_cours["Français"], 4.0)
        self.assertEqual(c5a_cours["Informatique Avancée"], 2.0)

        # Deuxième appel idempotent
        cours_crees_2, err_2 = injecter_matieres_onboarding(self.ecole_a.id, self.annee_a.id, matieres_config)
        self.assertIsNone(err_2)
        self.assertEqual(len(cours_crees_2), 0)

    def test_onboarding_mandatory_pipeline_classes_then_matieres(self):
        """Vérifie le tunnel obligatoire complet : Année -> Semestres -> Niveaux -> Classes -> Matières -> Terminé."""
        from app.services.semestres import configurer_semestres_annee
        from app.utils import get_school_setup_state

        # Nouvelle école
        ecole_new = Ecole(nom="College de l'Avenir", onboarding_complete=False)
        db.session.add(ecole_new)
        db.session.flush()

        admin_new = Utilisateur(
            nom="Directeur",
            email="directeur@avenir.local",
            mot_de_passe="pass123",
            role="admin",
            ecole_id=ecole_new.id,
        )
        db.session.add(admin_new)
        db.session.flush()

        annee_new = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole_new.id,
        )
        db.session.add(annee_new)
        db.session.flush()

        configurer_semestres_annee(ecole_new.id, annee_new.id, date(2027, 1, 31))
        sauvegarder_structure_annee(ecole_new.id, annee_new.id, [self.niveau_6e.id, self.niveau_5e.id])
        db.session.commit()

        self._login(admin_new)

        # 1. Étape Classes
        state_classes = get_school_setup_state(ecole_new.id, force_refresh=True)
        self.assertFalse(state_classes["setup_complete"])
        self.assertEqual(state_classes["current_step"], "classes")

        # GET /onboarding affiche la création des classes et NE CONTIENT PAS de bouton pour ignorer
        res_classes_get = self.client.get("/onboarding")
        self.assertEqual(res_classes_get.status_code, 200)
        body_classes = res_classes_get.get_data(as_text=True)
        self.assertIn("Creation rapide de vos classes", body_classes)
        self.assertNotIn("Ignorer cette etape", body_classes)

        # Tentative prématurée de finalisation -> Échoue
        res_early_fin = self.client.post("/onboarding", data={"action": "finaliser"}, follow_redirects=True)
        self.assertIn("Vous ne pouvez pas finaliser", res_early_fin.get_data(as_text=True))
        db.session.refresh(ecole_new)
        self.assertFalse(ecole_new.onboarding_complete)

        # Soumission de la création des classes : 6e A, 6e B et 5e A
        res_classes_post = self.client.post(
            "/onboarding",
            data={
                "action": "generer_classes_onboarding",
                "classes_selected": [
                    f"{self.niveau_6e.id}:A",
                    f"{self.niveau_6e.id}:B",
                    f"{self.niveau_5e.id}:A",
                ],
            },
            follow_redirects=True,
        )
        self.assertEqual(res_classes_post.status_code, 200)

        # Vérifier que les classes existent
        classes_creees = Classe.query.filter_by(ecole_id=ecole_new.id, annee_scolaire_id=annee_new.id).all()
        self.assertEqual(len(classes_creees), 3)

        # 2. Étape Matières (Obligatoire, immédiate après classes)
        state_matieres = get_school_setup_state(ecole_new.id, force_refresh=True)
        self.assertFalse(state_matieres["setup_complete"])
        self.assertEqual(state_matieres["current_step"], "matieres")

        # GET /onboarding affiche la configuration des matières
        res_matieres_get = self.client.get("/onboarding")
        self.assertEqual(res_matieres_get.status_code, 200)
        body_matieres = res_matieres_get.get_data(as_text=True)
        self.assertIn("Matieres et coefficients", body_matieres)
        self.assertIn("Confirmer le programme", body_matieres)
        self.assertNotIn("Ignorer", body_matieres)

        # Tentative prématurée de finalisation sans matières -> Échoue
        res_early_fin2 = self.client.post("/onboarding", data={"action": "finaliser"}, follow_redirects=True)
        self.assertIn("Vous ne pouvez pas finaliser", res_early_fin2.get_data(as_text=True))
        db.session.refresh(ecole_new)
        self.assertFalse(ecole_new.onboarding_complete)

        # Soumission de l'étape matières
        # Pour la 6ème (clé string niveau.id) : Français (coef 5.0), Mathématiques (coef 4.0)
        # Pour la 5ème (clé string niveau.id) : Français (coef 4.0), plus une matière personnalisée "Arabe" (coef 2.0)
        k6 = str(self.niveau_6e.id)
        k5 = str(self.niveau_5e.id)

        post_matieres_data = {
            "action": "configurer_matieres_onboarding",
            "pack_keys": [k6, k5],
            f"matieres_count_{k6}": "2",
            f"matiere_chk_{k6}_0": "1",
            f"matiere_nom_{k6}_0": "Français",
            f"matiere_coef_{k6}_0": "5.0",
            f"matiere_chk_{k6}_1": "1",
            f"matiere_nom_{k6}_1": "Mathématiques",
            f"matiere_coef_{k6}_1": "4.0",
            f"matieres_count_{k5}": "1",
            f"matiere_chk_{k5}_0": "1",
            f"matiere_nom_{k5}_0": "Français",
            f"matiere_coef_{k5}_0": "4.0",
            f"custom_nom_{k5}[]": ["Arabe"],
            f"custom_coef_{k5}[]": ["2.0"],
        }

        res_matieres_post = self.client.post("/onboarding", data=post_matieres_data, follow_redirects=True)
        self.assertEqual(res_matieres_post.status_code, 200)

        # Vérifier en base : les 2 classes de 6ème (6e A et 6e B) ont bien reçu Français(5.0) et Mathématiques(4.0)
        c6a = Classe.query.filter_by(ecole_id=ecole_new.id, nom="6e A").first()
        c6b = Classe.query.filter_by(ecole_id=ecole_new.id, nom="6e B").first()
        c5a = Classe.query.filter_by(ecole_id=ecole_new.id, nom="5e A").first()

        cours_6a = {c.nom: c.coefficient for c in Cours.query.filter_by(classe_id=c6a.id).all()}
        cours_6b = {c.nom: c.coefficient for c in Cours.query.filter_by(classe_id=c6b.id).all()}
        cours_5a = {c.nom: c.coefficient for c in Cours.query.filter_by(classe_id=c5a.id).all()}

        self.assertEqual(cours_6a["Français"], 5.0)
        self.assertEqual(cours_6a["Mathématiques"], 4.0)
        self.assertEqual(cours_6b["Français"], 5.0)
        self.assertEqual(cours_6b["Mathématiques"], 4.0)
        self.assertEqual(cours_5a["Français"], 4.0)
        self.assertEqual(cours_5a["Arabe"], 2.0)

        # 3. Étape Identité & Documents (Obligatoire, immédiate après matières)
        state_identite = get_school_setup_state(ecole_new.id, force_refresh=True)
        self.assertFalse(state_identite["setup_complete"])
        self.assertEqual(state_identite["current_step"], "identite")

        # L'écran affiche l'étape Identité & Documents et son formulaire
        body_identite = res_matieres_post.get_data(as_text=True)
        self.assertIn("Identité & Documents officiels", body_identite)
        self.assertIn("Valider l'identité et finaliser l'école", body_identite)

        # Tentative prématurée de finalisation sans avoir validé l'identité -> Échoue
        res_early_fin3 = self.client.post("/onboarding", data={"action": "finaliser"}, follow_redirects=True)
        self.assertIn("Vous ne pouvez pas finaliser", res_early_fin3.get_data(as_text=True))
        db.session.refresh(ecole_new)
        self.assertFalse(ecole_new.onboarding_complete)

        # Génération d'une vraie image PNG en mémoire avec Pillow pour tester l'upload sécurisé
        img_bytes = io.BytesIO()
        img = Image.new("RGBA", (80, 80), color=(67, 97, 238, 255))
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        # Soumission de l'étape Identité avec logo, coordonnées et devise/slogan
        post_identite_data = {
            "action": "configurer_identite_onboarding",
            "telephone": "+227 90 00 11 22",
            "ville": "Niamey",
            "adresse": "Quartier Plateau, Rue des Écoles",
            "slogan": "Excellence - Discipline - Succès",
            "logo": (img_bytes, "logo_test.png"),
        }
        res_identite_post = self.client.post(
            "/onboarding",
            data=post_identite_data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res_identite_post.status_code, 200)

        # Vérifier la mise à jour des coordonnées, de la devise et du logo sur l'école
        db.session.refresh(ecole_new)
        self.assertEqual(ecole_new.telephone, "+227 90 00 11 22")
        self.assertEqual(ecole_new.ville, "Niamey")
        self.assertEqual(ecole_new.adresse, "Quartier Plateau, Rue des Écoles")
        self.assertEqual(ecole_new.slogan, "Excellence - Discipline - Succès")
        self.assertEqual(ecole_new.devise, "Excellence - Discipline - Succès")
        self.assertIsNotNone(ecole_new.logo_path)
        self.assertTrue(ecole_new.logo_path.startswith(f"ecoles/{ecole_new.id}/logo_"))

        # 4. Étape Prêt / Complete
        state_complete = get_school_setup_state(ecole_new.id, force_refresh=True)
        self.assertEqual(state_complete["current_step"], "complete")

        # L'écran final propose d'accéder au dashboard
        body_end = res_identite_post.get_data(as_text=True)
        self.assertIn("Configuration terminee", body_end)
        self.assertIn("Acceder a mon tableau de bord", body_end)

        # 5. Finalisation
        res_fin = self.client.post("/onboarding", data={"action": "finaliser"}, follow_redirects=True)
        self.assertEqual(res_fin.status_code, 200)

        db.session.refresh(ecole_new)
        self.assertTrue(ecole_new.onboarding_complete)

        # Après finalisation, /onboarding redirige vers le dashboard
        res_after = self.client.get("/onboarding")
        self.assertEqual(res_after.status_code, 302)
        self.assertIn("/", res_after.location)

    def test_onboarding_identite_step_invalid_logo_rejected(self):
        """Vérifie que la validation Pillow rejette les faux fichiers images lors de l'onboarding."""
        from app.services.semestres import configurer_semestres_annee
        from app.utils import get_school_setup_state

        ecole_err = Ecole(nom="Ecole Test Faux Logo", onboarding_complete=False)
        db.session.add(ecole_err)
        db.session.flush()

        admin_err = Utilisateur(
            nom="DirecteurErr",
            email="directeur.err@test.local",
            mot_de_passe="pass123",
            role="admin",
            ecole_id=ecole_err.id,
        )
        db.session.add(admin_err)
        db.session.flush()

        annee_err = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole_err.id,
        )
        db.session.add(annee_err)
        db.session.flush()

        configurer_semestres_annee(ecole_err.id, annee_err.id, date(2027, 1, 31))
        sauvegarder_structure_annee(ecole_err.id, annee_err.id, [self.niveau_6e.id])
        classe_err = Classe(nom="6e A", niveau="6E", niveau_id=self.niveau_6e.id, annee_scolaire_id=annee_err.id, ecole_id=ecole_err.id)
        db.session.add(classe_err)
        db.session.flush()
        cours_err = Cours(nom="Français", coefficient=4.0, ecole_id=ecole_err.id, classe_id=classe_err.id)
        db.session.add(cours_err)
        db.session.commit()

        self._login(admin_err)
        state = get_school_setup_state(ecole_err.id, force_refresh=True)
        self.assertEqual(state["current_step"], "identite")

        # Faux fichier texte avec extension .png
        fake_file = io.BytesIO(b"CECI N'EST PAS UNE IMAGE VALIDE PILLOW")
        res_post = self.client.post(
            "/onboarding",
            data={
                "action": "configurer_identite_onboarding",
                "telephone": "123456",
                "logo": (fake_file, "hacker.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        body = res_post.get_data(as_text=True)
        # Message d'erreur Pillow affiché (dans le toast HTML)
        self.assertTrue(
            "pas une image valide" in body or
            "Le fichier n&#39;est pas une image valide" in body or
            "Format" in body
        )
        # L'école n'est toujours pas à l'étape complete
        state_after = get_school_setup_state(ecole_err.id, force_refresh=True)
        self.assertEqual(state_after["current_step"], "identite")

    def test_school_information_synchronization_and_security(self):
        """Vérifie la synchronisation automatique des informations école (nom, adresse, ville, téléphone, slogan)
        entre Super-Admin (création/modification), Onboarding (étape 6) et Profil École, avec sécurité multi-tenant."""
        super_admin = Utilisateur(
            nom="Super",
            prenom="Admin",
            email="superadmin@klasora.test",
            mot_de_passe="secret123",
            role="super_admin",
            ecole_id=None,
        )
        db.session.add(super_admin)
        db.session.commit()

        # 1. Super Admin crée une nouvelle école
        self._login(super_admin)
        res_create = self.client.post(
            "/admin/ecoles/ajouter",
            data={
                "nom_ecole": "Institut Supérieur Al-Baraka",
                "adresse": "Boulevard Mali Béro, Face Stade",
                "ville": "Niamey",
                "telephone": "+227 90 11 22 33",
                "email_admin": "admin.baraka@test.local",
                "mot_de_passe": "SecurPass2026!",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_create.status_code, 200)

        ecole = Ecole.query.filter_by(email="admin.baraka@test.local").first()
        self.assertIsNotNone(ecole)
        self.assertEqual(ecole.nom, "Institut Supérieur Al-Baraka")
        self.assertEqual(ecole.adresse, "Boulevard Mali Béro, Face Stade")
        self.assertEqual(ecole.ville, "Niamey")
        self.assertEqual(ecole.telephone, "+227 90 11 22 33")

        admin = Utilisateur.query.filter_by(email="admin.baraka@test.local").first()
        self.assertIsNotNone(admin)
        self.assertEqual(admin.nom, "Institut Supérieur Al-Baraka")

        # 2. L'administrateur de l'école se connecte et met à jour via l'Onboarding (Étape Identité)
        self._login(admin)
        res_identite = self.client.post(
            "/onboarding",
            data={
                "action": "configurer_identite_onboarding",
                "nom": "Complexe Scolaire Privé Al-Baraka",
                "telephone": "+227 96 99 88 77",
                "ville": "Maradi",
                "adresse": "Quartier Zongo, Rue 12",
                "slogan": "Excellence - Savoir - Vertu",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_identite.status_code, 200)

        # Vérification en base de données : Synchronisation parfaite
        db.session.refresh(ecole)
        db.session.refresh(admin)
        self.assertEqual(ecole.nom, "Complexe Scolaire Privé Al-Baraka")
        self.assertEqual(ecole.telephone, "+227 96 99 88 77")
        self.assertEqual(ecole.ville, "Maradi")
        self.assertEqual(ecole.adresse, "Quartier Zongo, Rue 12")
        self.assertEqual(ecole.slogan, "Excellence - Savoir - Vertu")
        self.assertEqual(admin.nom, "Complexe Scolaire Privé Al-Baraka")

        # 3. Super Admin consulte la liste des écoles : voit immédiatement les données synchronisées et le bouton Voir plus
        self._login(super_admin)
        res_list = self.client.get("/admin/ecoles")
        self.assertEqual(res_list.status_code, 200)
        content = res_list.get_data(as_text=True)
        self.assertIn("Complexe Scolaire Privé Al-Baraka", content)
        self.assertIn("Quartier Zongo, Rue 12", content)
        self.assertIn("Maradi", content)
        self.assertIn("+227 96 99 88 77", content)
        self.assertIn("Voir plus", content)
        self.assertIn("modalDetailsEcole", content)

        # Vérification de l'API de détails pour la modale 'Voir plus'
        res_api = self.client.get(f"/api/ecoles/{ecole.id}/details")
        self.assertEqual(res_api.status_code, 200)
        api_data = res_api.get_json()
        self.assertTrue(api_data["success"])
        self.assertEqual(api_data["ecole"]["nom"], "Complexe Scolaire Privé Al-Baraka")
        self.assertEqual(api_data["ecole"]["ville"], "Maradi")
        self.assertEqual(api_data["ecole"]["telephone"], "+227 96 99 88 77")
        self.assertEqual(api_data["ecole"]["slogan"], "Excellence - Savoir - Vertu")

        # 4. Modification via profil école avec protection par longueur maximale (sécurité)
        ecole.onboarding_complete = True
        db.session.commit()
        self._login(admin)
        long_nom = "Ecole " + ("A" * 300)
        res_profil = self.client.post(
            "/profil-ecole",
            data={
                "nom": long_nom,
                "telephone": "+227 90 00 11 22",
                "ville": "Zinder",
                "adresse": "Avenue des Martyrs",
                "slogan": "Travail et Réussite",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_profil.status_code, 200)
        db.session.refresh(ecole)
        # La longueur est tronquée à 200 caractères maximum pour la sécurité
        self.assertEqual(len(ecole.nom), 200)
        self.assertEqual(ecole.ville, "Zinder")
        self.assertEqual(ecole.telephone, "+227 90 00 11 22")


if __name__ == "__main__":
    unittest.main()


