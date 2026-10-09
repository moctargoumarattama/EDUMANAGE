from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import AnneeScolaire, Classe, Cours, DispenseMatiere, Ecole, Eleve, Inscription, NiveauScolaire, Note, PeriodeBulletin, Utilisateur
from app.services.bulletins_annuels import calculer_bulletin_data
from app.services.classes_annuelles import _classe_identity
from app.services.dispenses import accorder_dispense, revoquer_dispense
from app.services.evaluations import calculer_completude_inscription
from app.services.niveaux import creer_classe_depuis_niveau, modifier_classe_depuis_niveau
from app.services.niveaux_annuels import sauvegarder_selection_annuelle
from app.services.passage_annee import get_deliberations_annuelles_eleves
from app.services.pedagogie_standard import generer_classes_batch, injecter_matieres_standard, obtenir_matieres_standard
from app.services.notes_annuelles import creer_note, saisir_notes_classe


class Config:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {'poolclass': StaticPool, 'connect_args': {'check_same_thread': False}}
    SECRET_KEY = 'test-dispenses-divisions'
    SERVER_NAME = None


@pytest.fixture
def contexte():
    app = create_app(Config)
    with app.app_context():
        assert str(db.engine.url) == 'sqlite:///:memory:'
        db.create_all()
        ecole = Ecole(nom='École test', statut='actif', onboarding_complete=True)
        db.session.add(ecole)
        db.session.flush()
        annee = AnneeScolaire(ecole_id=ecole.id, nom='2025-2026',
                              date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30), statut='active')
        niveau = NiveauScolaire(nom='Terminale', code='TERMINALE', ordre=13, cycle='lycee')
        admin = Utilisateur(nom='Admin', prenom='Test', email='admin-dispense@example.ne',
                            mot_de_passe=generate_password_hash('secret'), role='admin',
                            ecole_id=ecole.id, statut='actif')
        db.session.add_all([annee, niveau, admin])
        db.session.flush()
        sauvegarder_selection_annuelle(ecole.id, annee.id, [niveau.id])
        for nom in ('Semestre 1', 'Semestre 2'):
            db.session.add(PeriodeBulletin(ecole_id=ecole.id, annee_id=annee.id, nom=nom, publie=False))
        db.session.commit()
        yield SimpleNamespace(app=app, client=app.test_client(), ecole=ecole, annee=annee,
                              niveau=niveau, admin=admin)
        db.session.remove()
        db.drop_all()


def _creer_eleve_avec_cours(ctx):
    classe, erreur = creer_classe_depuis_niveau(ctx.ecole.id, ctx.annee.id, ctx.niveau.id, section='D1')
    assert erreur is None
    eleve = Eleve(ecole_id=ctx.ecole.id, nom='Issa', prenom='Amina',
                  date_naissance=date(2008, 1, 2), genre='F')
    db.session.add(eleve)
    db.session.flush()
    inscription = Inscription(ecole_id=ctx.ecole.id, annee_scolaire_id=ctx.annee.id,
                              eleve_id=eleve.id, classe_id=classe.id, statut='inscrit')
    math = Cours(ecole_id=ctx.ecole.id, classe_id=classe.id, nom='Mathématiques', coefficient=2)
    eps = Cours(ecole_id=ctx.ecole.id, classe_id=classe.id, nom='EPS', coefficient=1)
    db.session.add_all([inscription, math, eps])
    db.session.commit()
    return classe, eleve, inscription, math, eps


def test_series_divisions_d1_d2_et_catalogue(contexte):
    ctx = contexte
    d1, erreur = creer_classe_depuis_niveau(ctx.ecole.id, ctx.annee.id, ctx.niveau.id, section='D1')
    assert erreur is None
    d2, erreur = creer_classe_depuis_niveau(ctx.ecole.id, ctx.annee.id, ctx.niveau.id,
                                             section='D', division='2')
    assert erreur is None
    assert (d1.section, d1.division, d1.nom) == ('D', '1', 'Terminale D1')
    assert (d2.section, d2.division, d2.nom) == ('D', '2', 'Terminale D2')
    assert _classe_identity(d1) != _classe_identity(d2)
    assert d1.to_dict()['serie'] == 'D'
    assert d1.to_dict()['division'] == '1'
    _, erreur = creer_classe_depuis_niveau(ctx.ecole.id, ctx.annee.id, ctx.niveau.id, section='D1')
    assert 'existe' in erreur
    creees, existantes, erreur = generer_classes_batch(ctx.ecole.id, ctx.annee.id, [
        {'niveau_id': ctx.niveau.id, 'section': 'D1'},
        {'niveau_id': ctx.niveau.id, 'section': 'D2'},
    ])
    assert erreur is None and not creees and len(existantes) == 2
    a1, erreur = creer_classe_depuis_niveau(ctx.ecole.id, ctx.annee.id, ctx.niveau.id, section='A1')
    assert erreur is None
    assert (a1.section, a1.division) == ('A1', '')
    tle_d1, erreur = creer_classe_depuis_niveau(
        ctx.ecole.id, ctx.annee.id, ctx.niveau.id,
        section='D', division='3', nom='Tle D3',
    )
    assert erreur is None and tle_d1.nom == 'Tle D3'
    _, erreur = creer_classe_depuis_niveau(
        ctx.ecole.id, ctx.annee.id, ctx.niveau.id,
        section='D', division='4', nom='Tle D2',
    )
    assert 'terminer par D4' in erreur
    d1, erreur = modifier_classe_depuis_niveau(
        d1, ctx.ecole.id, ctx.niveau.id, nom=d1.nom,
        section='D', division='5', capacite=35,
    )
    assert erreur is None and d1.nom == 'Terminale D5'
    ajoutes, erreur = injecter_matieres_standard(ctx.ecole.id, ctx.annee.id, d1.id)
    assert erreur is None
    assert {c.nom for c in ajoutes} == {nom for nom, _ in obtenir_matieres_standard('TERMINALE', 'lycee', 'D')}


def test_dispense_eps_ne_bloque_ni_bulletin_ni_deliberation(contexte):
    ctx = contexte
    classe, eleve, ins, math, eps = _creer_eleve_avec_cours(ctx)
    for periode, valeur in (('Semestre 1', 14.0), ('Semestre 2', 16.0)):
        db.session.add(Note(ecole_id=ctx.ecole.id, eleve_id=eleve.id, inscription_id=ins.id,
                            cours_id=math.id, annee_id=ctx.annee.id, periode=periode,
                            type_evaluation='Composition', valeur=valeur))
    db.session.commit()

    avant = calculer_completude_inscription(ctx.ecole.id, ctx.annee.id, ins, 'Semestre 1')
    assert avant['missing_subjects_names'] == ['EPS']
    dispense, erreur = accorder_dispense(ctx.ecole.id, ins.id, eps.id, '*',
                                         'Certificat médical CM-42', ctx.admin.id)
    assert erreur is None
    assert dispense.active
    apres = calculer_completude_inscription(ctx.ecole.id, ctx.annee.id, ins, 'Semestre 1')
    assert apres['is_pedagogically_complete']
    assert apres['expected_subjects'] == 1
    assert apres['missing_subjects_names'] == []
    assert apres['dispensed_subjects_names'] == ['EPS']
    assert apres['average'] == 14.0

    bulletin, erreur = calculer_bulletin_data(ctx.ecole.id, ctx.annee, ins,
                                               periode='Semestre 1', periode_publiee=True)
    assert erreur is None
    assert bulletin['moyenne_generale'] == 14.0
    assert next(d for d in bulletin['disciplines'] if d['cours_nom'] == 'EPS')['appreciation'] == 'Dispensé'
    decision = get_deliberations_annuelles_eleves(ctx.ecole.id, ctx.annee.id)[eleve.id]
    assert not decision['cursus_incomplet']
    assert decision['moyenne'] == 15.0

    note, erreur = creer_note(ctx.ecole.id, ctx.annee, ctx.admin, eleve.id, eps.id,
                              12, periode='Semestre 1')
    assert note is None and 'dispensé' in erreur
    nb, erreur = saisir_notes_classe(ctx.ecole.id, ctx.annee, ctx.admin, classe.id,
                                     eps.id, {eleve.id: '12'}, periode='Semestre 1')
    assert nb == 0 and 'dispensé' in erreur
    _, erreur = revoquer_dispense(ctx.ecole.id, dispense.id, ctx.admin.id)
    assert erreur is None
    assert not calculer_completude_inscription(ctx.ecole.id, ctx.annee.id, ins,
                                               'Semestre 1')['is_pedagogically_complete']


def test_dispense_refuse_note_existante_et_route_admin(contexte):
    ctx = contexte
    _, eleve, ins, _, eps = _creer_eleve_avec_cours(ctx)
    db.session.add(Note(ecole_id=ctx.ecole.id, eleve_id=eleve.id, inscription_id=ins.id,
                        cours_id=eps.id, annee_id=ctx.annee.id, periode='Semestre 1',
                        type_evaluation='Devoir', valeur=13))
    db.session.commit()
    _, erreur = accorder_dispense(ctx.ecole.id, ins.id, eps.id, 'Semestre 1',
                                  'Certificat médical CM-42', ctx.admin.id)
    assert 'note existe' in erreur

    with ctx.client.session_transaction() as session:
        session['_user_id'] = str(ctx.admin.id)
        session['user_id'] = ctx.admin.id
        session['role'] = 'admin'
        session['ecole_id'] = ctx.ecole.id
    response = ctx.client.get(f'/inscriptions/{ins.id}/dispenses')
    assert response.status_code == 200
    assert 'EPS' in response.get_data(as_text=True)
    response = ctx.client.post(f'/inscriptions/{ins.id}/dispenses', data={
        'cours_id': eps.id, 'periode': 'Semestre 2',
        'reference_justificatif': 'Certificat médical CM-43',
    })
    assert response.status_code == 302
    dispense = DispenseMatiere.query.filter_by(inscription_id=ins.id, cours_id=eps.id).one()
    assert dispense.periode == 'Semestre 2'
    response = ctx.client.post(f'/cours/{eps.id}/import_notes_excel', data={
        'file': (BytesIO(f'eleve id,note\n{eleve.id},12\n'.encode()), 'notes.csv'),
    }, content_type='multipart/form-data')
    assert response.status_code == 302
    assert Note.query.filter_by(cours_id=eps.id, eleve_id=eleve.id).count() == 1
    assert Note.query.filter_by(cours_id=eps.id, eleve_id=eleve.id).one().valeur == 13
    response = ctx.client.post(f'/dispenses/{dispense.id}/annuler')
    assert response.status_code == 302
    assert not db.session.get(DispenseMatiere, dispense.id).active


def test_migration_conserve_les_classes_existantes():
    engine = create_engine('sqlite:///:memory:')
    migration = importlib.import_module('migrations.versions.b7d1e2f3a4c5_dispenses_divisions_lycee')
    with engine.begin() as connexion:
        connexion.exec_driver_sql('CREATE TABLE niveau_scolaire (id INTEGER PRIMARY KEY, cycle VARCHAR(30))')
        connexion.exec_driver_sql("INSERT INTO niveau_scolaire (id, cycle) VALUES (1, 'lycee')")
        connexion.exec_driver_sql('CREATE TABLE classe (id INTEGER PRIMARY KEY, nom VARCHAR(50), section VARCHAR(30), niveau_id INTEGER)')
        connexion.exec_driver_sql("INSERT INTO classe (id, nom, section, niveau_id) VALUES (1, 'Terminale Serie D', 'D', 1)")
        connexion.exec_driver_sql("INSERT INTO classe (id, nom, section, niveau_id) VALUES (2, 'Tle D1', 'D1', 1)")
        connexion.exec_driver_sql("INSERT INTO classe (id, nom, section, niveau_id) VALUES (3, 'Tle D2', 'D', 1)")
        operations = Operations(MigrationContext.configure(connexion))
        with patch.object(migration, 'op', operations):
            migration.upgrade()
        ligne = connexion.exec_driver_sql('SELECT nom, section, division FROM classe WHERE id = 1').one()
        assert tuple(ligne) == ('Terminale Serie D', 'D', '')
        ancienne_d1 = connexion.exec_driver_sql('SELECT nom, section, division FROM classe WHERE id = 2').one()
        assert tuple(ancienne_d1) == ('Tle D1', 'D', '1')
        ancienne_d2 = connexion.exec_driver_sql('SELECT nom, section, division FROM classe WHERE id = 3').one()
        assert tuple(ancienne_d2) == ('Tle D2', 'D', '2')
        assert 'dispense_matiere' in inspect(connexion).get_table_names()
    engine.dispose()


def test_dispense_verrouillee_apres_publication_et_isolee_par_ecole(contexte):
    ctx = contexte
    _, _, ins, _, eps = _creer_eleve_avec_cours(ctx)
    _, erreur = accorder_dispense(ctx.ecole.id + 1, ins.id, eps.id, 'Semestre 1',
                                  'Certificat CM-99', ctx.admin.id)
    assert erreur is not None
    dispense, erreur = accorder_dispense(ctx.ecole.id, ins.id, eps.id, 'Semestre 1',
                                         'Certificat CM-99', ctx.admin.id)
    assert erreur is None
    periode = PeriodeBulletin.query.filter_by(ecole_id=ctx.ecole.id, annee_id=ctx.annee.id,
                                               nom='Semestre 1').one()
    periode.publie = True
    db.session.commit()
    _, erreur = revoquer_dispense(ctx.ecole.id, dispense.id, ctx.admin.id)
    assert erreur is not None
    assert db.session.get(DispenseMatiere, dispense.id).active
    _, erreur = accorder_dispense(ctx.ecole.id, ins.id, eps.id, '*',
                                  'Certificat CM-100', ctx.admin.id)
    assert erreur is not None
