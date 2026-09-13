"""
tests/test_phase2d3b_flux_classes.py
=============================================================
KLASORA — MINI-CORRECTION 2D-3B
Tests du flux Structure -> Classes -> Passage d'année.

Vérifications :
1. Règle 2C-5D : transition via POST /annees/<id>/consulter avec paramètre next safe
2. Sécurité : rejet des redirections externes dans next
3. UI structure : bouton conforme 2C-5D (POST consulter si non consultée, lien direct si déjà consultée)
4. Absence absolue de annee_id= dans le flux structure -> classes
5. GET /classes/add : filtrage des niveaux selon AnneeNiveauConfig de l'année cible consultée
6. POST /classes/add : succès avec HTTP 302, calcul automatique nom/niveau côté backend
7. POST /classes/add : échec avec affichage clair des erreurs (aucun 200 silencieux)
8. Visibilité : la classe créée apparaît dans /classes sous l'année consultée
9. Passage d'année : bouton Traiter individuel non bloqué globalement même si structure cible incomplète
10. Passage d'année : verrou individuel préservé (sortie possible sans classe cible, passage exige classe ouverte)
=============================================================
"""

import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.niveaux import ensure_standard_niveaux, set_niveau_actif
from app.services.niveaux_annuels import sauvegarder_selection_annuelle


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class FluxClassesPassageTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Niveaux standard
        niveaux = ensure_standard_niveaux(commit=False)
        db.session.flush()
        self.niv = {n.code: n for n in niveaux}

        # Ecole
        self.ecole = Ecole(nom="Ecole Test Flux")
        db.session.add(self.ecole)
        db.session.flush()

        # Année active source (2025-2026)
        self.annee_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole.id,
        )
        # Année cible planifiée (2026-2027)
        self.annee_cible = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.annee_active, self.annee_cible])
        db.session.flush()

        # Config niveaux annuels pour annee_cible : restreindre à 6e et 5e
        sauvegarder_selection_annuelle(
            self.ecole.id,
            self.annee_cible.id,
            [self.niv["6E"].id, self.niv["5E"].id],
        )

        # Classe source en 2025-2026
        self.cls_src = Classe(
            nom="6e A",
            niveau="6e",
            niveau_id=self.niv["6E"].id,
            section="A",
            capacite=35,
            statut="ouverte",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id,
        )
        db.session.add(self.cls_src)
        db.session.flush()

        # Élève dans la classe source
        self.eleve = Eleve(
            nom="Diallo",
            prenom="Aminata",
            date_naissance=date(2013, 5, 12),
            genre="F",
            ecole_id=self.ecole.id,
            classe_id=self.cls_src.id,
        )
        db.session.add(self.eleve)
        db.session.flush()

        creer_inscription_annuelle(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_active.id,
            classe_id=self.cls_src.id,
            sync_active=True,
        )

        # Admin
        self.admin = Utilisateur(
            nom="Admin",
            prenom="Directeur",
            email="directeur@test.local",
            mot_de_passe="password123",
            role="admin",
            ecole_id=self.ecole.id,
        )
        self.admin.set_mot_de_passe("password123")
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
        return client

    # -------------------------------------------------------------------------
    # 1. Navigation et règle 2C-5D
    # -------------------------------------------------------------------------
    def test_consulter_annee_avec_parametre_next_interne(self):
        """POST /annees/<id>/consulter avec next=/classes bascule la session et redirige vers /classes."""
        client = self.login_as(self.admin)
        response = client.post(
            f"/annees/{self.annee_cible.id}/consulter",
            data={"next": "/classes"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/classes")

        # Vérifier que la session a bien mémorisé l'année cible
        with client.session_transaction() as sess:
            self.assertEqual(sess.get("annee_consultee", {}).get(str(self.ecole.id)), self.annee_cible.id)

    def test_consulter_annee_sans_next_redirige_vers_gestion_annees(self):
        """POST /annees/<id>/consulter sans next redirige vers gestion_annees par défaut."""
        client = self.login_as(self.admin)
        response = client.post(
            f"/annees/{self.annee_cible.id}/consulter",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/annees", response.headers.get("Location"))

    def test_consulter_annee_rejette_url_externe(self):
        """Sécurité : toute tentative de redirection externe via next est ignorée."""
        client = self.login_as(self.admin)
        for evil_url in ["http://evil.com", "https://evil.com", "//evil.com", "\\evil.com"]:
            response = client.post(
                f"/annees/{self.annee_cible.id}/consulter",
                data={"next": evil_url},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)
            # Ne doit JAMAIS rediriger vers le domaine externe
            self.assertNotIn("evil.com", response.headers.get("Location"))
            self.assertIn("/annees", response.headers.get("Location"))

    def test_structure_annee_bouton_classes_affiche_action_consulter_si_non_consultee(self):
        """Sur /annees/<id>/structure, si l'année n'est pas consultée, un formulaire POST vers consulter est présent sans annee_id=."""
        client = self.login_as(self.admin)
        response = client.get(f"/annees/{self.annee_cible.id}/structure")
        self.assertEqual(response.status_code, 200)

        # Absence du lien obsolète annee_id=
        self.assertNotIn(f"/classesannee_id={self.annee_cible.id}".encode(), response.data)
        # Présence du bouton d'action vers consulter_annee
        self.assertIn(f"/annees/{self.annee_cible.id}/consulter".encode(), response.data)
        self.assertIn(b"Classes (consulter)", response.data)

    def test_structure_annee_bouton_classes_affiche_lien_direct_si_deja_consultee(self):
        """Sur /annees/<id>/structure, si l'année est DÉJÀ consultée, un lien direct vers /classes est présent."""
        client = self.login_as(self.admin)
        # Consulter d'abord l'année cible
        client.post(f"/annees/{self.annee_cible.id}/consulter")

        response = client.get(f"/annees/{self.annee_cible.id}/structure")
        self.assertEqual(response.status_code, 200)
        # Lien direct vers /classes sans annee_id=
        self.assertIn(b'href="/classes"', response.data)
        self.assertNotIn(f"/classesannee_id={self.annee_cible.id}".encode(), response.data)

    # -------------------------------------------------------------------------
    # 2. Ajout de classe (GET et POST)
    # -------------------------------------------------------------------------
    def test_get_classes_add_filtre_selon_annee_consultee(self):
        """GET /classes/add lorsque annee_cible est consultée ne propose que les niveaux actifs de annee_cible."""
        client = self.login_as(self.admin)
        # Consulter l'année cible (qui a seulement 6e et 5e configurés)
        client.post(f"/annees/{self.annee_cible.id}/consulter")

        response = client.get("/classes/add")
        self.assertEqual(response.status_code, 200)
        # 6e et 5e doivent être proposés
        self.assertIn(b"6e", response.data)
        self.assertIn(b"5e", response.data)
        # 4e et Terminale ne doivent PAS être proposés
        self.assertNotIn(b"4e</option>", response.data)
        self.assertNotIn(b"Terminale</option>", response.data)

    def test_post_classes_add_succes_sans_nom_ni_niveau_texte(self):
        """POST /classes/add sans nom ni niveau texte réussit avec HTTP 302 et crée la classe."""
        client = self.login_as(self.admin)
        # Consulter l'année cible
        client.post(f"/annees/{self.annee_cible.id}/consulter")

        # Soumission typique du formulaire navigateur (add_class.html)
        response = client.post(
            "/classes/add",
            data={
                "niveau_id": self.niv["6E"].id,
                "section": "B",
                "capacite": 35,
                "professeur_principal_id": 0,
            },
            follow_redirects=False,
        )
        # Doit retourner HTTP 302 vers /classes
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/classes")

        # Vérifier en base que la classe a été créée avec succès
        classe = Classe.query.filter_by(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            section="B",
        ).first()
        self.assertIsNotNone(classe)
        self.assertEqual(classe.nom, "6e B")
        self.assertEqual(classe.niveau, "6e")
        self.assertEqual(classe.statut, "ouverte")
        self.assertEqual(classe.capacite, 35)

    def test_classe_creee_est_visible_dans_liste_classes(self):
        """La classe créée dans l'année cible apparaît bien dans GET /classes lorsque l'année est consultée."""
        client = self.login_as(self.admin)
        client.post(f"/annees/{self.annee_cible.id}/consulter")

        # Créer la classe
        client.post(
            "/classes/add",
            data={
                "niveau_id": self.niv["6E"].id,
                "section": "C",
                "capacite": 30,
                "professeur_principal_id": 0,
            },
            follow_redirects=True,
        )

        # Consulter /classes
        response = client.get("/classes")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"6e C", response.data)

    def test_post_classes_add_echec_affiche_erreurs_clairement(self):
        """POST /classes/add avec données invalides affiche clairement le bloc d'erreurs (aucun 200 silencieux)."""
        client = self.login_as(self.admin)
        client.post(f"/annees/{self.annee_cible.id}/consulter")

        # Soumission avec capacité invalide (ex: 9999 > 500)
        response = client.post(
            "/classes/add",
            data={
                "niveau_id": self.niv["6E"].id,
                "section": "A",
                "capacite": 9999,  # Dépasse le max de 500
                "professeur_principal_id": 0,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        # Doit afficher le bloc d'erreurs
        self.assertIn(b"Veuillez corriger les erreurs", response.data)
        self.assertIn(b"alert-danger", response.data)

    # -------------------------------------------------------------------------
    # 3. Passage d'année : verrou global supprimé, verrou individuel préservé
    # -------------------------------------------------------------------------
    def test_passage_annee_bouton_traiter_accessible_sans_classe_ouverte_cible(self):
        """Sur /annees/<src>/passage/<cible>, le bouton Traiter n'est pas disabled même si aucune classe ouverte n'existe dans la cible."""
        # Aucune classe n'existe dans annee_cible
        classes_cible = Classe.query.filter_by(annee_scolaire_id=self.annee_cible.id).all()
        self.assertEqual(len(classes_cible), 0)

        client = self.login_as(self.admin)
        response = client.get(f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}")
        self.assertEqual(response.status_code, 200)

        # L'alerte informative est affichée
        self.assertIn(b"Structure de l", response.data)
        self.assertIn(b"en cours de configuration", response.data)

        # Le bouton Traiter pour l'élève est un lien actif et N'EST PAS disabled
        lien_traiter = f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.eleve.id}"
        self.assertIn(lien_traiter.encode(), response.data)
        self.assertNotIn(b'disabled title="Structure cible', response.data)

    def test_passage_eleve_permet_sortie_meme_sans_classe_cible(self):
        """Un élève peut être traité en sortie même s'il n'existe aucune classe ouverte dans l'année cible."""
        client = self.login_as(self.admin)
        response = client.post(
            f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.eleve.id}/executer",
            data={
                "decision": "sortie",
                "motif_sortie": "Déménagement familial",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        # Inscription source clôturée en sortie
        insc_src = Inscription.query.filter_by(
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_active.id,
        ).first()
        self.assertEqual(insc_src.statut, "sorti")
        self.assertEqual(insc_src.decision_fin_annee, "sortie")
        self.assertEqual(insc_src.motif_sortie, "Déménagement familial")

        # Aucune inscription cible créée
        insc_cible = Inscription.query.filter_by(
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_cible.id,
        ).first()
        self.assertIsNone(insc_cible)

    def test_passage_eleve_refuse_passage_si_aucune_classe_cible_ouverte(self):
        """Le verrou individuel refuse le passage si aucune classe ouverte n'existe pour le niveau suivant."""
        client = self.login_as(self.admin)
        # Tenter un passage alors qu'aucune classe 5e n'existe dans annee_cible
        response = client.post(
            f"/annees/{self.annee_active.id}/passage/{self.annee_cible.id}/eleves/{self.eleve.id}/executer",
            data={
                "decision": "passage",
                "classe_cible_id": 9999,  # Classe inexistante
            },
            follow_redirects=True,
        )
        # Doit échouer et réafficher la page de l'élève avec message danger
        self.assertIn("Classe cible introuvable".encode(), response.data)

        # L'élève reste inscrit en source
        insc_src = Inscription.query.filter_by(
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_active.id,
        ).first()
        self.assertEqual(insc_src.statut, "inscrit")


if __name__ == "__main__":
    unittest.main()
