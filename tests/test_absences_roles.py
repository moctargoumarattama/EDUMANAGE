"""
Tests KLASORA — Module Absences : Respect des rôles et sécurisation métier.
1. Admin ne peut plus créer directement une absence manuelle (POST /absences)
2. Admin peut consulter le suivi des absences, rechercher et filtrer (GET /absences)
3. Admin peut modifier le motif et la justification (POST /absences/edit/<id>)
4. Admin peut supprimer une absence erronée (POST /absences/delete/<id>)
5. Admin peut exporter les absences en Excel (GET /absences/export_excel)
6. Professeur peut enregistrer les absences lors de la prise d'appel (POST /absences/appel)
7. Admin ne peut pas accéder à la prise d'appel (/absences/appel)
8. Professeur peut consulter le suivi de ses absences (GET /absences)
"""
from datetime import date
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
    AnneeNiveauConfig,
    Absence,
    Professeur,
    Utilisateur,
    professeur_classes,
)
from app.services.semestres import configurer_semestres_annee
from sqlalchemy.pool import StaticPool


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False}
    }
    SECRET_KEY = "test-absences-roles"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestAbsencesRoles(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # 1. École
        self.ecole = Ecole(
            nom="École Pilote Klasora",
            adresse="Quartier Plateau",
            telephone="90000000",
            email="contact@klasora.ne",
            statut="actif"
        )
        db.session.add(self.ecole)
        db.session.commit()

        # 2. Année scolaire active
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 15),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        configurer_semestres_annee(self.ecole.id, self.annee.id, date(2026, 1, 31))

        # 3. Niveau & Classe
        self.niveau = NiveauScolaire(code="6EME", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niveau)
        db.session.commit()

        self.anc = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            niveau_id=self.niveau.id,
            actif=True
        )
        db.session.add(self.anc)
        db.session.commit()

        self.classe = Classe(
            nom="6ème A",
            niveau_id=self.niveau.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            statut="ouverte"
        )
        db.session.add(self.classe)
        db.session.commit()

        # 4. Utilisateurs (Admin & Professeur)
        self.user_admin = Utilisateur(
            nom="Directeur",
            prenom="Admin",
            email="admin@klasora.ne",
            role="admin",
            ecole_id=self.ecole.id
        )
        self.user_admin.set_mot_de_passe("AdminPass123")

        self.user_prof = Utilisateur(
            nom="Kassoum",
            prenom="Ibrahim",
            email="prof@klasora.ne",
            role="professeur",
            ecole_id=self.ecole.id
        )
        self.user_prof.set_mot_de_passe("ProfPass123")

        db.session.add_all([self.user_admin, self.user_prof])
        db.session.commit()

        self.prof = Professeur(
            nom="Kassoum",
            prenom="Ibrahim",
            code_prof="PRF-001",
            utilisateur_id=self.user_prof.id,
            ecole_id=self.ecole.id
        )
        db.session.add(self.prof)
        db.session.commit()

        db.session.execute(
            professeur_classes.insert().values(
                professeur_id=self.prof.id,
                classe_id=self.classe.id,
                ecole_id=self.ecole.id
            )
        )
        db.session.commit()

        # 5. Cours
        self.cours = Cours(
            nom="Mathématiques",
            classe_id=self.classe.id,
            professeur_id=self.prof.id,
            ecole_id=self.ecole.id
        )
        db.session.add(self.cours)
        db.session.commit()

        # 6. Élève & Inscription
        self.eleve = Eleve(
            nom="Oumarou",
            prenom="Ali",
            date_naissance=date(2013, 5, 12),
            ecole_id=self.ecole.id,
            classe_id=self.classe.id
        )
        db.session.add(self.eleve)
        db.session.commit()

        self.inscription = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=120000.0,
            statut="inscrit"
        )
        db.session.add(self.inscription)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_admin(self):
        return self.client.post('/login', data={
            'email': 'admin@klasora.ne',
            'mot_de_passe': 'AdminPass123'
        }, follow_redirects=True)

    def _login_prof(self):
        return self.client.post('/login', data={
            'email': 'prof@klasora.ne',
            'mot_de_passe': 'ProfPass123'
        }, follow_redirects=True)

    # 1. L'admin ne peut pas créer directement une absence manuelle via POST /absences
    def test_01_admin_cannot_create_manual_absence_post(self):
        self._login_admin()
        count_before = Absence.query.count()

        resp = self.client.post('/absences', data={
            'eleve_id': self.eleve.id,
            'cours_id': self.cours.id,
            'date_absence': '2025-10-15',
            'motif': 'Absence créée manuellement',
            'justifiee': False
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        # Aucune absence ne doit avoir été créée
        self.assertEqual(Absence.query.count(), count_before)
        # Un message clair doit avertir que l'appel est réservé aux professeurs
        self.assertIn("L'enregistrement initial des absences est réservé aux professeurs", resp.get_data(as_text=True))

    # 2. L'admin peut consulter le suivi des absences, rechercher et filtrer
    def test_02_admin_can_view_absences_and_filters(self):
        # Créer une absence historique
        abs1 = Absence(
            date_absence=date(2025, 10, 10),
            motif="Rendez-vous dentaire",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            ecole_id=self.ecole.id,
            inscription_id=self.inscription.id
        )
        db.session.add(abs1)
        db.session.commit()

        self._login_admin()
        resp = self.client.get('/absences')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Vérifier le nouveau titre de page
        self.assertIn("Suivi des absences", html)
        self.assertIn("Ali Oumarou", html)
        self.assertIn("Rendez-vous dentaire", html)

        # Vérifier l'absence du bouton et du formulaire de création manuelle
        self.assertNotIn("Nouvelle absence", html)
        self.assertNotIn("Déclarer une absence", html)

    # 3. L'admin peut modifier la justification et le motif d'une absence
    def test_03_admin_can_edit_absence_justification_and_motif(self):
        abs1 = Absence(
            date_absence=date(2025, 10, 10),
            motif="Motif initial",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            ecole_id=self.ecole.id,
            inscription_id=self.inscription.id
        )
        db.session.add(abs1)
        db.session.commit()

        self._login_admin()
        resp = self.client.post(f'/absences/edit/{abs1.id}', data={
            'eleve_id': self.eleve.id,
            'cours_id': self.cours.id,
            'date_absence': '2025-10-10',
            'motif': 'Certificat médical validé',
            'justifiee': True
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        db.session.refresh(abs1)
        self.assertTrue(abs1.justifiee)
        self.assertEqual(abs1.motif, "Certificat médical validé")

    # 4. L'admin peut supprimer une absence erronée (correction d'erreur)
    def test_04_admin_can_delete_erroneous_absence(self):
        abs1 = Absence(
            date_absence=date(2025, 10, 10),
            motif="Erreur de saisie",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            ecole_id=self.ecole.id,
            inscription_id=self.inscription.id
        )
        db.session.add(abs1)
        db.session.commit()

        self._login_admin()
        resp = self.client.post(f'/absences/delete/{abs1.id}', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(Absence.query.get(abs1.id))

    # 5. L'admin peut exporter les absences en Excel
    def test_05_admin_can_export_absences_excel(self):
        abs1 = Absence(
            date_absence=date(2025, 10, 10),
            motif="Motif test export",
            justifiee=True,
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            ecole_id=self.ecole.id,
            inscription_id=self.inscription.id
        )
        db.session.add(abs1)
        db.session.commit()

        self._login_admin()
        resp = self.client.get('/absences/export_excel')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", resp.content_type)

    # 6. Le professeur peut enregistrer les absences via la prise d'appel
    def test_06_professeur_can_record_absences_via_appel(self):
        self._login_prof()
        count_before = Absence.query.count()

        resp = self.client.post(
            f'/absences/appel?classe_id={self.classe.id}&cours_id={self.cours.id}&date_appel=2025-10-22',
            data={'absent_inscription_ids': [str(self.inscription.id)]},
            follow_redirects=True
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Absence.query.count(), count_before + 1)
        created_abs = Absence.query.filter_by(
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            date_absence=date(2025, 10, 22)
        ).first()
        self.assertIsNotNone(created_abs)
        self.assertFalse(created_abs.justifiee)
        self.assertEqual(created_abs.motif, "Absence signalee pendant l'appel")

    # 7. L'admin ne peut pas accéder à la prise d'appel (réservée au professeur)
    def test_07_admin_cannot_access_faire_appel(self):
        self._login_admin()
        resp = self.client.get(
            f'/absences/appel?classe_id={self.classe.id}&cours_id={self.cours.id}',
            follow_redirects=False
        )
        # Redirigé ou 403 (selon le décorateur role_required)
        self.assertIn(resp.status_code, [302, 403])

    # 8. Le professeur peut consulter le suivi des absences de ses enseignements
    def test_08_professeur_can_consult_absences(self):
        abs1 = Absence(
            date_absence=date(2025, 10, 10),
            motif="Absence cours math",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=self.cours.id,
            ecole_id=self.ecole.id,
            inscription_id=self.inscription.id
        )
        db.session.add(abs1)
        db.session.commit()

        self._login_prof()
        resp = self.client.get('/absences')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Suivi des absences", html)
        self.assertIn("Ali Oumarou", html)


if __name__ == '__main__':
    unittest.main()
