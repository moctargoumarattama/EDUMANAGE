"""Régressions des droits de comptes, du blocage et du journal de corrections.

La factory, le chargeur Flask-Login, les routes et les templates restent réels.
Chaque test possède une base SQLite en mémoire et ne garde aucun app_context
entre les requêtes : le chargeur de session est donc exécuté à chaque accès.
"""

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import event

from app import create_app, db
from app.models import AnneeScolaire, Ecole, JournalCorrection, MessageQueue, Utilisateur


class SecurityAccountsTestConfig:
    TESTING = True
    APP_ENV = "testing"
    SECRET_KEY = "securite-comptes-tests-isoles"
    WTF_CSRF_ENABLED = False
    LOGIN_DISABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"
    CHECK_BACKUP_ON_REQUEST = False
    LOG_ALL_ACCESS = False
    USE_PROXY_FIX = False
    MAIL_SUPPRESS_SEND = True


class SecuriteComptesEtAuditTestCase(unittest.TestCase):
    PASSWORD = "12345678"
    NEW_PASSWORD = "87654321"

    def setUp(self):
        self.app = create_app(SecurityAccountsTestConfig)
        self.user_ids = {}
        with self.app.app_context():
            db.create_all()
            school = Ecole(nom="École sécurité A", statut="actif", onboarding_complete=True)
            other_school = Ecole(nom="École sécurité B", statut="actif", onboarding_complete=True)
            db.session.add_all([school, other_school])
            db.session.flush()
            self.school_id = school.id
            self.other_school_id = other_school.id
            for school_id in (self.school_id, self.other_school_id):
                db.session.add(AnneeScolaire(
                    nom="2026-2027", date_debut=date(2026, 9, 1),
                    date_fin=date(2027, 7, 31), statut="active", ecole_id=school_id,
                ))

            # Dates explicites : le principal ne dépend pas de l'ordre d'un flush.
            first_created = datetime(2026, 1, 1)
            specs = (
                ("principal", "admin", self.school_id, "90110001"),
                ("secondaire", "admin", self.school_id, "90110002"),
                ("pair", "admin", self.school_id, "90110003"),
                ("professeur", "professeur", self.school_id, "90110004"),
                ("parent", "parent", self.school_id, "90110005"),
                ("super_admin", "super_admin", None, "90110006"),
                ("autre_principal", "admin", self.other_school_id, "90110007"),
            )
            fixture_hash = None
            for offset, (key, role, school_id, phone) in enumerate(specs):
                user = Utilisateur(
                    nom=key, prenom="Test", email=f"{key}@securite.test",
                    telephone=phone, role=role, statut="actif", ecole_id=school_id,
                    date_creation=first_created + timedelta(days=offset),
                )
                if fixture_hash is None:
                    user.set_mot_de_passe(self.PASSWORD)
                    fixture_hash = user.mot_de_passe
                else:
                    user.mot_de_passe = fixture_hash
                db.session.add(user)
                db.session.flush()
                self.user_ids[key] = user.id
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def user_state(self, key):
        with self.app.app_context():
            user = db.session.get(Utilisateur, self.user_ids[key])
            if user is None:
                return None
            return {name: getattr(user, name) for name in (
                "id", "nom", "prenom", "email", "telephone", "role",
                "statut", "ecole_id", "mot_de_passe", "dernier_acces",
            )}

    def set_status(self, key, status):
        with self.app.app_context():
            db.session.get(Utilisateur, self.user_ids[key]).statut = status
            db.session.commit()

    def assert_password(self, key, password):
        with self.app.app_context():
            self.assertTrue(db.session.get(Utilisateur, self.user_ids[key]).check_mot_de_passe(password))

    def client_as(self, key):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_ids[key])
            session["_fresh"] = True
            session["ecole_id"] = self.school_id
            session["onboarding_complete"] = True
        return client

    def login(self, client, key, remember=False):
        user = self.user_state(key)
        data = {"password": self.PASSWORD, "mot_de_passe": self.PASSWORD}
        if user["role"] in ("admin", "super_admin"):
            data.update(login_type="admin", email=user["email"])
        else:
            data.update(login_type="terrain", telephone=user["telephone"])
        if remember:
            data["remember"] = "y"
        return client.post("/login", data=data)

    def assert_no_auth_session(self, client):
        with client.session_transaction() as session:
            self.assertNotIn("_user_id", session)
            self.assertNotIn("ecole_id", session)
            self.assertNotIn("role", session)
        self.assertIsNone(client.get_cookie("remember_token"))

    def assert_no_orphan_accounts(self):
        with self.app.app_context():
            self.assertEqual(Utilisateur.query.filter(
                Utilisateur.ecole_id.is_(None), Utilisateur.role != "super_admin",
            ).count(), 0)

    def test_secondaire_reset_principal_refuse_sans_secret_et_journalise(self):
        client = self.client_as("secondaire")
        before = self.user_state("principal")
        with self.assertLogs(self.app.logger, level="WARNING") as captured:
            with patch("app.routes.utilisateurs.generate_access_code") as generate:
                response = client.post(f"/admin/utilisateur/{before['id']}/reset-password")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["success"])
        self.assertNotIn("password", response.get_json())
        self.assertEqual(self.user_state("principal"), before)
        generate.assert_not_called()
        messages = " ".join(record.getMessage() for record in captured.records)
        self.assertIn(str(self.user_ids["secondaire"]), messages)
        self.assertIn(str(self.user_ids["principal"]), messages)
        self.assertNotIn(before["mot_de_passe"], messages)
        self.assertNotIn(self.PASSWORD, messages)

    def test_secondaire_ne_modifie_ni_principal_ni_admin_pair(self):
        operations = (
            ("POST", "/admin/utilisateur/{id}/modifier", {"data": {
                "nom": "Intrus", "email": "intrus@securite.test", "statut": "bloque",
            }}),
            ("POST", "/admin/utilisateur/{id}/statut", {"json": {"statut": "bloque"}}),
            ("PUT", "/api/users/{id}/status", {"json": {}}),
            ("DELETE", "/admin/utilisateur/{id}", {}),
            ("DELETE", "/api/users/{id}", {}),
            ("POST", "/admin/utilisateur/{id}/reset-password", {}),
        )
        for target in ("principal", "pair"):
            for method, route, kwargs in operations:
                with self.subTest(target=target, method=method, route=route):
                    before = self.user_state(target)
                    response = self.client_as("secondaire").open(
                        route.format(id=before["id"]), method=method, **kwargs,
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(self.user_state(target), before)
                    self.assertNotIn("password", response.get_json() or {})
        with self.app.app_context():
            self.assertEqual(MessageQueue.query.count(), 0)

    def test_principal_reset_secondaire_et_professeur_autorise(self):
        for key in ("secondaire", "professeur"):
            with self.subTest(target=key):
                old_hash = self.user_state(key)["mot_de_passe"]
                with patch("app.routes.utilisateurs.generate_access_code", return_value=self.NEW_PASSWORD):
                    response = self.client_as("principal").post(
                        f"/admin/utilisateur/{self.user_ids[key]}/reset-password",
                    )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["success"])
                self.assertEqual(response.get_json()["password"], self.NEW_PASSWORD)
                self.assertNotEqual(self.user_state(key)["mot_de_passe"], old_hash)
                self.assert_password(key, self.NEW_PASSWORD)

    def test_principal_reset_son_propre_compte_autorise(self):
        with patch("app.routes.utilisateurs.generate_access_code", return_value=self.NEW_PASSWORD):
            response = self.client_as("principal").post(
                f"/admin/utilisateur/{self.user_ids['principal']}/reset-password",
            )
        self.assertEqual(response.status_code, 200)
        self.assert_password("principal", self.NEW_PASSWORD)

    def test_super_admin_reset_principal_autorise(self):
        with patch("app.routes.utilisateurs.generate_access_code", return_value=self.NEW_PASSWORD):
            response = self.client_as("super_admin").post(
                f"/admin/utilisateur/{self.user_ids['principal']}/reset-password",
            )
        self.assertEqual(response.status_code, 200)
        self.assert_password("principal", self.NEW_PASSWORD)

    def test_reset_autre_ecole_refuse_sans_modifier_le_compte(self):
        before = self.user_state("autre_principal")
        response = self.client_as("principal").post(
            f"/admin/utilisateur/{before['id']}/reset-password",
        )
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.user_state("autre_principal"), before)
        self.assertNotIn("password", response.get_json() or {})

    def test_principal_peut_modifier_et_bloquer_un_secondaire(self):
        user_id = self.user_ids["secondaire"]
        client = self.client_as("principal")
        response = client.post(f"/admin/utilisateur/{user_id}/modifier", data={
            "nom": "Secondaire modifié", "email": "secondaire@securite.test", "statut": "actif",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.user_state("secondaire")["nom"], "Secondaire modifié")
        response = client.post(f"/admin/utilisateur/{user_id}/statut", json={"statut": "bloque"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user_state("secondaire")["statut"], "bloque")

    def test_session_active_revoquee_des_la_requete_suivante(self):
        client = self.app.test_client()
        self.assertEqual(self.login(client, "secondaire", remember=True).status_code, 302)
        self.assertIsNotNone(client.get_cookie("remember_token"))
        with client.session_transaction() as session:
            session["onboarding_complete"] = True
        self.assertEqual(client.get("/journaux_corrections").status_code, 200)
        self.set_status("secondaire", "bloque")

        target_before = self.user_state("professeur")
        response = client.put(f"/api/users/{self.user_ids['professeur']}/status", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.user_state("professeur"), target_before)
        self.assert_no_auth_session(client)

        # La réactivation ne ressuscite pas le cookie de la session révoquée.
        self.set_status("secondaire", "actif")
        response = client.get("/admin/utilisateurs")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)
        self.assert_no_auth_session(client)

    def test_cookie_remember_seul_ne_reconnecte_pas_un_compte_bloque(self):
        original = self.app.test_client()
        self.assertEqual(self.login(original, "secondaire", remember=True).status_code, 302)
        remembered = original.get_cookie("remember_token")
        self.assertIsNotNone(remembered)
        self.set_status("secondaire", "bloque")
        client = self.app.test_client()
        client.set_cookie("remember_token", remembered.value)

        response = client.put(f"/api/users/{self.user_ids['professeur']}/status", json={})
        self.assertEqual(response.status_code, 401)
        self.assert_no_auth_session(client)
        self.set_status("secondaire", "actif")
        self.assertEqual(client.put(
            f"/api/users/{self.user_ids['professeur']}/status", json={},
        ).status_code, 401)

    def test_login_compte_bloque_refuse_sans_session_ni_cookie(self):
        for key in ("secondaire", "professeur", "parent", "super_admin"):
            with self.subTest(role=key):
                self.set_status(key, "bloque")
                before = self.user_state(key)
                client = self.app.test_client()
                response = self.login(client, key, remember=True)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)
                self.assert_no_auth_session(client)
                with client.session_transaction() as session:
                    flashes = [message for _, message in session.get("_flashes", [])]
                self.assertIn(
                    "Votre compte a été suspendu. Veuillez contacter l'administration.", flashes,
                )
                self.assertEqual(self.user_state(key), before)

    def test_is_active_reflete_blocage_et_indicateur_est_actif(self):
        with self.app.app_context():
            user = db.session.get(Utilisateur, self.user_ids["professeur"])
            self.assertTrue(user.is_active)
            user.statut = "bloque"
            self.assertFalse(user.is_active)
            user.statut = "actif"
            user.est_actif = False
            self.assertFalse(user.is_active)

    def test_get_journaux_vides_necrit_aucune_table_et_affiche_etat_vide(self):
        for actor in ("principal", "super_admin"):
            with self.subTest(actor=actor):
                with self.app.app_context():
                    self.assertEqual(JournalCorrection.query.count(), 0)
                    engine = db.engine
                writes = []

                def record_write(connection, cursor, statement, parameters, context, executemany):
                    first_word = statement.lstrip().split(None, 1)[0].upper()
                    if first_word in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}:
                        writes.append(statement)

                event.listen(engine, "before_cursor_execute", record_write)
                try:
                    response = self.client_as(actor).get("/journaux_corrections")
                finally:
                    event.remove(engine, "before_cursor_execute", record_write)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    "Aucune correction enregistrée pour le moment" in response.get_data(as_text=True),
                    "Un journal vide doit afficher son état vide pour l'école accessible",
                )
                self.assertEqual(writes, [], "Le GET du journal a effectué des écritures SQL")
                with self.app.app_context():
                    self.assertEqual(JournalCorrection.query.count(), 0)

    def test_secondaire_creation_refuse_sur_les_deux_routes_sans_orphelin(self):
        for route in ("/admin/create_user", "/admin/creer_utilisateur"):
            for method in ("GET", "POST"):
                with self.subTest(route=route, method=method):
                    with self.app.app_context():
                        count_before = Utilisateur.query.count()
                    response = self.client_as("secondaire").open(route, method=method, data={
                        "nom": "Admin interdit", "email": "nouveau@securite.test", "role": "admin",
                        "mot_de_passe": self.NEW_PASSWORD, "password": self.NEW_PASSWORD,
                        "confirm_password": self.NEW_PASSWORD, "eleve_id": "0",
                    })
                    self.assertEqual(response.status_code, 403)
                    with self.app.app_context():
                        self.assertEqual(Utilisateur.query.count(), count_before)
                        self.assertIsNone(Utilisateur.query.filter_by(email="nouveau@securite.test").first())
                    self.assert_no_orphan_accounts()

    def test_ancienne_creation_principal_utilise_ecole_et_hash_compatibles(self):
        response = self.client_as("principal").post("/admin/create_user", data={
            "nom": "Admin legacy", "email": "legacy@securite.test", "password": self.NEW_PASSWORD,
            "confirm_password": self.NEW_PASSWORD, "role": "super_admin", "eleve_id": "0",
            "ecole_id": str(self.other_school_id),
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            created = Utilisateur.query.filter_by(email="legacy@securite.test").one()
            self.assertEqual(created.ecole_id, self.school_id)
            self.assertEqual(created.role, "admin")
            self.assertNotEqual(created.mot_de_passe, self.NEW_PASSWORD)
            self.assertTrue(created.check_mot_de_passe(self.NEW_PASSWORD))
        self.assert_no_orphan_accounts()

        # Vérifie l'interopérabilité avec la vraie route d'authentification.
        client = self.app.test_client()
        login_response = client.post("/login", data={
            "login_type": "admin", "email": "legacy@securite.test", "password": self.NEW_PASSWORD,
        })
        self.assertEqual(login_response.status_code, 302)
        with client.session_transaction() as session:
            self.assertEqual(session["ecole_id"], self.school_id)
            self.assertIn("_user_id", session)

    def test_creation_canonique_principal_et_doublon_sans_compte_supplementaire(self):
        client = self.client_as("principal")
        data = {"nom": "Admin canonique", "email": "canonique@securite.test", "mot_de_passe": self.NEW_PASSWORD}
        self.assertEqual(client.post("/admin/creer_utilisateur", data=data).status_code, 302)
        with self.app.app_context():
            user = Utilisateur.query.filter_by(email=data["email"]).one()
            self.assertEqual(user.ecole_id, self.school_id)
            self.assertTrue(user.check_mot_de_passe(self.NEW_PASSWORD))
            before = Utilisateur.query.count()
            previous_hash = user.mot_de_passe
        # Même service derrière l'ancienne URL : pas de doublon ni changement du secret.
        data["password"] = "11111111"
        data["mot_de_passe"] = "11111111"
        self.assertEqual(client.post("/admin/create_user", data=data).status_code, 302)
        with self.app.app_context():
            self.assertEqual(Utilisateur.query.count(), before)
            self.assertEqual(Utilisateur.query.filter_by(email=data["email"]).one().mot_de_passe, previous_hash)
        self.assert_no_orphan_accounts()


if __name__ == "__main__":
    unittest.main()
