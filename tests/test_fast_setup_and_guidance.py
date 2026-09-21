"""Tests unitaires et d'intégration pour l'accélération ergonomique et le guidage de démarrage d'école.

Couvre :
1. La génération par lot de classes (service et route) avec détection et évitement strict des doublons.
2. L'injection des matières et coefficients standard (service et route) avec idempotence.
3. L'étanchéité multi-tenant stricte (refus HTTP 403 en cas d'action sur l'école d'un tiers).
4. Le calcul dynamique de l'état d'avancement (guidance state) et les transitions Étape 1 -> 2 -> 3 -> Complet.
5. Le rendu conditionnel de la carte pas-à-pas sur le tableau de bord (index.html).
"""

from datetime import date
import unittest

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

    def test_onboarding_classes_step_display_and_batch_creation(self):
        """Vérifie l'étape Classes dans le wizard, la création par lot et la finalisation."""
        from app.services.semestres import configurer_semestres_annee
        from app.utils import get_school_setup_state

        # Création d'une nouvelle école en cours d'onboarding
        ecole_new = Ecole(nom="Ecole Pilote Onboarding", onboarding_complete=False)
        db.session.add(ecole_new)
        db.session.flush()

        admin_new = Utilisateur(
            nom="AdminPilote",
            email="pilote@test.local",
            mot_de_passe="x",
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

        # Étape semestres
        configurer_semestres_annee(ecole_new.id, annee_new.id, date(2027, 1, 31))

        # Étape pédagogie (activation de 6E et 5E)
        sauvegarder_structure_annee(ecole_new.id, annee_new.id, [self.niveau_6e.id, self.niveau_5e.id])
        db.session.commit()

        self._login(admin_new)

        # 1. Vérifier que l'état d'onboarding oriente vers l'étape 'classes'
        state = get_school_setup_state(ecole_new.id, force_refresh=True)
        self.assertFalse(state["setup_complete"])
        self.assertEqual(state["current_step"], "classes")

        # 2. Accès GET /onboarding affiche l'écran de création rapide des classes
        res_get = self.client.get("/onboarding")
        self.assertEqual(res_get.status_code, 200)
        body = res_get.get_data(as_text=True)
        self.assertIn("Creation rapide de vos classes", body)
        self.assertIn("Creer les classes et continuer", body)
        self.assertIn("Ignorer cette etape", body)
        self.assertIn(self.niveau_6e.nom, body)
        self.assertIn(self.niveau_5e.nom, body)

        # 3. Soumission POST sans cocher de classe -> Message d'avertissement
        res_empty = self.client.post("/onboarding", data={"action": "generer_classes_onboarding"}, follow_redirects=True)
        self.assertEqual(res_empty.status_code, 200)

        # 4. Soumission POST avec sélection de classes (6e A, 6e B, 5e A)
        res_post = self.client.post(
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
        self.assertEqual(res_post.status_code, 200)

        # Vérifier que les classes ont bien été créées
        classes_creees = Classe.query.filter_by(ecole_id=ecole_new.id, annee_scolaire_id=annee_new.id).all()
        self.assertEqual(len(classes_creees), 3)
        noms = {c.nom for c in classes_creees}
        self.assertIn("6e A", noms)
        self.assertIn("6e B", noms)
        self.assertIn("5e A", noms)

        # Le wizard est maintenant à l'étape 'complete' (Prêt)
        body_after = res_post.get_data(as_text=True)
        self.assertIn("Configuration terminee", body_after)
        self.assertIn("Acceder a mon tableau de bord", body_after)

        # 5. Finalisation de l'onboarding
        res_final = self.client.post("/onboarding", data={"action": "finaliser"}, follow_redirects=True)
        self.assertEqual(res_final.status_code, 200)

        # Vérifier que ecole_new.onboarding_complete est True
        db.session.refresh(ecole_new)
        self.assertTrue(ecole_new.onboarding_complete)

    def test_onboarding_classes_step_skip_action(self):
        """Vérifie que l'administrateur peut ignorer l'étape classes et finaliser sans bloquer."""
        from app.services.semestres import configurer_semestres_annee
        from app.utils import get_school_setup_state

        ecole_skip = Ecole(nom="Ecole Skip Classes", onboarding_complete=False)
        db.session.add(ecole_skip)
        db.session.flush()

        admin_skip = Utilisateur(
            nom="AdminSkip",
            email="skip@test.local",
            mot_de_passe="x",
            role="admin",
            ecole_id=ecole_skip.id,
        )
        db.session.add(admin_skip)
        db.session.flush()

        annee_skip = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole_skip.id,
        )
        db.session.add(annee_skip)
        db.session.flush()

        configurer_semestres_annee(ecole_skip.id, annee_skip.id, date(2027, 1, 31))
        sauvegarder_structure_annee(ecole_skip.id, annee_skip.id, [self.niveau_6e.id])
        db.session.commit()

        self._login(admin_skip)

        # Clic sur "Ignorer cette étape"
        res_skip = self.client.post("/onboarding", data={"action": "ignorer_classes"}, follow_redirects=True)
        self.assertEqual(res_skip.status_code, 200)
        body = res_skip.get_data(as_text=True)
        self.assertIn("Configuration terminee", body)
        self.assertIn("Acceder a mon tableau de bord", body)

        # Aucune classe créée
        self.assertEqual(Classe.query.filter_by(ecole_id=ecole_skip.id).count(), 0)

        # Finalisation réussie
        res_fin = self.client.post("/onboarding", data={"action": "finaliser"}, follow_redirects=True)
        self.assertEqual(res_fin.status_code, 200)
        db.session.refresh(ecole_skip)
        self.assertTrue(ecole_skip.onboarding_complete)


if __name__ == "__main__":
    unittest.main()


