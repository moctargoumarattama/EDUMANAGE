from datetime import date

from app import create_app, db
from app.models import AnneeScolaire, Classe, Cours, Ecole
from app.services.cours_uniqueness import find_duplicate_cours, normalize_cours_nom


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-cours-duplicates"
    WTF_CSRF_ENABLED = False


def _annee(ecole_id, nom):
    annee = AnneeScolaire(
        nom=nom,
        date_debut=date(2026, 9, 1),
        date_fin=date(2027, 6, 30),
        statut="active",
        ecole_id=ecole_id,
    )
    db.session.add(annee)
    db.session.flush()
    return annee


def _classe(ecole_id, annee_id, nom):
    classe = Classe(nom=nom, niveau="6e", section="A", ecole_id=ecole_id, annee_scolaire_id=annee_id)
    db.session.add(classe)
    db.session.flush()
    return classe


def test_cours_duplicate_key_is_school_class_and_normalized_name():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        ecole_a = Ecole(nom="Ecole A")
        ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([ecole_a, ecole_b])
        db.session.flush()

        annee_a = _annee(ecole_a.id, "2026-2027")
        autre_annee_a = _annee(ecole_a.id, "2027-2028")
        annee_b = _annee(ecole_b.id, "2026-2027")
        classe_a = _classe(ecole_a.id, annee_a.id, "6e A")
        autre_classe_a = _classe(ecole_a.id, annee_a.id, "6e B")
        classe_autre_annee = _classe(ecole_a.id, autre_annee_a.id, "6e A")
        classe_b = _classe(ecole_b.id, annee_b.id, "6e A")

        math = Cours(nom="Mathematiques", coefficient=1, ecole_id=ecole_a.id, classe_id=classe_a.id)
        db.session.add(math)
        db.session.commit()

        assert normalize_cours_nom("  Mathematiques   ") == "Mathematiques"
        assert find_duplicate_cours(ecole_a.id, classe_a.id, " mathematiques  ").id == math.id
        assert find_duplicate_cours(ecole_a.id, classe_a.id, "Mathematiques", exclude_id=math.id) is None
        assert find_duplicate_cours(ecole_a.id, autre_classe_a.id, "Mathematiques") is None
        assert find_duplicate_cours(ecole_a.id, classe_autre_annee.id, "Mathematiques") is None
        assert find_duplicate_cours(ecole_b.id, classe_b.id, "Mathematiques") is None
        assert find_duplicate_cours(ecole_a.id, classe_a.id, "Francais") is None

        db.session.remove()
        db.drop_all()
