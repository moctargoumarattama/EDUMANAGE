import unittest
from datetime import date, time

from app import create_app, db
from app.config import Config
from app.models import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    EmploiTemps,
    Inscription,
    NiveauScolaire,
    Note,
    Professeur,
    Utilisateur,
)
from app.services.classes_annuelles import preparer_structure_annee, set_classe_ouverte
from app.services.niveaux import creer_classe_depuis_niveau, ensure_ecole_niveau_configs


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2C6AffectationsProfesseursTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A", onboarding_complete=True)
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()
        ensure_ecole_niveau_configs(self.ecole_a.id, commit=True)
        ensure_ecole_niveau_configs(self.ecole_b.id, commit=True)

        self.n6 = NiveauScolaire.query.filter_by(code="6E").first()
        self.source = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.target = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        self.archivee = AnneeScolaire(nom="2024-2025", date_debut=date(2024, 9, 1), date_fin=date(2025, 7, 31), statut="archivee", ecole_id=self.ecole_a.id)
        self.annee_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=self.ecole_b.id)
        db.session.add_all([self.source, self.target, self.archivee, self.annee_b])
        db.session.commit()

        from app.services.structure_annuelle import sauvegarder_structure_annee
        sauvegarder_structure_annee(self.ecole_a.id, self.source.id, [self.n6.id])
        sauvegarder_structure_annee(self.ecole_a.id, self.target.id, [self.n6.id])
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b.id, [self.n6.id])
        db.session.commit()

        self.classe_source, error = creer_classe_depuis_niveau(self.ecole_a.id, self.source.id, self.n6.id, section="A", capacite=30)
        self.assertIsNone(error)
        self.classe_archivee = Classe(
            nom="6e Archivee",
            niveau=self.n6.nom,
            niveau_id=self.n6.id,
            section="A",
            capacite=30,
            capacite_max=30,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.archivee.id,
        )
        db.session.add(self.classe_archivee)
        db.session.flush()
        self.classe_b, error = creer_classe_depuis_niveau(self.ecole_b.id, self.annee_b.id, self.n6.id, section="A", capacite=30)
        self.assertIsNone(error)

        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.super_admin = Utilisateur(nom="Root", email="root@test.local", mot_de_passe="x", role="super_admin")
        self.parent = Utilisateur(nom="Parent", email="parent@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        self.prof_user = Utilisateur(nom="ProfUser", email="prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.prof_ali_user = Utilisateur(nom="Ali", email="ali@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.prof_moussa_user = Utilisateur(nom="Moussa", email="moussa@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.prof_b_user = Utilisateur(nom="B", email="b@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin, self.super_admin, self.parent, self.prof_user, self.prof_ali_user, self.prof_moussa_user, self.prof_b_user])
        db.session.flush()

        self.prof_ali = Professeur(nom="Ali", prenom="Prof", specialite="Math", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_ali_user.id)
        self.prof_moussa = Professeur(nom="Moussa", prenom="Prof", specialite="Math", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_moussa_user.id)
        self.prof_connecte = Professeur(nom="Connecte", prenom="Prof", specialite="Math", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_user.id)
        self.prof_b = Professeur(nom="B", prenom="Prof", specialite="Math", ecole_id=self.ecole_b.id, utilisateur_id=self.prof_b_user.id)
        db.session.add_all([self.prof_ali, self.prof_moussa, self.prof_connecte, self.prof_b])
        db.session.flush()

        self.math_source = Cours(nom="Mathematiques", coefficient=4, ecole_id=self.ecole_a.id, classe_id=self.classe_source.id, professeur_id=self.prof_ali.id)
        self.archive_course = Cours(nom="Mathematiques", coefficient=4, ecole_id=self.ecole_a.id, classe_id=self.classe_archivee.id, professeur_id=self.prof_ali.id)
        self.course_b = Cours(nom="Mathematiques", coefficient=4, ecole_id=self.ecole_b.id, classe_id=self.classe_b.id, professeur_id=self.prof_b.id)
        db.session.add_all([self.math_source, self.archive_course, self.course_b])
        db.session.flush()

        self.eleve = Eleve(nom="Eleve", prenom="A", date_naissance=date(2014, 1, 1), ecole_id=self.ecole_a.id)
        db.session.add(self.eleve)
        db.session.flush()
        db.session.add_all([
            Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.source.id, classe_id=self.classe_source.id),
            Note(valeur=12, coefficient=1, eleve_id=self.eleve.id, cours_id=self.math_source.id, ecole_id=self.ecole_a.id, annee_id=self.source.id),
            Absence(eleve_id=self.eleve.id, cours_id=self.math_source.id, ecole_id=self.ecole_a.id, date_absence=date(2026, 1, 10)),
            EmploiTemps(professeur_id=self.prof_ali.id, jour="Lundi", heure_debut=time(8, 0), heure_fin=time(9, 0), cours_id=self.math_source.id, classe_id=self.classe_source.id, ecole_id=self.ecole_a.id),
        ])
        db.session.commit()

        result, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.source.id)
        self.assertIsNone(error)
        self.target_classe = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom=self.classe_source.nom).first()
        self.target_course = Cours.query.filter_by(ecole_id=self.ecole_a.id, classe_id=self.target_classe.id, nom="Mathematiques").first()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user, ecole_id=None):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            if ecole_id:
                session["ecole_id"] = ecole_id
        return client

    def test_parcours_annuel_affectations_et_protections(self):
        self.assertIsNone(self.target_course.professeur_id)

        client = self.login_as(self.admin)
        with client.session_transaction() as session:
            session["annee_consultee"] = {str(self.ecole_a.id): self.target.id}
        response = client.get("/cours")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mathematiques", response.data)
        self.assertIn(b"Non affect", response.data)
        self.assertNotIn(b"Prof B", response.data)

        with client.session_transaction() as session:
            session["annee_consultee"] = {str(self.ecole_a.id): self.source.id}
        response = client.get("/cours")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Prof Ali", response.data)

        response = client.post(f"/cours/{self.target_course.id}/professeur", data={"professeur_id": self.prof_moussa.id})
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.target_course)
        db.session.refresh(self.math_source)
        self.assertEqual(self.target_course.professeur_id, self.prof_moussa.id)
        self.assertEqual(self.math_source.professeur_id, self.prof_ali.id)

        response = client.post(f"/cours/{self.target_course.id}/professeur", data={"professeur_id": self.prof_ali.id})
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.target_course)
        self.assertEqual(self.target_course.professeur_id, self.prof_ali.id)

        response = client.post(f"/cours/{self.target_course.id}/professeur", data={"professeur_id": "0"})
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.target_course)
        self.assertIsNone(self.target_course.professeur_id)

        response = client.post(f"/cours/{self.target_course.id}/professeur", json={"professeur_id": self.prof_b.id})
        self.assertEqual(response.status_code, 400)
        db.session.refresh(self.target_course)
        self.assertIsNone(self.target_course.professeur_id)

        response = client.post(f"/cours/{self.course_b.id}/professeur", json={"professeur_id": self.prof_moussa.id})
        self.assertEqual(response.status_code, 400)

        response = client.post(f"/cours/{self.archive_course.id}/professeur", json={"professeur_id": self.prof_moussa.id})
        self.assertEqual(response.status_code, 400)
        db.session.refresh(self.archive_course)
        self.assertEqual(self.archive_course.professeur_id, self.prof_ali.id)

        self.target_course.professeur_id = self.prof_ali.id
        db.session.commit()
        _, error = set_classe_ouverte(self.ecole_a.id, self.target_classe.id, False)
        self.assertIsNone(error)
        response = client.post(f"/cours/{self.target_course.id}/professeur", json={"professeur_id": self.prof_moussa.id})
        self.assertEqual(response.status_code, 400)
        db.session.refresh(self.target_course)
        self.assertEqual(self.target_course.professeur_id, self.prof_ali.id)

        _, error = set_classe_ouverte(self.ecole_a.id, self.target_classe.id, True)
        self.assertIsNone(error)
        response = client.post(f"/cours/{self.target_course.id}/professeur", data={"professeur_id": self.prof_moussa.id})
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.target_course)
        self.assertEqual(self.target_course.professeur_id, self.prof_moussa.id)

        prof_client = self.login_as(self.prof_user)
        self.assertEqual(prof_client.post(f"/cours/{self.target_course.id}/professeur", json={"professeur_id": self.prof_ali.id}).status_code, 403)

        parent_client = self.login_as(self.parent)
        self.assertEqual(parent_client.post(f"/cours/{self.target_course.id}/professeur", json={"professeur_id": self.prof_ali.id}).status_code, 403)

        super_client = self.login_as(self.super_admin, ecole_id=self.ecole_a.id)
        self.assertEqual(super_client.post(f"/cours/{self.target_course.id}/professeur", data={"professeur_id": self.prof_ali.id}).status_code, 302)
        self.assertEqual(super_client.post(f"/cours/{self.target_course.id}/professeur", json={"professeur_id": self.prof_b.id}).status_code, 400)

        self.assertEqual(Professeur.query.count(), 4)
        self.assertEqual(Note.query.count(), 1)
        self.assertEqual(Absence.query.count(), 1)
        self.assertEqual(EmploiTemps.query.count(), 1)
        self.assertEqual(Inscription.query.count(), 1)

    def test_assigner_classes_affiche_classe_avec_ou_sans_cours_et_isole_ecole(self):
        self.prof_ali.specialite = "Non renseignée"
        self.prof_ali.matieres_enseignees = "Science"
        classe_5b = Classe(
            nom="5e B",
            niveau="5e",
            section="B",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.target.id,
        )
        autre_annee = Classe(
            nom="Autre Annee",
            niveau="5e",
            section="Y",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.source.id,
        )
        classe_4a = Classe(
            nom="4e A",
            niveau="4e",
            section="A",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.target.id,
        )
        classe_3a = Classe(
            nom="3e A",
            niveau="3e",
            section="A",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.target.id,
        )
        autre_ecole = Classe(
            nom="Classe Autre Ecole",
            niveau="5e",
            section="X",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_b.id,
        )
        db.session.add_all([classe_5b, classe_4a, classe_3a, autre_annee, autre_ecole])
        db.session.flush()
        db.session.delete(self.target_course)
        db.session.flush()
        cours_5b = Cours(
            nom="Science",
            coefficient=1,
            ecole_id=self.ecole_a.id,
            classe_id=classe_5b.id,
            professeur_id=self.prof_ali.id,
        )
        cours_math_4a = Cours(
            nom="Mathematiques",
            coefficient=1,
            ecole_id=self.ecole_a.id,
            classe_id=classe_4a.id,
        )
        cours_science_3a_moussa = Cours(
            nom="Science",
            coefficient=1,
            ecole_id=self.ecole_a.id,
            classe_id=classe_3a.id,
            professeur_id=self.prof_moussa.id,
        )
        db.session.add_all([cours_5b, cours_math_4a, cours_science_3a_moussa])
        db.session.add(
            Inscription(
                ecole_id=self.ecole_a.id,
                eleve_id=self.eleve.id,
                annee_scolaire_id=self.target.id,
                classe_id=self.target_classe.id,
            )
        )
        db.session.commit()

        client = self.login_as(self.admin)
        with client.session_transaction() as session:
            session["annee_consultee"] = {str(self.ecole_a.id): self.target.id}

        response = client.get(f"/professeur/{self.prof_ali.id}/assigner_classes")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("5e B - Science", html)
        self.assertIn(f'value="{cours_5b.id}"', html)
        self.assertNotIn(f'value="new:{classe_5b.id}:0"', html)
        self.assertIn("(deja affecte)", html)
        self.assertIn(f'value="new:{self.target_classe.id}:0"', html)
        self.assertIn(f"{self.target_classe.nom} - Science (creer et affecter)", html)
        self.assertIn(f'value="new:{classe_4a.id}:0"', html)
        self.assertIn("4e A - Science (creer et affecter)", html)
        self.assertNotIn(f'value="{cours_science_3a_moussa.id}"', html)
        self.assertNotIn(f'value="new:{classe_3a.id}:0"', html)
        self.assertNotIn("5e B - Non renseign", html)
        self.assertNotIn("5e B - Science (creer et affecter)", html)
        self.assertNotIn("Classe Autre Ecole", html)
        self.assertNotIn("Autre Annee", html)

        response = client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={"action": "assign", "cours_id": f"new:{self.target_classe.id}"},
        )
        self.assertEqual(response.status_code, 302)
        cours_6a = Cours.query.filter_by(
            ecole_id=self.ecole_a.id,
            classe_id=self.target_classe.id,
            nom="Science",
        ).all()
        self.assertEqual(len(cours_6a), 1)
        self.assertEqual(cours_6a[0].professeur_id, self.prof_ali.id)

        response = client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={"action": "assign", "cours_id": f"new:{self.target_classe.id}"},
        )
        self.assertEqual(response.status_code, 302)
        cours_6a = Cours.query.filter_by(
            ecole_id=self.ecole_a.id,
            classe_id=self.target_classe.id,
            nom="Science",
        ).all()
        self.assertEqual(len(cours_6a), 1)
        self.assertEqual(cours_6a[0].professeur_id, self.prof_ali.id)

        self.assertEqual(
            Cours.query.filter_by(
                ecole_id=self.ecole_a.id,
                classe_id=classe_5b.id,
                nom="Science",
            ).count(),
            1,
        )
        self.assertEqual(
            Cours.query.filter_by(
                ecole_id=self.ecole_a.id,
                classe_id=classe_4a.id,
                nom="Science",
            ).count(),
            0,
        )

        response = client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={"action": "assign", "cours_id": f"new:{classe_3a.id}:0"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(cours_science_3a_moussa)
        self.assertEqual(cours_science_3a_moussa.professeur_id, self.prof_moussa.id)

        response = client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={"action": "assign", "cours_id": str(cours_science_3a_moussa.id)},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(cours_science_3a_moussa)
        self.assertEqual(cours_science_3a_moussa.professeur_id, self.prof_moussa.id)

        response = client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={"action": "assign", "cours_id": f"new:{autre_ecole.id}"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(
            Cours.query.filter_by(
                ecole_id=self.ecole_a.id,
                classe_id=autre_ecole.id,
                nom="Science",
            ).first()
        )

        response = client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={"action": "assign", "cours_id": f"new:{autre_annee.id}"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(
            Cours.query.filter_by(
                ecole_id=self.ecole_a.id,
                classe_id=autre_annee.id,
                nom="Science",
            ).first()
        )

    def test_multi_matieres_independantes_sur_classes_et_modules_professeur(self):
        from app.services.absences_annuelles import get_classes_absences, get_inscriptions_absences
        from app.services.notes_annuelles import get_cours_annee

        self.prof_ali.specialite = "Non renseignée"
        self.prof_ali.matieres_enseignees = "Science, Francais"
        self.prof_moussa.specialite = "Science"
        classe_5b = Classe(
            nom="5e B",
            niveau="5e",
            section="B",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.target.id,
        )
        eleve_5b = Eleve(
            nom="Eleve",
            prenom="B",
            date_naissance=date(2014, 2, 2),
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([classe_5b, eleve_5b])
        db.session.flush()

        self.target_course.nom = "Science"
        self.target_course.professeur_id = self.prof_ali.id
        science_5b = Cours(
            nom="Science",
            coefficient=1,
            ecole_id=self.ecole_a.id,
            classe_id=classe_5b.id,
            professeur_id=self.prof_ali.id,
        )
        francais_5b = Cours(
            nom="Francais",
            coefficient=1,
            ecole_id=self.ecole_a.id,
            classe_id=classe_5b.id,
            professeur_id=self.prof_ali.id,
        )
        db.session.add_all([science_5b, francais_5b])
        db.session.add_all([
            Inscription(
                ecole_id=self.ecole_a.id,
                eleve_id=self.eleve.id,
                annee_scolaire_id=self.target.id,
                classe_id=self.target_classe.id,
            ),
            Inscription(
                ecole_id=self.ecole_a.id,
                eleve_id=eleve_5b.id,
                annee_scolaire_id=self.target.id,
                classe_id=classe_5b.id,
            ),
        ])
        db.session.commit()

        prof_client = self.login_as(self.prof_ali_user)
        with prof_client.session_transaction() as session:
            session["annee_consultee"] = {str(self.ecole_a.id): self.target.id}

        response = prof_client.get("/mes_enseignements")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("5e B", html)
        self.assertIn(self.target_classe.nom, html)
        self.assertIn("Science", html)
        self.assertIn("Francais", html)

        cours_notes = get_cours_annee(self.ecole_a.id, self.target, user=self.prof_ali_user)
        cours_par_classe = {(cours.classe_id, cours.nom) for cours in cours_notes}
        self.assertEqual(
            cours_par_classe,
            {
                (self.target_classe.id, "Science"),
                (classe_5b.id, "Science"),
                (classe_5b.id, "Francais"),
            },
        )

        classes_absences = get_classes_absences(self.ecole_a.id, self.target, self.prof_ali_user)
        self.assertEqual({classe.id for classe in classes_absences}, {self.target_classe.id, classe_5b.id})
        inscriptions_absences = get_inscriptions_absences(self.ecole_a.id, self.target, self.prof_ali_user)
        inscription_ids = [inscription.id for inscription in inscriptions_absences]
        self.assertEqual(len(inscription_ids), len(set(inscription_ids)))
        self.assertEqual(len(inscription_ids), 2)

        admin_client = self.login_as(self.admin)
        with admin_client.session_transaction() as session:
            session["annee_consultee"] = {str(self.ecole_a.id): self.target.id}

        response = admin_client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={"action": "remove", "cours_id": science_5b.id},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(science_5b)
        db.session.refresh(francais_5b)
        self.assertIsNone(science_5b.professeur_id)
        self.assertEqual(francais_5b.professeur_id, self.prof_ali.id)

        science_5b.professeur_id = self.prof_ali.id
        db.session.commit()
        response = admin_client.post(
            f"/professeur/{self.prof_ali.id}/assigner_classes",
            data={
                "action": "change",
                "cours_id": science_5b.id,
                "nouveau_professeur_id": self.prof_moussa.id,
            },
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(science_5b)
        db.session.refresh(francais_5b)
        self.assertEqual(science_5b.professeur_id, self.prof_moussa.id)
        self.assertEqual(francais_5b.professeur_id, self.prof_ali.id)


if __name__ == "__main__":
    unittest.main()
