"""
Tests KLASORA — Phase 5L : Paiements + QR Code (Sécurité, Cohérence Annuelle, Audit).
Exactement 10 tests ciblés.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    JournalCorrection,
    NiveauScolaire,
    Paiement,
    Utilisateur,
)
from app.services.paiements_annuels import (
    enregistrer_paiement,
    get_finances_inscription,
    supprimer_paiement_securise,
    valider_mutation_paiement,
)


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5l"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestPhase5LPaiementsQR(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Écoles
        self.ecole1 = Ecole(nom="Ecole Alpha")
        self.ecole2 = Ecole(nom="Ecole Beta")
        db.session.add_all([self.ecole1, self.ecole2])
        db.session.flush()

        # Année scolaire active & archivée École 1
        self.annee_active = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="active",
            ecole_id=self.ecole1.id,
        )
        self.annee_archivee = AnneeScolaire(
            nom="2023-2024",
            date_debut=date(2023, 9, 1),
            date_fin=date(2024, 6, 30),
            statut="archivee",
            ecole_id=self.ecole1.id,
        )
        db.session.add_all([self.annee_active, self.annee_archivee])
        db.session.flush()

        # Niveau & Classes
        self.niveau = NiveauScolaire(code="6EME", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niveau)
        db.session.flush()

        anc1 = AnneeNiveauConfig(ecole_id=self.ecole1.id, annee_scolaire_id=self.annee_active.id, niveau_id=self.niveau.id, actif=True)
        anc2 = AnneeNiveauConfig(ecole_id=self.ecole2.id, annee_scolaire_id=self.annee_active.id, niveau_id=self.niveau.id, actif=True)
        db.session.add_all([anc1, anc2])
        db.session.flush()

        self.classe_active = Classe(nom="6ème A", ecole_id=self.ecole1.id, annee_scolaire_id=self.annee_active.id, niveau_id=self.niveau.id)
        self.classe_archivee = Classe(nom="6ème Old", ecole_id=self.ecole1.id, annee_scolaire_id=self.annee_archivee.id, niveau_id=self.niveau.id)
        db.session.add_all([self.classe_active, self.classe_archivee])
        db.session.flush()

        # Utilisateurs
        self.admin1 = Utilisateur(nom="Admin1", email="admin1@test.com", mot_de_passe="pass", role="admin", ecole_id=self.ecole1.id)
        self.admin2 = Utilisateur(nom="Admin2", email="admin2@test.com", mot_de_passe="pass", role="admin", ecole_id=self.ecole2.id)
        db.session.add_all([self.admin1, self.admin2])
        db.session.flush()

        # Élèves
        self.eleve1 = Eleve(nom="Diallo", prenom="Mamadou", date_naissance=date(2012, 5, 10), ecole_id=self.ecole1.id, frais_annuels=100000.0)
        self.eleve2 = Eleve(nom="Sow", prenom="Fatou", date_naissance=date(2012, 8, 15), ecole_id=self.ecole2.id, frais_annuels=100000.0)
        db.session.add_all([self.eleve1, self.eleve2])
        db.session.flush()

        # Inscriptions
        self.ins1 = Inscription(eleve_id=self.eleve1.id, classe_id=self.classe_active.id, annee_scolaire_id=self.annee_active.id, ecole_id=self.ecole1.id, frais_annuels=100000.0)
        self.ins_arch = Inscription(eleve_id=self.eleve1.id, classe_id=self.classe_archivee.id, annee_scolaire_id=self.annee_archivee.id, ecole_id=self.ecole1.id, frais_annuels=90000.0)
        self.ins2 = Inscription(eleve_id=self.eleve2.id, classe_id=self.classe_active.id, annee_scolaire_id=self.annee_active.id, ecole_id=self.ecole2.id, frais_annuels=120000.0)
        db.session.add_all([self.ins1, self.ins_arch, self.ins2])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, email, annee_consultee_id=None):
        with self.client.session_transaction() as sess:
            u = Utilisateur.query.filter_by(email=email).first()
            sess['_user_id'] = str(u.id)
            sess['ecole_id'] = u.ecole_id
            sess['annee_consultee_id'] = annee_consultee_id or self.annee_active.id

    # 1. Inscription frais 100000, aucun paiement -> payé 0 / reste 100000
    def test_01_frais_aucun_paiement(self):
        finances = get_finances_inscription(self.ins1)
        self.assertEqual(finances["frais_annuels"], 100000.0)
        self.assertEqual(finances["total_paye"], 0.0)
        self.assertEqual(finances["reste_a_payer"], 100000.0)
        self.assertEqual(finances["statut_solde"], "aucun")

    # 2. Deux paiements partiels -> somme et reste corrects
    def test_02_paiements_partiels(self):
        enregistrer_paiement(self.ecole1.id, self.annee_active, self.admin1, self.eleve1.id, 30000, "Octobre", 2024)
        enregistrer_paiement(self.ecole1.id, self.annee_active, self.admin1, self.eleve1.id, 20000, "Novembre", 2024)
        db.session.commit()

        finances = get_finances_inscription(self.ins1)
        self.assertEqual(finances["total_paye"], 50000.0)
        self.assertEqual(finances["reste_a_payer"], 50000.0)
        self.assertEqual(finances["statut_solde"], "partiel")

    # 3. Paiement autre école par ID forgé -> refus
    def test_03_paiement_autre_ecole_forged(self):
        # Admin1 (Ecole 1) tente de faire un paiement pour Eleve2 (Ecole 2)
        ins, err = valider_mutation_paiement(self.ecole1.id, self.annee_active, self.admin1, self.eleve2.id, 50000)
        self.assertIsNotNone(err)
        self.assertIsNone(ins)

    # 4. Année archivée -> nouveau paiement / modification refusé
    def test_04_annee_archivee_paiement_refuse(self):
        ins, err = valider_mutation_paiement(self.ecole1.id, self.annee_archivee, self.admin1, self.eleve1.id, 50000)
        self.assertIsNotNone(err)
        self.assertIn("archivée", err.lower())

    # 5. Modification/suppression paiement -> Log créé avec ancienne donnée utile
    def test_05_suppression_paiement_audit_log(self):
        self._login("admin1@test.com")
        p, _ = enregistrer_paiement(self.ecole1.id, self.annee_active, self.admin1, self.eleve1.id, 25000, "Décembre", 2024)
        db.session.commit()

        p_id = p.id
        ok, err = supprimer_paiement_securise(self.ecole1.id, self.annee_active, p_id, self.admin1)
        self.assertTrue(ok)

        # Vérifier création du log d'audit
        log = JournalCorrection.query.filter_by(action="PAIEMENT_SUPPRIME", cible_id=p_id).first()
        self.assertIsNotNone(log)
        self.assertIn("25000", log.ancienne_valeur)

    # 6. QR utilise année ACTIVE et pas annee_consultee
    def test_06_qr_utilise_annee_active(self):
        self._login("admin1@test.com", annee_consultee_id=self.annee_archivee.id)

        res = self.client.get(f"/api/qr/info/{self.eleve1.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["annee_scolaire"], "2024-2025")
        self.assertEqual(data["classe"], "6ème A")

    # 7. Après changement année ACTIVE, ancien QR retourne nouvelle inscription/classe
    def test_07_changement_annee_active_qr(self):
        self._login("admin1@test.com")
        # Créer nouvelle année active 2025-2026
        self.annee_active.statut = "archivee"
        annee_nouvelle = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30), statut="active", ecole_id=self.ecole1.id)
        db.session.add(annee_nouvelle)
        db.session.flush()

        anc_new = AnneeNiveauConfig(ecole_id=self.ecole1.id, annee_scolaire_id=annee_nouvelle.id, niveau_id=self.niveau.id, actif=True)
        db.session.add(anc_new)
        db.session.flush()

        classe_5eme = Classe(nom="5ème A", ecole_id=self.ecole1.id, annee_scolaire_id=annee_nouvelle.id, niveau_id=self.niveau.id)
        db.session.add(classe_5eme)
        db.session.flush()

        ins_new = Inscription(eleve_id=self.eleve1.id, classe_id=classe_5eme.id, annee_scolaire_id=annee_nouvelle.id, ecole_id=self.ecole1.id)
        db.session.add(ins_new)
        db.session.commit()

        res = self.client.get(f"/api/qr/info/{self.eleve1.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["annee_scolaire"], "2025-2026")
        self.assertEqual(data["classe"], "5ème A")

    # 8. Élève sans inscription ACTIVE -> aucune fausse classe historique
    def test_08_eleve_sans_inscription_active(self):
        self._login("admin1@test.com")
        # Créer élève sans aucune inscription dans l'année active
        eleve_inactif = Eleve(nom="Kaba", prenom="Sekou", date_naissance=date(2013, 1, 1), ecole_id=self.ecole1.id)
        db.session.add(eleve_inactif)
        db.session.commit()

        res = self.client.get(f"/api/qr/info/{eleve_inactif.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["classe"], "Aucune inscription active")
        self.assertEqual(data["statut"], "Non inscrit")

    # 9. QR école A ne donne pas accès école B
    def test_09_qr_isolation_ecole(self):
        self._login("admin1@test.com") # Admin Ecole 1
        res = self.client.get(f"/eleve/{self.eleve2.id}/qrcode") # Eleve2 appartient à Ecole 2
        self.assertIn(res.status_code, [403, 302])

    # 10. Réponse QR publique ne contient aucune donnée financière ni contact parent sensible
    def test_10_qr_securite_donnees_publiques(self):
        self._login("admin1@test.com")
        res = self.client.get(f"/api/qr/info/{self.eleve1.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        # Clés autorisées uniquement
        self.assertIn("nom", data)
        self.assertIn("ecole", data)
        self.assertIn("annee_scolaire", data)
        self.assertIn("classe", data)
        self.assertIn("statut", data)

        # Aucune donnée financière ou contact privé
        self.assertNotIn("frais_annuels", data)
        self.assertNotIn("total_paye", data)
        self.assertNotIn("paiements", data)
        self.assertNotIn("email", data)
        self.assertNotIn("telephone", data)
        self.assertNotIn("contact_parent", data)


if __name__ == "__main__":
    unittest.main()

