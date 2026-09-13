import unittest
from datetime import date, datetime

from flask import template_rendered

from app import create_app, db
from app.config import Config
from app.models import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
)
from app.services.statistiques_annuelles import (
    get_dashboard_admin_annuel,
    get_professeur_dashboard_annuel,
    get_rapport_absences_par_classe_annuel,
    get_rapport_notes_par_classe_annuel,
    get_rapports_annuels,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase3FRapportsAnnuelsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A", statut="actif")
        self.ecole_b = Ecole(nom="Ecole B", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.archivee = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="archivee", ecole_id=self.ecole_a.id)
        self.active = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.planifiee = AnneeScolaire(nom="2027-2028", date_debut=date(2027, 9, 1), date_fin=date(2028, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        self.annee_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=self.ecole_b.id)
        db.session.add_all([self.archivee, self.active, self.planifiee, self.annee_b])
        db.session.flush()

        self.classe_arch = Classe(nom="6e Archive", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.archivee.id)
        self.classe_act = Classe(nom="5e Active", niveau="5e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id)
        self.classe_plan = Classe(nom="4e Plan", niveau="4e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.planifiee.id)
        self.classe_b = Classe(nom="B Active", niveau="5e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_arch, self.classe_act, self.classe_plan, self.classe_b])
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="admin3f@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.prof_user = Utilisateur(nom="Prof", email="prof3f@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent3f@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Admin B", email="adminb3f@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin, self.prof_user, self.parent, self.admin_b])
        db.session.flush()

        self.professeur = Professeur(nom="Prof", prenom="A", utilisateur_id=self.prof_user.id, ecole_id=self.ecole_a.id)
        db.session.add(self.professeur)
        db.session.flush()

        self.eleve = Eleve(nom="Diallo", prenom="Awa", date_naissance=date(2014, 1, 1), genre="F", ecole_id=self.ecole_a.id, parent_id=self.parent.id, classe_id=None)
        self.eleve_autre_annee = Eleve(nom="Bah", prenom="Moussa", date_naissance=date(2013, 1, 1), genre="M", ecole_id=self.ecole_a.id, parent_id=self.parent.id, classe_id=self.classe_act.id)
        self.eleve_b = Eleve(nom="B", prenom="Ecole", date_naissance=date(2014, 1, 1), ecole_id=self.ecole_b.id, classe_id=self.classe_b.id)
        db.session.add_all([self.eleve, self.eleve_autre_annee, self.eleve_b])
        db.session.flush()

        self.insc_arch = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, classe_id=self.classe_arch.id, annee_scolaire_id=self.archivee.id, frais_annuels=1000)
        self.insc_active = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, classe_id=self.classe_act.id, annee_scolaire_id=self.active.id, frais_annuels=2000)
        self.insc_active_2 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve_autre_annee.id, classe_id=self.classe_act.id, annee_scolaire_id=self.active.id, frais_annuels=2000)
        self.insc_b = Inscription(ecole_id=self.ecole_b.id, eleve_id=self.eleve_b.id, classe_id=self.classe_b.id, annee_scolaire_id=self.annee_b.id, frais_annuels=9999)
        db.session.add_all([self.insc_arch, self.insc_active, self.insc_active_2, self.insc_b])
        db.session.flush()

        self.cours_arch = Cours(nom="Math", ecole_id=self.ecole_a.id, classe_id=self.classe_arch.id, professeur_id=self.professeur.id)
        self.cours_active = Cours(nom="Math", ecole_id=self.ecole_a.id, classe_id=self.classe_act.id, professeur_id=self.professeur.id)
        self.cours_b = Cours(nom="Math", ecole_id=self.ecole_b.id, classe_id=self.classe_b.id)
        db.session.add_all([self.cours_arch, self.cours_active, self.cours_b])
        db.session.flush()

        db.session.add_all([
            Note(valeur=10, coefficient=1, eleve_id=self.eleve.id, cours_id=self.cours_arch.id, ecole_id=self.ecole_a.id, inscription_id=self.insc_arch.id),
            Note(valeur=16, coefficient=1, eleve_id=self.eleve.id, cours_id=self.cours_active.id, ecole_id=self.ecole_a.id, inscription_id=self.insc_active.id),
            Note(valeur=12, coefficient=1, eleve_id=self.eleve_autre_annee.id, cours_id=self.cours_active.id, ecole_id=self.ecole_a.id, inscription_id=self.insc_active_2.id),
            Note(valeur=20, coefficient=1, eleve_id=self.eleve_b.id, cours_id=self.cours_b.id, ecole_id=self.ecole_b.id, inscription_id=self.insc_b.id),
            Absence(date_absence=date(2026, 10, 1), eleve_id=self.eleve.id, cours_id=self.cours_active.id, ecole_id=self.ecole_a.id, inscription_id=self.insc_active.id, justifiee=False),
            Absence(date_absence=date(2025, 10, 1), eleve_id=self.eleve.id, cours_id=self.cours_arch.id, ecole_id=self.ecole_a.id, inscription_id=self.insc_arch.id, justifiee=True),
            Absence(date_absence=date(2026, 10, 1), eleve_id=self.eleve_b.id, cours_id=self.cours_b.id, ecole_id=self.ecole_b.id, inscription_id=self.insc_b.id, justifiee=False),
            Paiement(montant=100, mois="Octobre", annee=2026, eleve_id=self.eleve.id, ecole_id=self.ecole_a.id, inscription_id=self.insc_active.id, statut="en attente"),
            Paiement(montant=100, mois="Octobre", annee=2025, eleve_id=self.eleve.id, ecole_id=self.ecole_a.id, inscription_id=self.insc_arch.id, statut="en attente"),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _client_as(self, user, annee=None):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["role"] = user.role
            session["ecole_id"] = user.ecole_id
            if annee:
                session["annee_consultee"] = {str(user.ecole_id): annee.id}
        return client

    def _capture_templates(self):
        recorded = []

        def record(_sender, template, context, **_extra):
            recorded.append((template, context))

        template_rendered.connect(record, self.app)
        self.addCleanup(lambda: template_rendered.disconnect(record, self.app))
        return recorded

    def test_01_rapports_service_filtre_annee_active(self):
        data = get_rapports_annuels(self.ecole_a.id, self.active)
        self.assertEqual(data["statistiques"]["total_eleves"], 2)
        self.assertEqual(data["statistiques"]["total_absences"], 1)
        self.assertEqual(data["statistiques"]["total_classes"], 1)
        self.assertEqual(data["classes_data"][0]["moyenne"], 14)

    def test_02_rapports_service_archive_ne_melange_pas_active(self):
        data = get_rapports_annuels(self.ecole_a.id, self.archivee)
        self.assertEqual(data["statistiques"]["total_eleves"], 1)
        self.assertEqual(data["statistiques"]["total_absences"], 1)
        self.assertEqual(data["classes_data"][0]["moyenne"], 10)

    def test_03_planifiee_sans_donnees_retourne_zero(self):
        data = get_rapports_annuels(self.ecole_a.id, self.planifiee)
        self.assertEqual(data["statistiques"]["total_eleves"], 0)
        self.assertEqual(data["statistiques"]["total_absences"], 0)
        self.assertEqual(data["statistiques"]["total_classes"], 1)

    def test_04_multi_ecole_isolee(self):
        data = get_rapports_annuels(self.ecole_a.id, self.active)
        self.assertNotIn("B Active", data["chart_data"]["labels"])
        self.assertEqual(data["statistiques"]["total_eleves"], 2)

    def test_05_admin_dashboard_annuel(self):
        stats = get_dashboard_admin_annuel(self.ecole_a.id, self.active)
        self.assertEqual(stats["total_eleves"], 2)
        self.assertEqual(stats["total_cours"], 1)
        self.assertEqual(stats["paiements_attente"], 1)

    def test_06_professeur_dashboard_annuel(self):
        data = get_professeur_dashboard_annuel(self.ecole_a.id, self.active, self.professeur.id)
        self.assertEqual(data["stats"]["total_cours"], 1)
        self.assertEqual(data["stats"]["total_eleves"], 2)
        self.assertAlmostEqual(float(data["stats"]["moyenne_generale"]), 14)

    def test_07_json_notes_par_classe_active(self):
        client = self._client_as(self.admin, self.active)
        response = client.get("/rapport/notes_par_classe")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"5e Active": 14})

    def test_08_json_absences_par_classe_active(self):
        client = self._client_as(self.admin, self.active)
        response = client.get("/rapport/absences_par_classe")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"5e Active": 1})

    def test_09_api_notes_moyennes_active(self):
        client = self._client_as(self.admin, self.active)
        data = client.get("/api/stats/notes_moyennes").get_json()
        self.assertEqual(data["matieres"], ["Math"])
        self.assertEqual(data["moyennes"], [14.0])

    def test_10_api_absences_par_mois_active(self):
        client = self._client_as(self.admin, self.active)
        data = client.get("/api/stats/absences_par_mois").get_json()
        self.assertEqual(data["mois"], ["10/2026"])
        self.assertEqual(data["absences"], [1])

    def test_11_route_rapports_transmet_context_annuel(self):
        recorded = self._capture_templates()
        client = self._client_as(self.admin, self.active)
        response = client.get("/rapports")
        self.assertEqual(response.status_code, 200)
        context = recorded[-1][1]
        self.assertEqual(context["annee_consultee"].id, self.active.id)
        self.assertEqual(context["statistiques"]["total_eleves"], 2)

    def test_12_consulter_archive_ne_change_pas_statut(self):
        client = self._client_as(self.admin, self.archivee)
        self.assertEqual(client.get("/rapports").status_code, 200)
        self.assertEqual(AnneeScolaire.query.get(self.archivee.id).statut, "archivee")

    def test_13_parent_dashboard_utilise_inscription_annee(self):
        recorded = self._capture_templates()
        client = self._client_as(self.parent, self.active)
        response = client.get("/parent/dashboard")
        self.assertEqual(response.status_code, 200)
        enfants = recorded[-1][1]["enfants"]
        self.assertEqual(len(enfants), 2)
        self.assertEqual({e.total_absences for e in enfants}, {0, 1})

    def test_14_professeur_dashboard_ne_lit_pas_ancienne_annee(self):
        recorded = self._capture_templates()
        client = self._client_as(self.prof_user, self.active)
        response = client.get("/professeur/dashboard")
        self.assertEqual(response.status_code, 200)
        context = recorded[-1][1]
        self.assertEqual(context["stats"]["total_cours"], 1)
        self.assertEqual(
            {note.inscription_id for note in context["dernieres_notes"]},
            {self.insc_active.id, self.insc_active_2.id},
        )

    def test_15_helpers_json_directs(self):
        self.assertEqual(get_rapport_notes_par_classe_annuel(self.ecole_a.id, self.archivee), {"6e Archive": 10})
        self.assertEqual(get_rapport_absences_par_classe_annuel(self.ecole_a.id, self.archivee), {"6e Archive": 1})


if __name__ == "__main__":
    unittest.main()
