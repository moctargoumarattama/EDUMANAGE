"""Régressions sur les verrous, les moyennes et les transferts de classe."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.models import (
    AnneeScolaire, Bulletin, Classe, Cours, Ecole, Eleve, Inscription,
    Note, PeriodeBulletin, Utilisateur,
)
from app.services.evaluations import (
    calculer_moyenne_matiere, calculer_completude_inscription,
    calculer_completude_annuelle_inscription,
)
from app.services.bulletins_annuels import calculer_bulletin_data, generer_ou_recuperer_bulletin
from app.services.inscriptions_annuelles import modifier_inscription_annuelle
from app.services.notes_annuelles import get_palmares_notes_annuel, modifier_note, saisir_notes_classe, supprimer_note
from app.services.passage_annee import get_moyennes_annuelles_eleves


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool, "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-integrite-pedagogique"


@pytest.fixture
def contexte():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        ecole = Ecole(nom="École test", adresse="Niamey", telephone="12345678", email="pedagogie@test.ne",
                      statut="active", onboarding_complete=True)
        db.session.add(ecole)
        db.session.flush()
        annee = AnneeScolaire(
            ecole_id=ecole.id, nom="2026-2027", date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30), statut="active",
        )
        db.session.add(annee)
        db.session.flush()
        classe_a = Classe(ecole_id=ecole.id, annee_scolaire_id=annee.id, nom="A", statut="ouverte")
        classe_b = Classe(ecole_id=ecole.id, annee_scolaire_id=annee.id, nom="B", statut="ouverte")
        eleve = Eleve(ecole_id=ecole.id, nom="Diallo", prenom="Awa", date_naissance=date(2015, 1, 1), genre="F")
        admin = Utilisateur(ecole_id=ecole.id, nom="Admin", prenom="Test", email="admin-ped@test.ne",
                            mot_de_passe="test", role="admin", statut="actif")
        db.session.add_all([classe_a, classe_b, eleve, admin])
        db.session.flush()
        inscription = Inscription(ecole_id=ecole.id, eleve_id=eleve.id,
                                  annee_scolaire_id=annee.id, classe_id=classe_a.id, statut="inscrit")
        maths_a = Cours(ecole_id=ecole.id, classe_id=classe_a.id, nom="Mathématiques", coefficient=3)
        maths_b = Cours(ecole_id=ecole.id, classe_id=classe_b.id, nom="Mathématiques", coefficient=3)
        francais_a = Cours(ecole_id=ecole.id, classe_id=classe_a.id, nom="Français", coefficient=1)
        db.session.add_all([inscription, maths_a, maths_b, francais_a])
        db.session.commit()
        ctx = SimpleNamespace(app=app, ecole=ecole, annee=annee, classe_a=classe_a,
                              classe_b=classe_b, eleve=eleve, admin=admin, inscription=inscription,
                              maths_a=maths_a, maths_b=maths_b, francais_a=francais_a)
        yield ctx
        db.session.remove()
        db.drop_all()


def note(ctx, cours, valeur, periode, type_evaluation="Devoir", coefficient=1):
    n = Note(ecole_id=ctx.ecole.id, annee_id=ctx.annee.id, eleve_id=ctx.eleve.id,
             inscription_id=ctx.inscription.id, cours_id=cours.id, valeur=valeur,
             coefficient=coefficient, periode=periode, type_evaluation=type_evaluation,
             date_evaluation=datetime(2026, 10, 1))
    db.session.add(n)
    db.session.commit()
    return n


def login(ctx, client):
    with client.session_transaction() as session:
        session["_user_id"] = str(ctx.admin.id)
        session["user_id"] = ctx.admin.id
        session["role"] = "admin"
        session["ecole_id"] = ctx.ecole.id


def sync_payload(ctx, op_id, **changes):
    payload = dict(type="note", client_op_id=op_id, user_id=ctx.admin.id,
                   ecole_id=ctx.ecole.id, eleve_id=ctx.eleve.id,
                   cours_id=ctx.maths_a.id, valeur=12,
                   coefficient=1, periode="Semestre 1", type_evaluation="Devoir",
                   date_evaluation="2026-10-01")
    payload.update(changes)
    return payload


def test_saisie_lot_rejette_bulletin_individuel_verrouille_atomiquement(contexte):
    c = contexte
    n = note(c, c.maths_a, 8, "Semestre 1", "Composition")
    db.session.add(Bulletin(ecole_id=c.ecole.id, annee_scolaire_id=c.annee.id,
                            inscription_id=c.inscription.id, eleve_id=c.eleve.id,
                            classe_id=c.classe_a.id, periode="Semestre 1", statut="archive"))
    db.session.commit()
    count, error = saisir_notes_classe(c.ecole.id, c.annee, c.admin, c.classe_a.id,
                                       c.maths_a.id, {c.eleve.id: 18},
                                       periode="Semestre 1", type_evaluation="Composition")
    assert count == 0 and "Diallo" in error and "clôturé" in error
    db.session.refresh(n)
    assert n.valeur == 8
    deleted, delete_error = supprimer_note(c.ecole.id, c.annee, c.admin, n.id)
    assert not deleted and "clôturé" in delete_error
    assert db.session.get(Note, n.id) is not None
    changed, change_error = modifier_note(c.ecole.id, c.annee, c.admin, n.id, 18,
                                          periode="Semestre 2")
    assert changed is None and "clôturé" in change_error


def test_sync_periode_publiee_signale_conflit_sans_changement(contexte):
    c = contexte
    n = note(c, c.maths_a, 12, "Semestre 1")
    db.session.add(PeriodeBulletin(ecole_id=c.ecole.id, annee_id=c.annee.id,
                                   nom="Semestre 1", publie=True))
    db.session.commit()
    client = c.app.test_client()
    login(c, client)
    response = client.post("/api/sync", json=[sync_payload(c, "op-locked", note_id=n.id,
                                                            valeur=18, base_version=1, force=True)])
    assert "results" in response.get_json(), response.get_json()
    assert response.get_json()["results"][0]["status"] == "conflict"
    db.session.refresh(n)
    assert n.valeur == 12 and n.sync_version == 1

    sans_periode = client.post("/api/sync", json=[sync_payload(c, "op-no-period", periode="",
                                                              date_evaluation="2026-11-01")])
    assert sans_periode.get_json()["results"][0]["status"] == "conflict"
    assert Note.query.filter_by(eleve_id=c.eleve.id).count() == 1


def test_sync_coefficient_seul_est_applique(contexte):
    c = contexte
    n = note(c, c.maths_a, 12, "Semestre 1")
    client = c.app.test_client()
    login(c, client)
    result = client.post("/api/sync", json=[sync_payload(c, "op-coef", note_id=n.id,
                                                       coefficient=3, base_version=1)])
    assert "results" in result.get_json(), result.get_json()
    assert result.get_json()["results"][0]["status"] == "synced"
    db.session.refresh(n)
    assert n.valeur == 12 and n.coefficient == 3 and n.sync_version == 2


@pytest.mark.parametrize("type_examen", ["Examen", "examen", "Composition", "composition"])
def test_examen_est_traite_comme_composition(contexte, type_examen):
    c = contexte
    controle = note(c, c.maths_a, 2, "Semestre 1")
    examen = note(c, c.maths_a, 20, "Semestre 1", type_examen)
    assert calculer_moyenne_matiere([controle, examen]) == 11


def test_composition_saisie_en_lot_reutilise_un_ancien_examen(contexte):
    c = contexte
    examen = note(c, c.maths_a, 9, "Semestre 1", "Examen")
    count, error = saisir_notes_classe(c.ecole.id, c.annee, c.admin, c.classe_a.id,
                                       c.maths_a.id, {c.eleve.id: 14},
                                       periode="Semestre 1", type_evaluation="Composition")
    assert (count, error) == (1, None)
    db.session.refresh(examen)
    assert examen.valeur == 14 and examen.type_evaluation == "Composition"
    assert Note.query.filter_by(inscription_id=c.inscription.id, cours_id=c.maths_a.id).count() == 1


def test_passage_sans_bulletin_utilise_coefficients_des_matieres(contexte):
    c = contexte
    for periode in ("Semestre 1", "Semestre 2"):
        note(c, c.maths_a, 6, periode)
        note(c, c.francais_a, 16, periode)
    moyennes = get_moyennes_annuelles_eleves(c.ecole.id, c.annee.id)
    assert moyennes[c.eleve.id] == 8.5
    assert moyennes[c.eleve.id] < 10


def test_passage_sans_deux_semestres_ne_suggere_pas_de_decision(contexte):
    c = contexte
    note(c, c.maths_a, 18, "Semestre 1")
    assert c.eleve.id not in get_moyennes_annuelles_eleves(c.ecole.id, c.annee.id)


def test_note_sans_periode_ne_complete_aucun_semestre_annuel(contexte):
    c = contexte
    ancienne_note = note(c, c.maths_a, 15, "Semestre 1")
    ancienne_note.periode = None
    db.session.commit()

    completude = calculer_completude_annuelle_inscription(
        c.ecole.id, c.annee.id, c.inscription, ["Semestre 1", "Semestre 2"]
    )
    assert not completude["is_pedagogically_complete"]
    assert completude["missing_subjects_by_period"]["Semestre 1"] == [
        "Français", "Mathématiques"
    ]
    assert completude["missing_subjects_by_period"]["Semestre 2"] == [
        "Français", "Mathématiques"
    ]


def test_transfert_conserve_classe_close_et_reaffecte_periode_ouverte(contexte):
    c = contexte
    ancienne = note(c, c.maths_a, 5, "Semestre 1")
    courante = note(c, c.maths_a, 15, "Semestre 2")
    c.inscription.cours_id = c.maths_a.id
    db.session.add(PeriodeBulletin(ecole_id=c.ecole.id, annee_id=c.annee.id,
                                   nom="Semestre 1", publie=True))
    db.session.commit()
    inscription, error = modifier_inscription_annuelle(
        c.ecole.id, c.eleve.id, c.annee.id, c.classe_b.id)
    assert error is None and inscription.classe_id == c.classe_b.id
    assert inscription.cours_id == c.maths_b.id
    db.session.commit()
    db.session.refresh(ancienne)
    db.session.refresh(courante)
    assert ancienne.cours_id == c.maths_a.id
    assert courante.cours_id == c.maths_b.id
    assert calculer_completude_inscription(c.ecole.id, c.annee.id, inscription,
                                           periode="Semestre 1")["average"] == 5
    assert calculer_completude_inscription(c.ecole.id, c.annee.id, inscription,
                                           periode="Semestre 2")["average"] == 15
    bulletin_data, error = calculer_bulletin_data(c.ecole.id, c.annee, inscription, "Semestre 1")
    assert error is None and bulletin_data["classe"].id == c.classe_a.id
    bulletin, error = generer_ou_recuperer_bulletin(c.ecole.id, c.annee, c.admin,
                                                     inscription.id, "Semestre 1")
    assert error is None and bulletin.classe_id == c.classe_a.id
    palmares = get_palmares_notes_annuel(c.ecole.id, c.annee)
    by_class = {row["id"]: row for row in palmares["classes_meilleures"]}
    assert by_class[c.classe_a.id]["nb_notes"] == 1
    assert by_class[c.classe_b.id]["nb_notes"] == 1
