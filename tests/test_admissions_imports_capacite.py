import unittest
from datetime import date

from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription
from app.services.import_eleves_service import executer_import_excel
from app.services.inscriptions_annuelles import CapaciteClasseDepasseeError, creer_inscription_annuelle


class _Config:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    SECRET_KEY = "admissions-imports-tests"


class AdmissionsImportsCapaciteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_Config)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.ecole = Ecole(nom="Ecole admissions", statut="actif", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.flush()
        self.annee = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30),
            statut="active", ecole_id=self.ecole.id,
        )
        self.classe = Classe(
            nom="Tle D1", niveau="Tle", capacite_max=10,
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id,
        )
        db.session.add_all([self.annee, self.classe])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _eleve(self, prenom):
        eleve = Eleve(nom="DIALLO", prenom=prenom, date_naissance=date(2010, 1, 1), ecole_id=self.ecole.id)
        db.session.add(eleve)
        db.session.commit()
        return eleve

    def _ligne(self, prenom, matricule, acte, telephone="90123456"):
        return {
            "status": "valid", "nom": "DIALLO", "prenom": prenom,
            "date_naissance": "2010-01-01", "genre": "M", "classe_id": self.classe.id,
            "telephone_parent": telephone, "nom_parent": "Parent Diallo",
            "email_parent": None, "numero_acte": acte, "matricule": matricule,
            "nationalite": "Nigérienne", "statut": "actif",
        }

    def test_import_avec_telephone_cree_et_lie_parent(self):
        ok, message, crees, reinscrits, ignores = executer_import_excel(
            [self._ligne("Aminata", "25-0001", "ACT-001")], self.ecole.id, self.annee
        )
        self.assertTrue(ok, message)
        self.assertEqual((crees, reinscrits, ignores), (1, 0, 0))
        eleve = Eleve.query.one()
        self.assertIsNotNone(eleve.parent_id)
        self.assertEqual(eleve.contact_parent, "90123456")
        self.assertEqual(eleve.parent.role, "parent")

    def test_homonymes_identifies_par_matricule_sont_deux_dossiers(self):
        lignes = [
            self._ligne("Aminata", "25-0001", "ACT-001"),
            self._ligne("Aminata", "25-0002", "ACT-002", telephone="90123457"),
        ]
        ok, message, crees, _, ignores = executer_import_excel(lignes, self.ecole.id, self.annee)
        self.assertTrue(ok, message)
        self.assertEqual((crees, ignores), (2, 0))
        self.assertEqual(Eleve.query.count(), 2)
        self.assertEqual({e.matricule for e in Eleve.query.all()}, {"25-0001", "25-0002"})

    def test_capacite_bloque_inscription(self):
        self.classe.capacite_max = 1
        db.session.flush()
        premier = self._eleve("Premier")
        inscription, erreur = creer_inscription_annuelle(
            self.ecole.id, premier.id, self.annee.id, self.classe.id
        )
        self.assertIsNone(erreur)
        second = self._eleve("Second")
        with self.assertRaises(CapaciteClasseDepasseeError):
            creer_inscription_annuelle(self.ecole.id, second.id, self.annee.id, self.classe.id)

    def test_capacite_forcee_explicitement(self):
        self.test_capacite_bloque_inscription()
        second = Eleve.query.filter_by(prenom="Second").first()
        inscription, erreur = creer_inscription_annuelle(
            self.ecole.id, second.id, self.annee.id, self.classe.id,
            forcer_surcapacite=True,
        )
        self.assertIsNone(erreur)
        self.assertIsNotNone(inscription)
        self.assertEqual(Inscription.query.count(), 2)

    def test_import_sans_contact_est_signale_sans_compte_parent(self):
        ligne = self._ligne("SansContact", "25-0003", "ACT-003", telephone="")
        ok, message, *_ = executer_import_excel([ligne], self.ecole.id, self.annee)
        self.assertTrue(ok, message)
        eleve = Eleve.query.one()
        self.assertIsNone(eleve.parent_id)
        self.assertIsNone(eleve.contact_parent)


if __name__ == "__main__":
    unittest.main()
