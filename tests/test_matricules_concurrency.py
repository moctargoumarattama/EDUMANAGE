"""Concurrence réelle SQLite sur un fichier temporaire et des connexions séparées."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from threading import Barrier, Event

from sqlalchemy.pool import NullPool

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, MatriculeSequence, Utilisateur
from app.services.matricule_service import generer_prochain_matricule


class MatriculesConcurrencyTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(__file__).resolve().parents[1]
        self.scratch = (self.workspace / "scratch").resolve()
        self.scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="matricules-concurrency-", dir=self.scratch
        )
        self.database_path = (Path(self.temporary.name) / "dedicated-test.db").resolve()
        self.assertTrue(self.database_path.is_relative_to(self.scratch))
        database_url = f"sqlite:///{self.database_path.as_posix()}"

        class ConcurrentTestConfig(Config):
            TESTING = True
            SECRET_KEY = "concurrency-test-only"
            WTF_CSRF_ENABLED = False
            SQLALCHEMY_DATABASE_URI = database_url
            SQLALCHEMY_ENGINE_OPTIONS = {
                "poolclass": NullPool,
                "connect_args": {"timeout": 15, "check_same_thread": False},
            }

        self.app = create_app(ConcurrentTestConfig)
        with self.app.app_context():
            self.assertEqual(Path(db.engine.url.database).resolve(), self.database_path)
            self.assertIsInstance(db.engine.pool, NullPool)
            db.create_all()
            ecole = Ecole(nom="Ecole concurrence temporaire", onboarding_complete=True)
            db.session.add(ecole)
            db.session.flush()
            annee = AnneeScolaire(
                nom="2026-2027", date_debut=date(2026, 9, 1),
                date_fin=date(2027, 7, 31), statut="active", ecole_id=ecole.id,
            )
            db.session.add(annee)
            db.session.flush()
            classe = Classe(
                nom="6e A", niveau="6e", statut="ouverte",
                ecole_id=ecole.id, annee_scolaire_id=annee.id,
            )
            admin = Utilisateur(
                nom="Administration temporaire", email="concurrent-admin@test.local",
                role="admin", ecole_id=ecole.id,
            )
            admin.set_mot_de_passe("concurrency-admin-test-only")
            parent = Utilisateur(
                nom="Tuteur temporaire", telephone="90123456",
                role="parent", ecole_id=ecole.id,
            )
            parent.set_mot_de_passe("48295106")
            db.session.add_all([classe, admin, parent])
            db.session.commit()
            self.ecole_id = ecole.id
            self.annee_id = annee.id
            self.classe_id = classe.id
            self.admin_id = admin.id
            self.parent_id = parent.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        # Le dossier supprimé contient uniquement le fichier de cette fixture.
        target = Path(self.temporary.name).resolve()
        self.assertTrue(target.is_relative_to(self.scratch))
        self.temporary.cleanup()

    def _sequence(self):
        with self.app.app_context():
            sequence = db.session.get(MatriculeSequence, (self.ecole_id, "26"))
            return sequence.dernier_numero if sequence else None

    def test_creations_concurrentes_obtiennent_quatre_matricules_distincts(self):
        barrier = Barrier(4)

        def create_student(index):
            with self.app.app_context():
                barrier.wait(timeout=15)
                eleve = Eleve(
                    nom=f"Concurrent {index}", prenom="Elève temporaire",
                    date_naissance=date(2014, 1, index + 1),
                    ecole_id=self.ecole_id, annee_premiere_ecole=2026,
                    date_inscription=datetime(2026, 9, 1),
                )
                db.session.add(eleve)
                db.session.commit()
                return eleve.id, eleve.matricule

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(create_student, index) for index in range(4)]
            results = [future.result(timeout=30) for future in futures]

        self.assertEqual(len({identifier for identifier, _ in results}), 4)
        self.assertEqual({matricule for _, matricule in results}, {
            "26-0001", "26-0002", "26-0003", "26-0004"
        })
        with self.app.app_context():
            self.assertEqual(Eleve.query.filter_by(ecole_id=self.ecole_id).count(), 4)
        self.assertEqual(self._sequence(), 4)

    def test_reservations_concurrentes_uniques_meme_sans_insertion(self):
        barrier = Barrier(4)

        def reserve():
            with self.app.app_context():
                barrier.wait(timeout=15)
                matricule = generer_prochain_matricule(self.ecole_id, 2026)
                db.session.commit()
                return matricule

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(reserve) for _ in range(4)]
            matricules = [future.result(timeout=30) for future in futures]

        self.assertEqual(set(matricules), {"26-0001", "26-0002", "26-0003", "26-0004"})
        with self.app.app_context():
            self.assertEqual(Eleve.query.count(), 0)
        self.assertEqual(self._sequence(), 4)

    def test_rollback_libere_numero_pour_transaction_concurrente_en_attente(self):
        reserved = Event()
        attempting = Event()
        completed = Event()
        release = Event()

        def reserve_then_rollback():
            with self.app.app_context():
                matricule = generer_prochain_matricule(self.ecole_id, 2026)
                reserved.set()
                if not release.wait(timeout=15):
                    raise AssertionError("La transaction de test n'a pas été libérée.")
                db.session.rollback()
                return matricule

        def reserve_then_commit():
            with self.app.app_context():
                attempting.set()
                matricule = generer_prochain_matricule(self.ecole_id, 2026)
                db.session.commit()
                completed.set()
                return matricule

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(reserve_then_rollback)
            try:
                self.assertTrue(reserved.wait(timeout=15))
                second = executor.submit(reserve_then_commit)
                self.assertTrue(attempting.wait(timeout=15))
                self.assertFalse(completed.wait(timeout=0.15))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=30), "26-0001")
            self.assertEqual(second.result(timeout=30), "26-0001")

        self.assertEqual(self._sequence(), 1)
        with self.app.app_context():
            self.assertEqual(generer_prochain_matricule(self.ecole_id, 2026), "26-0002")
            db.session.commit()

    def test_deux_ajouts_http_simultanes_meme_identite_creent_une_seule_fiche(self):
        barrier = Barrier(2)
        payload = {
            "nom": "Sow", "prenom": "Awa", "genre": "F",
            "date_naissance": "2014-01-01", "lieu_naissance": "Niamey",
            "adresse": "Adresse temporaire", "classe_id": str(self.classe_id),
            "frais_annuels": "150000", "parent_id": str(self.parent_id),
            "code_parent": "",
        }

        def submit():
            with self.app.app_context(), self.app.test_client() as client:
                with client.session_transaction() as session:
                    session["_user_id"] = str(self.admin_id)
                    session["_fresh"] = True
                    session["ecole_id"] = self.ecole_id
                    session["annee_consultee"] = {str(self.ecole_id): self.annee_id}
                barrier.wait(timeout=15)
                response = client.post("/ajouter_eleve", data=payload, follow_redirects=False)
                with client.session_transaction() as session:
                    flashes = list(session.get("_flashes", []))
                return response.status_code, flashes

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]

        self.assertEqual([status for status, _ in results], [302, 302])
        categories = [category for _, flashes in results for category, _ in flashes]
        self.assertEqual(categories.count("success"), 1)
        self.assertEqual(categories.count("warning"), 1)
        self.assertNotIn("danger", categories)
        with self.app.app_context():
            self.assertEqual(Eleve.query.count(), 1)
            self.assertEqual(Inscription.query.count(), 1)
            eleve = Eleve.query.one()
            self.assertEqual(eleve.matricule, "26-0001")
            self.assertEqual(Inscription.query.one().eleve_id, eleve.id)
        self.assertEqual(self._sequence(), 1)


if __name__ == "__main__":
    unittest.main()
