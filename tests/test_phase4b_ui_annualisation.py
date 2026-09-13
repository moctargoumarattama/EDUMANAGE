import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    NiveauScolaire,
    Professeur,
    Utilisateur,
    professeur_classes,
)
from app.services.annees_scolaires import set_annee_consultee
from app.services.niveaux import creer_classe_depuis_niveau, ensure_ecole_niveau_configs
from app.services.structure_annuelle import sauvegarder_structure_annee


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase4BUIConsultationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Ecoles
        self.ecole_a = Ecole(nom="Ecole Alpha")
        self.ecole_b = Ecole(nom="Ecole Beta")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()

        ensure_ecole_niveau_configs(self.ecole_a.id, commit=True)
        ensure_ecole_niveau_configs(self.ecole_b.id, commit=True)

        self.n6 = NiveauScolaire.query.filter_by(code="6E").first()
        self.n5 = NiveauScolaire.query.filter_by(code="5E").first()

        # Annees scolaires pour Ecole A
        self.annee_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_planifiee = AnneeScolaire(
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

        # Annee pour Ecole B
        self.annee_b = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )

        db.session.add_all([self.annee_active, self.annee_planifiee, self.annee_archivee, self.annee_b])
        db.session.commit()

        # Structure annuelle AnneeNiveauConfig
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_active.id, [self.n6.id, self.n5.id])
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_planifiee.id, [self.n6.id])
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_archivee.id, [self.n6.id])
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b.id, [self.n6.id])
        db.session.commit()

        # Classes
        self.classe_active_1, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_active.id, self.n6.id, section="A")
        self.classe_active_2, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_active.id, self.n5.id, section="A")
        self.classe_planifiee_1, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_planifiee.id, self.n6.id, section="A")
        self.classe_archivee_1 = Classe(

            nom="6e A",
            niveau="6e",
            niveau_id=self.n6.id,
            section="A",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_archivee.id,
            statut="fermee"
        )
        db.session.add(self.classe_archivee_1)
        self.classe_b_1, _ = creer_classe_depuis_niveau(self.ecole_b.id, self.annee_b.id, self.n6.id, section="A")
        db.session.commit()


        # Utilisateurs
        self.admin = Utilisateur(nom="Admin A", email="admin.a@test.local", mot_de_passe="secret", role="admin", ecole_id=self.ecole_a.id)
        self.prof_user = Utilisateur(nom="Prof A", email="prof.a@test.local", mot_de_passe="secret", role="professeur", ecole_id=self.ecole_a.id)
        self.prof_b_user = Utilisateur(nom="Prof B", email="prof.b@test.local", mot_de_passe="secret", role="professeur", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin, self.prof_user, self.prof_b_user])
        db.session.commit()

        self.prof = Professeur(nom="Toure", prenom="Amadou", specialite="Maths", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_user.id)
        self.prof_b = Professeur(nom="Kone", prenom="Moussa", specialite="Physique", ecole_id=self.ecole_b.id, utilisateur_id=self.prof_b_user.id)
        db.session.add_all([self.prof, self.prof_b])
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
        return client

    def test_assigner_classes_professeur_annee_active(self):
        client = self.login_as(self.admin)
        # Par défaut, annee_consultee = annee_active
        response = client.get(f"/professeur/{self.prof.id}/assigner_classes")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.annee_active.nom.encode("utf-8"), response.data)
        self.assertIn(b"ACTIVE", response.data)
        self.assertIn(self.classe_active_1.nom.encode("utf-8"), response.data)
        self.assertIn(self.classe_active_2.nom.encode("utf-8"), response.data)
        # Ne doit pas contenir les classes de l'année planifiée ni d'une autre école
        self.assertNotIn(self.classe_b_1.nom.encode("utf-8") + b" - Ecole Beta", response.data)

        # POST affectation dans l'année active
        post_resp = client.post(
            f"/professeur/{self.prof.id}/assigner_classes",
            data={"classes": [str(self.classe_active_1.id)]},
            follow_redirects=True,
        )
        self.assertEqual(post_resp.status_code, 200)
        # Vérification en base
        assigned_ids = [c.id for c in self.prof.classes]
        self.assertIn(self.classe_active_1.id, assigned_ids)
        self.assertNotIn(self.classe_active_2.id, assigned_ids)

    def test_assigner_classes_professeur_annee_planifiee(self):
        client = self.login_as(self.admin)

        # D'abord affecter à l'année active pour tester l'isolation
        client.post(
            f"/professeur/{self.prof.id}/assigner_classes",
            data={"classes": [str(self.classe_active_1.id)]},
        )
        self.assertIn(self.classe_active_1.id, [c.id for c in self.prof.classes])

        # Basculer vers l'année planifiée
        with client.session_transaction() as sess:
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_planifiee.id}

        # GET sur l'année planifiée
        response = client.get(f"/professeur/{self.prof.id}/assigner_classes")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.annee_planifiee.nom.encode("utf-8"), response.data)
        self.assertIn(b"PLANIFI", response.data)
        # Doit afficher la classe planifiée
        self.assertIn(self.classe_planifiee_1.nom.encode("utf-8"), response.data)

        # POST affectation pour l'année planifiée
        post_resp = client.post(
            f"/professeur/{self.prof.id}/assigner_classes",
            data={"classes": [str(self.classe_planifiee_1.id)]},
            follow_redirects=True,
        )
        self.assertEqual(post_resp.status_code, 200)

        # Vérification stricte : l'affectation dans l'année active n'a PAS été écrasée (isolation inter-annuelle)
        assigned_ids = [c.id for c in self.prof.classes]
        self.assertIn(self.classe_active_1.id, assigned_ids)
        self.assertIn(self.classe_planifiee_1.id, assigned_ids)

    def test_assigner_classes_professeur_annee_archivee_lecture_seule(self):
        client = self.login_as(self.admin)
        with client.session_transaction() as sess:
            sess["annee_consultee"] = {str(self.ecole_a.id): self.annee_archivee.id}

        # GET sur l'année archivée : lecture seule
        response = client.get(f"/professeur/{self.prof.id}/assigner_classes")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARCHIV", response.data)
        self.assertIn(b"disabled", response.data)

        # POST sur l'année archivée : bloqué
        post_resp = client.post(
            f"/professeur/{self.prof.id}/assigner_classes",
            data={"classes": [str(self.classe_active_1.id)]},
            follow_redirects=True,
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertIn("archiv".encode("utf-8"), post_resp.data.lower())

    def test_assigner_classes_isolation_ecole(self):
        client = self.login_as(self.admin)
        # Tenter d'accéder au professeur de l'Ecole B -> 404
        response = client.get(f"/professeur/{self.prof_b.id}/assigner_classes")
        self.assertEqual(response.status_code, 404)

    def test_classes_add_empty_structure_state(self):
        client = self.login_as(self.admin)
        # Créer une nouvelle année sans aucune structure
        annee_vierge = AnneeScolaire(
            nom="2027-2028",
            date_debut=date(2027, 9, 1),
            date_fin=date(2028, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(annee_vierge)
        db.session.commit()

        # Consulter cette année vierge
        with client.session_transaction() as sess:
            sess["annee_consultee"] = {str(self.ecole_a.id): annee_vierge.id}

        response = client.get("/classes/add")
        self.assertEqual(response.status_code, 200)
        # Le template doit afficher l'état vide invitant à configurer la structure
        self.assertIn("structure".encode("utf-8"), response.data.lower())
        self.assertIn(f"/annees/{annee_vierge.id}/structure".encode("utf-8"), response.data)

    def test_navbar_and_pages_render_without_errors(self):
        client = self.login_as(self.admin)
        routes_to_test = [
            "/",
            "/annees",
            f"/annees/{self.annee_active.id}/structure",
            "/classes",
            "/classes/add",
            "/eleves",
            "/professeurs",
            "/cours",
            "/notes",
            "/absences",
            "/paiements",
            "/bulletins",
            "/emplois",
            "/qrcodes_etudiants",
            "/alertes",
            "/rapports",
        ]
        for r in routes_to_test:
            res = client.get(r)
            self.assertEqual(res.status_code, 200, f"Route {r} a échoué avec code {res.status_code}")

