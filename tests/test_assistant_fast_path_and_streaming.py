import json
import datetime as dt
import pytest
from app import create_app, db
from app.models import Utilisateur, Ecole, AnneeScolaire, Classe, Eleve, Inscription, Absence, Note, Cours


@pytest.fixture
def app_and_client():
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        db.create_all()

        # Création d'une école et d'un utilisateur admin
        ecole = Ecole(nom="Groupe Scolaire Excellence", adresse="Rue 1", telephone="0102030405", email="contact@gse.test")
        db.session.add(ecole)
        db.session.flush()

        annee = AnneeScolaire(nom="2025-2026", date_debut=dt.date(2025, 9, 1), date_fin=dt.date(2026, 6, 30), statut="active", ecole_id=ecole.id)
        db.session.add(annee)

        admin = Utilisateur(
            nom="Directeur",
            prenom="Test",
            email="directeur@test.com",
            mot_de_passe="Secret123!",
            role="directeur",
            statut="actif",
            ecole_id=ecole.id
        )
        db.session.add(admin)

        prof = Utilisateur(
            nom="Diallo",
            prenom="Amadou",
            email="diallo@test.com",
            mot_de_passe="Secret123!",
            role="professeur",
            statut="actif",
            ecole_id=ecole.id
        )
        db.session.add(prof)
        db.session.flush()

        classe = Classe(nom="6ème A", niveau="6e", salle="Salle 101", ecole_id=ecole.id, annee_scolaire_id=annee.id)
        db.session.add(classe)
        db.session.flush()

        cours = Cours(nom="Mathématiques", professeur_id=prof.id, classe_id=classe.id, ecole_id=ecole.id)
        db.session.add(cours)
        db.session.flush()

        # Création d'un élève test
        eleve1 = Eleve(
            nom="Toure",
            prenom="Mamadou",
            genre="M",
            date_naissance=dt.date(2012, 5, 14),
            code_parent="ELV-001",
            contact_parent="0701020304",
            frais_annuels=150000,
            ecole_id=ecole.id
        )
        db.session.add(eleve1)
        db.session.flush()

        insc1 = Inscription(
            eleve_id=eleve1.id,
            classe_id=classe.id,
            annee_scolaire_id=annee.id,
            ecole_id=ecole.id,
            statut="inscrit"
        )
        db.session.add(insc1)
        db.session.flush()

        # Note et absence pour tester les statistiques et fast-paths
        note1 = Note(
            valeur=17.5,
            coefficient=2.0,
            periode="Trimestre 1",
            eleve_id=eleve1.id,
            cours_id=cours.id,
            annee_id=annee.id,
            ecole_id=ecole.id,
            inscription_id=insc1.id
        )
        db.session.add(note1)

        abs1 = Absence(
            date_absence=dt.date(2025, 10, 10),
            justifiee=False,
            motif="Non justifié",
            eleve_id=eleve1.id,
            ecole_id=ecole.id,
            inscription_id=insc1.id
        )
        db.session.add(abs1)

        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin.id)
            sess['user_id'] = str(admin.id)
            sess['_fresh'] = True

        yield app, client

        db.session.remove()
        db.drop_all()


def test_greeting_tolerance_bonjours(app_and_client):
    """Vérifie que 'Bonjours' avec une faute / pluriel est reconnu comme une salutation chaleureuse et ne cherche pas un élève."""
    app, client = app_and_client

    for query in ["Bonjours", "bonjours !", "bjr", "Salut", "salam", "hello"]:
        res = client.post('/api/assistant/query-data', json={'question': query})
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert data['intention'] == 'salutation'
        assert "Bonjour" in data['reply']
        assert "élève" not in data['reply'].lower() or "exemple" in data['reply'].lower()


def test_chitchat_ca_va_and_role(app_and_client):
    """Vérifie 'comment ça va' et 'qui es-tu'."""
    app, client = app_and_client

    res = client.post('/api/assistant/query-data', json={'question': "Comment tu vas ?"})
    assert res.status_code == 200
    data = res.get_json()
    assert "merveille" in data['reply']

    res2 = client.post('/api/assistant/query-data', json={'question': "Qui es-tu ?"})
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert "Assistant IA de Direction KLASORA" in data2['reply']


def test_fast_path_statistiques_globales(app_and_client):
    """Vérifie le fast-path des statistiques globales de l'école (<50ms)."""
    app, client = app_and_client

    res = client.post('/api/assistant/query-data', json={'question': "Effectif total de l'école"})
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['intention'] == 'statistiques_globales'
    assert "Tableau de Bord & Statistiques" in data['reply']
    assert "Effectif total" in data['reply']


def test_fast_path_effectif_classe(app_and_client):
    """Vérifie le fast-path d'effectif d'une classe spécifique."""
    app, client = app_and_client

    res = client.post('/api/assistant/query-data', json={'question': "Effectif de la 6ème A"})
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['intention'] == 'effectif_classe'
    assert "Effectif de la classe" in data['reply']
    assert "Mamadou" in data['reply']


def test_fast_path_absences_critiques(app_and_client):
    """Vérifie le fast-path pour trouver qui a le plus d'absences."""
    app, client = app_and_client

    res = client.post('/api/assistant/query-data', json={'question': "Qui a le plus d'absences ?"})
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['intention'] == 'absences_critiques'
    assert "Élèves présentant le plus d'absences" in data['reply']
    assert "Toure" in data['reply']


def test_fast_path_meilleurs_eleves(app_and_client):
    """Vérifie le fast-path pour le tableau d'honneur des meilleurs élèves."""
    app, client = app_and_client

    res = client.post('/api/assistant/query-data', json={'question': "Quels sont les meilleurs élèves ?"})
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['intention'] == 'meilleurs_eleves'
    assert "Tableau d'Honneur" in data['reply']
    assert "17.5" in data['reply']


def test_fast_path_professeurs(app_and_client):
    """Vérifie le fast-path de la liste des professeurs."""
    app, client = app_and_client

    res = client.post('/api/assistant/query-data', json={'question': "Liste des professeurs"})
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['intention'] == 'professeurs_cours'
    assert "Corps Enseignant" in data['reply']
    assert "Amadou" in data['reply']


def test_sse_streaming_endpoint(app_and_client):
    """Vérifie l'endpoint de streaming SSE /api/assistant/stream."""
    app, client = app_and_client

    res = client.post('/api/assistant/stream', json={'question': "Bonjours"})
    assert res.status_code == 200
    assert "text/event-stream" in res.content_type
    body = res.get_data(as_text=True)
    assert "data: " in body
    assert "Bonjour" in body
