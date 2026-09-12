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
from app.services.niveaux import (
    creer_classe_depuis_niveau,
    ensure_ecole_niveau_configs,
    set_cycle_actif,
    set_niveau_actif,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2C5StructureUITestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()
        ensure_ecole_niveau_configs(self.ecole_a.id, commit=True)
        ensure_ecole_niveau_configs(self.ecole_b.id, commit=True)

        self.n6 = NiveauScolaire.query.filter_by(code="6E").first()
        self.n5 = NiveauScolaire.query.filter_by(code="5E").first()
        self.nci = NiveauScolaire.query.filter_by(code="CI").first()

        self.source = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.target = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        self.archivee = AnneeScolaire(nom="2024-2025", date_debut=date(2024, 9, 1), date_fin=date(2025, 7, 31), statut="archivee", ecole_id=self.ecole_a.id)
        self.annee_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole_b.id)
        db.session.add_all([self.source, self.target, self.archivee, self.annee_b])
        db.session.commit()

        self.classe_6a, error = creer_classe_depuis_niveau(self.ecole_a.id, self.source.id, self.n6.id, nom="6e A", section="A", capacite=40)
        self.assertIsNone(error)
        self.classe_6b, error = creer_classe_depuis_niveau(self.ecole_a.id, self.source.id, self.n6.id, nom="6e B", section="B", capacite=35)
        self.assertIsNone(error)
        self.classe_ci, error = creer_classe_depuis_niveau(self.ecole_a.id, self.source.id, self.nci.id, nom="CI A", section="A")
        self.assertIsNone(error)
        set_cycle_actif(self.ecole_a.id, "primaire", False)

        prof_user = Utilisateur(nom="Ali", email="ali.prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        db.session.add(prof_user)
        db.session.flush()
        self.professeur = Professeur(nom="Ali", prenom="Prof", specialite="Math", ecole_id=self.ecole_a.id, utilisateur_id=prof_user.id)
        db.session.add(self.professeur)
        db.session.flush()

        self.math = Cours(nom="Mathematiques", description="Base", coefficient=4, ecole_id=self.ecole_a.id, classe_id=self.classe_6a.id, professeur_id=self.professeur.id)
        db.session.add(self.math)
        db.session.flush()

        self.eleve = Eleve(nom="Moussa", prenom="A", date_naissance=date(2014, 1, 1), ecole_id=self.ecole_a.id, classe_id=self.classe_6a.id)
        db.session.add(self.eleve)
        db.session.flush()
        db.session.add_all([
            Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.source.id, classe_id=self.classe_6a.id),
            Note(valeur=13, coefficient=1, eleve_id=self.eleve.id, cours_id=self.math.id, ecole_id=self.ecole_a.id, annee_id=self.source.id),
            Absence(eleve_id=self.eleve.id, cours_id=self.math.id, ecole_id=self.ecole_a.id, date_absence=date(2026, 1, 10)),
            EmploiTemps(professeur_id=self.professeur.id, jour="Lundi", heure_debut=time(8, 0), heure_fin=time(10, 0), cours_id=self.math.id, classe_id=self.classe_6a.id, ecole_id=self.ecole_a.id),
        ])

        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.super_admin = Utilisateur(nom="Root", email="root@test.local", mot_de_passe="x", role="super_admin", ecole_id=None)
        self.prof_user = Utilisateur(nom="ProfUser", email="prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        db.session.add_all([self.admin, self.super_admin, self.prof_user, self.parent])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user, ecole_id=None):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            if ecole_id is not None:
                session["ecole_id"] = ecole_id
        return client

    def test_annees_lien_structure_et_page_planifiee_prepare(self):
        client = self.login_as(self.admin)
        response = client.get("/annees")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/annees/{self.target.id}/structure".encode(), response.data)

        response = client.get(f"/annees/{self.target.id}/structure")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Preparer la structure", response.data)
        self.assertIn(b"Ajouter une classe", response.data)
        self.assertIn(b"6e", response.data)
        self.assertNotIn(b"CI", response.data)

        response = client.post(f"/annees/{self.target.id}/preparer-structure", data={"_redirect_to_structure": "1"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"6e A", response.data)
        self.assertIn(b"Mathematiques", response.data)
        self.assertIn(b"professeur non affecte", response.data)

        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id).count(), 2)
        target_6a = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="6e A").first()
        copied_math = Cours.query.filter_by(ecole_id=self.ecole_a.id, classe_id=target_6a.id, nom="Mathematiques").first()
        self.assertIsNotNone(copied_math)
        self.assertIsNone(copied_math.professeur_id)
        self.assertEqual(Inscription.query.filter_by(annee_scolaire_id=self.target.id).count(), 0)
        self.assertEqual(Note.query.filter_by(annee_id=self.target.id).count(), 0)
        self.assertEqual(Absence.query.join(Cours).filter(Cours.classe_id == target_6a.id).count(), 0)
        self.assertEqual(EmploiTemps.query.filter_by(classe_id=target_6a.id).count(), 0)

        client.post(f"/annees/{self.target.id}/preparer-structure", data={"_redirect_to_structure": "1"}, follow_redirects=True)
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="6e A").count(), 1)
        self.assertEqual(Cours.query.filter_by(ecole_id=self.ecole_a.id, classe_id=target_6a.id, nom="Mathematiques").count(), 1)

    def test_ouvrir_fermer_classe_et_archivee_lecture_seule(self):
        client = self.login_as(self.admin)
        client.post(f"/annees/{self.target.id}/preparer-structure", data={"_redirect_to_structure": "1"})
        classe = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="6e A").first()

        response = client.post(f"/classes/{classe.id}/statut", data={"statut": "fermee"}, headers={"Referer": f"/annees/{self.target.id}/structure"}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/annees/{self.target.id}/structure", response.headers["Location"])
        self.assertEqual(db.session.get(Classe, classe.id).statut, "fermee")

        response = client.get(f"/annees/{self.target.id}/structure")
        self.assertIn(b"Fermee", response.data)
        self.assertIn(b"Ouvrir", response.data)

        self.archivee.statut = "active"
        db.session.flush()
        archived_classe, error = creer_classe_depuis_niveau(self.ecole_a.id, self.archivee.id, self.n6.id, nom="6e Archive", section="A")
        self.assertIsNone(error)
        self.archivee.statut = "archivee"
        db.session.commit()
        response = client.get(f"/annees/{self.archivee.id}/structure")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"consultation seulement", response.data)
        self.assertNotIn(b"Preparer la structure", response.data)
        self.assertNotIn(b"Ajouter une classe", response.data)
        self.assertNotIn(f"/classes/{archived_classe.id}/statut".encode(), response.data)
        self.assertEqual(client.post(f"/classes/{archived_classe.id}/statut", json={"statut": "fermee"}).status_code, 400)

    def test_roles_et_multi_ecoles(self):
        admin_client = self.login_as(self.admin)
        self.assertEqual(admin_client.get(f"/annees/{self.annee_b.id}/structure").status_code, 404)

        super_client = self.login_as(self.super_admin, self.ecole_a.id)
        self.assertEqual(super_client.get(f"/annees/{self.target.id}/structure").status_code, 200)

        prof_client = self.login_as(self.prof_user)
        self.assertIn(prof_client.get(f"/annees/{self.target.id}/structure").status_code, (302, 403))

        parent_client = self.login_as(self.parent)
        self.assertIn(parent_client.get(f"/annees/{self.target.id}/structure").status_code, (302, 403))

    def test_premiere_annee_sans_historique_et_ajout_manuel_cible(self):
        empty_school = Ecole(nom="Nouvelle Ecole")
        db.session.add(empty_school)
        db.session.commit()
        ensure_ecole_niveau_configs(empty_school.id, commit=True)
        n6 = NiveauScolaire.query.filter_by(code="6E").first()
        set_niveau_actif(empty_school.id, n6.id, True)
        active = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=empty_school.id)
        annee = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=empty_school.id)
        admin = Utilisateur(nom="Admin B", email="admin.b@test.local", mot_de_passe="x", role="admin", ecole_id=empty_school.id)
        db.session.add_all([active, annee, admin])
        db.session.commit()

        client = self.login_as(admin)
        response = client.get(f"/annees/{annee.id}/structure")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Aucune classe", response.data)

        response = client.post(f"/annees/{annee.id}/preparer-structure", data={"_redirect_to_structure": "1"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Classe.query.filter_by(ecole_id=empty_school.id, annee_scolaire_id=annee.id).count(), 0)

        response = client.post(
            f"/classes/add?annee_id={annee.id}",
            data={
                "nom": "6e C",
                "niveau_id": n6.id,
                "niveau": n6.nom,
                "section": "C",
                "capacite": 30,
                "effectif": 0,
                "professeur_principal_id": 0,
                "annee_scolaire_id": annee.id,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(Classe.query.filter_by(ecole_id=empty_school.id, annee_scolaire_id=annee.id, nom="6e C").first())

    def test_normalisation_noms_primaire_college_lycee_et_nom_post_ignore(self):
        levels = {code: NiveauScolaire.query.filter_by(code=code).first() for code in ["CM2", "6E", "2NDE", "1ERE", "TERMINALE"]}
        set_niveau_actif(self.ecole_a.id, levels["CM2"].id, True)

        cases = [
            ("CM2", "B", "CM2 B"),
            ("6E", "a", "6e A"),
            ("2NDE", "a", "2nde Serie A"),
            ("1ERE", "C", "1ere Serie C"),
            ("TERMINALE", "D", "Terminale Serie D"),
        ]
        for code, section, expected_name in cases:
            classe, error = creer_classe_depuis_niveau(
                self.ecole_a.id,
                self.target.id,
                levels[code].id,
                nom="Nom POST falsifie",
                section=section,
            )
            self.assertIsNone(error)
            self.assertEqual(classe.section, section.upper())
            self.assertEqual(classe.nom, expected_name)

    def test_sections_invalides_doublons_annee_ecole_et_inactifs(self):
        for invalid in ["AA", "A1", "1", "Bleu", "Serie A", " A "]:
            classe, error = creer_classe_depuis_niveau(self.ecole_a.id, self.target.id, self.n6.id, section=invalid)
            self.assertIsNone(classe)
            self.assertIn("une seule lettre", error)

        classe_a, error = creer_classe_depuis_niveau(self.ecole_a.id, self.target.id, self.n6.id, section="A")
        self.assertIsNone(error)
        duplicate, error = creer_classe_depuis_niveau(self.ecole_a.id, self.target.id, self.n6.id, section="A")
        self.assertIsNone(duplicate)
        self.assertIn("existe deja", error)

        classe_b, error = creer_classe_depuis_niveau(self.ecole_a.id, self.target.id, self.n6.id, section="B")
        self.assertIsNone(error)
        self.assertEqual(classe_b.nom, "6e B")

        next_year = AnneeScolaire(nom="2027-2028", date_debut=date(2027, 9, 1), date_fin=date(2028, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        db.session.add(next_year)
        db.session.commit()
        other_year, error = creer_classe_depuis_niveau(self.ecole_a.id, next_year.id, self.n6.id, section="A")
        self.assertIsNone(error)
        self.assertNotEqual(classe_a.id, other_year.id)

        other_school, error = creer_classe_depuis_niveau(self.ecole_b.id, self.annee_b.id, self.n6.id, section="A")
        self.assertIsNone(error)
        self.assertNotEqual(classe_a.ecole_id, other_school.ecole_id)

        set_niveau_actif(self.ecole_a.id, self.n5.id, False)
        inactive, error = creer_classe_depuis_niveau(self.ecole_a.id, self.target.id, self.n5.id, section="C")
        self.assertIsNone(inactive)
        self.assertIn("desactive", error)

    def test_formulaire_niveaux_actifs_libelles_section_serie_et_post_forge(self):
        deuxnde = NiveauScolaire.query.filter_by(code="2NDE").first()
        set_niveau_actif(self.ecole_a.id, self.n5.id, False)
        client = self.login_as(self.admin)

        response = client.get(f"/classes/add?annee_id={self.target.id}&niveau_id={self.n6.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Section", response.data)
        self.assertIn(b"6e", response.data)
        self.assertNotIn(b"5e</option>", response.data)
        self.assertNotIn(b"Nom de la classe", response.data)

        response = client.get(f"/classes/add?annee_id={self.target.id}&niveau_id={deuxnde.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Série".encode(), response.data)

        response = client.post(
            f"/classes/add?annee_id={self.target.id}",
            data={
                "nom": "Classe Pirate",
                "niveau_id": self.n5.id,
                "niveau": self.n5.nom,
                "section": "A",
                "capacite": 35,
                "effectif": 0,
                "professeur_principal_id": 0,
                "annee_scolaire_id": self.target.id,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="Classe Pirate").first())
        self.assertIsNone(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, niveau_id=self.n5.id, section="A").first())

    def test_structure_affiche_niveaux_actifs_sans_classe_et_actions_par_niveau(self):
        empty_school = Ecole(nom="Structure Vide")
        db.session.add(empty_school)
        db.session.commit()
        ensure_ecole_niveau_configs(empty_school.id, commit=True)
        levels = {code: NiveauScolaire.query.filter_by(code=code).first() for code in ["CI", "CP", "6E", "2NDE", "TERMINALE"]}
        for niveau in NiveauScolaire.query.all():
            set_niveau_actif(empty_school.id, niveau.id, niveau.code in levels)
        active = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=empty_school.id)
        annee = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=empty_school.id)
        admin = Utilisateur(nom="Admin Empty", email="empty@test.local", mot_de_passe="x", role="admin", ecole_id=empty_school.id)
        db.session.add_all([active, annee, admin])
        db.session.commit()

        client = self.login_as(admin)
        response = client.get(f"/annees/{annee.id}/structure")
        self.assertEqual(response.status_code, 200)
        for label in [b"CI", b"CP", b"6e", b"2nde", b"Terminale"]:
            self.assertIn(label, response.data)
        self.assertIn(b"Ajouter une section", response.data)
        self.assertIn(b"Ajouter une serie", response.data)
        self.assertIn(b"Aucune section", response.data)
        self.assertIn(b"Aucune serie", response.data)
        self.assertNotIn(b"CE1", response.data)


if __name__ == "__main__":
    unittest.main()
