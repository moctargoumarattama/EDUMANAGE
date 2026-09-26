"""
Tests automatisés pour :
1. Tableau unique des moyennes par matière avec pop-up modal détaillé par matière et par semestre/trimestre.
2. Consultation directe des notes et du dossier d'un élève pour une année archivée (?annee_id=...).
3. Accès au bulletin officiel de n'importe quelle année archivée (bulletin_eleve avec inscription_id).
4. Recherche globale (/recherche) retrouvant un élève ancien (même d'il y a 20 ans) avec son parcours et ses liens directs.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Utilisateur,
)
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class DossierArchivesTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-dossier-archives"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestDossierArchivesEtPopups(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DossierArchivesTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Établissement
        self.ecole = Ecole(
            nom="Groupe Scolaire Excellence",
            adresse="Niamey",
            telephone="90112233",
            email="contact@excellence.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Principal",
            email="admin@excellence.ne",
            mot_de_passe=generate_password_hash("secret123"),
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.admin)

        # 1. Année archivée (ex: 2024-2025)
        self.annee_archivee = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="archivee",
        )
        db.session.add(self.annee_archivee)

        # 2. Année active (ex: 2025-2026)
        self.annee_active = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
        )
        db.session.add(self.annee_active)
        db.session.flush()

        # Niveaux et Classes
        self.niveau_cp = NiveauScolaire(nom="CP", code="CP", ordre=1, cycle="primaire")
        self.niveau_ce1 = NiveauScolaire(nom="CE1", code="CE1", ordre=2, cycle="primaire")
        db.session.add_all([self.niveau_cp, self.niveau_ce1])
        db.session.flush()

        self.classe_cp = Classe(
            nom="CP A",
            niveau="CP",
            niveau_id=self.niveau_cp.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_archivee.id,
            statut="ouverte",
        )
        self.classe_ce1 = Classe(
            nom="CE1 A",
            niveau="CE1",
            niveau_id=self.niveau_ce1.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id,
            statut="ouverte",
        )
        db.session.add_all([self.classe_cp, self.classe_ce1])
        db.session.flush()

        # Cours (Matières)
        self.cours_maths_cp = Cours(
            nom="Mathématiques",
            ecole_id=self.ecole.id,
            classe_id=self.classe_cp.id,
            coefficient=2.0,
        )
        self.cours_francais_cp = Cours(
            nom="Français",
            ecole_id=self.ecole.id,
            classe_id=self.classe_cp.id,
            coefficient=3.0,
        )
        self.cours_maths_ce1 = Cours(
            nom="Mathématiques",
            ecole_id=self.ecole.id,
            classe_id=self.classe_ce1.id,
            coefficient=2.0,
        )
        db.session.add_all([self.cours_maths_cp, self.cours_francais_cp, self.cours_maths_ce1])
        db.session.flush()

        # Élève avec historique de 2 ans
        self.eleve = Eleve(
            nom="Oumarou",
            prenom="Ibrahim",
            genre="M",
            date_naissance=date(2017, 5, 10),
            code_parent="P-EX-001",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.eleve)
        db.session.flush()

        # Inscription 2024-2025 (archivée)
        self.insc_2024 = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_archivee.id,
            classe_id=self.classe_cp.id,
            statut="termine",
            date_inscription=datetime(2024, 9, 2),
            frais_annuels=120000.0,
        )
        # Inscription 2025-2026 (active)
        self.insc_2025 = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_active.id,
            classe_id=self.classe_ce1.id,
            statut="inscrit",
            date_inscription=datetime(2025, 9, 5),
            frais_annuels=150000.0,
        )
        db.session.add_all([self.insc_2024, self.insc_2025])
        db.session.flush()

        # Notes en 2024-2025 (Semestre 1 et Semestre 2)
        self.note_cp_s1 = Note(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            cours_id=self.cours_maths_cp.id,
            annee_id=self.annee_archivee.id,
            inscription_id=self.insc_2024.id,
            periode="Semestre 1",
            type_evaluation="Devoir",
            valeur=14.0,
            coefficient=1.0,
            date_evaluation=datetime(2024, 11, 15),
        )
        self.note_cp_s2 = Note(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            cours_id=self.cours_maths_cp.id,
            annee_id=self.annee_archivee.id,
            inscription_id=self.insc_2024.id,
            periode="Semestre 2",
            type_evaluation="Composition",
            valeur=16.0,
            coefficient=2.0,
            date_evaluation=datetime(2025, 4, 20),
        )
        # Note en 2025-2026 (Année active)
        self.note_ce1 = Note(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            cours_id=self.cours_maths_ce1.id,
            annee_id=self.annee_active.id,
            inscription_id=self.insc_2025.id,
            periode="Trimestre 1",
            type_evaluation="Interrogation",
            valeur=15.0,
            coefficient=1.0,
            date_evaluation=datetime(2025, 10, 10),
        )
        db.session.add_all([self.note_cp_s1, self.note_cp_s2, self.note_ce1])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['user_id'] = self.admin.id
            sess['ecole_id'] = self.ecole.id
            sess['annee_active_id'] = self.annee_active.id
            sess['annee_consultee_id'] = self.annee_active.id

    def test_01_tableau_unique_et_popup_modal_matieres(self):
        """Le dossier affiche un tableau unique de matières et chaque matière a son pop-up modal."""
        self._login_admin()

        resp = self.client.get(f"/eleve/{self.eleve.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Vérification du tableau unique
        self.assertIn("Moyennes par Matière", html)
        self.assertIn("Mathématiques", html)
        self.assertIn('data-bs-target="#modalMatiereDynamique"', html)

        # Vérification du modal pop-up
        self.assertIn('id="modalMatiereDynamique"', html)
        self.assertIn("Détails", html)
        self.assertIn("15.0", html)

    def test_02_consultation_notes_annee_archivee(self):
        """L'accès à ?annee_id=<archive_id> charge l'historique et les notes de cette année passée."""
        self._login_admin()

        resp = self.client.get(f"/eleve/{self.eleve.id}?annee_id={self.annee_archivee.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Vérification du bandeau d'archive
        self.assertIn("Consultation de l'archive scolaire", html)
        self.assertIn("2024-2025", html)
        self.assertIn("CP A", html)

        # Vérification des notes de 2024-2025 (Semestre 1 et 2)
        self.assertIn("Semestre 1", html)
        self.assertIn("Semestre 2", html)
        self.assertIn("14.0", html)
        self.assertIn("16.0", html)

    def test_03_historique_classes_affiche_liens_bulletins_et_notes(self):
        """La section Historique des classes propose le bouton Bulletin et Notes pour chaque année."""
        self._login_admin()

        resp = self.client.get(f"/eleve/{self.eleve.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertIn("Historique des Classes", html)
        self.assertIn("2024-2025", html)
        self.assertIn("CP A", html)
        self.assertIn("2025-2026", html)
        self.assertIn("CE1 A", html)
        self.assertIn(f"annee_id={self.annee_archivee.id}", html)
        self.assertIn(f"/bulletin/inscription/{self.insc_2024.id}", html)

    def test_04_recherche_globale_trouve_eleve_ancien_avec_bulletin(self):
        """La recherche globale /recherche retrouve l'élève avec son parcours complet et accès direct."""
        self._login_admin()

        resp = self.client.get(f"/recherche?q=Oumarou")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertIn("Ibrahim Oumarou", html)
        self.assertIn("2024-2025", html)
        self.assertIn("CP A", html)
        self.assertIn(f"annee_id={self.annee_archivee.id}", html)
        self.assertIn(f"/bulletin/inscription/{self.insc_2024.id}", html)

    def test_05_vue_impression_excel_compacte(self):
        """La fiche élève contient une vue impression dédiée compacte style Excel (1 à 2 pages max)."""
        self._login_admin()

        resp = self.client.get(f"/eleve/{self.eleve.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Vérification du conteneur d'impression Excel dédié
        self.assertIn("print-excel-view", html)
        self.assertIn("screen-dossier-view", html)
        self.assertIn("d-print-none", html)

        # Vérification des sections Excel compactes
        self.assertIn("IDENTITÉ DE L'ÉLÈVE ET SITUATION ADMINISTRATIVE", html)
        self.assertIn("RÉSULTATS ACADÉMIQUES & ÉVALUATIONS PAR MATIÈRE", html)
        self.assertIn("REGISTRE D'ASSIDUITÉ ET DE DISCIPLINE", html)
        self.assertIn("SITUATION DES FRAIS DE SCOLARITÉ", html)
        self.assertNotIn("Visa du Parent / Tuteur Légal", html)
        self.assertNotIn("La Direction / L'Administration", html)
        self.assertNotIn("La Direction", html)
        self.assertIn("Logo École", html)
        self.assertIn("QR Code Élève", html)
        self.assertIn("Scanner pour vérifier", html)
        self.assertIn("HISTORIQUE DU PARCOURS SCOLAIRE & MOYENNES ANNUELLES", html)

        # Vérification de la configuration CSS d'impression
        self.assertIn("@media print", html)
        self.assertIn("size: A4 portrait", html)
        self.assertIn("excel-table", html)

