import unittest
from datetime import date, datetime
from flask import g, session
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Ecole, AnneeScolaire, Utilisateur, Classe, Eleve, Inscription, NiveauScolaire, AnneeNiveauConfig, Professeur
from app.services.classes_annuelles import precharger_effectifs_classes, precharger_effectif_classe
from sqlalchemy import event
from sqlalchemy.engine import Engine

class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

class Phase3EffectifDernierAccesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Ecole A
        self.ecole_a = Ecole(nom="Ecole A")
        # Ecole B
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Année Scolaire 2024-2025 pour Ecole A
        self.annee_a_2024 = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id
        )
        # Année Scolaire 2025-2026 pour Ecole A (active)
        self.annee_a_2025 = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id
        )
        # Année Scolaire 2025-2026 pour Ecole B (active)
        self.annee_b_2025 = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.annee_a_2024, self.annee_a_2025, self.annee_b_2025])
        db.session.flush()

        # Niveaux
        self.niveau_a = NiveauScolaire(code="6E", nom="Sixième", cycle="college", ordre=1)
        self.niveau_b = NiveauScolaire(code="5E", nom="Cinquième", cycle="college", ordre=2)
        db.session.add_all([self.niveau_a, self.niveau_b])
        db.session.flush()

        # Classes
        self.classe_a_2024 = Classe(
            nom="6e A (2024)",
            niveau="6e",
            niveau_id=self.niveau_a.id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a_2024.id,
            capacite=30,
            capacite_max=30
        )
        self.classe_a_2025 = Classe(
            nom="6e A (2025)",
            niveau="6e",
            niveau_id=self.niveau_a.id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a_2025.id,
            capacite=30,
            capacite_max=30
        )
        self.classe_b_2025 = Classe(
            nom="6e B (Ecole B)",
            niveau="6e",
            niveau_id=self.niveau_a.id,
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_b_2025.id,
            capacite=30,
            capacite_max=30
        )
        self.classe_a_5e = Classe(
            nom="5e A (2025)",
            niveau="5e",
            niveau_id=self.niveau_b.id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a_2025.id,
            capacite=30,
            capacite_max=30
        )
        db.session.add_all([self.classe_a_2024, self.classe_a_2025, self.classe_b_2025, self.classe_a_5e])
        db.session.flush()

        config_a = AnneeNiveauConfig(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a_2025.id,
            niveau_id=self.niveau_a.id,
            actif=True
        )
        db.session.add(config_a)

        # Utilisateur Admin pour Ecole A
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Admin",
            email="admin@ecole-a.local",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole_a.id,
            dernier_acces=None
        )
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_01_effectif_eleve_sans_classe_id(self):
        """Cas 1 : Un élève sans Eleve.classe_id (None) mais avec une Inscription active est bien comptabilisé."""
        eleve = Eleve(
            nom="DIALLO", prenom="Mamadou", date_naissance=date(2012, 5, 10),
            ecole_id=self.ecole_a.id, classe_id=None
        )
        db.session.add(eleve)
        db.session.flush()

        ins = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve.id,
            classe_id=self.classe_a_2025.id,
            annee_scolaire_id=self.annee_a_2025.id,
            statut="inscrit"
        )
        db.session.add(ins)
        db.session.commit()

        # Préchargement explicite de l'effectif
        precharger_effectif_classe(self.classe_a_2025)
        self.assertEqual(self.classe_a_2025.effectif_reel, 1)

    def test_02_effectif_historique_repond_a_inscription_pas_eleve_classe_id(self):
        """Cas 2 : Eleve.classe_id pointe sur la nouvelle classe (5e A), mais la classe historique (6e A 2024) conserve son effectif via Inscription."""
        eleve = Eleve(
            nom="BAH", prenom="Aissatou", date_naissance=date(2011, 4, 15),
            ecole_id=self.ecole_a.id, classe_id=self.classe_a_5e.id
        )
        db.session.add(eleve)
        db.session.flush()

        # Inscription historique en 2024-2025 dans 6e A
        ins_2024 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve.id,
            classe_id=self.classe_a_2024.id,
            annee_scolaire_id=self.annee_a_2024.id,
            statut="inscrit"
        )
        # Inscription actuelle en 2025-2026 dans 5e A
        ins_2025 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve.id,
            classe_id=self.classe_a_5e.id,
            annee_scolaire_id=self.annee_a_2025.id,
            statut="inscrit"
        )
        db.session.add_all([ins_2024, ins_2025])
        db.session.commit()

        classes = [self.classe_a_2024, self.classe_a_5e, self.classe_a_2025]
        precharger_effectifs_classes(classes, ecole_id=self.ecole_a.id)

        # La classe 2024 conserve 1 élève malgré le fait que Eleve.classe_id pointe sur 5e
        self.assertEqual(self.classe_a_2024.effectif_reel, 1)
        # La classe 5e de 2025 a 1 élève
        self.assertEqual(self.classe_a_5e.effectif_reel, 1)
        # La classe 6e de 2025 a 0 élève
        self.assertEqual(self.classe_a_2025.effectif_reel, 0)

    def test_03_effectif_exclut_desinscrits(self):
        """Cas 3 : Une inscription avec statut='desinscrit' n'est pas comptée dans l'effectif réel."""
        eleve1 = Eleve(nom="CISSE", prenom="Oumar", date_naissance=date(2012, 1, 10), ecole_id=self.ecole_a.id)
        eleve2 = Eleve(nom="TRAORE", prenom="Fatou", date_naissance=date(2012, 2, 20), ecole_id=self.ecole_a.id)
        db.session.add_all([eleve1, eleve2])
        db.session.flush()

        ins_actif = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve1.id,
            classe_id=self.classe_a_2025.id,
            annee_scolaire_id=self.annee_a_2025.id,
            statut="inscrit"
        )
        ins_desinscrit = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve2.id,
            classe_id=self.classe_a_2025.id,
            annee_scolaire_id=self.annee_a_2025.id,
            statut="desinscrit"
        )
        db.session.add_all([ins_actif, ins_desinscrit])
        db.session.commit()

        precharger_effectif_classe(self.classe_a_2025)
        # Seul l'élève actif doit être comptabilisé
        self.assertEqual(self.classe_a_2025.effectif_reel, 1)

    def test_04_isolation_multi_ecoles(self):
        """Cas 4 : Les inscriptions d'une autre école ne polluent jamais l'effectif d'une classe."""
        eleve_a = Eleve(nom="KONE", prenom="Ali", date_naissance=date(2012, 3, 15), ecole_id=self.ecole_a.id)
        eleve_b = Eleve(nom="TOURE", prenom="Sekou", date_naissance=date(2012, 4, 18), ecole_id=self.ecole_b.id)
        db.session.add_all([eleve_a, eleve_b])
        db.session.flush()

        ins_a = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve_a.id,
            classe_id=self.classe_a_2025.id,
            annee_scolaire_id=self.annee_a_2025.id,
            statut="inscrit"
        )
        ins_b = Inscription(
            ecole_id=self.ecole_b.id,
            eleve_id=eleve_b.id,
            classe_id=self.classe_b_2025.id,
            annee_scolaire_id=self.annee_b_2025.id,
            statut="inscrit"
        )
        db.session.add_all([ins_a, ins_b])
        db.session.commit()

        precharger_effectifs_classes([self.classe_a_2025, self.classe_b_2025])
        self.assertEqual(self.classe_a_2025.effectif_reel, 1)
        self.assertEqual(self.classe_b_2025.effectif_reel, 1)

    def test_05_isolation_multi_annees(self):
        """Cas 5 : L'effectif annuel est strictement isolé par annee_scolaire_id."""
        eleve1 = Eleve(nom="SOW", prenom="Ibrahima", date_naissance=date(2011, 7, 8), ecole_id=self.ecole_a.id)
        eleve2 = Eleve(nom="SOW", prenom="Mariam", date_naissance=date(2012, 8, 9), ecole_id=self.ecole_a.id)
        db.session.add_all([eleve1, eleve2])
        db.session.flush()

        # Inscription eleve1 en 2024
        ins1 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve1.id,
            classe_id=self.classe_a_2024.id,
            annee_scolaire_id=self.annee_a_2024.id,
            statut="inscrit"
        )
        # Inscription eleve2 en 2025
        ins2 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve2.id,
            classe_id=self.classe_a_2025.id,
            annee_scolaire_id=self.annee_a_2025.id,
            statut="inscrit"
        )
        db.session.add_all([ins1, ins2])
        db.session.commit()

        precharger_effectifs_classes([self.classe_a_2024, self.classe_a_2025], ecole_id=self.ecole_a.id)
        self.assertEqual(self.classe_a_2024.effectif_reel, 1)
        self.assertEqual(self.classe_a_2025.effectif_reel, 1)

    def test_06_route_classes_batch_effectif_et_zero_n_plus_un(self):
        """Cas 6 : /classes calcule l'effectif en lot (GROUP BY) et injecte _effectif_annuel sans requêtes par classe."""
        for i in range(5):
            e = Eleve(nom=f"Eleve_{i}", prenom="Test", date_naissance=date(2012, 1, i + 1), ecole_id=self.ecole_a.id)
            db.session.add(e)
            db.session.flush()
            target_classe = self.classe_a_2025 if i < 3 else self.classe_a_5e
            ins = Inscription(
                ecole_id=self.ecole_a.id,
                eleve_id=e.id,
                classe_id=target_classe.id,
                annee_scolaire_id=self.annee_a_2025.id,
                statut="inscrit"
            )
            db.session.add(ins)
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole_a.id

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        resp = client.get('/classes')
        self.assertEqual(resp.status_code, 200)

        # Vérifier qu'aucune requête SELECT eleve... ou SELECT inscription individuelle n'a été exécutée
        eleve_queries = [q for q in queries if 'FROM eleve' in q.lower()]
        self.assertEqual(len(eleve_queries), 0, "Aucun SELECT sur la table eleve ne doit être émis pour l'effectif")

        # Vérifier qu'il y a bien une requête avec GROUP BY classe_id
        group_by_queries = [q for q in queries if 'group by' in q.lower() and 'classe_id' in q.lower()]
        self.assertEqual(len(group_by_queries), 1, "Une seule requête d'agrégation GROUP BY classe_id doit être émise")

    def test_07_get_index_ne_modifie_pas_dernier_acces(self):
        """Cas 7 : Un simple GET / ne modifie pas dernier_acces et n'exécute aucun UPDATE sur utilisateur."""
        initial_time = datetime(2025, 1, 1, 12, 0, 0)
        self.admin.dernier_acces = initial_time
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole_a.id

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        resp = client.get('/', follow_redirects=False)
        self.assertIn(resp.status_code, (200, 302))

        # Vérifier qu'aucun UPDATE utilisateur n'a eu lieu
        update_queries = [q for q in queries if 'update utilisateur' in q.lower()]
        self.assertEqual(len(update_queries), 0, "GET / ne doit pas émettre d'UPDATE sur utilisateur")

        # Vérifier que dernier_acces en base n'a pas bougé
        db.session.refresh(self.admin)
        self.assertEqual(self.admin.dernier_acces, initial_time)

    def test_08_connexion_reussie_met_a_jour_dernier_acces(self):
        """Cas 8 : Une connexion réussie via POST /login met à jour dernier_acces."""
        client = self.app.test_client()
        self.assertIsNone(self.admin.dernier_acces)

        resp = client.post('/login', data={
            'email': self.admin.email,
            'mot_de_passe': 'Secret123!'
        }, follow_redirects=False)

        # Redirection post-login attendue (302)
        self.assertEqual(resp.status_code, 302)

        # Vérifier que dernier_acces a été mis à jour
        db.session.refresh(self.admin)
        self.assertIsNotNone(self.admin.dernier_acces)
        self.assertTrue((datetime.utcnow() - self.admin.dernier_acces).total_seconds() < 10)

    def test_09_connexion_echouee_ne_met_pas_a_jour_dernier_acces(self):
        """Cas 9 : Une tentative de connexion échouée ne met pas à jour dernier_acces."""
        client = self.app.test_client()
        self.assertIsNone(self.admin.dernier_acces)

        resp = client.post('/login', data={
            'email': self.admin.email,
            'mot_de_passe': 'MauvaisMotDePasse'
        }, follow_redirects=False)

        self.assertEqual(resp.status_code, 200)

        db.session.refresh(self.admin)
        self.assertIsNone(self.admin.dernier_acces)

    def test_10_effectif_reel_zero_sql_when_not_preloaded(self):
        """Cas 10 : Classe.effectif_reel n'émet STRICTEMENT AUCUNE requête SQL lorsqu'il est appelé."""
        # Toucher l'instance pour dé-expirer après setUp commit
        _ = (self.classe_a_2025.id, self.classe_a_2025.capacite, self.classe_a_2025.capacite_max)

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        # Appel direct sur l'instance
        eff = self.classe_a_2025.effectif_reel
        pleine = self.classe_a_2025.est_pleine

        self.assertEqual(len(queries), 0, "Classe.effectif_reel ne doit jamais déclencher de requête SQL")
        self.assertEqual(eff, 0, "Sans préchargement, effectif_reel doit retourner 0 sans SQL")
        self.assertFalse(pleine)

    def test_11_liste_25_classes_exactement_une_requete_agregation(self):
        """Cas 11 : Une liste de 25 classes génère EXACTEMENT 1 seule requête SQL d'agrégation pour précharger les effectifs."""
        classes_25 = []
        for i in range(25):
            c = Classe(
                nom=f"Classe_{i}",
                niveau="6e",
                ecole_id=self.ecole_a.id,
                annee_scolaire_id=self.annee_a_2025.id,
                capacite=30
            )
            db.session.add(c)
            classes_25.append(c)
        db.session.commit()

        # Toucher les attributs des classes et resoudre ecole_id / annee_id avant d'ecouter
        ecole_id = self.ecole_a.id
        annee_id = self.annee_a_2025.id
        for c in classes_25:
            _ = (c.id, c.annee_scolaire_id)

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        precharger_effectifs_classes(classes_25, ecole_id=ecole_id, annee_id=annee_id)

        self.assertEqual(len(queries), 1, "Exactement 1 requête SQL doit être émise pour 25 classes")
        self.assertTrue(all(hasattr(c, '_effectif_annuel') for c in classes_25))

    def test_12_classe_to_dict_zero_n_plus_un(self):
        """Cas 12 : Classe.to_dict() sur une liste de classes n'émet aucun SELECT SQL."""
        classes = [self.classe_a_2024, self.classe_a_2025, self.classe_a_5e]
        precharger_effectifs_classes(classes)

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        dicts = [c.to_dict() for c in classes]

        # Vérifier que to_dict n'a émis aucune requête sur inscription ou eleve
        sql_inscriptions = [q for q in queries if 'inscriptions' in q.lower() or 'eleve' in q.lower()]
        self.assertEqual(len(sql_inscriptions), 0)
        self.assertEqual(len(dicts), 3)

    def test_13_professeur_to_dict_zero_n_plus_un(self):
        """Cas 13 : Professeur.to_dict() n'émet aucun SELECT d'effectif."""
        u_prof = Utilisateur(
            nom="Prof",
            prenom="Amara",
            email="prof_amara@ecole-a.local",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="professeur",
            ecole_id=self.ecole_a.id
        )
        db.session.add(u_prof)
        db.session.flush()

        prof = Professeur(
            nom="TOURE",
            prenom="Amara",
            utilisateur_id=u_prof.id,
            ecole_id=self.ecole_a.id,
            date_naissance=date(1980, 1, 1),
            specialite="Maths"
        )
        db.session.add(prof)
        db.session.flush()

        from app.models import professeur_classes
        db.session.execute(
            professeur_classes.insert().values(
                professeur_id=prof.id,
                classe_id=self.classe_a_2025.id,
                ecole_id=self.ecole_a.id
            )
        )
        db.session.execute(
            professeur_classes.insert().values(
                professeur_id=prof.id,
                classe_id=self.classe_a_5e.id,
                ecole_id=self.ecole_a.id
            )
        )
        db.session.commit()

        # Toucher les objets pour dé-expirer la session
        db.session.refresh(prof)
        _ = (prof.id, prof.nom, [c.id for c in prof.classes_assignees])

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        data = prof.to_dict()

        # Ne doit émettre aucune requête SQL sur inscriptions
        sql_inscriptions = [q for q in queries if 'inscriptions' in q.lower()]
        self.assertEqual(len(sql_inscriptions), 0)
        self.assertEqual(len(data["classes_assignees"]), 2)

    def test_14_modifier_classe_zero_sql_depuis_jinja(self):
        """Cas 14 : GET /classes/<id>/modifier précharge la classe en amont et n'exécute aucun SQL dans Jinja."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole_a.id

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        resp = client.get(f'/classes/{self.classe_a_2025.id}/modifier')
        self.assertEqual(resp.status_code, 200)

        # Vérifier qu'il n'y a eu qu'une seule requête de comptage explicite sur inscription
        count_queries = [q for q in queries if 'count(' in q.lower() and 'inscriptions' in q.lower()]
        self.assertEqual(len(count_queries), 1, "La route doit émettre exactement 1 comptage explicite en amont du template")

    def test_15_get_statistics_batch_precharge_et_zero_n_plus_un(self):
        """Cas 15 : get_statistics(classes) précharge en lot les classes non préparées, sans N+1 et avec Inscription comme vérité."""
        from app.services import get_statistics

        c1 = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a_2025.id, capacite=30)
        c2 = Classe(nom="6e B", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a_2025.id, capacite=30)
        c3 = Classe(nom="6e C", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a_2025.id, capacite=30)
        db.session.add_all([c1, c2, c3])
        db.session.commit()

        # Inscrire 5 élèves en c1, 2 élèves en c2, 0 en c3
        for i in range(5):
            e = Eleve(nom=f"E1_{i}", prenom="Test", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id)
            # Piège : attribuer classe_id=c3.id sur Eleve mais inscrire dans c1
            e.classe_id = c3.id
            db.session.add(e)
            db.session.flush()
            db.session.add(Inscription(
                ecole_id=self.ecole_a.id,
                eleve_id=e.id,
                classe_id=c1.id,
                annee_scolaire_id=self.annee_a_2025.id,
                statut="inscrit"
            ))
        for i in range(2):
            e = Eleve(nom=f"E2_{i}", prenom="Test", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id)
            db.session.add(e)
            db.session.flush()
            db.session.add(Inscription(
                ecole_id=self.ecole_a.id,
                eleve_id=e.id,
                classe_id=c2.id,
                annee_scolaire_id=self.annee_a_2025.id,
                statut="inscrit"
            ))
        db.session.commit()

        classes_non_preparees = [c1, c2, c3]
        # Dé-expirer les instances
        for c in classes_non_preparees:
            _ = (c.id, c.capacite, c.annee_scolaire_id)

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        stats = get_statistics(classes_non_preparees)

        # Exactement 1 requête SQL émise pour tout le calcul de stats (le batch precharger_effectifs_classes)
        self.assertEqual(len(queries), 1, "get_statistics doit déclencher au plus 1 requête d'agrégation pour toutes les classes")
        self.assertEqual(stats['total_eleves'], 7, "Total élèves = 5 (c1) + 2 (c2) + 0 (c3) = 7")
        self.assertEqual(c1.effectif_reel, 5)
        self.assertEqual(c2.effectif_reel, 2)
        self.assertEqual(c3.effectif_reel, 0, "c3 doit avoir 0 malgré eleve.classe_id=c3")

    def test_16_export_serialization_classes_zero_n_plus_un(self):
        """Cas 16 : La sérialisation / export JSON de classes n'émet aucun N+1 d'effectif et n'utilise pas Eleve.classe_id."""
        import json

        # Créer des élèves inscrits dans classe_a_2025
        for i in range(3):
            e = Eleve(nom=f"Eleve_Exp_{i}", prenom="Test", date_naissance=date(2012, 1, 1), ecole_id=self.ecole_a.id)
            # Piège : attribuer classe_id=classe_a_5e.id mais inscrire dans classe_a_2025
            e.classe_id = self.classe_a_5e.id
            db.session.add(e)
            db.session.flush()
            db.session.add(Inscription(
                ecole_id=self.ecole_a.id,
                eleve_id=e.id,
                classe_id=self.classe_a_2025.id,
                annee_scolaire_id=self.annee_a_2025.id,
                statut="inscrit"
            ))
        db.session.commit()

        classes = [self.classe_a_2024, self.classe_a_2025, self.classe_a_5e]
        precharger_effectifs_classes(classes)

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        data = [c.to_dict() for c in classes]
        json_output = json.dumps(data)

        # Vérifier qu'aucun SELECT sur inscriptions ou eleves n'a été émis pour le calcul d'effectif
        sql_effectif = [q for q in queries if 'inscriptions' in q.lower() or 'eleve' in q.lower()]
        self.assertEqual(len(sql_effectif), 0, "Aucun SELECT sur Inscription/Eleve lors de l'export/sérialisation")
        self.assertIn("effectif_reel", json_output)

        # Vérifier que l'effectif exporté correspond à Inscription et pas à Eleve.classe_id
        dict_2025 = next(d for d in data if d["id"] == self.classe_a_2025.id)
        dict_5e = next(d for d in data if d["id"] == self.classe_a_5e.id)
        self.assertEqual(dict_2025["effectif_reel"], 3)
        self.assertEqual(dict_5e["effectif_reel"], 0, "La classe 5e ne doit pas compter les élèves ayant Eleve.classe_id=5e sans Inscription")

if __name__ == '__main__':
    unittest.main()


