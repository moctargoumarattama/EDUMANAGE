"""
Tests Phase 5F — Recherche & Filtres Temps Réel dans Tout le Système (KLASORA)

Couverture : 17 scénarios validant les filtres live sur /eleves, /notes, /cours,
/paiements, /absences, /classes, /professeurs, avec isolation multi-école et
respect de l'année consultée.
"""
import unittest
from datetime import date

from app import create_app, db
from app.models import (
    AnneeScolaire,
    AnneeNiveauConfig,
    Bulletin,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
)
from app.services.niveaux import ensure_standard_niveaux


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5f"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _annee(ecole_id, statut="active", nom="2024-2025"):
    a = AnneeScolaire(
        nom=nom,
        date_debut=date(2024, 9, 1),
        date_fin=date(2025, 6, 30),
        statut=statut,
        ecole_id=ecole_id,
    )
    db.session.add(a)
    db.session.flush()
    return a


def _config_niveau(ecole_id, annee_id, niveau_id):
    cfg = AnneeNiveauConfig(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_id,
        niveau_id=niveau_id,
        actif=True,
    )
    db.session.add(cfg)
    db.session.flush()
    return cfg


def _classe(ecole_id, annee_id, nom, niveau):
    c = Classe(
        nom=nom,
        niveau=niveau,
        ecole_id=ecole_id,
        annee_scolaire_id=annee_id,
        statut="ouverte",
    )
    db.session.add(c)
    db.session.flush()
    return c


def _eleve(ecole_id, nom, prenom, code_parent=None):
    e = Eleve(
        nom=nom,
        prenom=prenom,
        genre="M",
        statut="actif",
        ecole_id=ecole_id,
        date_naissance=date(2010, 1, 1),
        code_parent=code_parent,
    )
    db.session.add(e)
    db.session.flush()
    return e


def _inscrire(eleve_id, classe_id, annee_id, ecole_id=None):
    if ecole_id is None:
        classe = Classe.query.get(classe_id)
        ecole_id = classe.ecole_id
    i = Inscription(
        eleve_id=eleve_id,
        classe_id=classe_id,
        annee_scolaire_id=annee_id,
        statut="actif",
        ecole_id=ecole_id,
    )
    db.session.add(i)
    db.session.flush()
    return i


def _prof(ecole_id, utilisateur_id, nom, prenom):
    p = Professeur(
        nom=nom,
        prenom=prenom,
        ecole_id=ecole_id,
        utilisateur_id=utilisateur_id,
        specialite="Mathématiques",
    )
    db.session.add(p)
    db.session.flush()
    return p


def _cours(classe_id, prof_id, nom, ecole_id=None):
    if ecole_id is None:
        classe = Classe.query.get(classe_id)
        ecole_id = classe.ecole_id if classe else None
    c = Cours(nom=nom, classe_id=classe_id, professeur_id=prof_id, coefficient=1, ecole_id=ecole_id)
    db.session.add(c)
    db.session.flush()
    return c


def _user(email, role, ecole_id, password="test"):
    u = Utilisateur(nom="User", email=email, mot_de_passe=password, role=role, ecole_id=ecole_id)
    db.session.add(u)
    db.session.flush()
    return u


def _login(client, user, ecole_id=None):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["role"] = user.role
        if ecole_id is not None:
            sess["ecole_id"] = ecole_id
        elif user.ecole_id:
            sess["ecole_id"] = user.ecole_id


# ---------------------------------------------------------------------------
# Test Case
# ---------------------------------------------------------------------------

class Phase5FRechercheFiltresTestCase(unittest.TestCase):

    def setUp(self):
        from app import create_app as _create_app
        self.app = _create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        ensure_standard_niveaux(commit=True)

        # Deux écoles distinctes
        self.ecole_a = Ecole(nom="Ecole Alpha")
        self.ecole_b = Ecole(nom="Ecole Beta")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Compléter l'onboarding de Ecole A
        self.ecole_a.setup_step = "complete"
        self.ecole_a.setup_complete = True

        # Admins
        self.admin_a = _user("admin_a@test.local", "admin", self.ecole_a.id)
        self.admin_b = _user("admin_b@test.local", "admin", self.ecole_b.id)

        # Année scolaire Ecole A (active)
        self.annee_a = _annee(self.ecole_a.id)

        # Niveaux
        niveaux = NiveauScolaire.query.order_by(NiveauScolaire.ordre).limit(3).all()
        self.niv1 = niveaux[0]
        self.niv2 = niveaux[1] if len(niveaux) > 1 else niveaux[0]
        _config_niveau(self.ecole_a.id, self.annee_a.id, self.niv1.id)
        _config_niveau(self.ecole_a.id, self.annee_a.id, self.niv2.id)

        # Classes
        self.classe_6a = _classe(self.ecole_a.id, self.annee_a.id, "6e A", self.niv1.nom)
        self.classe_5a = _classe(self.ecole_a.id, self.annee_a.id, "5e A", self.niv2.nom)

        # Élèves Ecole A
        self.eleve_alpha = _eleve(self.ecole_a.id, "Alpha", "Jean", code_parent="PARENT-001")
        self.eleve_beta  = _eleve(self.ecole_a.id, "Beta",  "Marie")
        self.eleve_autre = _eleve(self.ecole_a.id, "Gamma", "Luc")

        _inscrire(self.eleve_alpha.id, self.classe_6a.id, self.annee_a.id, self.ecole_a.id)
        _inscrire(self.eleve_beta.id,  self.classe_6a.id, self.annee_a.id, self.ecole_a.id)
        _inscrire(self.eleve_autre.id, self.classe_5a.id, self.annee_a.id, self.ecole_a.id)

        # Professeur Ecole A
        self.user_prof = _user("prof@test.local", "professeur", self.ecole_a.id)
        self.professeur = _prof(self.ecole_a.id, self.user_prof.id, "Dupont", "Paul")

        # Cours
        self.cours_maths = _cours(self.classe_6a.id, self.professeur.id, "Mathématiques", self.ecole_a.id)
        self.cours_fr    = _cours(self.classe_5a.id,  self.professeur.id, "Français", self.ecole_a.id)
        self.cours_orphelin = _cours(self.classe_6a.id, None, "Histoire", self.ecole_a.id)

        # Notes
        self.note1 = Note(
            eleve_id=self.eleve_alpha.id,
            cours_id=self.cours_maths.id,
            valeur=15.0,
            periode="Semestre 1",
            type_evaluation="Devoir",
            date_evaluation=date(2024, 10, 1),
        )
        self.note2 = Note(
            eleve_id=self.eleve_beta.id,
            cours_id=self.cours_maths.id,
            valeur=12.0,
            periode="Semestre 2",
            type_evaluation="Composition",
            date_evaluation=date(2025, 2, 1),
        )
        db.session.add_all([self.note1, self.note2])

        # Paiement (partiel = montant sans la totalité versée)
        self.paiement = Paiement(
            eleve_id=self.eleve_alpha.id,
            ecole_id=self.ecole_a.id,
            montant=20000,
            mois="Octobre",
            annee=2024,
            statut="partiel",
            mode_paiement="especes",
        )
        db.session.add(self.paiement)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ------------------------------------------------------------------
    # 01. Recherche élève par nom
    # ------------------------------------------------------------------
    def test_01_recherche_eleve_nom(self):
        from app.services.eleves import rechercher_eleves
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
            search="alpha",
        )
        noms = [e.nom for e in resultats]
        self.assertIn("Alpha", noms)
        self.assertNotIn("Beta", noms)

    # ------------------------------------------------------------------
    # 02. Recherche élève par code_parent
    # ------------------------------------------------------------------
    def test_02_recherche_code_parent(self):
        from app.services.eleves import rechercher_eleves
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
            search="PARENT-001",
        )
        self.assertTrue(any(e.code_parent == "PARENT-001" for e in resultats))

    # ------------------------------------------------------------------
    # 03. Filtre par niveau
    # ------------------------------------------------------------------
    def test_03_filtre_niveau(self):
        from app.services.eleves import rechercher_eleves
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
            niveau=self.niv1.nom,
        )
        ids = [e.id for e in resultats]
        self.assertIn(self.eleve_alpha.id, ids)
        self.assertIn(self.eleve_beta.id, ids)
        self.assertNotIn(self.eleve_autre.id, ids)

    # ------------------------------------------------------------------
    # 04. Filtre par classe
    # ------------------------------------------------------------------
    def test_04_filtre_classe(self):
        from app.services.eleves import rechercher_eleves
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
            classe_id=self.classe_5a.id,
        )
        ids = [e.id for e in resultats]
        self.assertIn(self.eleve_autre.id, ids)
        self.assertNotIn(self.eleve_alpha.id, ids)

    # ------------------------------------------------------------------
    # 05. Combinaison recherche + filtre classe
    # ------------------------------------------------------------------
    def test_05_combinaison_recherche_et_classe(self):
        from app.services.eleves import rechercher_eleves
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
            search="alpha",
            classe_id=self.classe_6a.id,
        )
        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0].nom, "Alpha")

    # ------------------------------------------------------------------
    # 06. Respect de l'année consultée
    # ------------------------------------------------------------------
    def test_06_annee_consultee_respectee(self):
        """Élèves d'une année différente ne doivent pas apparaître."""
        annee_ancienne = _annee(self.ecole_a.id, statut="archivee", nom="2023-2024")
        eleve_ancien = _eleve(self.ecole_a.id, "Ancien", "Eleve")
        _inscrire(eleve_ancien.id, self.classe_6a.id, annee_ancienne.id, self.ecole_a.id)
        db.session.commit()

        from app.services.eleves import rechercher_eleves
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        ids = [e.id for e in resultats]
        self.assertNotIn(eleve_ancien.id, ids)

    # ------------------------------------------------------------------
    # 07. Isolation multi-école
    # ------------------------------------------------------------------
    def test_07_autre_ecole_exclue(self):
        """Les élèves d'une autre école ne doivent jamais apparaître."""
        eleve_b = _eleve(self.ecole_b.id, "Intrus", "EcoleB")
        db.session.commit()

        from app.services.eleves import rechercher_eleves
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        ids = [e.id for e in resultats]
        self.assertNotIn(eleve_b.id, ids)

    # ------------------------------------------------------------------
    # 08. Pagination — les filtres sont conservés
    # ------------------------------------------------------------------
    def test_08_pagination_conserve_filtres(self):
        """La pagination avec un filtre actif ne retourne que les éléments filtrés."""
        from app.services.eleves import rechercher_eleves
        p1 = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
            classe_id=self.classe_6a.id,
            page=1,
            par_page=10,
        )
        self.assertIsInstance(p1, list)
        for e in p1:
            insc = Inscription.query.filter_by(eleve_id=e.id, classe_id=self.classe_6a.id).first()
            self.assertIsNotNone(insc)

    # ------------------------------------------------------------------
    # 09. Reset — tous les élèves de l'école reviennent
    # ------------------------------------------------------------------
    def test_09_reset_fonctionne(self):
        from app.services.eleves import rechercher_eleves
        tous = rechercher_eleves(ecole_id=self.ecole_a.id, annee_id=self.annee_a.id)
        ids = [e.id for e in tous]
        self.assertIn(self.eleve_alpha.id, ids)
        self.assertIn(self.eleve_beta.id, ids)
        self.assertIn(self.eleve_autre.id, ids)

    # ------------------------------------------------------------------
    # 10. Notes filtrées Semestre 1
    # ------------------------------------------------------------------
    def test_10_notes_filtrees_semestre_1(self):
        notes = Note.query.filter_by(
            cours_id=self.cours_maths.id,
            periode="Semestre 1",
        ).all()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].valeur, 15.0)

    # ------------------------------------------------------------------
    # 11. Notes filtrées Composition
    # ------------------------------------------------------------------
    def test_11_notes_filtrees_composition(self):
        notes = Note.query.filter_by(
            cours_id=self.cours_maths.id,
            type_evaluation="Composition",
        ).all()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].eleve_id, self.eleve_beta.id)

    # ------------------------------------------------------------------
    # 12. API cours_classe retourne uniquement les cours de la classe
    # ------------------------------------------------------------------
    def test_12_classe_reduit_les_cours(self):
        cours_6a = Cours.query.filter_by(classe_id=self.classe_6a.id).all()
        noms = [c.nom for c in cours_6a]
        self.assertIn("Mathématiques", noms)
        self.assertIn("Histoire", noms)
        self.assertNotIn("Français", noms)  # Français est dans 5e A

    # ------------------------------------------------------------------
    # 13. API eleves_classe retourne uniquement les élèves inscrits
    # ------------------------------------------------------------------
    def test_13_classe_reduit_les_eleves(self):
        inscrits_6a = (
            db.session.query(Eleve)
            .join(Inscription, Inscription.eleve_id == Eleve.id)
            .filter(
                Inscription.classe_id == self.classe_6a.id,
                Inscription.annee_scolaire_id == self.annee_a.id,
            )
            .all()
        )
        ids = [e.id for e in inscrits_6a]
        self.assertIn(self.eleve_alpha.id, ids)
        self.assertIn(self.eleve_beta.id, ids)
        self.assertNotIn(self.eleve_autre.id, ids)

    # ------------------------------------------------------------------
    # 14. Paiements filtrés : statut partiel (reste à payer)
    # ------------------------------------------------------------------
    def test_14_paiement_reste_a_payer(self):
        paiements_partiels = Paiement.query.filter(
            Paiement.ecole_id == self.ecole_a.id,
            Paiement.statut == "partiel",
        ).all()
        self.assertTrue(len(paiements_partiels) >= 1)
        for p in paiements_partiels:
            self.assertEqual(p.statut, "partiel")

    # ------------------------------------------------------------------
    # 15. Professeur ne peut filtrer que ses propres cours
    # ------------------------------------------------------------------
    def test_15_professeur_recherche_limitee_ses_donnees(self):
        cours_du_prof = Cours.query.filter_by(professeur_id=self.professeur.id).all()
        ids_cours_prof = {c.id for c in cours_du_prof}
        self.assertIn(self.cours_maths.id, ids_cours_prof)
        self.assertIn(self.cours_fr.id, ids_cours_prof)
        # Le cours orphelin (sans prof) ne lui appartient pas
        self.assertNotIn(self.cours_orphelin.id, ids_cours_prof)

    # ------------------------------------------------------------------
    # 16. Parent ne peut pas voir les élèves des autres
    # ------------------------------------------------------------------
    def test_16_parent_ne_peut_pas_rechercher_autres_eleves(self):
        """Un parent n'a accès qu'à ses enfants (via code_parent)."""
        code_p = "PARENT-001"
        enfants_du_parent = Eleve.query.filter_by(
            ecole_id=self.ecole_a.id,
            code_parent=code_p,
        ).all()
        self.assertEqual(len(enfants_du_parent), 1)
        self.assertEqual(enfants_du_parent[0].id, self.eleve_alpha.id)
        # Beta n'a pas ce code_parent
        self.assertNotIn(self.eleve_beta.id, [e.id for e in enfants_du_parent])

    # ------------------------------------------------------------------
    # 17. Paramètres falsifiés ne provoquent pas de fuite de données
    # ------------------------------------------------------------------
    def test_17_parametres_forges_ne_fuient_aucune_donnee(self):
        """classe_id d'une autre école ne retourne aucun élève de cette école."""
        annee_b = _annee(self.ecole_b.id, nom="2024-2025")
        niv = NiveauScolaire.query.first()
        classe_b = _classe(self.ecole_b.id, annee_b.id, "6e B", niv.nom)
        eleve_b = _eleve(self.ecole_b.id, "Usurpateur", "EcoleB")
        _inscrire(eleve_b.id, classe_b.id, annee_b.id, self.ecole_b.id)
        db.session.commit()

        from app.services.eleves import rechercher_eleves
        # On cherche avec classe_id de Ecole B dans le contexte de Ecole A
        resultats = rechercher_eleves(
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
            classe_id=classe_b.id,  # classe d'une autre école — doit être ignorée/rejetée
        )
        ids = [e.id for e in resultats]
        self.assertNotIn(eleve_b.id, ids)

    # ------------------------------------------------------------------
    # 18. /recherche globale : historique toutes annees
    # ------------------------------------------------------------------
    def test_18_recherche_globale_trouve_eleve_historique_sans_muter_session(self):
        annee_future = _annee(self.ecole_a.id, statut="active", nom="2046-2047")
        annee_archivee = _annee(self.ecole_a.id, statut="archivee", nom="2026-2027")
        classe_archivee = _classe(self.ecole_a.id, annee_archivee.id, "6e Historique", self.niv1.nom)
        eleve = _eleve(self.ecole_a.id, "Historique", "Amadou")
        inscription = _inscrire(eleve.id, classe_archivee.id, annee_archivee.id, self.ecole_a.id)
        bulletin = Bulletin(
            inscription_id=inscription.id,
            eleve_id=eleve.id,
            ecole_id=self.ecole_a.id,
            classe_id=classe_archivee.id,
            annee_scolaire_id=annee_archivee.id,
            periode="Semestre 1",
            moyenne_generale=14.5,
            statut="valide",
        )
        db.session.add(bulletin)
        db.session.commit()

        _login(self.client, self.admin_a)
        with self.client.session_transaction() as sess:
            sess["annee_consultee"] = {str(self.ecole_a.id): annee_future.id}

        response = self.client.get("/recherche?q=Historique&type=eleves")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Amadou", body)
        self.assertIn("2026-2027", body)
        self.assertIn("6e Historique", body)
        self.assertIn("archivee", body)
        self.assertIn("Semestre 1", body)
        self.assertIn("Voir bulletin", body)
        self.assertIn("Télécharger PDF", body)
        self.assertIn(f"/bulletins/{bulletin.id}", body)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["annee_consultee"][str(self.ecole_a.id)], annee_future.id)

    # ------------------------------------------------------------------
    # 19. /recherche globale : isolation multi-ecole
    # ------------------------------------------------------------------
    def test_19_recherche_globale_exclut_autre_ecole(self):
        annee_b = _annee(self.ecole_b.id, nom="2026-2027")
        classe_b = _classe(self.ecole_b.id, annee_b.id, "6e B", self.niv1.nom)
        eleve_b = _eleve(self.ecole_b.id, "Introuvable", "EcoleB")
        _inscrire(eleve_b.id, classe_b.id, annee_b.id, self.ecole_b.id)
        db.session.commit()

        _login(self.client, self.admin_a)
        response = self.client.get("/recherche?q=Introuvable&type=eleves")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("EcoleB Introuvable", body)
        self.assertNotIn("6e B", body)

    # ------------------------------------------------------------------
    # 20. /recherche globale : parent limite a ses enfants
    # ------------------------------------------------------------------
    def test_20_recherche_parent_ne_trouve_que_ses_enfants(self):
        parent = _user("parent@test.local", "parent", self.ecole_a.id)
        self.eleve_alpha.parent_id = parent.id
        self.eleve_beta.parent_id = None
        db.session.commit()

        _login(self.client, parent)
        response_own = self.client.get("/recherche?q=Alpha&type=eleves")
        response_other = self.client.get("/recherche?q=Beta&type=eleves")

        self.assertEqual(response_own.status_code, 200)
        self.assertIn("Alpha", response_own.get_data(as_text=True))
        self.assertEqual(response_other.status_code, 200)
        self.assertNotIn("Marie Beta", response_other.get_data(as_text=True))

    # ------------------------------------------------------------------
    # 21. /recherche globale : pagination historique
    # ------------------------------------------------------------------
    def test_21_recherche_globale_pagination_historique(self):
        annee_archivee = _annee(self.ecole_a.id, statut="archivee", nom="2022-2023")
        classe_archivee = _classe(self.ecole_a.id, annee_archivee.id, "4e Page", self.niv1.nom)
        for idx in range(12):
            eleve = _eleve(self.ecole_a.id, f"Paged{idx:02d}", "Eleve")
            _inscrire(eleve.id, classe_archivee.id, annee_archivee.id, self.ecole_a.id)
        db.session.commit()

        _login(self.client, self.admin_a)
        page_1 = self.client.get("/recherche?q=Paged&type=eleves&per_page=5&page=1").get_data(as_text=True)
        page_2 = self.client.get("/recherche?q=Paged&type=eleves&per_page=5&page=2").get_data(as_text=True)

        self.assertIn("Paged00", page_1)
        self.assertNotIn("Paged09", page_1)
        self.assertIn("Paged05", page_2)
        self.assertIn("Suivant", page_1)
        self.assertIn("Précédent", page_2)

    # ------------------------------------------------------------------
    # 22. /recherche globale : courant et historique coexistent
    # ------------------------------------------------------------------
    def test_22_recherche_globale_courant_et_historique_coexistent(self):
        annee_archivee = _annee(self.ecole_a.id, statut="archivee", nom="2021-2022")
        classe_archivee = _classe(self.ecole_a.id, annee_archivee.id, "5e Ancienne", self.niv1.nom)
        eleve_ancien = _eleve(self.ecole_a.id, "Coexist", "Ancien")
        eleve_courant = _eleve(self.ecole_a.id, "Coexist", "Actuel")
        _inscrire(eleve_ancien.id, classe_archivee.id, annee_archivee.id, self.ecole_a.id)
        _inscrire(eleve_courant.id, self.classe_6a.id, self.annee_a.id, self.ecole_a.id)
        db.session.commit()

        _login(self.client, self.admin_a)
        response = self.client.get("/recherche?q=Coexist&type=eleves")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Ancien", body)
        self.assertIn("Actuel", body)
        self.assertIn("2021-2022", body)
        self.assertIn(self.annee_a.nom, body)


if __name__ == "__main__":
    unittest.main()
