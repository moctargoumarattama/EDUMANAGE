import unittest
from datetime import date

from flask import template_rendered

from app import create_app, db
from app.config import Config
from app.models import Absence, AnneeScolaire, Classe, Cours, Ecole, Eleve, Inscription, Professeur, Utilisateur, professeur_classes
from app.services.absences_annuelles import get_absences_annee, verifier_mutation_absence


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase3AAbsencesAnnuellesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.archivee = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="archivee", ecole_id=self.ecole_a.id)
        self.active = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.planifiee = AnneeScolaire(nom="2027-2028", date_debut=date(2027, 9, 1), date_fin=date(2028, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        self.active_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=self.ecole_b.id)
        db.session.add_all([self.archivee, self.active, self.planifiee, self.active_b])
        db.session.flush()

        self.classe_archive = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.archivee.id)
        self.classe_active = Classe(nom="5e A", niveau="5e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id)
        self.classe_plan = Classe(nom="4e A", niveau="4e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.planifiee.id)
        self.classe_b = Classe(nom="5e B", niveau="5e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.active_b.id)
        db.session.add_all([self.classe_archive, self.classe_active, self.classe_plan, self.classe_b])
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="admin3a@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent3a@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        self.prof_user = Utilisateur(nom="Prof", email="prof3a@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Admin B", email="adminb3a@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin, self.parent, self.prof_user, self.admin_b])
        db.session.flush()

        self.prof = Professeur(nom="Diallo", prenom="Ali", email="ali@test.local", utilisateur_id=self.prof_user.id, ecole_id=self.ecole_a.id)
        db.session.add(self.prof)
        db.session.flush()
        db.session.execute(professeur_classes.insert().values(
            professeur_id=self.prof.id,
            classe_id=self.classe_active.id,
            ecole_id=self.ecole_a.id,
        ))

        self.eleve = Eleve(nom="Moussa", prenom="Abdou", date_naissance=date(2014, 2, 3), ecole_id=self.ecole_a.id, classe_id=self.classe_active.id, parent_id=self.parent.id)
        self.eleve_b = Eleve(nom="Awa", prenom="B", date_naissance=date(2014, 5, 6), ecole_id=self.ecole_b.id, classe_id=self.classe_b.id)
        db.session.add_all([self.eleve, self.eleve_b])
        db.session.flush()

        self.cours_archive = Cours(nom="Math archive", ecole_id=self.ecole_a.id, classe_id=self.classe_archive.id)
        self.cours_active = Cours(nom="Math active", ecole_id=self.ecole_a.id, classe_id=self.classe_active.id, professeur_id=self.prof.id)
        self.cours_plan = Cours(nom="Math plan", ecole_id=self.ecole_a.id, classe_id=self.classe_plan.id)
        self.cours_b = Cours(nom="Math B", ecole_id=self.ecole_b.id, classe_id=self.classe_b.id)
        db.session.add_all([self.cours_archive, self.cours_active, self.cours_plan, self.cours_b])
        db.session.flush()

        self.insc_archive = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.archivee.id, classe_id=self.classe_archive.id)
        self.insc_active = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.active.id, classe_id=self.classe_active.id)
        self.insc_planifiee = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.planifiee.id, classe_id=self.classe_plan.id)
        self.insc_b = Inscription(ecole_id=self.ecole_b.id, eleve_id=self.eleve_b.id, annee_scolaire_id=self.active_b.id, classe_id=self.classe_b.id)
        db.session.add_all([self.insc_archive, self.insc_active, self.insc_planifiee, self.insc_b])
        db.session.flush()

        # Absence historique SANS inscription_id (simule données existantes avant 3A-Bis)
        self.abs_archive = Absence(
            date_absence=date(2026, 1, 10), motif="Historique", justifiee=False,
            eleve_id=self.eleve.id, cours_id=self.cours_archive.id, ecole_id=self.ecole_a.id,
            inscription_id=None
        )
        # Absence active AVEC inscription_id
        self.abs_active = Absence(
            date_absence=date(2026, 10, 10), motif="Actuelle", justifiee=False,
            eleve_id=self.eleve.id, cours_id=self.cours_active.id, ecole_id=self.ecole_a.id,
            inscription_id=None  # sera mis à jour dans le test d'ancrage
        )
        self.abs_b = Absence(
            date_absence=date(2026, 10, 11), motif="Autre ecole", justifiee=False,
            eleve_id=self.eleve_b.id, cours_id=self.cours_b.id, ecole_id=self.ecole_b.id,
            inscription_id=None
        )
        db.session.add_all([self.abs_archive, self.abs_active, self.abs_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _client_as(self, user, annee=None):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            if annee:
                session["annee_consultee"] = {str(user.ecole_id): annee.id}
        return client

    def _capture_templates(self):
        recorded = []

        def record(_sender, template, context, **_extra):
            recorded.append((template, context))

        template_rendered.connect(record, self.app)
        return recorded, record

    # ==========================================================================
    # GROUPE 0 : Tests existants (6 tests de base)
    # ==========================================================================

    def test_01_lecture_absences_depuis_inscriptions_annuelles_et_isolation_ecole(self):
        """1. Les absences lues sont isolées par année + école (chemin de secours date-based)."""
        absences_archive = get_absences_annee(self.ecole_a.id, self.archivee, self.admin)
        absences_active = get_absences_annee(self.ecole_a.id, self.active, self.admin)

        self.assertEqual([a.id for a in absences_archive], [self.abs_archive.id])
        self.assertEqual(absences_archive[0].annee_classe.id, self.classe_archive.id)
        self.assertEqual([a.id for a in absences_active], [self.abs_active.id])
        self.assertNotIn(self.abs_b.id, [a.id for a in absences_active])

    def test_02_route_absences_ignore_annee_id_url_et_utilise_session(self):
        """2. La route /absences ignore annee_id dans l'URL et utilise uniquement la session (règle 2C-5D)."""
        client = self._client_as(self.admin, self.archivee)
        recorded, receiver = self._capture_templates()
        try:
            response = client.get(f"/absencesannee_id={self.active.id}")
            self.assertEqual(response.status_code, 200)
            context = recorded[-1][1]
            self.assertEqual(context["annee_consultee"].id, self.archivee.id)
            self.assertEqual([a.id for a in context["absences"]], [self.abs_archive.id])
            self.assertFalse(context["can_mutate"])
        finally:
            template_rendered.disconnect(receiver, self.app)

    def test_03_creation_active_autorisee_archivee_et_planifiee_refusees(self):
        """3. Création autorisée sur année active ; refusée sur archivée et planifiée."""
        client = self._client_as(self.admin, self.active)
        response = client.post("/absences", data={
            "eleve_id": self.eleve.id,
            "cours_id": self.cours_active.id,
            "date_absence": "2026-10-12",
            "motif": "Nouvelle",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Absence.query.filter_by(motif="Nouvelle").count(), 1)

        for annee in (self.archivee, self.planifiee):
            client = self._client_as(self.admin, annee)
            response = client.post("/absences", data={
                "eleve_id": self.eleve.id,
                "cours_id": self.cours_archive.id if annee == self.archivee else self.cours_plan.id,
                "date_absence": annee.date_debut.isoformat(),
                "motif": "Interdite",
            }, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Absence.query.filter_by(motif="Interdite").count(), 0)

    def test_04_mutation_refuse_cours_hors_annee_et_ecole(self):
        """4. verifier_mutation_absence refuse cours hors année et élève hors école."""
        _eleve, _cours, _inscription, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, self.admin, self.eleve.id, self.cours_archive.id, date(2026, 10, 10)
        )
        self.assertIn("Cours non disponible", error)

        _eleve, _cours, _inscription, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, self.admin, self.eleve_b.id, self.cours_active.id, date(2026, 10, 10)
        )
        self.assertIn("non inscrit", error)

    def test_05_professeur_limite_a_ses_cours_et_parent_lecture_seule(self):
        """5. Professeur peut créer sur ses cours ; parent en lecture seule."""
        prof_client = self._client_as(self.prof_user, self.active)
        response = prof_client.post("/absences", data={
            "eleve_id": self.eleve.id,
            "cours_id": self.cours_active.id,
            "date_absence": "2026-10-13",
            "motif": "Prof ok",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Absence.query.filter_by(motif="Prof ok").count(), 1)

        parent_client = self._client_as(self.parent, self.active)
        recorded, receiver = self._capture_templates()
        try:
            response = parent_client.get("/absences")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(recorded[-1][1]["can_mutate"])
            self.assertTrue(all(a.eleve_id == self.eleve.id for a in recorded[-1][1]["absences"]))
        finally:
            template_rendered.disconnect(receiver, self.app)

    def test_06_edit_delete_archive_refuses_active_delete_accepts(self):
        """6. Suppression/édition refusée sur archivée ; acceptée sur active."""
        archive_client = self._client_as(self.admin, self.archivee)
        response = archive_client.post(f"/absences/delete/{self.abs_archive.id}", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(db.session.get(Absence, self.abs_archive.id))

        active_client = self._client_as(self.admin, self.active)
        response = active_client.post(f"/absences/delete/{self.abs_active.id}", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(db.session.get(Absence, self.abs_active.id))

    # ==========================================================================
    # GROUPE 1 : Ancrage inscription_id — modèle et schéma
    # ==========================================================================

    def test_07_absence_a_colonne_inscription_id(self):
        """7. Le modèle Absence possède la colonne inscription_id."""
        from sqlalchemy import inspect
        insp = inspect(Absence)
        col_names = [c.key for c in insp.columns]
        self.assertIn("inscription_id", col_names)

    def test_08_absence_inscription_id_nullable(self):
        """8. inscription_id est nullable (les absences historiques gardent NULL)."""
        self.assertIsNone(self.abs_archive.inscription_id)
        self.assertIsNone(self.abs_active.inscription_id)

    def test_09_absence_inscription_id_peut_etre_assigne(self):
        """9. On peut assigner inscription_id directement sur une Absence."""
        self.abs_active.inscription_id = self.insc_active.id
        db.session.commit()
        db.session.refresh(self.abs_active)
        self.assertEqual(self.abs_active.inscription_id, self.insc_active.id)

    def test_10_absence_relationship_inscription(self):
        """10. Absence.inscription est une relationship vers Inscription."""
        self.abs_active.inscription_id = self.insc_active.id
        db.session.commit()
        db.session.refresh(self.abs_active)
        self.assertIsNotNone(self.abs_active.inscription)
        self.assertEqual(self.abs_active.inscription.id, self.insc_active.id)

    def test_11_backref_inscription_absences(self):
        """11. Inscription.absences retourne les absences liées (backref)."""
        self.abs_active.inscription_id = self.insc_active.id
        db.session.commit()
        db.session.refresh(self.insc_active)
        ids = [a.id for a in self.insc_active.absences]
        self.assertIn(self.abs_active.id, ids)

    def test_12_to_dict_contient_inscription_id(self):
        """12. Absence.to_dict() retourne inscription_id."""
        self.abs_active.inscription_id = self.insc_active.id
        db.session.commit()
        d = self.abs_active.to_dict()
        self.assertIn("inscription_id", d)
        self.assertEqual(d["inscription_id"], self.insc_active.id)

    def test_13_to_dict_inscription_id_null_pour_historique(self):
        """13. to_dict() retourne inscription_id = None pour absences sans ancrage."""
        d = self.abs_archive.to_dict()
        self.assertIn("inscription_id", d)
        self.assertIsNone(d["inscription_id"])

    # ==========================================================================
    # GROUPE 2 : Création d'absence — inscription_id automatiquement attribué
    # ==========================================================================

    def test_14_creation_via_route_assigne_inscription_id(self):
        """14. POST /absences assigne inscription_id à partir de l'inscription courante."""
        client = self._client_as(self.admin, self.active)
        response = client.post("/absences", data={
            "eleve_id": self.eleve.id,
            "cours_id": self.cours_active.id,
            "date_absence": "2026-10-15",
            "motif": "Avec ancrage",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        nouvelle = Absence.query.filter_by(motif="Avec ancrage").first()
        self.assertIsNotNone(nouvelle)
        self.assertEqual(nouvelle.inscription_id, self.insc_active.id)

    def test_15_creation_via_route_sans_cours_assigne_inscription_id(self):
        """15. POST /absences sans cours_id assigne quand même inscription_id."""
        client = self._client_as(self.admin, self.active)
        response = client.post("/absences", data={
            "eleve_id": self.eleve.id,
            "cours_id": "",
            "date_absence": "2026-10-16",
            "motif": "Sans cours ancre",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        nouvelle = Absence.query.filter_by(motif="Sans cours ancre").first()
        self.assertIsNotNone(nouvelle)
        self.assertEqual(nouvelle.inscription_id, self.insc_active.id)

    def test_16_verifier_mutation_retourne_inscription(self):
        """16. verifier_mutation_absence retourne l'inscription correcte."""
        _eleve, _cours, inscription, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, self.admin,
            self.eleve.id, self.cours_active.id, date(2026, 10, 10)
        )
        self.assertIsNone(error)
        self.assertIsNotNone(inscription)
        self.assertEqual(inscription.id, self.insc_active.id)
        self.assertEqual(inscription.annee_scolaire_id, self.active.id)

    def test_17_verifier_mutation_erreur_annee_archivee(self):
        """17. verifier_mutation_absence refuse toute création sur année archivée."""
        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole_a.id, self.archivee, self.admin,
            self.eleve.id, self.cours_archive.id, date(2026, 1, 10)
        )
        self.assertIsNotNone(error)

    def test_18_verifier_mutation_erreur_annee_planifiee(self):
        """18. verifier_mutation_absence refuse toute création sur année planifiée."""
        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole_a.id, self.planifiee, self.admin,
            self.eleve.id, self.cours_plan.id, date(2027, 9, 5)
        )
        self.assertIsNotNone(error)

    def test_19_verifier_mutation_erreur_date_hors_annee(self):
        """19. verifier_mutation_absence refuse date hors plage de l'année active."""
        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, self.admin,
            self.eleve.id, self.cours_active.id, date(2025, 5, 5)  # dans l'archivée
        )
        self.assertIn("date d'absence", error.lower())

    def test_20_verifier_mutation_erreur_eleve_hors_ecole(self):
        """20. verifier_mutation_absence refuse élève appartenant à une autre école."""
        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, self.admin,
            self.eleve_b.id, self.cours_active.id, date(2026, 10, 10)
        )
        self.assertIsNotNone(error)
        self.assertIn("non inscrit", error)

    # ==========================================================================
    # GROUPE 3 : Lecture — chemin rapide inscription_id vs chemin secours
    # ==========================================================================

    def test_21_get_absences_annee_chemin_rapide_inscription_id(self):
        """21. get_absences_annee utilise le chemin rapide inscription_id quand disponible."""
        # Ancrer abs_active
        self.abs_active.inscription_id = self.insc_active.id
        db.session.commit()

        absences = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        self.assertEqual(len(absences), 1)
        self.assertEqual(absences[0].id, self.abs_active.id)
        self.assertEqual(absences[0].inscription_id, self.insc_active.id)

    def test_22_get_absences_annee_chemin_secours_sans_inscription_id(self):
        """22. get_absences_annee utilise le chemin de secours (date) pour absences sans inscription_id."""
        # abs_archive n'a pas d'inscription_id
        self.assertIsNone(self.abs_archive.inscription_id)
        absences = get_absences_annee(self.ecole_a.id, self.archivee, self.admin)
        self.assertEqual(len(absences), 1)
        self.assertEqual(absences[0].id, self.abs_archive.id)

    def test_23_absence_inscription_id_autre_annee_non_retournee(self):
        """23. Une absence dont inscription_id appartient à une autre année n'est pas retournée."""
        # Forcer une incohérence : abs_active pointe sur insc_archive (autre année)
        self.abs_active.inscription_id = self.insc_archive.id
        db.session.commit()

        # En cherchant l'année active, abs_active ne doit pas apparaître
        # car son inscription_id est explicitement ancré à une autre année (insc_archive)
        absences_active = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        ids = [a.id for a in absences_active]
        self.assertNotIn(self.abs_active.id, ids)

    def test_24_etancheite_ecole_get_absences_annee(self):
        """24. get_absences_annee n'expose jamais les absences d'une autre école."""
        absences = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        ids = [a.id for a in absences]
        self.assertNotIn(self.abs_b.id, ids)

    def test_25_get_absences_annee_retourne_vide_sans_inscriptions(self):
        """25. get_absences_annee retourne [] si aucune inscription dans l'année."""
        annee_vide = AnneeScolaire(
            nom="2030-2031", date_debut=date(2030, 9, 1), date_fin=date(2031, 7, 31),
            statut="active", ecole_id=self.ecole_a.id
        )
        db.session.add(annee_vide)
        db.session.commit()
        absences = get_absences_annee(self.ecole_a.id, annee_vide, self.admin)
        self.assertEqual(absences, [])

    def test_26_contexte_attache_classe_et_annee(self):
        """26. Les absences retournées ont annee_classe et annee_scolaire attachés."""
        self.abs_active.inscription_id = self.insc_active.id
        db.session.commit()
        absences = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        self.assertEqual(len(absences), 1)
        self.assertEqual(absences[0].annee_classe.id, self.classe_active.id)
        self.assertEqual(absences[0].annee_scolaire.id, self.active.id)

    # ==========================================================================
    # GROUPE 4 : Règles professeur et parent
    # ==========================================================================

    def test_27_professeur_voit_absences_de_ses_classes_seulement(self):
        """27. Professeur voit uniquement les absences des classes qui lui sont assignées."""
        # Créer une 2e classe non assignée au prof
        classe2 = Classe(nom="3e B", niveau="3e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id)
        db.session.add(classe2)
        eleve2 = Eleve(nom="Diao", prenom="Fatoum", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_a.id, classe_id=classe2.id)
        db.session.add(eleve2)
        db.session.flush()
        insc2 = Inscription(ecole_id=self.ecole_a.id, eleve_id=eleve2.id, annee_scolaire_id=self.active.id, classe_id=classe2.id)
        db.session.add(insc2)
        cours2 = Cours(nom="Français", ecole_id=self.ecole_a.id, classe_id=classe2.id)
        db.session.add(cours2)
        abs2 = Absence(
            date_absence=date(2026, 10, 20), motif="Autre classe", justifiee=False,
            eleve_id=eleve2.id, cours_id=cours2.id, ecole_id=self.ecole_a.id,
            inscription_id=None
        )
        db.session.add(abs2)
        db.session.commit()

        absences_prof = get_absences_annee(self.ecole_a.id, self.active, self.prof_user)
        ids = [a.id for a in absences_prof]
        self.assertIn(self.abs_active.id, ids)
        self.assertNotIn(abs2.id, ids)

    def test_28_parent_voit_absences_de_ses_enfants_seulement(self):
        """28. Parent voit uniquement les absences de ses propres enfants."""
        absences_parent = get_absences_annee(self.ecole_a.id, self.active, self.parent)
        self.assertTrue(all(a.eleve_id == self.eleve.id for a in absences_parent))

    def test_29_professeur_peut_creer_absence_sur_son_cours(self):
        """29. Professeur peut créer une absence sur son propre cours via verifier_mutation_absence."""
        _e, _c, inscription, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, self.prof_user,
            self.eleve.id, self.cours_active.id, date(2026, 10, 10)
        )
        self.assertIsNone(error)
        self.assertIsNotNone(inscription)

    def test_30_professeur_refuse_cours_dun_autre_prof(self):
        """30. Professeur ne peut pas créer une absence sur un cours qui n'est pas le sien."""
        autre_prof_user = Utilisateur(nom="Autre", email="autre@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        db.session.add(autre_prof_user)
        db.session.flush()
        autre_prof = Professeur(nom="Autre", prenom="Prof", email="autre_p@test.local", utilisateur_id=autre_prof_user.id, ecole_id=self.ecole_a.id)
        db.session.add(autre_prof)
        db.session.flush()  # nécessaire pour obtenir autre_prof.id
        db.session.execute(professeur_classes.insert().values(
            professeur_id=autre_prof.id,
            classe_id=self.classe_active.id,
            ecole_id=self.ecole_a.id,
        ))
        db.session.commit()

        # Autre prof essaie d'enregistrer absence sur cours_active (qui appartient à self.prof)
        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, autre_prof_user,
            self.eleve.id, self.cours_active.id, date(2026, 10, 10)
        )
        self.assertIsNotNone(error)
        self.assertIn("non autorisé", error)

    # ==========================================================================
    # GROUPE 5 : Cohérence inscription_id — règles de protection
    # ==========================================================================

    def test_31_absence_existante_sans_inscription_id_lisible_via_chemin_secours(self):
        """31. Une absence sans inscription_id est toujours lisible via le chemin de secours (date)."""
        self.assertIsNone(self.abs_active.inscription_id)
        absences = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        ids = [a.id for a in absences]
        self.assertIn(self.abs_active.id, ids)

    def test_32_absence_avec_inscription_id_incorrect_exclue_chemin_rapide(self):
        """32. Une absence avec inscription_id incorrect n'est pas incluse via chemin rapide."""
        # Créer une inscription pour une 3e école fictive non présente dans les inscriptions visibles
        ecole_c = Ecole(nom="Ecole C")
        db.session.add(ecole_c)
        db.session.flush()
        annee_c = AnneeScolaire(nom="2026-2027 C", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=ecole_c.id)
        db.session.add(annee_c)
        classe_c = Classe(nom="5e C", niveau="5e", ecole_id=ecole_c.id, annee_scolaire_id=annee_c.id)
        db.session.add(classe_c)
        db.session.flush()
        eleve_c = Eleve(nom="Toto", prenom="Coco", date_naissance=date(2015, 1, 1), ecole_id=ecole_c.id, classe_id=classe_c.id)
        db.session.add(eleve_c)
        db.session.flush()
        insc_c = Inscription(ecole_id=ecole_c.id, eleve_id=eleve_c.id, annee_scolaire_id=annee_c.id, classe_id=classe_c.id)
        db.session.add(insc_c)
        db.session.commit()

        # Forcer abs_active à pointer sur une inscription d'une autre école
        self.abs_active.inscription_id = insc_c.id
        db.session.commit()

        # La recherche sur ecole_a, active ne doit pas inclure abs_active
        # car son inscription_id est explicitement ancré à une inscription non visible
        absences = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        ids = [a.id for a in absences]
        self.assertNotIn(self.abs_active.id, ids)

    def test_33_statut_annee_planifiee_bloque_creation(self):
        """33. statut_annee_absences signale le blocage pour année planifiée."""
        from app.services.absences_annuelles import statut_annee_absences, absences_modifiables
        msg = statut_annee_absences(self.planifiee)
        self.assertIsNotNone(msg)
        self.assertFalse(absences_modifiables(self.planifiee, self.admin))

    def test_34_statut_annee_archivee_lecture_seule(self):
        """34. statut_annee_absences signale le mode lecture seule pour année archivée."""
        from app.services.absences_annuelles import statut_annee_absences, absences_modifiables
        msg = statut_annee_absences(self.archivee)
        self.assertIsNotNone(msg)
        self.assertFalse(absences_modifiables(self.archivee, self.admin))

    def test_35_statut_annee_active_permet_mutation(self):
        """35. absences_modifiables retourne True sur année active pour admin et professeur."""
        from app.services.absences_annuelles import absences_modifiables
        self.assertTrue(absences_modifiables(self.active, self.admin))
        self.assertTrue(absences_modifiables(self.active, self.prof_user))
        self.assertFalse(absences_modifiables(self.active, self.parent))

    # ==========================================================================
    # GROUPE 6 : Isolation multi-école
    # ==========================================================================

    def test_36_admin_ecole_a_ne_peut_pas_creer_absence_ecole_b(self):
        """36. Un admin de l'école A ne peut pas créer une absence pour l'école B."""
        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole_a.id, self.active, self.admin,
            self.eleve_b.id, self.cours_active.id, date(2026, 10, 10)
        )
        self.assertIsNotNone(error)

    def test_37_get_absences_annee_retourne_vide_pour_ecole_inconnue(self):
        """37. get_absences_annee retourne [] pour un ecole_id inexistant."""
        absences = get_absences_annee(99999, self.active, self.admin)
        self.assertEqual(absences, [])

    def test_38_get_inscription_eleve_annee_retourne_inscription_correcte(self):
        """38. get_inscription_eleve_annee retourne l'inscription active pour l'élève."""
        from app.services.absences_annuelles import get_inscription_eleve_annee
        insc = get_inscription_eleve_annee(self.ecole_a.id, self.eleve.id, self.active.id)
        self.assertIsNotNone(insc)
        self.assertEqual(insc.id, self.insc_active.id)

    def test_39_get_inscription_eleve_annee_retourne_none_si_aucune(self):
        """39. get_inscription_eleve_annee retourne None si aucune inscription dans l'année."""
        annee_vide = AnneeScolaire(
            nom="2031-2032", date_debut=date(2031, 9, 1), date_fin=date(2032, 7, 31),
            statut="planifiee", ecole_id=self.ecole_a.id
        )
        db.session.add(annee_vide)
        db.session.commit()
        from app.services.absences_annuelles import get_inscription_eleve_annee
        insc = get_inscription_eleve_annee(self.ecole_a.id, self.eleve.id, annee_vide.id)
        self.assertIsNone(insc)

    def test_40_resolve_annee_absence_via_cours(self):
        """40. resolve_annee_absence résout l'année via le cours (chemin prioritaire)."""
        from app.services.absences_annuelles import resolve_annee_absence
        annee = resolve_annee_absence(self.abs_active)
        self.assertIsNotNone(annee)
        self.assertEqual(annee.id, self.active.id)

    def test_41_resolve_annee_absence_via_date_sans_cours(self):
        """41. resolve_annee_absence résout l'année via date_absence quand pas de cours."""
        from app.services.absences_annuelles import resolve_annee_absence
        abs_sans_cours = Absence(
            date_absence=date(2026, 10, 15), motif="Sans cours", justifiee=False,
            eleve_id=self.eleve.id, cours_id=None, ecole_id=self.ecole_a.id,
            inscription_id=None
        )
        db.session.add(abs_sans_cours)
        db.session.commit()
        annee = resolve_annee_absence(abs_sans_cours)
        self.assertIsNotNone(annee)
        self.assertEqual(annee.id, self.active.id)

    def test_42_tri_absences_par_date_decroissante(self):
        """42. get_absences_annee retourne les absences triées par date décroissante."""
        abs1 = Absence(date_absence=date(2026, 10, 1), motif="Older", justifiee=False,
                       eleve_id=self.eleve.id, ecole_id=self.ecole_a.id,
                       cours_id=self.cours_active.id, inscription_id=self.insc_active.id)
        abs2 = Absence(date_absence=date(2026, 10, 20), motif="Newer", justifiee=False,
                       eleve_id=self.eleve.id, ecole_id=self.ecole_a.id,
                       cours_id=self.cours_active.id, inscription_id=self.insc_active.id)
        db.session.add_all([abs1, abs2])
        db.session.commit()

        absences = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        dates = [a.date_absence for a in absences]
        self.assertEqual(dates, sorted(dates, reverse=True))

    # ==========================================================================
    # GROUPE 7 : Test base migrée réelle (SQLite)
    # ==========================================================================

    def test_43_base_migree_reelle_inscription_id_present(self):
        """43. La vraie base SQLite ecole.db contient la colonne inscription_id dans la table absence."""
        import sqlite3
        import os
        db_path = os.path.join(os.path.dirname(__file__), '..', 'instance', 'ecole.db')
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(absence)")
        colonnes = {row[1] for row in cursor.fetchall()}
        conn.close()

        self.assertIn("inscription_id", colonnes, "La colonne inscription_id doit être présente dans absence (migration 3864936b0572)")

    def test_44_base_migree_alembic_head_correct(self):
        """44. La base ecole.db est bien à la révision 3864936b0572 (head 3A-Bis)."""
        import sqlite3
        import os
        db_path = os.path.join(os.path.dirname(__file__), '..', 'instance', 'ecole.db')
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT version_num FROM alembic_version")
        rows = cursor.fetchall()
        conn.close()

        versions = {row[0] for row in rows}
        self.assertTrue(bool(versions & {"3864936b0572", "15ae5fdad90d", "8ab121cfcb45", "9bc234dfde56"}),
                        f"La révision 3864936b0572 ou ultérieure doit être active. Révisions actuelles: {versions}")

    # ==========================================================================
    # GROUPE 8 : Fallback historique strict & gestion des chevauchements (A, B, C, D)
    # ==========================================================================

    def test_45_fallback_historique_avec_cours_visible_uniquement_dans_son_annee(self):
        """A. Absence historique (inscription_id IS NULL) avec cours -> visible uniquement dans l'année du cours."""
        from app.services.absences_annuelles import resolve_annee_absence
        abs_cours_hist = Absence(
            date_absence=date(2026, 1, 10),
            motif="Historique avec cours",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=self.cours_archive.id,  # lié à classe_archive -> archivee
            ecole_id=self.ecole_a.id,
            inscription_id=None
        )
        db.session.add(abs_cours_hist)
        db.session.commit()

        # Résolution directe
        annee_resolue = resolve_annee_absence(abs_cours_hist)
        self.assertIsNotNone(annee_resolue)
        self.assertEqual(annee_resolue.id, self.archivee.id)

        # Visibilité par get_absences_annee
        absences_archive = get_absences_annee(self.ecole_a.id, self.archivee, self.admin)
        ids_archive = [a.id for a in absences_archive]
        self.assertIn(abs_cours_hist.id, ids_archive)

        absences_active = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        ids_active = [a.id for a in absences_active]
        self.assertNotIn(abs_cours_hist.id, ids_active)

    def test_46_fallback_historique_sans_cours_annee_unique_visible(self):
        """B. Absence historique (inscription_id IS NULL) sans cours, date correspondant à une année UNIQUE -> visible."""
        from app.services.absences_annuelles import resolve_annee_absence
        # 2026-11-15 appartient uniquement à 2026-2027 (active)
        abs_sans_cours_unique = Absence(
            date_absence=date(2026, 11, 15),
            motif="Sans cours date unique",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=None,
            ecole_id=self.ecole_a.id,
            inscription_id=None
        )
        db.session.add(abs_sans_cours_unique)
        db.session.commit()

        annee_resolue = resolve_annee_absence(abs_sans_cours_unique)
        self.assertIsNotNone(annee_resolue)
        self.assertEqual(annee_resolue.id, self.active.id)

        absences_active = get_absences_annee(self.ecole_a.id, self.active, self.admin)
        ids_active = [a.id for a in absences_active]
        self.assertIn(abs_sans_cours_unique.id, ids_active)

        absences_archive = get_absences_annee(self.ecole_a.id, self.archivee, self.admin)
        ids_archive = [a.id for a in absences_archive]
        self.assertNotIn(abs_sans_cours_unique.id, ids_archive)

    def test_47_fallback_historique_sans_cours_chevauchement_non_attribuee(self):
        """C. Absence historique (inscription_id IS NULL) sans cours, date dans un CHEVAUCHEMENT de 2 années -> non attribuée."""
        from app.services.absences_annuelles import resolve_annee_absence
        # Création de 2 années qui se chevauchent dans ecole_a
        annee_ch1 = AnneeScolaire(
            nom="2034-2035 Chevauchée", date_debut=date(2034, 9, 1), date_fin=date(2035, 7, 31),
            statut="active", ecole_id=self.ecole_a.id
        )
        annee_ch2 = AnneeScolaire(
            nom="2035 Calendaire Chevauchée", date_debut=date(2035, 1, 1), date_fin=date(2035, 12, 31),
            statut="active", ecole_id=self.ecole_a.id
        )
        db.session.add_all([annee_ch1, annee_ch2])
        db.session.flush()

        classe_ch1 = Classe(nom="Classe CH1", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=annee_ch1.id)
        classe_ch2 = Classe(nom="Classe CH2", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=annee_ch2.id)
        db.session.add_all([classe_ch1, classe_ch2])
        db.session.flush()

        insc_ch1 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=annee_ch1.id, classe_id=classe_ch1.id)
        insc_ch2 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=annee_ch2.id, classe_id=classe_ch2.id)
        db.session.add_all([insc_ch1, insc_ch2])
        db.session.flush()

        # Date 2035-03-15 est comprise dans LES DEUX années (chevauchement)
        abs_ambigue = Absence(
            date_absence=date(2035, 3, 15),
            motif="Absence ambigue chevauchement",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=None,
            ecole_id=self.ecole_a.id,
            inscription_id=None
        )
        db.session.add(abs_ambigue)
        db.session.commit()

        # 1. resolve_annee_absence doit retourner None (pas de choix arbitraire)
        annee_resolue = resolve_annee_absence(abs_ambigue)
        self.assertIsNone(annee_resolue, "En cas de chevauchement de dates sans cours, resolve_annee_absence doit retourner None")

        # 2. get_absences_annee ne doit l'attribuer à AUCUNE des deux années
        absences_ch1 = get_absences_annee(self.ecole_a.id, annee_ch1, self.admin)
        self.assertNotIn(abs_ambigue.id, [a.id for a in absences_ch1])

        absences_ch2 = get_absences_annee(self.ecole_a.id, annee_ch2, self.admin)
        self.assertNotIn(abs_ambigue.id, [a.id for a in absences_ch2])

    def test_48_eleve_classe_id_ne_departage_jamais_ambiguite(self):
        """D. Eleve.classe_id ne doit JAMAIS servir à départager l'ambiguïté d'un chevauchement."""
        from app.services.absences_annuelles import resolve_annee_absence
        # Même configuration de chevauchement
        annee_ch1 = AnneeScolaire(
            nom="2036-2037 Chevauchée A", date_debut=date(2036, 9, 1), date_fin=date(2037, 7, 31),
            statut="active", ecole_id=self.ecole_a.id
        )
        annee_ch2 = AnneeScolaire(
            nom="2037 Calendaire Chevauchée B", date_debut=date(2037, 1, 1), date_fin=date(2037, 12, 31),
            statut="active", ecole_id=self.ecole_a.id
        )
        db.session.add_all([annee_ch1, annee_ch2])
        db.session.flush()

        classe_ch1 = Classe(nom="Classe CH1-D", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=annee_ch1.id)
        classe_ch2 = Classe(nom="Classe CH2-D", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=annee_ch2.id)
        db.session.add_all([classe_ch1, classe_ch2])
        db.session.flush()

        insc_ch1 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=annee_ch1.id, classe_id=classe_ch1.id)
        insc_ch2 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=annee_ch2.id, classe_id=classe_ch2.id)
        db.session.add_all([insc_ch1, insc_ch2])

        # On force Eleve.classe_id à pointer sur classe_ch1
        self.eleve.classe_id = classe_ch1.id
        db.session.flush()

        abs_ambigue = Absence(
            date_absence=date(2037, 4, 10),
            motif="Absence chevauchement test D",
            justifiee=False,
            eleve_id=self.eleve.id,
            cours_id=None,
            ecole_id=self.ecole_a.id,
            inscription_id=None
        )
        db.session.add(abs_ambigue)
        db.session.commit()

        # Malgré que eleve.classe_id soit renseigné sur annee_ch1, resolve_annee_absence doit retourner None
        self.assertIsNone(resolve_annee_absence(abs_ambigue))

        # Ni annee_ch1 ni annee_ch2 ne doivent s'approprier l'absence
        self.assertNotIn(abs_ambigue.id, [a.id for a in get_absences_annee(self.ecole_a.id, annee_ch1, self.admin)])
        self.assertNotIn(abs_ambigue.id, [a.id for a in get_absences_annee(self.ecole_a.id, annee_ch2, self.admin)])


if __name__ == "__main__":
    unittest.main()
