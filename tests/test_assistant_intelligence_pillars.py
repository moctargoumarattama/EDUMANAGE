import json
import datetime as dt
import pytest
from app import create_app, db
from app.models import Utilisateur, Ecole, AnneeScolaire, Classe, Eleve, Inscription, Absence, Note, Cours, Paiement


@pytest.fixture
def intelligence_app_and_client():
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        db.create_all()

        # Établissement
        ecole = Ecole(nom="Collège Moderne de Niamey", adresse="Avenue de l'Indépendance", telephone="90000000", email="contact@college.ne")
        db.session.add(ecole)
        db.session.flush()

        # Année précédente et active
        annee_prev = AnneeScolaire(nom="2024-2025", date_debut=dt.date(2024, 9, 1), date_fin=dt.date(2025, 6, 30), statut="cloturee", ecole_id=ecole.id)
        db.session.add(annee_prev)
        annee_curr = AnneeScolaire(nom="2025-2026", date_debut=dt.date(2025, 9, 1), date_fin=dt.date(2026, 6, 30), statut="active", ecole_id=ecole.id)
        db.session.add(annee_curr)
        db.session.flush()

        # Utilisateur Directeur
        admin = Utilisateur(
            nom="Directeur",
            prenom="Oumar",
            email="directeur@college.ne",
            mot_de_passe="AdminPass123!",
            role="directeur",
            statut="actif",
            ecole_id=ecole.id
        )
        db.session.add(admin)

        # Enseignant M. Diallo
        prof_diallo = Utilisateur(
            nom="Diallo",
            prenom="Mamadou",
            email="m.diallo@college.ne",
            mot_de_passe="ProfPass123!",
            role="professeur",
            statut="actif",
            ecole_id=ecole.id
        )
        db.session.add(prof_diallo)
        db.session.flush()

        # Classes
        c_6a = Classe(nom="6ème A", niveau="6e", ecole_id=ecole.id, annee_scolaire_id=annee_curr.id)
        c_4b = Classe(nom="4ème B", niveau="4e", ecole_id=ecole.id, annee_scolaire_id=annee_curr.id)
        db.session.add_all([c_6a, c_4b])
        db.session.flush()

        # Cours SVT et Maths
        cours_svt = Cours(nom="SVT", professeur_id=prof_diallo.id, classe_id=c_6a.id, ecole_id=ecole.id)
        cours_maths = Cours(nom="Mathématiques", professeur_id=prof_diallo.id, classe_id=c_6a.id, ecole_id=ecole.id)
        db.session.add_all([cours_svt, cours_maths])
        db.session.flush()

        # 1. Deux élèves homonymes : Mamadou Diallo (6ème A) et Mamadou Diallo (4ème B)
        h1 = Eleve(
            nom="Diallo", prenom="Mamadou", genre="M",
            date_naissance=dt.date(2013, 2, 10),
            code_parent="ELV-6A-01", contact_parent="91111111",
            frais_annuels=200000, ecole_id=ecole.id
        )
        h2 = Eleve(
            nom="Diallo", prenom="Mamadou", genre="M",
            date_naissance=dt.date(2011, 4, 15),
            code_parent="ELV-4B-02", contact_parent="92222222",
            frais_annuels=220000, ecole_id=ecole.id
        )
        db.session.add_all([h1, h2])
        db.session.flush()

        i_h1 = Inscription(eleve_id=h1.id, classe_id=c_6a.id, annee_scolaire_id=annee_curr.id, ecole_id=ecole.id, statut="inscrit")
        i_h2 = Inscription(eleve_id=h2.id, classe_id=c_4b.id, annee_scolaire_id=annee_curr.id, ecole_id=ecole.id, statut="inscrit")
        db.session.add_all([i_h1, i_h2])
        db.session.flush()

        # 2. Élève Fatou Traoré en 6ème A avec notes en SVT
        fatou = Eleve(
            nom="Traoré", prenom="Fatou", genre="F",
            date_naissance=dt.date(2013, 8, 20),
            code_parent="ELV-6A-FATOU", contact_parent="93333333",
            frais_annuels=200000, ecole_id=ecole.id
        )
        db.session.add(fatou)
        db.session.flush()

        i_fatou = Inscription(eleve_id=fatou.id, classe_id=c_6a.id, annee_scolaire_id=annee_curr.id, ecole_id=ecole.id, statut="inscrit")
        db.session.add(i_fatou)
        db.session.flush()

        # Notes de Fatou en SVT (Devoir 1 : 16/20, Compo : 14/20 au Trimestre 1)
        n_fatou_1 = Note(
            valeur=16.0, coefficient=1.0, type_evaluation="Devoir",
            periode="Trimestre 1", date_evaluation=dt.date(2025, 10, 15),
            eleve_id=fatou.id, cours_id=cours_svt.id, annee_id=annee_curr.id,
            ecole_id=ecole.id, inscription_id=i_fatou.id
        )
        n_fatou_2 = Note(
            valeur=14.0, coefficient=2.0, type_evaluation="Composition",
            periode="Trimestre 1", date_evaluation=dt.date(2025, 11, 20),
            eleve_id=fatou.id, cours_id=cours_svt.id, annee_id=annee_curr.id,
            ecole_id=ecole.id, inscription_id=i_fatou.id
        )
        db.session.add_all([n_fatou_1, n_fatou_2])

        # 3. Élève Aissatou Ba en 6ème A : 0 absence et 0 note saisie
        aissatou = Eleve(
            nom="Ba", prenom="Aissatou", genre="F",
            date_naissance=dt.date(2013, 5, 5),
            code_parent="ELV-6A-AISSATOU", contact_parent="94444444",
            frais_annuels=200000, ecole_id=ecole.id
        )
        db.session.add(aissatou)
        db.session.flush()

        i_aissatou = Inscription(eleve_id=aissatou.id, classe_id=c_6a.id, annee_scolaire_id=annee_curr.id, ecole_id=ecole.id, statut="inscrit")
        db.session.add(i_aissatou)
        db.session.flush()

        # 4. Paiements pour tester les indicateurs financiers
        p1 = Paiement(
            eleve_id=fatou.id, montant=100000,
            date_paiement=dt.datetime.now(), mode_paiement="Espèces",
            mois="Septembre", reference="REC-001", ecole_id=ecole.id
        )
        p2 = Paiement(
            eleve_id=h1.id, montant=200000,
            date_paiement=dt.datetime.now(), mode_paiement="Virement",
            mois="Septembre", reference="REC-002", ecole_id=ecole.id
        )
        db.session.add_all([p1, p2])
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin.id)
            sess['user_id'] = str(admin.id)
            sess['_fresh'] = True

        yield app, client

        db.session.remove()
        db.drop_all()


# =========================================================================
# PILLAR 1 : HOMONYM DISAMBIGUATION
# =========================================================================
def test_pillar_1_homonym_disambiguation(intelligence_app_and_client):
    """
    Vérifie qu'en cas d'homonymes parfaits (2 Mamadou Diallo dans 2 classes différentes) :
    1. Le système ne choisit pas arbitrairement le premier.
    2. Il renvoie un choix clair avec boutons interactifs de prompt et liens fiches.
    3. Lorsque la classe est précisée, il résout directement l'élève ciblé.
    """
    app, client = intelligence_app_and_client

    # 1. Demande ambiguë sans préciser la classe
    res = client.post('/api/assistant/query-data', json={'question': "Fiche de Mamadou Diallo"})
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['intention'] == 'homonymes_detectes'
    assert data['donnees_trouvees'] == 2
    reply = data['reply']
    assert "Plusieurs élèves correspondent à votre recherche" in reply
    assert "6ème A" in reply
    assert "4ème B" in reply
    # Présence des boutons d'actions prompts interactifs
    assert "prompt:Fiche de Mamadou Diallo 6ème A" in reply
    assert "prompt:Fiche de Mamadou Diallo 4ème B" in reply

    # 2. Demande précise en mentionnant la classe (action du bouton ou directeur précis)
    res_direct = client.post('/api/assistant/query-data', json={'question': "Fiche de Mamadou Diallo 6ème A"})
    assert res_direct.status_code == 200
    data_direct = res_direct.get_json()
    assert data_direct['success'] is True
    assert data_direct['intention'] == 'fiche_eleve'
    assert "6ème A" in data_direct['reply']
    assert "ELV-6A-01" in data_direct['reply']


# =========================================================================
# PILLAR 2 : PRÉCISION PAR MATIÈRE ET ENSEIGNANT
# =========================================================================
def test_pillar_2_precision_par_matiere(intelligence_app_and_client):
    """
    Vérifie que pour 'sa note en SVT', l'assistant :
    1. Extrait la matière demandée (SVT).
    2. Mentionne le nom de l'enseignant (M. Mamadou Diallo).
    3. Calcule la moyenne pondérée exacte dans la matière (14.67/20).
    4. Liste le détail des évaluations avec dates et coefficients.
    """
    app, client = intelligence_app_and_client

    # Demande ciblée élève + matière
    res = client.post('/api/assistant/query-data', json={'question': "Quelle est la note de Fatou en SVT ?"})
    assert res.status_code == 200
    data = res.get_json()
    assert data['success'] is True
    assert data['intention'] == 'notes_matiere'
    reply = data['reply']
    assert "SVT" in reply
    assert "Fatou Traoré" in reply
    assert "6ème A" in reply
    assert "Mamadou Diallo" in reply  # Professeur assigné au cours
    assert "14.67/20" in reply or "14.7/20" in reply or "14.66/20" in reply  # (16*1 + 14*2)/3 = 44/3 = 14.67
    assert "Devoir" in reply
    assert "Composition" in reply


def test_pillar_2_anaphora_matiere(intelligence_app_and_client):
    """
    Vérifie la mémoire contextuelle d'anaphore : après avoir consulté Fatou,
    demander 'sa note en maths' résout Fatou en Mathématiques.
    """
    app, client = intelligence_app_and_client

    # Étape 1 : Consultation du profil de Fatou
    res1 = client.post('/api/assistant/query-data', json={'question': "Fiche de Fatou Traoré"})
    assert res1.status_code == 200

    # Étape 2 : Question de suivi sans répéter le nom
    res2 = client.post('/api/assistant/query-data', json={'question': "Quelle est sa note en maths ?"})
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2['success'] is True
    assert data2['intention'] == 'notes_matiere'
    assert "Mathématiques" in data2['reply']
    assert "Fatou Traoré" in data2['reply']


# =========================================================================
# PILLAR 3 : NUANCE CHIRURGICALE "PAS ENCORE SAISI" vs "INTROUVABLE" vs "0 ABSENCE"
# =========================================================================
def test_pillar_3_nuance_saisi_vs_introuvable(intelligence_app_and_client):
    """
    Vérifie que l'assistant formule avec une exactitude chirurgicale :
    1. Si un élève existe mais n'a pas encore de note : 'aucun professeur n'a encore enregistré d'évaluation'.
    2. Si un élève a 0 absence : 'Assiduité exemplaire : ... aucune absence enregistrée'.
    3. Si un élève n'existe pas : 'Aucun élève correspondant ... n'a été trouvé'.
    """
    app, client = intelligence_app_and_client

    # Cas 1 : Élève inscrit sans note
    res_nonote = client.post('/api/assistant/query-data', json={'question': "Notes de Aissatou Ba"})
    assert res_nonote.status_code == 200
    data_nonote = res_nonote.get_json()
    assert data_nonote['success'] is True
    assert "aucun professeur n'a encore enregistré" in data_nonote['reply'].lower()
    assert "6ème A" in data_nonote['reply']

    # Cas 2 : Élève avec 0 absence (assiduité exemplaire)
    res_noabs = client.post('/api/assistant/query-data', json={'question': "Absences de Aissatou Ba"})
    assert res_noabs.status_code == 200
    data_noabs = res_noabs.get_json()
    assert data_noabs['success'] is True
    assert "exemplaire" in data_noabs['reply'].lower()
    assert "aucune absence" in data_noabs['reply'].lower()

    # Cas 3 : Élève introuvable dans l'école
    res_notfound = client.post('/api/assistant/query-data', json={'question': "Fiche de Lionel Messi"})
    assert res_notfound.status_code == 200
    data_notfound = res_notfound.get_json()
    assert data_notfound['success'] is True
    assert "aucun élève correspondant" in data_notfound['reply'].lower()
    assert "Lionel Messi" in data_notfound['reply']


# =========================================================================
# PILLAR 4 : INDICATEURS FINANCIERS & CAISSE 100% SQL CERTIFIÉ
# =========================================================================
def test_pillar_4_taux_recouvrement_fast_path(intelligence_app_and_client):
    """Vérifie le calcul SQL certifié du taux de recouvrement global."""
    app, client = intelligence_app_and_client

    for q in ["Quel est le taux de recouvrement ?", "taux recouvrement", "pourcentage de recouvrement"]:
        res = client.post('/api/assistant/query-data', json={'question': q})
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert data['intention'] == 'taux_recouvrement'
        reply = data['reply']
        assert "Taux de recouvrement" in reply
        assert "Total attendu" in reply
        assert "déjà encaissé" in reply
        assert "Reste total" in reply


def test_pillar_4_encaissements_periode_fast_path(intelligence_app_and_client):
    """Vérifie le total des encaissements du mois en cours."""
    app, client = intelligence_app_and_client

    for q in ["Combien a-t-on encaissé ce mois-ci ?", "recettes du mois", "caisse du mois"]:
        res = client.post('/api/assistant/query-data', json={'question': q})
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert data['intention'] == 'encaissements_periode'
        reply = data['reply']
        assert "Encaissements du mois" in reply
        assert "Montant total collecté" in reply
        assert "300 000 FCFA" in reply or "300000 FCFA" in reply  # 100k + 200k = 300k


def test_pillar_4_impayes_par_classe_fast_path(intelligence_app_and_client):
    """Vérifie le classement des classes par retard de paiement."""
    app, client = intelligence_app_and_client

    for q in ["Quelle classe a le plus d'impayés ?", "impayes par classe", "classe la plus en retard"]:
        res = client.post('/api/assistant/query-data', json={'question': q})
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert data['intention'] == 'impayes_par_classe'
        reply = data['reply']
        assert "Situation des Impayés par Classe" in reply
        assert "restant dû" in reply


# =========================================================================
# PILLAR 5 : DIMENSIONS TEMPORELLES (TRIMESTRES, SEMESTRES, ANNÉE PASSÉE)
# =========================================================================
def test_pillar_5_dimensions_temporelles(intelligence_app_and_client):
    """Vérifie l'extraction et l'application des filtres de période et d'année."""
    app, client = intelligence_app_and_client

    # 1. Période Trimestre 1
    res1 = client.post('/api/assistant/query-data', json={'question': "Notes de Fatou au 1er trimestre"})
    assert res1.status_code == 200
    data1 = res1.get_json()
    assert data1['criteres'].get('periode') == "Trimestre 1"

    # 2. Abréviation T2
    res2 = client.post('/api/assistant/query-data', json={'question': "Ses notes au t2"})
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2['criteres'].get('periode') == "Trimestre 2"

    # 3. Année scolaire passée
    res3 = client.post('/api/assistant/query-data', json={'question': "Résultats de l'année passée"})
    assert res3.status_code == 200
    data3 = res3.get_json()
    assert data3['criteres'].get('annee_cible') is not None


# =========================================================================
# STREAMING SSE : COMPATIBILITÉ AVEC LES NOUVELLES FONCTIONNALITÉS
# =========================================================================
def test_sse_stream_homonym_and_matiere(intelligence_app_and_client):
    """Vérifie que l'endpoint SSE émet correctement les homonymes et notes par matière."""
    app, client = intelligence_app_and_client

    # Homonymes en SSE
    res_sse_h = client.post('/api/assistant/stream', json={'question': "Mamadou Diallo"})
    assert res_sse_h.status_code == 200
    assert "text/event-stream" in res_sse_h.content_type
    body_h = res_sse_h.get_data(as_text=True)
    assert "Plusieurs élèves correspondent" in body_h
    assert "prompt:Fiche de Mamadou Diallo 6ème A" in body_h

    # Matière en SSE
    res_sse_m = client.post('/api/assistant/stream', json={'question': "Note de Fatou en SVT"})
    assert res_sse_m.status_code == 200
    body_m = res_sse_m.get_data(as_text=True)
    assert "SVT" in body_m
    assert "Mamadou Diallo" in body_m
