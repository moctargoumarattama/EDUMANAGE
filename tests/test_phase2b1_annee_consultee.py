import unittest
from datetime import date

from sqlalchemy import event

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Utilisateur
from flask import template_rendered
from app.services.annees_scolaires import (
    get_annee_active,
    get_annee_consultee,
    get_classes_annee,
    get_eleves_annee_query,
    set_annee_consultee,
)
from app.services.inscriptions_annuelles import get_parcours_eleve


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2B1AnneeConsulteeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.annee_archivee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        self.annee_active = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_vide = AnneeScolaire(
            nom="2027-2028",
            date_debut=date(2027, 9, 1),
            date_fin=date(2028, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_archivee, self.annee_active, self.annee_vide, self.annee_b])
        db.session.flush()

        self.classe_6a = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_archivee.id)
        self.classe_5a = Classe(nom="5e A", niveau="5e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_active.id)
        self.classe_5b = Classe(nom="5e B", niveau="5e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_active.id)
        self.classe_b = Classe(nom="5e X", niveau="5e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_6a, self.classe_5a, self.classe_5b, self.classe_b])
        db.session.flush()

        self.eleve = Eleve(
            nom="Moussa",
            prenom="Abdou",
            date_naissance=date(2014, 2, 3),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_5a.id,
        )
        self.eleve_b = Eleve(
            nom="Awa",
            prenom="B",
            date_naissance=date(2014, 5, 6),
            ecole_id=self.ecole_b.id,
            classe_id=self.classe_b.id,
        )
        self.admin = Utilisateur(nom="Admin", email="admin2b1@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        db.session.add_all([self.eleve, self.eleve_b, self.admin])
        db.session.flush()

        db.session.add_all([
            Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.annee_archivee.id, classe_id=self.classe_6a.id, statut="inscrit"),
            Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.annee_active.id, classe_id=self.classe_5a.id, statut="inscrit"),
            Inscription(ecole_id=self.ecole_b.id, eleve_id=self.eleve_b.id, annee_scolaire_id=self.annee_b.id, classe_id=self.classe_b.id, statut="inscrit"),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        return client

    def _capture_templates(self):
        recorded = []

        def record(_sender, template, context, **_extra):
            recorded.append((template, context))

        template_rendered.connect(record, self.app)
        return recorded, record

    def test_moteur_annee_consultee_fallback_session_et_multi_ecoles(self):
        with self.app.test_request_context("/"):
            self.assertEqual(get_annee_active(self.ecole_a.id).id, self.annee_active.id)
            self.assertEqual(get_annee_consultee(self.ecole_a.id).id, self.annee_active.id)

            archivee = get_annee_consultee(self.ecole_a.id, self.annee_archivee.id)
            self.assertEqual(archivee.id, self.annee_archivee.id)
            self.assertEqual(db.session.get(AnneeScolaire, self.annee_archivee.id).statut, "archivee")
            self.assertEqual(get_annee_consultee(self.ecole_a.id).id, self.annee_active.id)

            self.assertIsNone(set_annee_consultee(self.ecole_a.id, self.annee_b.id))
            self.assertEqual(get_annee_consultee(self.ecole_b.id).id, self.annee_b.id)

    def test_classes_par_annee_et_url_sans_annee(self):
        self.assertEqual([c.nom for c in get_classes_annee(self.ecole_a.id, self.annee_archivee.id).all()], ["6e A"])
        self.assertEqual(
            [c.nom for c in get_classes_annee(self.ecole_a.id, self.annee_active.id).order_by(Classe.nom).all()],
            ["5e A", "5e B"],
        )

        client = self._client_as_admin()
        self.assertEqual(client.get("/classes").status_code, 200)
        self.assertEqual(client.get(f"/classesannee_id={self.annee_archivee.id}").status_code, 200)
        self.assertEqual(client.get("/classesannee_id=999999").status_code, 200)

    def test_classes_template_recoit_annee_consultee_et_annees_ecole(self):
        client = self._client_as_admin()
        recorded, receiver = self._capture_templates()
        try:
            response = client.get("/classes")
            self.assertEqual(response.status_code, 200)
            context = recorded[-1][1]
            self.assertEqual(context["annee_consultee"].id, self.annee_active.id)

            response = client.get(f"/classesannee_id={self.annee_archivee.id}")
            self.assertEqual(response.status_code, 200)
            context = recorded[-1][1]
            self.assertEqual(context["annee_consultee"].id, self.annee_active.id)
            self.assertEqual(db.session.get(AnneeScolaire, self.annee_archivee.id).statut, "archivee")

            with client.session_transaction() as session:
                session["annee_consultee"] = {str(self.ecole_a.id): self.annee_archivee.id}
            response = client.get("/classes")
            self.assertEqual(response.status_code, 200)
            context = recorded[-1][1]
            self.assertEqual(context["annee_consultee"].id, self.annee_archivee.id)
        finally:
            template_rendered.disconnect(receiver, self.app)

    def test_eleves_annuels_filtre_historique_pagination_et_cache_classe(self):
        eleves_2025 = get_eleves_annee_query(self.ecole_a.id, self.annee_archivee.id).all()
        eleves_2026 = get_eleves_annee_query(self.ecole_a.id, self.annee_active.id).all()

        self.assertEqual([e.id for e in eleves_2025], [self.eleve.id])
        self.assertEqual([e.id for e in eleves_2026], [self.eleve.id])
        self.assertEqual(db.session.get(Eleve, self.eleve.id).classe_id, self.classe_5a.id)

        historique = (
            get_eleves_annee_query(self.ecole_a.id, self.annee_archivee.id)
            .filter(Inscription.classe_id == self.classe_6a.id)
            .all()
        )
        self.assertEqual([e.id for e in historique], [self.eleve.id])

        client = self._client_as_admin()
        self.assertEqual(client.get(f"/elevesannee_id={self.annee_archivee.id}&classe_id={self.classe_6a.id}&page=1").status_code, 200)

    def test_eleves_template_recoit_annee_consultee_annees_ecole_et_annee_vide(self):
        client = self._client_as_admin()
        recorded, receiver = self._capture_templates()
        try:
            response = client.get("/eleves")
            self.assertEqual(response.status_code, 200)
            context = recorded[-1][1]
            self.assertEqual(context["annee_consultee"].id, self.annee_active.id)
            self.assertEqual({a.id for a in context["annees_ecole"]}, {self.annee_active.id, self.annee_archivee.id, self.annee_vide.id})
            self.assertNotIn(self.annee_b.id, {a.id for a in context["annees_ecole"]})
            self.assertEqual(context["total_eleves"], 1)

            response = client.get(f"/elevesannee_id={self.annee_archivee.id}&search=Moussa")
            self.assertEqual(response.status_code, 200)
            context = recorded[-1][1]
            self.assertEqual(context["annee_consultee"].id, self.annee_active.id)
            self.assertEqual(context["total_eleves"], 1)
            self.assertEqual(db.session.get(AnneeScolaire, self.annee_archivee.id).statut, "archivee")

            with client.session_transaction() as session:
                session["annee_consultee"] = {str(self.ecole_a.id): self.annee_vide.id}
            response = client.get("/eleves")
            self.assertEqual(response.status_code, 200)
            context = recorded[-1][1]
            self.assertEqual(context["annee_consultee"].id, self.annee_vide.id)
            self.assertEqual(context["total_eleves"], 0)
            self.assertEqual(list(context["eleves"].items), [])
        finally:
            template_rendered.disconnect(receiver, self.app)

    def test_parcours_deux_annees_et_ecole_b_inaccessible(self):
        parcours = get_parcours_eleve(self.eleve)
        self.assertEqual([p.classe_id for p in parcours], [self.classe_6a.id, self.classe_5a.id])
        self.assertEqual(get_classes_annee(self.ecole_a.id, self.annee_b.id).count(), 0)
        self.assertEqual(get_eleves_annee_query(self.ecole_a.id, self.annee_b.id).count(), 0)

    def test_archive_bloque_creation_modification_suppression_et_changement_historique(self):
        client = self._client_as_admin()
        with client.session_transaction() as session:
            session["annee_consultee"] = {str(self.ecole_a.id): self.annee_archivee.id}

        self.assertEqual(client.get("/classes/add").status_code, 302)
        self.assertEqual(client.get(f"/classes/{self.classe_6a.id}/modifier").status_code, 302)
        self.assertEqual(client.post(f"/classes/{self.classe_6a.id}/supprimer").status_code, 302)
        self.assertEqual(client.get("/ajouter_eleve").status_code, 302)

    def test_pas_de_n_plus_un_majeur_liste_eleves_service(self):
        statements = []

        def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
        try:
            get_eleves_annee_query(self.ecole_a.id, self.annee_active.id).all()
        finally:
            event.remove(db.engine, "before_cursor_execute", before_cursor_execute)

        self.assertLessEqual(len(statements), 3)


if __name__ == "__main__":
    unittest.main()
