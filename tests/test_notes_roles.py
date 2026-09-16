"""
Tests KLASORA — Module Notes & Évaluations : Respect des rôles et sécurisation métier.
1. Admin consulte le suivi des évaluations (GET /notes) avec le titre « Suivi des évaluations »
2. Admin ne voit pas les boutons « Saisie par classe » ni « Saisie individuelle »
3. Admin ne peut pas créer de nouvelle note via POST /notes (bloqué serveur)
4. Admin ne peut pas accéder à la saisie par classe (/notes/saisie_classe -> 403)
5. Admin voit les moyennes par matière et l'indicateur d'avancement « X/Y matières renseignées »
6. L'indicateur X/Y respecte rigoureusement la période sélectionnée (notes S2 ne comptent pas dans S1)
7. Professeur peut enregistrer des notes par classe (saisie_notes_classe)
8. Professeur peut enregistrer une note individuelle (POST /notes)
9. Admin peut corriger une note existante en cas d'erreur (/note/<id>/modifier)
10. Parent ne voit strictement que les notes de son propre enfant
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    PeriodeBulletin,
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
    SECRET_KEY = "test-notes-roles"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestNotesRoles(unittest.TestCase):

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

        # 3. Niveau & Config
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

        # 4. Classe
        self.classe = Classe(
            nom="6ème A",
            niveau_id=self.niveau.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            statut="ouverte"
        )
        db.session.add(self.classe)
        db.session.commit()

        # 5. Utilisateurs : Admin, Professeur, Parent
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

        self.user_parent = Utilisateur(
            nom="Oumarou",
            prenom="Parent",
            email="parent@klasora.ne",
            role="parent",
            ecole_id=self.ecole.id
        )
        self.user_parent.set_mot_de_passe("ParentPass123")

        db.session.add_all([self.user_admin, self.user_prof, self.user_parent])
        db.session.commit()

        # Profil Professeur
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

        # 6. Cours (Matières attendues dans la classe : Maths et Français)
        self.cours_math = Cours(
            nom="Mathématiques",
            coefficient=2.0,
            classe_id=self.classe.id,
            professeur_id=self.prof.id,
            ecole_id=self.ecole.id
        )
        self.cours_fr = Cours(
            nom="Français",
            coefficient=2.0,
            classe_id=self.classe.id,
            professeur_id=self.prof.id,
            ecole_id=self.ecole.id
        )
        db.session.add_all([self.cours_math, self.cours_fr])
        db.session.commit()

        # 7. Élèves (Ali enfant du parent, Moussa autre enfant)
        self.eleve1 = Eleve(
            nom="Oumarou",
            prenom="Ali",
            date_naissance=date(2013, 5, 12),
            ecole_id=self.ecole.id,
            classe_id=self.classe.id
        )
        self.eleve2 = Eleve(
            nom="Diallo",
            prenom="Moussa",
            date_naissance=date(2013, 8, 20),
            ecole_id=self.ecole.id,
            classe_id=self.classe.id
        )
        db.session.add_all([self.eleve1, self.eleve2])
        db.session.commit()

        self.ins1 = Inscription(
            eleve_id=self.eleve1.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=120000.0,
            statut="inscrit"
        )
        self.ins2 = Inscription(
            eleve_id=self.eleve2.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=120000.0,
            statut="inscrit"
        )
        db.session.add_all([self.ins1, self.ins2])
        db.session.commit()

        # Élève 1 est rattaché au compte parent
        self.eleve1.parent_id = self.user_parent.id
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_as(self, user, ecole_id=None):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            sess["role"] = user.role
            sess["ecole_id"] = ecole_id if ecole_id is not None else user.ecole_id
        return client

    # 1. Admin consulte le suivi des évaluations et voit le bon titre
    def test_01_admin_can_view_notes_page_and_header(self):
        client = self.login_as(self.user_admin)
        resp = client.get('/notes')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Suivi des évaluations", html)
        self.assertNotIn("btnSaisieNotesClasse", html)
        self.assertNotIn("btnToggleAddNote", html)
        self.assertNotIn("Saisie par classe", html)
        self.assertNotIn("Saisie individuelle", html)

    # 2. Admin ne peut pas créer de note via POST /notes (bloqué côté serveur)
    def test_02_admin_cannot_create_note_post(self):
        client = self.login_as(self.user_admin)
        count_before = Note.query.count()

        resp = client.post('/notes', data={
            'eleve_id': self.eleve1.id,
            'cours_id': self.cours_math.id,
            'valeur': '16.0',
            'coefficient': '2.0',
            'type_evaluation': 'Devoir',
            'periode': 'Semestre 1'
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Note.query.count(), count_before)
        self.assertIn("La saisie des notes est réservée aux professeurs", resp.get_data(as_text=True))

    # 3. Admin ne peut pas accéder à /notes/saisie_classe (interdit côté serveur)
    def test_03_admin_cannot_access_saisie_classe(self):
        client = self.login_as(self.user_admin)
        resp = client.get('/notes/saisie_classe', follow_redirects=False)
        self.assertIn(resp.status_code, [302, 403])

    # 4. Professeur peut accéder à /notes/saisie_classe et saisir des notes
    def test_04_professeur_can_access_and_submit_saisie_classe(self):
        client = self.login_as(self.user_prof)
        resp = client.get('/notes/saisie_classe')
        self.assertEqual(resp.status_code, 200)

        # Enregistrer des notes pour la classe
        resp_post = client.post('/notes/saisie_classe', data={
            'classe_id': str(self.classe.id),
            'cours_id': str(self.cours_math.id),
            'periode': 'Semestre 1',
            'type_evaluation': 'Devoir',
            'coefficient': '2.0',
            f'note_{self.eleve1.id}': '15.5',
            f'note_{self.eleve2.id}': '12.0',
        }, follow_redirects=True)

        self.assertEqual(resp_post.status_code, 200)
        n1 = Note.query.filter_by(eleve_id=self.eleve1.id, cours_id=self.cours_math.id).first()
        self.assertIsNotNone(n1)
        self.assertEqual(n1.valeur, 15.5)

    # 5. Professeur peut saisir une note individuelle via POST /notes
    def test_05_professeur_can_create_individual_note_post(self):
        client = self.login_as(self.user_prof)
        count_before = Note.query.count()

        resp = client.post('/notes', data={
            'eleve_id': str(self.eleve1.id),
            'cours_id': str(self.cours_fr.id),
            'valeur': '14.0',
            'coefficient': '2.0',
            'type_evaluation': 'Devoir',
            'periode': 'Semestre 1',
            'annee_id': str(self.annee.id)
        }, follow_redirects=True)

        self.assertEqual(resp_post := resp, resp_post)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Note.query.count(), count_before + 1)
        n_fr = Note.query.filter_by(eleve_id=self.eleve1.id, cours_id=self.cours_fr.id).first()
        self.assertIsNotNone(n_fr)
        self.assertEqual(n_fr.valeur, 14.0)

    # 6. Admin voit les moyennes par matière et l'indicateur d'avancement X/Y
    def test_06_admin_sees_subject_averages_and_advancement_indicator(self):
        # 1 note de maths pour Eleve 1 au Semestre 1
        n1 = Note(
            valeur=16.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Semestre 1",
            date_evaluation=datetime(2025, 10, 15),
            eleve_id=self.eleve1.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id
        )
        db.session.add(n1)
        db.session.commit()

        client = self.login_as(self.user_admin)
        resp = client.get('/notes?periode=Semestre+1')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Vérifier l'indicateur 1/2 matières renseignées pour l'élève 1 (Maths noté, Français manquant)
        self.assertIn("1/2 matière", html)
        self.assertIn("Moyenne matière : 16.00/20", html)
        self.assertIn("En attente", html)
        self.assertIn("Français", html)

    # 7. L'indicateur X/Y respecte rigoureusement la période sélectionnée
    def test_07_indicator_respects_selected_period_strictly(self):
        # Eleve 1 a :
        # - Maths noté au Semestre 1
        # - Français noté UNIQUEMENT au Semestre 2
        n_s1 = Note(
            valeur=15.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Semestre 1",
            date_evaluation=datetime(2025, 10, 10),
            eleve_id=self.eleve1.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id
        )
        n_s2 = Note(
            valeur=18.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Semestre 2",
            date_evaluation=datetime(2026, 3, 15),
            eleve_id=self.eleve1.id,
            cours_id=self.cours_fr.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id
        )
        db.session.add_all([n_s1, n_s2])
        db.session.commit()

        client = self.login_as(self.user_admin)

        # Consultation Semestre 1 : seul Maths doit être compté -> 1/2
        resp_s1 = client.get('/notes?periode=Semestre+1')
        self.assertEqual(resp_s1.status_code, 200)
        html_s1 = resp_s1.get_data(as_text=True)
        self.assertIn("1/2 matière", html_s1)
        self.assertIn("En attente", html_s1)
        self.assertIn("Français", html_s1)

        # Consultation Semestre 2 : seul Français doit être compté -> 1/2
        resp_s2 = client.get('/notes?periode=Semestre+2')
        self.assertEqual(resp_s2.status_code, 200)
        html_s2 = resp_s2.get_data(as_text=True)
        self.assertIn("1/2 matière", html_s2)
        self.assertIn("En attente", html_s2)
        self.assertIn("Mathématiques", html_s2)

    # 8. Admin peut modifier exceptionnellement une note existante (/note/<id>/modifier)
    def test_08_admin_can_edit_existing_note(self):
        note = Note(
            valeur=10.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Semestre 1",
            date_evaluation=datetime(2025, 10, 10),
            eleve_id=self.eleve1.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id
        )
        db.session.add(note)
        db.session.commit()

        client = self.login_as(self.user_admin)
        resp = client.post(f'/note/{note.id}/modifier', data={
            'eleve_id': str(self.eleve1.id),
            'cours_id': str(self.cours_math.id),
            'valeur': '17.5',
            'coefficient': '2.0',
            'type_evaluation': 'Devoir',
            'periode': 'Semestre 1',
            'annee_id': str(self.annee.id)
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        db.session.refresh(note)
        self.assertEqual(note.valeur, 17.5)

    # 9. Parent ne voit que les notes de son propre enfant
    def test_09_parent_only_sees_own_children_notes(self):
        # Note pour Eleve 1 (Ali Oumarou - enfant du parent)
        n1 = Note(
            valeur=15.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Semestre 1",
            date_evaluation=datetime(2025, 10, 10),
            eleve_id=self.eleve1.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id
        )
        # Note pour Eleve 2 (Moussa Diallo - autre enfant)
        n2 = Note(
            valeur=11.0,
            coefficient=2.0,
            type_evaluation="Devoir",
            periode="Semestre 1",
            date_evaluation=datetime(2025, 10, 10),
            eleve_id=self.eleve2.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
            inscription_id=self.ins2.id
        )
        db.session.add_all([n1, n2])
        db.session.commit()

        client = self.login_as(self.user_parent)
        resp = client.get('/notes')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Le parent voit son enfant Ali Oumarou
        self.assertIn("Ali Oumarou", html)
        # Le parent ne voit PAS Moussa Diallo
        self.assertNotIn("Moussa Diallo", html)


if __name__ == '__main__':
    unittest.main()
