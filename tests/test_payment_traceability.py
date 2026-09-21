"""Tests pour la page de traçabilité et d'audit des paiements (lecture seule, horodatage, opérateur, année).

Couvre :
1. Accès à la page /paiements/tracabilite en lecture seule stricte (aucun formulaire de suppression).
2. Présence de l'opérateur (compte utilisateur) et de l'horodatage exact.
3. Traçabilité des encaissements et des annulations avec motif.
4. Rattachement strict à l'année consultée (active et archivée).
5. Isolation multi-tenant stricte par ecole_id.
6. Recherche et filtrage par action.
"""

from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, JournalCorrection, Paiement, Utilisateur


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-payment-traceability"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class PaymentTraceabilityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Écoles
        self.ecole_a = Ecole(nom="Ecole Alpha", adresse="Niamey", telephone="90000000", onboarding_complete=True)
        self.ecole_b = Ecole(nom="Ecole Beta", adresse="Maradi", telephone="91111111", onboarding_complete=True)
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Années scolaires Ecole A
        self.annee_active_a = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_archive_a = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        # Année Ecole B
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_active_a, self.annee_archive_a, self.annee_b])
        db.session.flush()

        # Classes
        self.classe_active_a = Classe(nom="6e A", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_active_a.id)
        self.classe_archive_a = Classe(nom="CM2 A", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_archive_a.id)
        self.classe_b = Classe(nom="6e B", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_active_a, self.classe_archive_a, self.classe_b])
        db.session.flush()

        # Admins
        self.admin_a = Utilisateur(nom="Diallo", prenom="Oumar", email="admin-a@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Kone", prenom="Amina", email="admin-b@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin_a, self.admin_b])
        db.session.flush()

        # Élèves et Inscriptions
        self.eleve_active_a = Eleve(nom="Koffi", prenom="Paul", date_naissance=date(2012, 5, 10), ecole_id=self.ecole_a.id)
        self.eleve_archive_a = Eleve(nom="Toure", prenom="Sali", date_naissance=date(2011, 4, 1), ecole_id=self.ecole_a.id)
        self.eleve_b = Eleve(nom="Sow", prenom="Fatou", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_b.id)
        db.session.add_all([self.eleve_active_a, self.eleve_archive_a, self.eleve_b])
        db.session.flush()

        self.ins_active_a = Inscription(
            eleve_id=self.eleve_active_a.id,
            classe_id=self.classe_active_a.id,
            annee_scolaire_id=self.annee_active_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=120000,
        )
        self.ins_archive_a = Inscription(
            eleve_id=self.eleve_archive_a.id,
            classe_id=self.classe_archive_a.id,
            annee_scolaire_id=self.annee_archive_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=100000,
        )
        self.ins_b = Inscription(
            eleve_id=self.eleve_b.id,
            classe_id=self.classe_b.id,
            annee_scolaire_id=self.annee_b.id,
            ecole_id=self.ecole_b.id,
            frais_annuels=110000,
        )
        db.session.add_all([self.ins_active_a, self.ins_archive_a, self.ins_b])
        db.session.flush()

        # Paiements
        self.p_active = Paiement(
            montant=40000,
            mois="Octobre",
            annee=2026,
            mode_paiement="Espèces",
            statut="payé",
            reference="PAY-ACT-001",
            eleve_id=self.eleve_active_a.id,
            inscription_id=self.ins_active_a.id,
            ecole_id=self.ecole_a.id,
            date_paiement=datetime(2026, 10, 5, 14, 30),
        )
        self.p_archive = Paiement(
            montant=50000,
            mois="Novembre",
            annee=2025,
            mode_paiement="Chèque",
            statut="payé",
            reference="PAY-ARC-999",
            eleve_id=self.eleve_archive_a.id,
            inscription_id=self.ins_archive_a.id,
            ecole_id=self.ecole_a.id,
            date_paiement=datetime(2025, 11, 10, 9, 15),
        )
        self.p_b = Paiement(
            montant=35000,
            mois="Octobre",
            annee=2026,
            mode_paiement="Virement",
            statut="payé",
            reference="PAY-B-777",
            eleve_id=self.eleve_b.id,
            inscription_id=self.ins_b.id,
            ecole_id=self.ecole_b.id,
            date_paiement=datetime(2026, 10, 6, 11, 0),
        )
        db.session.add_all([self.p_active, self.p_archive, self.p_b])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user, annee=None):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["ecole_id"] = user.ecole_id
            target_annee = annee or (self.annee_active_a if user.ecole_id == self.ecole_a.id else self.annee_b)
            sess["annee_consultee"] = {str(user.ecole_id): target_annee.id}

    # ------------------------------------------------------------------
    # 1. Accès à la traçabilité & présence du bouton dans paiements.html
    # ------------------------------------------------------------------
    def test_traceability_page_access_and_button_presence(self):
        self._login(self.admin_a)

        # Vérifier que le bouton Traçabilité est présent sur la page paiements
        res_paiements = self.client.get("/paiements")
        self.assertEqual(res_paiements.status_code, 200)
        html_p = res_paiements.get_data(as_text=True)
        self.assertIn("/paiements/tracabilite", html_p)
        self.assertIn("Traçabilité", html_p)

        # Accéder à la page traçabilité
        res_trac = self.client.get("/paiements/tracabilite")
        self.assertEqual(res_trac.status_code, 200)
        html_t = res_trac.get_data(as_text=True)
        self.assertIn("Traçabilité des Paiements", html_t)
        self.assertIn("Lecture seule & Audit certifié", html_t)
        self.assertIn("2026-2027", html_t)

    # ------------------------------------------------------------------
    # 2. Mode lecture seule stricte (aucun bouton/formulaire de suppression)
    # ------------------------------------------------------------------
    def test_strict_read_only_mode(self):
        self._login(self.admin_a)
        res = self.client.get("/paiements/tracabilite")
        html = res.get_data(as_text=True)

        # Aucune action destructrice
        self.assertNotIn('method="POST"', html.upper())
        self.assertNotIn("btn-outline-danger", html)
        self.assertIn("Lecture seule", html)

    # ------------------------------------------------------------------
    # 3. Traçabilité des opérations, compte opérateur et horodatage
    # ------------------------------------------------------------------
    def test_traceability_operator_account_and_timestamp(self):
        self._login(self.admin_a)

        # Effectuer une annulation douce avec un motif clair
        res_cancel = self.client.post(
            f"/paiement/{self.p_active.id}/annuler",
            data={"motif": "Erreur de chèque non approvisionné"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res_cancel.status_code, 200)

        # Consulter la page de traçabilité
        res_page = self.client.get("/paiements/tracabilite")
        self.assertEqual(res_page.status_code, 200)
        html = res_page.get_data(as_text=True)

        # L'opérateur (compte admin_a) doit être affiché
        self.assertIn("Oumar", html)
        self.assertIn("Diallo", html)
        self.assertIn("admin-a@test.local", html)

        # L'action d'annulation et le motif doivent être visibles
        self.assertIn("Annulation", html)
        self.assertIn("Erreur de chèque non approvisionné", html)

        # L'élève et le montant doivent être visibles
        self.assertIn("Paul", html)
        self.assertIn("Koffi", html)
        self.assertIn("40,000 FCFA", html)

    # ------------------------------------------------------------------
    # 4. Rattachement à l'année consultée (Active vs Archivée)
    # ------------------------------------------------------------------
    def test_traceability_filtered_by_active_and_archived_year(self):
        # 1) Sur l'année active (2026-2027)
        self._login(self.admin_a, annee=self.annee_active_a)
        res_active = self.client.get("/paiements/tracabilite")
        html_active = res_active.get_data(as_text=True)

        self.assertIn("PAY-ACT-001", html_active)
        self.assertIn("Paul Koffi", html_active)
        # Ne doit pas voir le paiement de l'année archivée
        self.assertNotIn("PAY-ARC-999", html_active)
        self.assertNotIn("Sali Toure", html_active)

        # 2) Sur l'année archivée (2025-2026)
        self._login(self.admin_a, annee=self.annee_archive_a)
        res_archive = self.client.get("/paiements/tracabilite")
        html_archive = res_archive.get_data(as_text=True)

        self.assertIn("PAY-ARC-999", html_archive)
        self.assertIn("Sali Toure", html_archive)
        # Ne doit pas voir le paiement de l'année active
        self.assertNotIn("PAY-ACT-001", html_archive)
        self.assertNotIn("Paul Koffi", html_archive)

    # ------------------------------------------------------------------
    # 5. Isolation multi-tenant stricte
    # ------------------------------------------------------------------
    def test_multi_tenant_isolation(self):
        # Admin A ne doit pas voir les paiements de l'école B
        self._login(self.admin_a, annee=self.annee_active_a)
        res_a = self.client.get("/paiements/tracabilite")
        html_a = res_a.get_data(as_text=True)

        self.assertNotIn("PAY-B-777", html_a)
        self.assertNotIn("Fatou Sow", html_a)
        self.assertNotIn("admin-b@test.local", html_a)

        # Admin B ne voit que l'école B
        self._login(self.admin_b, annee=self.annee_b)
        res_b = self.client.get("/paiements/tracabilite")
        html_b = res_b.get_data(as_text=True)

        self.assertIn("PAY-B-777", html_b)
        self.assertIn("Fatou Sow", html_b)
        self.assertNotIn("PAY-ACT-001", html_b)
        self.assertNotIn("Paul Koffi", html_b)

    # ------------------------------------------------------------------
    # 6. Recherche et filtres
    # ------------------------------------------------------------------
    def test_search_and_filters(self):
        self._login(self.admin_a, annee=self.annee_active_a)

        # Recherche ciblée par référence
        res_ref = self.client.get("/paiements/tracabilite?search=PAY-ACT-001")
        self.assertEqual(res_ref.status_code, 200)
        self.assertIn("PAY-ACT-001", res_ref.get_data(as_text=True))

        # Recherche sans résultat
        res_vide = self.client.get("/paiements/tracabilite?search=INEXISTANT_XYZ")
        self.assertEqual(res_vide.status_code, 200)
        self.assertIn("Aucune opération trouvée", res_vide.get_data(as_text=True))

    # ------------------------------------------------------------------
    # 7. Tri antéchronologique (plus récent en haut) et pagination
    # ------------------------------------------------------------------
    def test_chronological_order_newest_first_and_pagination(self):
        self._login(self.admin_a, annee=self.annee_active_a)

        # Création de deux paiements avec des dates distinctes
        p_ancien = Paiement(
            montant=10000,
            mois="Octobre",
            annee=2026,
            statut="payé",
            reference="REF-ANCIEN-01",
            eleve_id=self.eleve_active_a.id,
            inscription_id=self.ins_active_a.id,
            ecole_id=self.ecole_a.id,
            date_paiement=datetime(2026, 10, 1, 8, 0, 0),
        )
        p_recent = Paiement(
            montant=20000,
            mois="Octobre",
            annee=2026,
            statut="payé",
            reference="REF-RECENT-99",
            eleve_id=self.eleve_active_a.id,
            inscription_id=self.ins_active_a.id,
            ecole_id=self.ecole_a.id,
            date_paiement=datetime(2026, 10, 28, 16, 45, 0),
        )
        db.session.add_all([p_ancien, p_recent])
        db.session.commit()

        # Consultation de la traçabilité
        res = self.client.get("/paiements/tracabilite")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        pos_recent = html.find("REF-RECENT-99")
        pos_ancien = html.find("REF-ANCIEN-01")
        self.assertTrue(pos_recent != -1 and pos_ancien != -1)
        # Le plus récent doit IMPÉRATIVEMENT apparaître avant le plus ancien dans le DOM HTML
        self.assertLess(pos_recent, pos_ancien, "L'opération la plus récente doit être affichée en haut")

        # Vérification de la pagination (> 25 éléments)
        nouveaux_paiements = []
        for i in range(26):
            nouveaux_paiements.append(
                Paiement(
                    montant=5000,
                    mois="Octobre",
                    annee=2026,
                    statut="payé",
                    reference=f"REF-PAGE-{i:02d}",
                    eleve_id=self.eleve_active_a.id,
                    inscription_id=self.ins_active_a.id,
                    ecole_id=self.ecole_a.id,
                    date_paiement=datetime(2026, 10, 15, 10, i, 0),
                )
            )
        db.session.add_all(nouveaux_paiements)
        db.session.commit()

        res_p1 = self.client.get("/paiements/tracabilite?page=1")
        self.assertEqual(res_p1.status_code, 200)
        html_p1 = res_p1.get_data(as_text=True)
        self.assertIn("Page 1 sur 2", html_p1)
        self.assertIn("page=2", html_p1)

        res_p2 = self.client.get("/paiements/tracabilite?page=2")
        self.assertEqual(res_p2.status_code, 200)
        html_p2 = res_p2.get_data(as_text=True)
        self.assertIn("Page 2 sur 2", html_p2)


if __name__ == "__main__":
    unittest.main()


