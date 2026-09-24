import pytest
from datetime import date, time, datetime
from app import create_app, db
from app.models import Classe, AnneeScolaire, Cours, Professeur, EmploiTemps, Ecole, NiveauScolaire, Utilisateur
from app.services.emploi_temps_annuel import donnees_impression_classe
from sqlalchemy.pool import StaticPool
from flask import render_template
from werkzeug.security import generate_password_hash


class EmploiTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-emploi-grille"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


@pytest.fixture
def app_instance():
    app = create_app(EmploiTestConfig)
    with app.app_context():
        db.create_all()
        # Création de l'école
        ecole = Ecole(
            nom="Institut KLASORA Excellence",
            adresse="Boulevard de l'Éducation, Niamey",
            telephone="+227 90 00 00 00",
            email="contact@klasora.edu",
            statut="actif",
            onboarding_complete=True
        )
        db.session.add(ecole)
        db.session.flush()
        # On définit la devise après le flush pour qu'ecole.id soit renseigné
        ecole.devise = "Discipline - Travail - Réussite"

        annee = AnneeScolaire(
            ecole_id=ecole.id,
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active"
        )
        db.session.add(annee)
        db.session.flush()

        niveau = NiveauScolaire(nom="Terminale", code="TLE", ordre=7, cycle="secondaire")
        db.session.add(niveau)
        db.session.flush()

        classe = Classe(
            nom="Terminale S2",
            niveau="Terminale",
            niveau_id=niveau.id,
            ecole_id=ecole.id,
            annee_scolaire_id=annee.id,
            statut="ouverte"
        )
        db.session.add(classe)
        db.session.flush()

        c1 = Cours(nom="Mathématiques", classe_id=classe.id, ecole_id=ecole.id, coefficient=4.0)
        c2 = Cours(nom="Sciences Physiques", classe_id=classe.id, ecole_id=ecole.id, coefficient=3.0)
        db.session.add_all([c1, c2])
        db.session.flush()

        u_prof1 = Utilisateur(
            nom="Diallo",
            prenom="Amadou",
            email="amadou.diallo@test.com",
            mot_de_passe=generate_password_hash("pass123"),
            role="professeur",
            statut="actif",
            ecole_id=ecole.id
        )
        u_prof2 = Utilisateur(
            nom="Traore",
            prenom="Fatoumata",
            email="fatou.traore@test.com",
            mot_de_passe=generate_password_hash("pass123"),
            role="professeur",
            statut="actif",
            ecole_id=ecole.id
        )
        db.session.add_all([u_prof1, u_prof2])
        db.session.flush()

        prof1 = Professeur(
            nom="Diallo",
            prenom="Amadou",
            email="amadou.diallo@test.com",
            ecole_id=ecole.id,
            utilisateur_id=u_prof1.id
        )
        prof2 = Professeur(
            nom="Traore",
            prenom="Fatoumata",
            email="fatou.traore@test.com",
            ecole_id=ecole.id,
            utilisateur_id=u_prof2.id
        )
        db.session.add_all([prof1, prof2])
        db.session.flush()

        e1 = EmploiTemps(
            classe_id=classe.id,
            cours_id=c1.id,
            professeur_id=prof1.id,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(10, 0),
            salle="Labo 1",
            ecole_id=ecole.id
        )
        e2 = EmploiTemps(
            classe_id=classe.id,
            cours_id=c2.id,
            professeur_id=prof2.id,
            jour="Mardi",
            heure_debut=time(10, 15),
            heure_fin=time(12, 15),
            salle="Salle 04",
            ecole_id=ecole.id
        )
        db.session.add_all([e1, e2])
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


def test_donnees_impression_classe_matrice_et_heures(app_instance):
    with app_instance.app_context():
        ecole = Ecole.query.first()
        annee = AnneeScolaire.query.first()
        classe = Classe.query.filter_by(nom="Terminale S2").first()
        data, err = donnees_impression_classe(ecole.id, annee, classe.id)

        assert err is None
        assert data is not None

        # 1. Vérification de l'école
        assert data["ecole"] is not None
        assert "KLASORA" in (data["ecole_nom"] or "")
        assert data["ecole_devise"] == "Discipline - Travail - Réussite"

        # 2. Vérification des heures sans secondes dans la grille
        assert len(data["grille"]) == 2
        assert data["grille"][0]["heure_debut"] == "08:00"
        assert data["grille"][0]["heure_fin"] == "10:00"
        assert data["grille"][1]["heure_debut"] == "10:15"
        assert data["grille"][1]["heure_fin"] == "12:15"

        # 3. Vérification de la grille hebdomadaire
        cours_lundi = data["grille"][0]["jours"]["Lundi"]
        assert len(cours_lundi) == 1
        assert cours_lundi[0]["cours_nom"] == "Mathématiques"
        assert cours_lundi[0]["professeur_nom"] == "Amadou Diallo"
        assert cours_lundi[0]["salle"] == "Labo 1"

        # 4. Vérification récap matières et totaux
        assert len(data["recap_matieres"]) == 2
        assert "4h" in data["total_heures_hebdo_str"]

        # 5. Rendu du template d'impression
        with app_instance.test_request_context():
            html_rendu = render_template("imprimer_emploi_classe.html", donnees=data, annee_consultee=annee, now=datetime.now())
            # Pas de secondes au format 08:00:00 dans l'affichage
            assert "08:00:00" not in html_rendu
            assert "08:00" in html_rendu
            assert "Discipline - Travail - Réussite" in html_rendu
            assert "Mathématiques" in html_rendu
            assert "Planning de la Semaine" in html_rendu
            assert "Amadou Diallo" in html_rendu
            assert "Labo 1" in html_rendu

