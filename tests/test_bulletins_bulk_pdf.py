"""
Tests unitaires et d'intégration pour le chantier P3 :
Exportation et téléchargement groupé des bulletins de toute une classe
en un seul document PDF multipages (/bulletins/classes/<id>/export-pdf-groupe).
"""
import unittest
from datetime import date

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    PeriodeBulletin,
    Utilisateur,
)


class BulletinsBulkPdfTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-bulk-pdf-bulletins"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class BulletinsBulkPdfTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(BulletinsBulkPdfTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Établissement A
        self.ecole_a = Ecole(
            nom="École Principale",
            statut="actif",
            onboarding_complete=True,
            devise="Discipline - Travail - Succès",
        )
        # Établissement B (pour test multi-tenant)
        self.ecole_b = Ecole(
            nom="École Secondaire",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()

        # Année scolaire A
        self.annee_a = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        # Année scolaire B
        self.annee_b = AnneeScolaire(
            nom="2025-2026 B",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_a, self.annee_b])
        db.session.commit()

        # Période pour École A
        self.periode_a = PeriodeBulletin(
            nom="Semestre 1",
            annee_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            publie=True,
            periode_active=True,
        )
        # Période pour École B
        self.periode_b = PeriodeBulletin(
            nom="Semestre 1 B",
            annee_id=self.annee_b.id,
            ecole_id=self.ecole_b.id,
            publie=True,
            periode_active=True,
        )
        db.session.add_all([self.periode_a, self.periode_b])
        db.session.commit()

        # Utilisateurs École A : Admin, Directeur, Secrétaire, Parent
        self.admin_a = Utilisateur(
            nom="Admin",
            prenom="Directeur",
            email="admin@ecole-a.com",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole_a.id,
            statut="actif",
        )
        self.directeur_a = Utilisateur(
            nom="Directeur",
            prenom="Pedagogique",
            email="directeur@ecole-a.com",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="directeur",
            ecole_id=self.ecole_a.id,
            statut="actif",
        )
        self.secretaire_a = Utilisateur(
            nom="Secretaire",
            prenom="Bureau",
            email="secretaire@ecole-a.com",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="secretaire",
            ecole_id=self.ecole_a.id,
            statut="actif",
        )
        self.parent_a = Utilisateur(
            nom="Parent",
            prenom="Famille",
            email="parent@ecole-a.com",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="parent",
            ecole_id=self.ecole_a.id,
            statut="actif",
        )
        db.session.add_all([self.admin_a, self.directeur_a, self.secretaire_a, self.parent_a])
        db.session.commit()

        # Classe A avec élèves
        self.classe_a = Classe(
            nom="CM2 A",
            niveau="CM2",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a.id,
        )
        # Classe A vide (sans élèves)
        self.classe_vide = Classe(
            nom="6ème B",
            niveau="6ème",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a.id,
        )
        # Classe B (autre établissement)
        self.classe_b = Classe(
            nom="CM2 B Étrangère",
            niveau="CM2",
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_b.id,
        )
        db.session.add_all([self.classe_a, self.classe_vide, self.classe_b])
        db.session.commit()

        # Cours
        self.cours_math = Cours(
            nom="Mathématiques",
            coefficient=2.0,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_a.id,
        )
        self.cours_francais = Cours(
            nom="Français",
            coefficient=2.0,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_a.id,
        )
        db.session.add_all([self.cours_math, self.cours_francais])
        db.session.commit()

        # 2 Élèves dans Classe A
        self.eleve1 = Eleve(
            nom="Kouassi",
            prenom="Jean",
            date_naissance=date(2013, 3, 15),
            ecole_id=self.ecole_a.id,
        )
        self.eleve2 = Eleve(
            nom="Diallo",
            prenom="Awa",
            date_naissance=date(2013, 7, 22),
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([self.eleve1, self.eleve2])
        db.session.commit()

        # Inscriptions
        self.ins1 = Inscription(
            eleve_id=self.eleve1.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            statut="inscrit",
        )
        self.ins2 = Inscription(
            eleve_id=self.eleve2.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            statut="inscrit",
        )
        db.session.add_all([self.ins1, self.ins2])
        db.session.commit()

        # Notes pour Élève 1
        self.n1_math = Note(
            valeur=18.0,
            coefficient=1.0,
            type_evaluation="Composition",
            periode="Semestre 1",
            inscription_id=self.ins1.id,
            eleve_id=self.eleve1.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        self.n1_fr = Note(
            valeur=16.0,
            coefficient=1.0,
            type_evaluation="Composition",
            periode="Semestre 1",
            inscription_id=self.ins1.id,
            eleve_id=self.eleve1.id,
            cours_id=self.cours_francais.id,
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        # Notes pour Élève 2
        self.n2_math = Note(
            valeur=14.0,
            coefficient=1.0,
            type_evaluation="Composition",
            periode="Semestre 1",
            inscription_id=self.ins2.id,
            eleve_id=self.eleve2.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        self.n2_fr = Note(
            valeur=15.0,
            coefficient=1.0,
            type_evaluation="Composition",
            periode="Semestre 1",
            inscription_id=self.ins2.id,
            eleve_id=self.eleve2.id,
            cours_id=self.cours_francais.id,
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        db.session.add_all([self.n1_math, self.n1_fr, self.n2_math, self.n2_fr])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['annee_id'] = user.ecole_id and self.annee_a.id
            sess['annee_consultee'] = {str(user.ecole_id): self.annee_a.id}
            sess['ecole_id'] = user.ecole_id
            sess['role'] = user.role
            sess['onboarding_complete'] = True
            if user.ecole_id:
                sess[f'onboarding_complete_{user.ecole_id}'] = True

    def test_export_pdf_groupe_unauthenticated_or_unauthorized(self):
        """Vérifie le rejet des utilisateurs non connectés ou non autorisés (ex: parents)."""
        url = f"/bulletins/classes/{self.classe_a.id}/export-pdf-groupe?periode_id={self.periode_a.id}"

        # 1. Non connecté -> 302 vers login
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers.get("Location", ""))

        # 2. Connecté en tant que parent -> 403 Forbidden
        self._login(self.parent_a)
        resp_parent = self.client.get(url)
        self.assertEqual(resp_parent.status_code, 403)

    def test_export_pdf_groupe_multitenant_isolation(self):
        """Vérifie qu'un administrateur ne peut pas exporter une classe d'un autre établissement."""
        self._login(self.admin_a)
        url_autre_classe = f"/bulletins/classes/{self.classe_b.id}/export-pdf-groupe"

        resp = self.client.get(url_autre_classe)
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_groupe_cas_limites_sans_eleves_ou_sans_notes(self):
        """Vérifie que les cas limites (classe vide ou 0 note) redirigent avec message sans crash 500."""
        self._login(self.admin_a)

        # 1. Classe sans élèves
        url_vide = f"/bulletins/classes/{self.classe_vide.id}/export-pdf-groupe"
        resp_vide = self.client.get(url_vide)
        self.assertEqual(resp_vide.status_code, 302)
        self.assertIn("/bulletins", resp_vide.headers.get("Location", ""))

        # 2. Classe avec élèves mais sans note pour la période demandée
        periode_sans_notes = PeriodeBulletin(
            nom="Semestre 2",
            annee_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            publie=False,
            periode_active=False,
        )
        db.session.add(periode_sans_notes)
        db.session.commit()

        url_sans_notes = f"/bulletins/classes/{self.classe_a.id}/export-pdf-groupe?periode_id={periode_sans_notes.id}"
        resp_sans_notes = self.client.get(url_sans_notes)
        self.assertEqual(resp_sans_notes.status_code, 302)
        self.assertIn("/bulletins", resp_sans_notes.headers.get("Location", ""))

    def test_export_pdf_groupe_success_headers_and_pdf_structure(self):
        """Vérifie la génération réussie du PDF consolidé avec les en-têtes et le format PDF attendus."""
        self._login(self.admin_a)
        url = f"/bulletins/classes/{self.classe_a.id}/export-pdf-groupe?periode_id={self.periode_a.id}"

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Vérification des en-têtes HTTP de téléchargement
        self.assertEqual(resp.content_type, "application/pdf")
        content_disp = resp.headers.get("Content-Disposition", "")
        self.assertIn("attachment", content_disp)
        self.assertIn("Bulletins_CM2_A_Semestre_1.pdf", content_disp)

        # Vérification du flux binaire PDF
        pdf_bytes = resp.data
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 2000)

    def test_export_pdf_groupe_authorized_roles_directeur_et_secretaire(self):
        """Vérifie que les rôles directeur et secrétaire ont accès à l'export groupé."""
        url = f"/bulletins/classes/{self.classe_a.id}/export-pdf-groupe?periode_id={self.periode_a.id}"

        # 1. Rôle Directeur
        self._login(self.directeur_a)
        resp_dir = self.client.get(url)
        self.assertEqual(resp_dir.status_code, 200)
        self.assertEqual(resp_dir.content_type, "application/pdf")

        # 2. Rôle Secrétaire
        self._login(self.secretaire_a)
        resp_sec = self.client.get(url)
        self.assertEqual(resp_sec.status_code, 200)
        self.assertEqual(resp_sec.content_type, "application/pdf")

    def test_export_archive_ignore_inscription_annulee_sans_bulletin(self):
        eleve_annule = Eleve(
            nom="Annule", prenom="Dossier", date_naissance=date(2013, 1, 1),
            ecole_id=self.ecole_a.id,
        )
        db.session.add(eleve_annule)
        db.session.flush()
        db.session.add(Inscription(
            eleve_id=eleve_annule.id, classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id, ecole_id=self.ecole_a.id,
            statut="annulee",
        ))
        for inscription in (self.ins1, self.ins2):
            db.session.add(Bulletin(
                ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id,
                inscription_id=inscription.id, eleve_id=inscription.eleve_id,
                classe_id=self.classe_a.id, periode=self.periode_a.nom,
                moyenne_generale=14.0, statut="archive",
            ))
        self.annee_a.statut = "archivee"
        db.session.commit()

        self._login(self.admin_a)
        url = f"/bulletins/classes/{self.classe_a.id}/export-pdf-groupe?periode_id={self.periode_a.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
