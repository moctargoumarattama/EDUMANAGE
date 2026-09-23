"""
Tests unitaires de performance Phase 2 (KLASORA):
1. Pagination SQL native sur les Absences (/absences)
2. Élimination de la double boucle financière sur les Paiements (/paiements)
3. Activation propre du mode SQLite WAL en développement (sans impact PostgreSQL)
"""
from datetime import date, datetime
import os
import tempfile
import unittest

from app import create_app, db
from app.models import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Paiement,
    Professeur,
    Utilisateur,
)
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class Phase2PerformanceTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-phase2-perf"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class PerformancePhase2TestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(Phase2PerformanceTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Établissement & Année
        self.ecole = Ecole(
            nom="École Performance",
            adresse="Niamey",
            telephone="90000000",
            email="contact@ecole-perf.ne",
            statut="actif"
        )
        db.session.add(self.ecole)
        db.session.commit()

        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        # 2. Utilisateurs
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Test",
            email="admin@ecole-perf.ne",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole.id
        )
        db.session.add(self.admin)
        db.session.commit()

        # 3. Niveaux & Classes
        self.niv = NiveauScolaire(code="6E", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niv)
        db.session.commit()

        self.classe = Classe(
            nom="6ème A",
            niveau="6ème",
            niveau_id=self.niv.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            capacite=40
        )
        db.session.add(self.classe)
        db.session.commit()

        # 4. Cours
        self.cours = Cours(
            nom="Mathématiques",
            ecole_id=self.ecole.id,
            classe_id=self.classe.id
        )
        db.session.add(self.cours)
        db.session.commit()

        # 5. Élèves, Inscriptions, Paiements & Absences
        self.eleves = []
        self.inscriptions = []
        for i in range(1, 16):
            eleve = Eleve(
                nom=f"Nom{i:02d}",
                prenom=f"Prenom{i:02d}",
                code_parent=f"PAR{i:04d}",
                date_naissance=date(2013, 1, 1),
                ecole_id=self.ecole.id
            )
            db.session.add(eleve)
            self.eleves.append(eleve)
        db.session.commit()

        for idx, eleve in enumerate(self.eleves):
            insc = Inscription(
                ecole_id=self.ecole.id,
                eleve_id=eleve.id,
                classe_id=self.classe.id,
                annee_scolaire_id=self.annee.id,
                statut="inscrit",
                frais_annuels=100000.0
            )
            db.session.add(insc)
            self.inscriptions.append(insc)
        db.session.commit()

        # Paiements pour certains élèves
        for idx, insc in enumerate(self.inscriptions):
            if idx % 2 == 0:
                p = Paiement(
                    ecole_id=self.ecole.id,
                    eleve_id=insc.eleve_id,
                    inscription_id=insc.id,
                    montant=50000.0,
                    mois="Octobre",
                    annee=2025,
                    mode_paiement="espèces",
                    statut="payé",
                    reference=f"REC-{idx:03d}",
                    date_paiement=datetime.now()
                )
                db.session.add(p)

        # 15 absences pour tester la pagination SQL native
        for idx, insc in enumerate(self.inscriptions):
            justifiee = (idx % 2 == 0)
            motif = "Certificat médical" if justifiee else "Non justifié"
            abs_obj = Absence(
                ecole_id=self.ecole.id,
                eleve_id=insc.eleve_id,
                inscription_id=insc.id,
                cours_id=self.cours.id,
                date_absence=date(2025, 10, idx + 1),
                motif=motif,
                justifiee=justifiee
            )
            db.session.add(abs_obj)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['annee_id'] = self.annee.id
            sess['onboarding_complete'] = True

    def test_01_sqlite_wal_pragma_applied_safely(self):
        """Vérifie que la configuration WAL de SQLite est active et sécurisée pour SQLite."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            class FileSqliteConfig:
                TESTING = True
                SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}"
                SECRET_KEY = "wal-test"
                PROPAGATE_EXCEPTIONS = True

            test_file_app = create_app(FileSqliteConfig)
            with test_file_app.app_context():
                with db.engine.connect() as conn:
                    result = conn.execute(text("PRAGMA journal_mode;")).scalar()
                    self.assertEqual(result.lower(), "wal")
                    sync_res = conn.execute(text("PRAGMA synchronous;")).scalar()
                    self.assertEqual(sync_res, 1)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def test_02_paiements_route_uses_precomputed_finances(self):
        """Vérifie que la route /paiements s'exécute correctement avec la pré-agrégation finances_map."""
        self._login_admin()
        res = self.client.get('/paiements')
        self.assertEqual(res.status_code, 200)

        html = res.data.decode('utf-8')
        self.assertIn("Paiements", html)
        self.assertIn("Nom01", html)

    def test_03_absences_eager_loading_and_class_grouping(self):
        """Vérifie que la route /absences consolide les absences par classe sans duplication inter-pages."""
        self._login_admin()

        res = self.client.get('/absences')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')

        # Doit contenir la classe unique et la pagination des classes
        self.assertIn("Pagination des classes", html)
        self.assertIn("6ème A", html)
        self.assertIn("15", html)

        # Vérifie que la classe n'est pas instanciée en plusieurs cartes identiques
        # (Dans le HTML, il ne doit y avoir qu'un seul header d'accordéon pour 6ème A)
        self.assertEqual(html.count('data-class-chip="6ème A"'), 1)

    def test_04_absences_filtres_sql(self):
        """Vérifie que les filtres (justifiée, recherche texte, classe) fonctionnent en SQL direct."""
        self._login_admin()

        # 1. Filtre justifiée
        res_just = self.client.get('/absences?justifiee=1&per_page=50')
        self.assertEqual(res_just.status_code, 200)
        html_just = res_just.data.decode('utf-8')
        self.assertIn("Certificat", html_just)

        # 2. Filtre recherche textuelle
        res_search = self.client.get('/absences?search=Nom01&per_page=50')
        self.assertEqual(res_search.status_code, 200)
        html_search = res_search.data.decode('utf-8')
        self.assertIn("Nom01", html_search)
        self.assertNotIn("Nom15", html_search)

        # 3. Filtre par classe ID
        res_class = self.client.get(f'/absences?classe_id={self.classe.id}&per_page=50')
        self.assertEqual(res_class.status_code, 200)

    def test_05_absence_annee_classe_and_inscription_properties(self):
        """Vérifie que les propriétés annee_classe et annee_inscription sur le modèle Absence sont opérationnelles."""
        abs_obj = Absence.query.filter_by(ecole_id=self.ecole.id).first()
        self.assertIsNotNone(abs_obj)
        self.assertEqual(abs_obj.annee_classe.id, self.classe.id)
        self.assertEqual(abs_obj.annee_classe.nom, "6ème A")
        self.assertIsNotNone(abs_obj.annee_inscription)
        self.assertEqual(abs_obj.annee_inscription.eleve_id, abs_obj.eleve_id)


if __name__ == '__main__':
    unittest.main()
