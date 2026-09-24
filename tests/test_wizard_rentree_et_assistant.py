import datetime as dt
import unittest

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    PeriodeBulletin,
    Utilisateur,
)
from app.routes.assistant import _fast_detect_intent_and_entities
from app.services.annees_scolaires import set_annee_consultee
from app.services.duplication_structure import dupliquer_structure_annee
from werkzeug.security import generate_password_hash


class TestWizardRentreeEtAssistant(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Nettoyage et initialisation d'une école de test
        self.ecole = Ecole.query.filter_by(nom="Ecole Wizard Test").first()
        if not self.ecole:
            self.ecole = Ecole(
                nom="Ecole Wizard Test",
                adresse="123 Rue de la Rentrée",
                telephone="+221770001122",
                email="contact@wizard.test",
                onboarding_complete=True
            )
            db.session.add(self.ecole)
            db.session.commit()
        else:
            self.ecole.onboarding_complete = True
            db.session.commit()

        # Admin
        self.admin = Utilisateur.query.filter_by(email="admin.wizard@test.com").first()
        if not self.admin:
            self.admin = Utilisateur(
                nom="Admin",
                prenom="Wizard",
                email="admin.wizard@test.com",
                mot_de_passe=generate_password_hash("Admin1234!"),
                role="admin",
                ecole_id=self.ecole.id,
                statut="actif"
            )
            db.session.add(self.admin)
            db.session.commit()

        # Année active source (ex: 2024-2025)
        self.annee_active = AnneeScolaire.query.filter_by(
            ecole_id=self.ecole.id,
            nom="2024-2025"
        ).first()
        if not self.annee_active:
            self.annee_active = AnneeScolaire(
                nom="2024-2025",
                date_debut=dt.date(2024, 10, 1),
                date_fin=dt.date(2025, 6, 30),
                statut="active",
                ecole_id=self.ecole.id
            )
            db.session.add(self.annee_active)
            db.session.commit()

        # Année planifiée cible (ex: 2025-2026)
        self.annee_cible = AnneeScolaire.query.filter_by(
            ecole_id=self.ecole.id,
            nom="2025-2026"
        ).first()
        if not self.annee_cible:
            self.annee_cible = AnneeScolaire(
                nom="2025-2026",
                date_debut=dt.date(2025, 10, 1),
                date_fin=dt.date(2026, 6, 30),
                statut="planifiee",
                ecole_id=self.ecole.id
            )
            db.session.add(self.annee_cible)
            db.session.commit()

        # Nettoyage préalable des données cible pour isolation des tests
        Inscription.query.filter_by(ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id).delete()
        Classe.query.filter_by(ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id).delete()
        PeriodeBulletin.query.filter_by(ecole_id=self.ecole.id, annee_id=self.annee_cible.id).delete()
        AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id).delete()
        db.session.commit()

        # Niveau et classe source
        self.niveau = NiveauScolaire.query.filter_by(nom="6ème Wizard").first()
        if not self.niveau:
            self.niveau = NiveauScolaire(
                nom="6ème Wizard",
                code="6WIZ",
                cycle="college",
                ordre=60
            )
            db.session.add(self.niveau)
            db.session.commit()

        # Activation du niveau sur la source
        cfg_src = AnneeNiveauConfig.query.filter_by(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id,
            niveau_id=self.niveau.id
        ).first()
        if not cfg_src:
            cfg_src = AnneeNiveauConfig(
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee_active.id,
                niveau_id=self.niveau.id,
                actif=True
            )
            db.session.add(cfg_src)
            db.session.commit()

        self.classe_source = Classe.query.filter_by(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id,
            nom="6ème A"
        ).first()
        if not self.classe_source:
            self.classe_source = Classe(
                nom="6ème A",
                niveau="6ème",
                niveau_id=self.niveau.id,
                capacite=30,
                statut="ouverte",
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee_active.id
            )
            db.session.add(self.classe_source)
            db.session.commit()

        # Périodes pour l'année source
        p1 = PeriodeBulletin.query.filter_by(ecole_id=self.ecole.id, annee_id=self.annee_active.id, nom="Semestre 1").first()
        if not p1:
            p1 = PeriodeBulletin(
                nom="Semestre 1",
                ecole_id=self.ecole.id,
                annee_id=self.annee_active.id,
                date_debut=dt.date(2024, 10, 1),
                date_fin=dt.date(2025, 1, 31)
            )
            db.session.add(p1)
        p2 = PeriodeBulletin.query.filter_by(ecole_id=self.ecole.id, annee_id=self.annee_active.id, nom="Semestre 2").first()
        if not p2:
            p2 = PeriodeBulletin(
                nom="Semestre 2",
                ecole_id=self.ecole.id,
                annee_id=self.annee_active.id,
                date_debut=dt.date(2025, 2, 1),
                date_fin=dt.date(2025, 6, 30)
            )
            db.session.add(p2)
        db.session.commit()

        # Élèves dans l'année active
        self.eleve1 = Eleve.query.filter_by(nom="Diop", prenom="Amina", ecole_id=self.ecole.id).first()
        if not self.eleve1:
            self.eleve1 = Eleve(
                nom="Diop",
                prenom="Amina",
                date_naissance=dt.date(2013, 3, 10),
                genre="F",
                ecole_id=self.ecole.id,
                contact_parent="+221771112233",
                statut="actif"
            )
            db.session.add(self.eleve1)
            db.session.commit()

        self.eleve2 = Eleve.query.filter_by(nom="Fall", prenom="Cheikh", ecole_id=self.ecole.id).first()
        if not self.eleve2:
            self.eleve2 = Eleve(
                nom="Fall",
                prenom="Cheikh",
                date_naissance=dt.date(2013, 7, 22),
                genre="M",
                ecole_id=self.ecole.id,
                contact_parent="+221774445566",
                statut="actif"
            )
            db.session.add(self.eleve2)
            db.session.commit()

        # Inscriptions dans l'année source
        insc1 = Inscription.query.filter_by(
            eleve_id=self.eleve1.id,
            annee_scolaire_id=self.annee_active.id,
            ecole_id=self.ecole.id
        ).first()
        if not insc1:
            insc1 = Inscription(
                eleve_id=self.eleve1.id,
                classe_id=self.classe_source.id,
                annee_scolaire_id=self.annee_active.id,
                ecole_id=self.ecole.id,
                statut="inscrit"
            )
            db.session.add(insc1)

        insc2 = Inscription.query.filter_by(
            eleve_id=self.eleve2.id,
            annee_scolaire_id=self.annee_active.id,
            ecole_id=self.ecole.id
        ).first()
        if not insc2:
            insc2 = Inscription(
                eleve_id=self.eleve2.id,
                classe_id=self.classe_source.id,
                annee_scolaire_id=self.annee_active.id,
                ecole_id=self.ecole.id,
                statut="inscrit"
            )
            db.session.add(insc2)
        db.session.commit()

    def tearDown(self):
        self.app_context.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["user_id"] = self.admin.id
            sess["_fresh"] = True
            sess["ecole_id"] = self.ecole.id
            sess["annee_active_id"] = self.annee_active.id
            sess["annee_consultee_id"] = self.annee_active.id
            sess["onboarding_complete"] = True
            sess[f"onboarding_complete_{self.ecole.id}"] = True

    def test_01_fast_detect_rentree_intents(self):
        """Vérifie la détection ultra-rapide des intentions statut_rentree et relance_reinscriptions."""
        # 1. statut_rentree
        res1 = _fast_detect_intent_and_entities("Où en est la rentrée ?")
        self.assertIsNotNone(res1)
        self.assertEqual(res1.get("intention"), "statut_rentree")

        res2 = _fast_detect_intent_and_entities("Avancement de la préparation de la rentrée")
        self.assertIsNotNone(res2)
        self.assertEqual(res2.get("intention"), "statut_rentree")

        res3 = _fast_detect_intent_and_entities("Statut de la rentrée 2025")
        self.assertIsNotNone(res3)
        self.assertEqual(res3.get("intention"), "statut_rentree")

        # 2. relance_reinscriptions
        res4 = _fast_detect_intent_and_entities("Qui n'a pas payé sa réinscription ?")
        self.assertIsNotNone(res4)
        self.assertEqual(res4.get("intention"), "relance_reinscriptions")

        res5 = _fast_detect_intent_and_entities("Liste des préinscrits en attente d'acompte")
        self.assertIsNotNone(res5)
        self.assertEqual(res5.get("intention"), "relance_reinscriptions")

        res6 = _fast_detect_intent_and_entities("élèves non confirmés")
        self.assertIsNotNone(res6)
        self.assertEqual(res6.get("intention"), "relance_reinscriptions")

    def test_02_wizard_view_rendering_and_stepper(self):
        """Vérifie que la page du Wizard s'affiche avec le stepper et les 4 cartes d'action."""
        self._login_admin()
        resp = self.client.get(f"/annees/{self.annee_cible.id}/onboarding_rentree")
        self.assertEqual(resp.status_code, 200)
        content = resp.get_data(as_text=True)

        # Vérifications de structure du template
        self.assertIn("Parcours Rentrée Scolaire", content)
        self.assertIn(self.annee_cible.nom, content)
        self.assertIn("1. Structure", content)
        self.assertIn("2. Décisions", content)
        self.assertIn("3. Finances", content)
        self.assertIn("4. Lancement", content)
        self.assertIn("Étape 1 : Structure", content)
        self.assertIn("Périodes", content)
        self.assertIn("Étape 2 : Décisions du Conseil", content)
        self.assertIn("Étape 3 : Pointage Financier", content)
        self.assertIn("Étape 4 : Lancement Officiel", content)

    def test_03_wizard_integration_with_duplication_and_preinscriptions(self):
        """Vérifie le recalcul dynamique des étapes 1, 2, 3 dans le Wizard."""
        self._login_admin()

        # Étape 1 : Duplication de structure en 1 clic
        ok, msg = dupliquer_structure_annee(
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id
        )
        self.assertTrue(ok)

        # Création d'une inscription préinscrite dans l'année cible
        classe_cible = Classe.query.filter_by(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            nom="6ème A"
        ).first()
        self.assertIsNotNone(classe_cible)

        insc_pre = Inscription(
            eleve_id=self.eleve1.id,
            classe_id=classe_cible.id,
            annee_scolaire_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
            statut="preinscrit"
        )
        db.session.add(insc_pre)
        db.session.commit()

        # Consultation du Wizard
        resp = self.client.get(f"/annees/{self.annee_cible.id}/onboarding_rentree")
        self.assertEqual(resp.status_code, 200)
        content = resp.get_data(as_text=True)

        # Étape 1 doit être validée (classes et périodes créées)
        self.assertIn("Classes ouvertes", content)
        # Étape 3 doit identifier l'élève préinscrit en attente d'acompte
        self.assertIn("Consulter les 1 élève(s) en attente d'acompte", content)
        self.assertIn("Diop", content)
        self.assertIn("Amina", content)
        self.assertIn("+221771112233", content)

    def test_04_assistant_api_statut_rentree(self):
        """Vérifie la réponse Fast-Path de l'API Assistant pour la question de statut de rentrée."""
        self._login_admin()

        resp = self.client.post("/api/assistant/query-data", json={
            "question": "Où en est la rentrée scolaire ?"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("intention"), "statut_rentree")
        reply = data.get("reply", "")
        self.assertIn(f"Rentrée {self.annee_cible.nom}", reply)
        self.assertIn("Structure pédagogique", reply)
        self.assertIn(f"/annees/{self.annee_cible.id}/onboarding_rentree", reply)

    def test_05_assistant_api_relance_reinscriptions(self):
        """Vérifie la réponse Fast-Path de l'API Assistant pour la relance des réinscriptions/acomptes."""
        self._login_admin()

        # 1. Cas sans aucun préinscrit
        resp = self.client.post("/api/assistant/query-data", json={
            "question": "Qui n'a pas encore payé sa réinscription ?"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("intention"), "relance_reinscriptions")
        self.assertIn("Tous les élèves sont à jour", data.get("reply", ""))

        # 2. Cas avec un élève préinscrit en attente d'acompte
        cl = Classe.query.filter_by(ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id).first()
        if not cl:
            cl = Classe(
                nom="6ème A",
                niveau="6ème",
                niveau_id=self.niveau.id,
                capacite=30,
                statut="ouverte",
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee_cible.id
            )
            db.session.add(cl)
            db.session.commit()

        insc_pre = Inscription(
            eleve_id=self.eleve1.id,
            classe_id=cl.id,
            annee_scolaire_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
            statut="preinscrit"
        )
        db.session.add(insc_pre)
        db.session.commit()

        resp2 = self.client.post("/api/assistant/query-data", json={
            "question": "Qui n'a pas payé sa réinscription ?"
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertTrue(data2.get("success"))
        self.assertEqual(data2.get("intention"), "relance_reinscriptions")
        reply2 = data2.get("reply", "")
        self.assertIn("Amina Diop", reply2)
        self.assertIn(f"/annees/{self.annee_cible.id}/onboarding_rentree", reply2)

    def test_06_dashboard_rentree_banner(self):
        """Vérifie que la bannière d'avancement de la rentrée planifiée s'affiche sur l'accueil."""
        self._login_admin()

        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        content = resp.get_data(as_text=True)

        self.assertIn(f"Préparation de la rentrée {self.annee_cible.nom} en cours", content)
        self.assertIn(f"/annees/{self.annee_cible.id}/onboarding_rentree", content)
        self.assertIn("Continuer la préparation", content)

    def test_07_etape3_classe_par_classe_decisions_et_annulation(self):
        """Vérifie l'examen classe par classe, la validation par lot et l'annulation unitaire dans l'Étape 3."""
        self._login_admin()

        # 1. Vérifie l'affichage de l'Étape 3 avec la classe active
        resp = self.client.get(f"/annees/{self.annee_cible.id}/onboarding_rentree?step=3&classe_source_id={self.classe_source.id}")
        self.assertEqual(resp.status_code, 200)
        content = resp.get_data(as_text=True)
        self.assertIn("Revue des classes", content)
        self.assertIn(f"Examen du conseil de classe :", content)
        self.assertIn("Pré-cocher Admis / Redoublants", content)
        self.assertIn("Valider les décisions pour", content)
        self.assertIn(self.eleve1.nom, content)

        # 2. Configuration du niveau suivant et validation du passage
        niv_5 = NiveauScolaire.query.filter_by(nom="5ème Wizard").first()
        if not niv_5:
            niv_5 = NiveauScolaire(nom="5ème Wizard", code="5WIZ", cycle="college", ordre=70)
            db.session.add(niv_5)
            db.session.commit()
        self.niveau.niveau_suivant_id = niv_5.id

        cfg_5 = AnneeNiveauConfig.query.filter_by(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=niv_5.id
        ).first()
        if not cfg_5:
            cfg_5 = AnneeNiveauConfig(
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee_cible.id,
                niveau_id=niv_5.id,
                actif=True
            )
            db.session.add(cfg_5)

        cl_cible = Classe.query.filter_by(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            nom="5ème A"
        ).first()
        if not cl_cible:
            cl_cible = Classe(
                nom="5ème A",
                niveau="5ème",
                niveau_id=niv_5.id,
                capacite=30,
                statut="ouverte",
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee_cible.id
            )
            db.session.add(cl_cible)
        db.session.commit()

        post_data = {
            "action": "valider_decisions_classe",
            "classe_source_id": str(self.classe_source.id),
            f"decision_{self.eleve1.id}": "passage",
            f"classe_cible_{self.eleve1.id}": str(cl_cible.id),
        }
        resp_post = self.client.post(
            f"/annees/{self.annee_cible.id}/onboarding_rentree?step=3",
            data=post_data,
            follow_redirects=True
        )
        self.assertEqual(resp_post.status_code, 200)
        post_content = resp_post.get_data(as_text=True)
        self.assertIn("décision(s) enregistrée(s) avec succès", post_content)

        # Vérification en base : l'élève est inscrit dans l'année cible
        insc_cible = Inscription.query.filter_by(
            eleve_id=self.eleve1.id,
            annee_scolaire_id=self.annee_cible.id
        ).first()
        self.assertIsNotNone(insc_cible)
        self.assertEqual(insc_cible.classe_id, cl_cible.id)

        # 3. Test de l'annulation unitaire (droit à l'erreur)
        annul_data = {
            "action": "annuler_decision_eleve",
            "eleve_id": str(self.eleve1.id),
            "classe_source_id": str(self.classe_source.id),
        }
        resp_annul = self.client.post(
            f"/annees/{self.annee_cible.id}/onboarding_rentree?step=3",
            data=annul_data,
            follow_redirects=True
        )
        self.assertEqual(resp_annul.status_code, 200)
        annul_content = resp_annul.get_data(as_text=True)
        self.assertIn("Décision annulée pour cet élève", annul_content)

        # Vérification en base : l'inscription cible a été supprimée
        insc_apres = Inscription.query.filter_by(
            eleve_id=self.eleve1.id,
            annee_scolaire_id=self.annee_cible.id
        ).first()
        self.assertIsNone(insc_apres)

    def test_ouvrir_rentree_automatique_et_garde_fou(self):
        """Vérifie l'ouverture automatique en 1 clic de la rentrée suivante et le garde-fou 1 seule rentrée."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.admin.id
            sess["_user_id"] = str(self.admin.id)
            sess["role"] = "admin"
            sess["ecole_id"] = self.ecole.id

        # 1. Supprimer l'annee cible existante pour tester l'ouverture depuis l'année active
        if self.annee_cible:
            Inscription.query.filter_by(annee_scolaire_id=self.annee_cible.id).delete()
            Classe.query.filter_by(annee_scolaire_id=self.annee_cible.id).delete()
            db.session.delete(self.annee_cible)
            db.session.commit()

        # 2. Ouvrir la rentrée suivante en 1 clic
        resp = self.client.post(
            "/annees",
            data={"action": "ouvrir_rentree", "ecole_id": self.ecole.id},
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("créée avec succès", resp.get_data(as_text=True))

        # Vérifier en base
        annee_creee = AnneeScolaire.query.filter_by(ecole_id=self.ecole.id, statut="planifiee").first()
        self.assertIsNotNone(annee_creee)
        self.assertEqual(annee_creee.nom, "2025-2026")
        self.assertEqual(annee_creee.statut, "planifiee")

        # 3. Garde-fou : tenter d'ouvrir à nouveau une rentrée alors qu'une est déjà planifiée
        resp_bloque = self.client.post(
            "/annees",
            data={"action": "ouvrir_rentree", "ecole_id": self.ecole.id},
            follow_redirects=True
        )
        self.assertEqual(resp_bloque.status_code, 200)
        self.assertIn("déjà en cours de préparation", resp_bloque.get_data(as_text=True))

        # 4. Annuler la rentrée planifiée
        resp_suppr = self.client.post(
            f"/annees/{annee_creee.id}/supprimer",
            follow_redirects=True
        )
        self.assertEqual(resp_suppr.status_code, 200)
        self.assertIn("a été annulée avec succès", resp_suppr.get_data(as_text=True))
        self.assertIsNone(db.session.get(AnneeScolaire, annee_creee.id))


if __name__ == "__main__":
    unittest.main()

