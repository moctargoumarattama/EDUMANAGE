from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, g
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
import json
from math import isfinite
from sqlalchemy import case, func, update
from app.authorization import role_required, tenant_required
from app import db
from app.models import Professeur, PointagePersonnel, FichePaiePersonnel, Ecole, AnneeScolaire
from app.services.annees_scolaires import get_annee_consultee, get_annee_active

paie_personnel_bp = Blueprint('paie_personnel', __name__, url_prefix='/paie-personnel')

NOMS_MOIS_FR = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
]

MODES_REGLEMENT_AUTORISES = {'especes', 'virement', 'cheque', 'mobile_money', 'orange_money', 'wave'}
HISTORIQUE_REGLEMENTS = '\n\n--- HISTORIQUE DES RÈGLEMENTS (LECTURE SEULE) ---\n'
CENTIME = Decimal('0.01')


def _montant_decimal(valeur):
    """Refuse les montants non finis ou avec plus de deux décimales."""
    try:
        montant = Decimal(str(valeur))
        if not montant.is_finite() or montant != montant.quantize(CENTIME):
            return None
        return montant
    except (InvalidOperation, TypeError, ValueError):
        return None


def _montant_existant(valeur):
    """Normalise les légères imprécisions du stockage FLOAT existant."""
    try:
        montant = Decimal(str(valeur or 0))
        return montant.quantize(CENTIME) if montant.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _id_positif(valeur):
    """Identifiant JSON entier, sans coercition silencieuse de liste ou booléen."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, str)):
        return None
    if isinstance(valeur, str) and not valeur.strip().isdigit():
        return None
    try:
        identifiant = int(valeur)
    except (TypeError, ValueError):
        return None
    return identifiant if identifiant > 0 else None


def _decomposer_note(note):
    """Sépare le commentaire libre du journal JSONL des règlements."""
    commentaire, separateur, historique = (note or '').partition(HISTORIQUE_REGLEMENTS)
    evenements = []
    if separateur:
        for ligne in historique.splitlines():
            try:
                evenement = json.loads(ligne)
            except (TypeError, ValueError):
                continue
            if isinstance(evenement, dict):
                evenements.append(evenement)
    return commentaire, evenements


def _note_avec_evenement(evenement, solde_initial=None):
    """Expression SQL append-only : l'ajout suit le solde dans le même UPDATE."""
    ligne = json.dumps(evenement, ensure_ascii=False, sort_keys=True)
    premiere_ligne = ligne
    if solde_initial is not None and solde_initial > 0:
        # Les anciennes fiches ne détaillaient pas leurs versements. On rend
        # l'écart visible sans inventer une date, une référence ou un payeur.
        solde_legacy = json.dumps({
            'type': 'solde_initial_legacy',
            'montant': f'{solde_initial:.2f}',
        }, ensure_ascii=False, sort_keys=True)
        premiere_ligne = solde_legacy + '\n' + ligne
    note = FichePaiePersonnel.note
    return case(
        (note.contains(HISTORIQUE_REGLEMENTS), note + '\n' + ligne),
        else_=func.coalesce(note, '') + HISTORIQUE_REGLEMENTS + premiere_ligne,
    )


def _periode_dans_annee(annee_scolaire, annee_civile, mois):
    """Un mois est valide dès qu'il touche l'intervalle officiel de l'année."""
    if not annee_scolaire or not annee_scolaire.date_debut or not annee_scolaire.date_fin:
        return False
    try:
        debut_mois, fin_mois = _get_bornes_mois(annee_civile, mois)
    except (TypeError, ValueError, OverflowError):
        return False
    return debut_mois <= annee_scolaire.date_fin and fin_mois >= annee_scolaire.date_debut


def _garde_annee_mutable(annee_scolaire):
    if annee_scolaire and (annee_scolaire.statut or '').strip().casefold() == 'archivee':
        return jsonify({
            'success': False,
            'error': "Action interdite : les fiches de paie d'une année scolaire archivée sont scellées et immuables.",
        }), 403
    return None


def _garde_fiche_mutable(fiche, ecole_id):
    """Vérifie l'année *réelle* de la fiche, indépendamment de la sélection UI."""
    if not fiche.annee_scolaire_id:
        return jsonify({
            'success': False,
            'error': "La fiche n'est rattachée à aucune année scolaire ; mutation impossible sans rattachement sûr.",
        }), 409
    annee_scolaire = (
        AnneeScolaire.query.filter_by(id=fiche.annee_scolaire_id, ecole_id=ecole_id)
        .populate_existing().with_for_update().first()
    )
    if not annee_scolaire:
        return jsonify({'success': False, 'error': "Année scolaire de la fiche introuvable."}), 409
    erreur = _garde_annee_mutable(annee_scolaire)
    if erreur:
        return erreur
    if not _periode_dans_annee(annee_scolaire, fiche.annee, fiche.mois):
        return jsonify({
            'success': False,
            'error': f"Période invalide : le mois {fiche.mois}/{fiche.annee} ne correspond pas au calendrier de l'année scolaire {annee_scolaire.nom}.",
        }), 400
    return None


def _statut_paiement(montant_paye, net_a_payer):
    if montant_paye > 0 and montant_paye >= net_a_payer:
        return 'paye'
    return 'partiel' if montant_paye > 0 else 'en_attente'


def _get_bornes_mois(annee, mois):
    """Retourne la date de début et de fin pour un mois donné."""
    debut_mois = date(annee, mois, 1)
    if mois == 12:
        fin_mois = date.max if annee == 9999 else date(annee + 1, 1, 1) - timedelta(days=1)
    else:
        fin_mois = date(annee, mois + 1, 1) - timedelta(days=1)
    return debut_mois, fin_mois


def _generer_mois_annee_scolaire(annee_scolaire):
    """Génère la liste ordonnée des mois de l'année scolaire active pour la paie."""
    if not annee_scolaire or not annee_scolaire.date_debut or not annee_scolaire.date_fin:
        auj = date.today()
        return [{
            "valeur": f"{auj.year}-{auj.month:02d}",
            "nom": f"{NOMS_MOIS_FR[auj.month - 1]} {auj.year}",
            "mois": auj.month,
            "annee": auj.year
        }]

    cur = date(annee_scolaire.date_debut.year, annee_scolaire.date_debut.month, 1)
    fin = date(annee_scolaire.date_fin.year, annee_scolaire.date_fin.month, 1)

    mois_list = []
    while cur <= fin:
        nom_mois = NOMS_MOIS_FR[cur.month - 1]
        mois_list.append({
            "valeur": f"{cur.year}-{cur.month:02d}",
            "nom": f"{nom_mois} {cur.year}",
            "mois": cur.month,
            "annee": cur.year
        })
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

    return mois_list


@paie_personnel_bp.route('/', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def index():
    """Écran principal de gestion de la paie pour l'administration rattaché à l'année scolaire active."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)

    if not annee_active:
        flash("Aucune année scolaire active. Veuillez d'abord configurer une année scolaire.", "warning")
        return redirect(url_for('main.gestion_annees'))

    mois_annee_options = _generer_mois_annee_scolaire(annee_active)

    # Récupérer la période sélectionnée (soit ?periode=YYYY-MM soit ?mois=M&annee=Y)
    periode_param = request.args.get('periode', '').strip()
    mois_param = request.args.get('mois', type=int)
    annee_param = request.args.get('annee', type=int)

    mois = None
    annee = None

    if periode_param and '-' in periode_param:
        try:
            parts = periode_param.split('-')
            annee = int(parts[0])
            mois = int(parts[1])
        except (ValueError, IndexError):
            pass
    elif mois_param and annee_param:
        mois = mois_param
        annee = annee_param

    # Vérifier si (mois, annee) est bien dans la liste des mois de l'année scolaire active
    periode_valide = any(opt['mois'] == mois and opt['annee'] == annee for opt in mois_annee_options)

    if not periode_valide:
        # Par défaut : date du jour si elle est dans l'année active
        aujourdhui = date.today()
        opt_aujourdhui = next((opt for opt in mois_annee_options if opt['mois'] == aujourdhui.month and opt['annee'] == aujourdhui.year), None)
        if opt_aujourdhui:
            mois = opt_aujourdhui['mois']
            annee = opt_aujourdhui['annee']
        else:
            # Sinon, premier mois de l'année scolaire active
            premier_opt = mois_annee_options[0]
            mois = premier_opt['mois']
            annee = premier_opt['annee']

    periode_selectionnee_valeur = f"{annee}-{mois:02d}"

    # Charger tous les professeurs de l'école
    professeurs = (
        Professeur.query
        .filter_by(ecole_id=ecole_id)
        .order_by(Professeur.nom.asc(), Professeur.prenom.asc())
        .all()
    )

    # Charger les fiches de paie existantes pour ce mois, cette année et cette année scolaire
    fiches = (
        FichePaiePersonnel.query
        .filter(
            FichePaiePersonnel.ecole_id == ecole_id,
            (FichePaiePersonnel.annee_scolaire_id == annee_active.id) | (FichePaiePersonnel.annee_scolaire_id.is_(None)),
            FichePaiePersonnel.mois == mois,
            FichePaiePersonnel.annee == annee
        )
        .all()
    )

    fiches_par_prof = {f.professeur_id: f for f in fiches}

    # Liste des lignes à afficher (fiche existante ou enseignant prêt à être calculé)
    lignes_paie = []
    total_masse_brute = 0.0
    total_regle = 0.0
    total_reste_a_verser = 0.0
    nb_payes = 0
    nb_en_attente = 0

    for prof in professeurs:
        fiche = fiches_par_prof.get(prof.id)
        if fiche:
            commentaire_ajustement, historique_versements = _decomposer_note(fiche.note)
            total_masse_brute += (fiche.salaire_brut or 0.0)
            total_regle += (fiche.montant_paye or 0.0)
            total_reste_a_verser += (fiche.reste_a_payer or 0.0)
            if fiche.statut_paiement == 'paye':
                nb_payes += 1
            else:
                nb_en_attente += 1

            lignes_paie.append({
                "professeur": prof,
                "fiche": fiche,
                "est_calculee": True,
                "type_remuneration": fiche.type_remuneration or prof.type_remuneration or 'fixe',
                "salaire_brut": fiche.salaire_brut,
                "heures_travaillees": fiche.heures_travaillees,
                "primes": fiche.primes,
                "deductions": fiche.deductions,
                "net_a_payer": fiche.net_a_payer,
                "statut_paiement": fiche.statut_paiement,
                "montant_paye": fiche.montant_paye,
                "reste_a_payer": fiche.reste_a_payer,
                "commentaire_ajustement": commentaire_ajustement,
                "historique_versements": historique_versements,
            })
        else:
            nb_en_attente += 1
            lignes_paie.append({
                "professeur": prof,
                "fiche": None,
                "est_calculee": False,
                "type_remuneration": prof.type_remuneration or 'fixe',
                "salaire_brut": 0.0,
                "heures_travaillees": 0.0,
                "primes": 0.0,
                "deductions": 0.0,
                "net_a_payer": 0.0,
                "statut_paiement": 'non_calcule',
                "montant_paye": 0.0,
                "reste_a_payer": 0.0,
                "commentaire_ajustement": '',
                "historique_versements": [],
            })

    nom_mois_selectionne = NOMS_MOIS_FR[mois - 1]

    return render_template(
        'paie_personnel.html',
        annee_active=annee_active,
        mois_annee_options=mois_annee_options,
        periode_selectionnee_valeur=periode_selectionnee_valeur,
        mois_selectionne=mois,
        annee_selectionnee=annee,
        nom_mois_selectionne=nom_mois_selectionne,
        lignes_paie=lignes_paie,
        total_profs=len(professeurs),
        total_masse_brute=round(total_masse_brute, 2),
        total_regle=round(total_regle, 2),
        total_reste_a_verser=round(total_reste_a_verser, 2),
        nb_payes=nb_payes,
        nb_en_attente=nb_en_attente,
        noms_mois=NOMS_MOIS_FR
    )


@paie_personnel_bp.route('/calculer-mois', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def calculer_mois():
    """Calcule et génère/actualise les fiches de paie de tous les professeurs pour le mois donné rattaché à l'année active."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    if not annee_active:
        return jsonify({"success": False, "error": "Aucune année scolaire active configurée."}), 400
    annee_active = (
        AnneeScolaire.query.filter_by(id=annee_active.id, ecole_id=ecole_id)
        .populate_existing().with_for_update().first()
    )
    if not annee_active:
        return jsonify({"success": False, "error": "Année scolaire introuvable."}), 404
    erreur = _garde_annee_mutable(annee_active)
    if erreur:
        return erreur

    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Données de période invalides."}), 400
    mois = _id_positif(data.get('mois', date.today().month))
    annee = _id_positif(data.get('annee', date.today().year))
    if mois is None or annee is None:
        return jsonify({"success": False, "error": "Période invalide."}), 400

    if not _periode_dans_annee(annee_active, annee, mois):
        return jsonify({
            "success": False,
            "error": f"Période invalide : le mois {mois}/{annee} ne correspond pas au calendrier de l'année scolaire {annee_active.nom}."
        }), 400

    debut_mois, fin_mois = _get_bornes_mois(annee, mois)
    nom_mois = NOMS_MOIS_FR[mois - 1]
    periode_nom = f"{nom_mois} {annee}"

    professeurs = Professeur.query.filter_by(ecole_id=ecole_id).all()
    if not professeurs:
        return jsonify({"success": False, "error": "Aucun professeur trouvé pour cet établissement."}), 404

    # Précontrôle global : un seul dossier historique incohérent bloque tout le lot.
    fiches_existantes = FichePaiePersonnel.query.filter_by(
        ecole_id=ecole_id, mois=mois, annee=annee
    ).all()
    fiches_par_prof = {fiche.professeur_id: fiche for fiche in fiches_existantes}
    for fiche in fiches_existantes:
        if not fiche.annee_scolaire_id:
            annees_candidates = AnneeScolaire.query.filter(
                AnneeScolaire.ecole_id == ecole_id,
                AnneeScolaire.date_debut <= fin_mois,
                AnneeScolaire.date_fin >= debut_mois,
            ).all()
            if len(annees_candidates) != 1 or annees_candidates[0].id != annee_active.id:
                return jsonify({
                    'success': False,
                    'error': "Rattachement impossible : la période d'une ancienne fiche ne correspond pas à une année scolaire unique."
                }), 409
            continue
        erreur = _garde_fiche_mutable(fiche, ecole_id)
        if erreur:
            return erreur
        if fiche.annee_scolaire_id != annee_active.id:
            return jsonify({
                "success": False,
                "error": "Une fiche existe déjà pour cette période dans une autre année scolaire."
            }), 409

    # Charger les pointages du mois pour cette école ET pour l'année scolaire active
    pointages_mois = (
        PointagePersonnel.query
        .filter(
            PointagePersonnel.ecole_id == ecole_id,
            PointagePersonnel.annee_scolaire_id == annee_active.id,
            PointagePersonnel.date_pointage >= debut_mois,
            PointagePersonnel.date_pointage <= fin_mois
        )
        .all()
    )

    pointages_par_prof = {}
    for pt in pointages_mois:
        pointages_par_prof.setdefault(pt.professeur_id, []).append(pt)

    nb_calcules = 0
    for prof in professeurs:
        profs_pointages = pointages_par_prof.get(prof.id, [])
        heures_totales = sum(p.heures_effectuees for p in profs_pointages)
        jours_presence = sum(1 for p in profs_pointages if p.statut in ('present', 'retard'))
        retards_minutes = sum(p.retard_minutes for p in profs_pointages if p.statut == 'retard')
        absences_injustifiees = sum(1 for p in profs_pointages if p.statut == 'absent_injustifie')
        absences_justifiees = sum(1 for p in profs_pointages if p.statut == 'absent_justifie')

        # Trouver ou créer la fiche de paie rattachée à l'année scolaire active
        fiche = fiches_par_prof.get(prof.id)

        type_rem = prof.type_remuneration or 'fixe'
        salaire_base = float(prof.salaire_base or 0.0)
        taux_horaire = float(prof.taux_horaire or 0.0)

        if not fiche:
            fiche = FichePaiePersonnel(
                professeur_id=prof.id,
                ecole_id=ecole_id,
                annee_scolaire_id=annee_active.id,
                mois=mois,
                annee=annee,
                periode_nom=periode_nom,
                type_remuneration=type_rem,
                salaire_base=salaire_base,
                taux_horaire=taux_horaire,
                heures_prevues=0.0,
                heures_travaillees=round(heures_totales, 2),
                jours_presence=jours_presence,
                retards_total_minutes=retards_minutes,
                absences_injustifiees=absences_injustifiees,
                absences_justifiees=absences_justifiees,
                primes=0.0,
                deductions=0.0,
                statut_paiement='en_attente',
                montant_paye=0.0,
                cree_par_id=current_user.id
            )
            db.session.add(fiche)
        else:
            # Mise à jour des données de travail
            fiche.annee_scolaire_id = annee_active.id
            fiche.type_remuneration = type_rem
            fiche.salaire_base = salaire_base
            fiche.taux_horaire = taux_horaire
            fiche.heures_travaillees = round(heures_totales, 2)
            fiche.jours_presence = jours_presence
            fiche.retards_total_minutes = retards_minutes
            fiche.absences_injustifiees = absences_injustifiees
            fiche.absences_justifiees = absences_justifiees

        # Recalcul des montants bruts et nets
        fiche.recalculer_montants()
        if not all(isfinite(float(valeur)) for valeur in (
            fiche.salaire_brut, fiche.salaire_net, fiche.primes,
            fiche.deductions, fiche.montant_paye
        )):
            db.session.rollback()
            return jsonify({"success": False, "error": "Montants non finis dans le calcul de paie."}), 400
        montant_deja = _montant_existant(fiche.montant_paye)
        net_calcule = _montant_existant(fiche.salaire_net)
        if montant_deja is None or net_calcule is None or montant_deja < 0 or montant_deja > net_calcule:
            db.session.rollback()
            return jsonify({
                "success": False,
                "error": "Recalcul impossible : le montant déjà versé dépasse le nouveau salaire net."
            }), 400

        # Ajuster le statut selon le montant déjà payé
        fiche.statut_paiement = _statut_paiement(montant_deja, net_calcule)

        nb_calcules += 1

    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Calcul de paie terminé avec succès pour {nb_calcules} enseignant(s)."
    })


@paie_personnel_bp.route('/enregistrer-reglement', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def enregistrer_reglement():
    """Ajoute un versement au cumul et à son historique, de manière atomique."""
    ecole_id = g.ecole_id
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Données de règlement invalides."}), 400

    fiche_id = _id_positif(data.get('fiche_id'))
    montant_verse = _montant_decimal(data.get('montant_verse'))
    mode_brut = data.get('mode_reglement', 'especes')
    reference_brute = data.get('reference_recu', '')
    if not isinstance(mode_brut, str) or not isinstance(reference_brute, str):
        return jsonify({"success": False, "error": "Mode ou référence de règlement invalide."}), 400
    mode_reglement = mode_brut.strip() or 'especes'
    reference_recu = reference_brute.strip()

    if not fiche_id:
        return jsonify({"success": False, "error": "Identifiant de la fiche de paie manquant."}), 400
    if montant_verse is None or montant_verse <= 0:
        return jsonify({"success": False, "error": "Le versement doit être un montant positif valide (au centime près)."}), 400
    if mode_reglement not in MODES_REGLEMENT_AUTORISES:
        return jsonify({"success": False, "error": "Mode de règlement invalide."}), 400
    if len(reference_recu) > 100:
        return jsonify({"success": False, "error": "La référence du règlement dépasse 100 caractères."}), 400

    fiche = FichePaiePersonnel.query.filter_by(id=fiche_id, ecole_id=ecole_id).first()
    if not fiche:
        return jsonify({"success": False, "error": "Fiche de paie introuvable."}), 404
    erreur = _garde_fiche_mutable(fiche, ecole_id)
    if erreur:
        return erreur

    net = _montant_existant(fiche.net_a_payer)
    ancien_total = _montant_existant(fiche.montant_paye)
    if net is None or ancien_total is None or net <= 0 or ancien_total < 0 or ancien_total > net:
        return jsonify({"success": False, "error": "Montants de la fiche incohérents ; règlement refusé."}), 409
    if ancien_total + montant_verse > net:
        return jsonify({"success": False, "error": "Versement refusé : le cumul dépasserait le salaire net à payer."}), 400

    evenement = {
        'type': 'versement',
        'date': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'montant': f'{montant_verse:.2f}',
        'mode': mode_reglement,
        'reference': reference_recu,
        'utilisateur_id': current_user.id,
    }
    # Le SET et la condition sur le solde sont exécutés par la base en une seule
    # instruction : deux versements simultanés ne peuvent plus perdre un acompte.
    montant_sql = func.coalesce(FichePaiePersonnel.montant_paye, 0) + float(montant_verse)
    net_sql = FichePaiePersonnel.salaire_net
    presque_total = montant_sql >= net_sql - 0.000001
    resultat = db.session.execute(
        update(FichePaiePersonnel)
        .where(
            FichePaiePersonnel.id == fiche.id,
            FichePaiePersonnel.ecole_id == ecole_id,
            FichePaiePersonnel.annee_scolaire_id == fiche.annee_scolaire_id,
            func.coalesce(FichePaiePersonnel.montant_paye, 0) >= 0,
            net_sql > 0,
            montant_sql <= net_sql + 0.000001,
        )
        .values(
            montant_paye=case((presque_total, net_sql), else_=montant_sql),
            statut_paiement=case((presque_total, 'paye'), else_='partiel'),
            note=_note_avec_evenement(evenement, ancien_total),
            mode_paiement=mode_reglement,
            reference_paiement=reference_recu,
            date_paiement=date.today(),
        )
        .execution_options(synchronize_session=False)
    )
    if resultat.rowcount != 1:
        db.session.rollback()
        return jsonify({"success": False, "error": "Versement refusé : le solde a changé ou serait dépassé."}), 400

    db.session.commit()
    db.session.refresh(fiche)

    return jsonify({
        "success": True,
        "message": f"Versement enregistré ({montant_verse:,.0f} FCFA). Statut: {fiche.statut_paiement}.",
        "fiche": fiche.to_dict()
    })


@paie_personnel_bp.route('/corriger-total-reglement', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def corriger_total_reglement():
    """Rectifie explicitement le cumul d'une fiche, sans simuler un versement."""
    ecole_id = g.ecole_id
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': "Données de correction invalides."}), 400

    fiche_id = _id_positif(data.get('fiche_id'))
    montant_total = _montant_decimal(data.get('montant_total'))
    motif_brut = data.get('motif')
    reference_brute = data.get('reference_recu', '')
    if not isinstance(motif_brut, str) or not isinstance(reference_brute, str):
        return jsonify({'success': False, 'error': "Motif ou référence de correction invalide."}), 400
    motif = motif_brut.strip()
    reference_recu = reference_brute.strip()
    if not fiche_id:
        return jsonify({'success': False, 'error': "Identifiant de la fiche de paie manquant."}), 400
    if montant_total is None or montant_total < 0:
        return jsonify({'success': False, 'error': "Le total corrigé doit être un montant valide et positif ou nul."}), 400
    if len(motif) < 3 or len(motif) > 1000:
        return jsonify({'success': False, 'error': "Un motif précis de 3 à 1000 caractères est obligatoire."}), 400
    if len(reference_recu) > 100:
        return jsonify({'success': False, 'error': "La référence de correction dépasse 100 caractères."}), 400

    fiche = FichePaiePersonnel.query.filter_by(id=fiche_id, ecole_id=ecole_id).first()
    if not fiche:
        return jsonify({'success': False, 'error': "Fiche de paie introuvable."}), 404
    erreur = _garde_fiche_mutable(fiche, ecole_id)
    if erreur:
        return erreur
    ancien_total = _montant_existant(fiche.montant_paye)
    net = _montant_existant(fiche.net_a_payer)
    if ancien_total is None or net is None or ancien_total < 0 or net < 0:
        return jsonify({'success': False, 'error': "Montants de la fiche incohérents ; correction refusée."}), 409
    if montant_total > net:
        return jsonify({'success': False, 'error': "Le total corrigé ne peut pas dépasser le salaire net."}), 400
    if montant_total == ancien_total:
        return jsonify({'success': False, 'error': "Le total corrigé est identique au total actuel."}), 400

    evenement = {
        'type': 'correction_total',
        'date': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'ancien_total': f'{ancien_total:.2f}',
        'nouveau_total': f'{montant_total:.2f}',
        'motif': motif,
        'reference': reference_recu,
        'utilisateur_id': current_user.id,
    }
    resultat = db.session.execute(
        update(FichePaiePersonnel)
        .where(
            FichePaiePersonnel.id == fiche.id,
            FichePaiePersonnel.ecole_id == ecole_id,
            FichePaiePersonnel.annee_scolaire_id == fiche.annee_scolaire_id,
            FichePaiePersonnel.montant_paye == fiche.montant_paye,
            FichePaiePersonnel.salaire_net == fiche.salaire_net,
            FichePaiePersonnel.salaire_net >= float(montant_total),
        )
        .values(
            montant_paye=float(montant_total),
            statut_paiement=_statut_paiement(montant_total, net),
            note=_note_avec_evenement(evenement, ancien_total),
        )
        .execution_options(synchronize_session=False)
    )
    if resultat.rowcount != 1:
        db.session.rollback()
        return jsonify({'success': False, 'error': "Correction refusée : la fiche a changé entre-temps."}), 409
    db.session.commit()
    db.session.refresh(fiche)
    return jsonify({
        'success': True,
        'message': "Total des règlements corrigé avec traçabilité.",
        'fiche': fiche.to_dict(),
    })


@paie_personnel_bp.route('/ajuster-ligne', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def ajuster_ligne():
    """Ajuste les primes, déductions et commentaire d'une fiche de paie."""
    ecole_id = g.ecole_id
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Données d'ajustement invalides."}), 400

    fiche_id = _id_positif(data.get('fiche_id'))
    primes = _montant_decimal(data.get('primes', 0))
    retenues = _montant_decimal(data.get('retenues', 0))
    commentaire_brut = data.get('commentaire') if 'commentaire' in data else None
    if commentaire_brut is not None and not isinstance(commentaire_brut, str):
        return jsonify({"success": False, "error": "Commentaire d'ajustement invalide."}), 400
    commentaire = commentaire_brut.strip() if commentaire_brut is not None else None

    if not fiche_id:
        return jsonify({"success": False, "error": "Identifiant de fiche manquant."}), 400
    if primes is None or retenues is None or primes < 0 or retenues < 0:
        return jsonify({"success": False, "error": "Primes et retenues doivent être des montants valides et positifs ou nuls."}), 400
    if commentaire is not None and HISTORIQUE_REGLEMENTS.strip() in commentaire:
        return jsonify({"success": False, "error": "Le commentaire contient une marque réservée à l'historique."}), 400

    fiche = FichePaiePersonnel.query.filter_by(id=fiche_id, ecole_id=ecole_id).first()
    if not fiche:
        return jsonify({"success": False, "error": "Fiche de paie introuvable."}), 404
    erreur = _garde_fiche_mutable(fiche, ecole_id)
    if erreur:
        return erreur

    try:
        if fiche.type_remuneration == 'horaire' or (
            fiche.taux_horaire and float(fiche.taux_horaire) > 0 and not fiche.salaire_base
        ):
            salaire_brut = round(float(fiche.taux_horaire or 0) * float(fiche.heures_travaillees or 0), 2)
        else:
            salaire_brut = round(float(fiche.salaire_base or 0), 2)
        salaire_net = max(0.0, round(salaire_brut + float(primes) - float(retenues), 2))
    except (TypeError, ValueError, OverflowError):
        return jsonify({"success": False, "error": "Données salariales invalides dans la fiche."}), 409
    if not isfinite(salaire_brut) or not isfinite(salaire_net):
        return jsonify({"success": False, "error": "Données salariales non finies dans la fiche."}), 409
    montant_paye = _montant_existant(fiche.montant_paye)
    nouveau_net = _montant_existant(salaire_net)
    if montant_paye is None or nouveau_net is None or montant_paye < 0 or montant_paye > nouveau_net:
        return jsonify({
            "success": False,
            "error": "Ajustement impossible : le montant déjà versé dépasserait le nouveau salaire net."
        }), 400

    ancienne_note = fiche.note
    if commentaire is None:
        nouvelle_note = ancienne_note
    else:
        _, separateur, journal = (ancienne_note or '').partition(HISTORIQUE_REGLEMENTS)
        nouvelle_note = commentaire + (separateur + journal if separateur else '')
    condition_note = (
        FichePaiePersonnel.note.is_(None) if ancienne_note is None
        else FichePaiePersonnel.note == ancienne_note
    )
    resultat = db.session.execute(
        update(FichePaiePersonnel)
        .where(
            FichePaiePersonnel.id == fiche.id,
            FichePaiePersonnel.ecole_id == ecole_id,
            FichePaiePersonnel.annee_scolaire_id == fiche.annee_scolaire_id,
            FichePaiePersonnel.montant_paye == fiche.montant_paye,
            condition_note,
            FichePaiePersonnel.montant_paye <= salaire_net,
        )
        .values(
            primes=float(primes),
            deductions=float(retenues),
            salaire_brut=salaire_brut,
            salaire_net=salaire_net,
            statut_paiement=_statut_paiement(montant_paye, nouveau_net),
            note=nouvelle_note,
        )
        .execution_options(synchronize_session=False)
    )
    if resultat.rowcount != 1:
        db.session.rollback()
        return jsonify({'success': False, 'error': "Ajustement refusé : la fiche a changé entre-temps."}), 409

    db.session.commit()
    db.session.refresh(fiche)

    return jsonify({
        "success": True,
        "message": "Primes et retenues mises à jour avec succès.",
        "fiche": fiche.to_dict()
    })


@paie_personnel_bp.route('/configurer-contrat', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def configurer_contrat():
    """Configure le type de rémunération, le salaire fixe et le taux horaire d'un professeur."""
    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    if annee_consultee:
        annee_consultee = (
            AnneeScolaire.query.filter_by(id=annee_consultee.id, ecole_id=ecole_id)
            .populate_existing().with_for_update().first()
        )
    erreur = _garde_annee_mutable(annee_consultee)
    if erreur:
        return erreur
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Données de contrat invalides."}), 400

    professeur_id = _id_positif(data.get('professeur_id'))
    type_brut = data.get('type_remuneration', 'fixe')
    if not isinstance(type_brut, str):
        return jsonify({"success": False, "error": "Type de rémunération invalide."}), 400
    type_remuneration = type_brut.strip() or 'fixe'
    salaire_base = _montant_decimal(data.get('salaire_base', 0))
    taux_horaire = _montant_decimal(data.get('taux_horaire', 0))

    if not professeur_id:
        return jsonify({"success": False, "error": "Professeur non spécifié."}), 400

    if type_remuneration not in ('fixe', 'horaire'):
        return jsonify({"success": False, "error": "Type de rémunération invalide (doit être 'fixe' ou 'horaire')."}), 400
    if salaire_base is None or taux_horaire is None or salaire_base < 0 or taux_horaire < 0:
        return jsonify({"success": False, "error": "Salaire et taux horaire doivent être des montants valides et positifs ou nuls."}), 400

    prof = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
    if not prof:
        return jsonify({"success": False, "error": "Professeur introuvable."}), 404

    prof.type_remuneration = type_remuneration
    prof.salaire_base = float(salaire_base)
    prof.taux_horaire = float(taux_horaire)

    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Contrat de {prof.prenom} {prof.nom} mis à jour.",
        "professeur": prof.to_dict()
    })


@paie_personnel_bp.route('/bulletin/<int:fiche_id>/print', methods=['GET'])
@login_required
@role_required('admin', 'professeur')
@tenant_required
def print_bulletin(fiche_id):
    """Affiche le bulletin de paie officiel optimisé pour l'impression A4.
    Accessible par l'administration et le professeur concerné uniquement.
    """
    from flask import abort

    ecole_id = g.ecole_id
    fiche = FichePaiePersonnel.query.filter_by(id=fiche_id, ecole_id=ecole_id).first_or_404()

    # RÈGLE DE SÉCURITÉ STRICTE : Si rôle professeur, doit être impérativement sa propre fiche
    if current_user.role == 'professeur':
        prof = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
        if not prof or fiche.professeur_id != prof.id:
            abort(403)
    elif current_user.role != 'admin':
        abort(403)

    prof = fiche.professeur
    ecole = db.session.get(Ecole, ecole_id)
    commentaire_ajustement, historique_versements = _decomposer_note(fiche.note)

    return render_template(
        'bulletin_paie_print.html',
        fiche=fiche,
        prof=prof,
        ecole=ecole,
        commentaire_ajustement=commentaire_ajustement,
        historique_versements=historique_versements,
        date_impression=datetime.now()
    )


@paie_personnel_bp.route('/mes-fiches', methods=['GET'])
@login_required
@role_required('professeur')
@tenant_required
def mes_fiches():
    """API retournant l'historique des fiches de paie de l'enseignant connecté."""
    prof = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
    if not prof:
        return jsonify({"success": False, "error": "Profil professeur introuvable."}), 404

    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)

    query = FichePaiePersonnel.query.filter(
        FichePaiePersonnel.professeur_id == prof.id,
        FichePaiePersonnel.ecole_id == ecole_id
    )
    if annee_active:
        query = query.filter(
            (FichePaiePersonnel.annee_scolaire_id == annee_active.id) | (FichePaiePersonnel.annee_scolaire_id.is_(None))
        )

    fiches = query.order_by(FichePaiePersonnel.annee.desc(), FichePaiePersonnel.mois.desc()).all()

    data = [
        {
            "id": f.id,
            "mois": f.mois,
            "annee": f.annee,
            "periode_nom": f.periode_nom,
            "heures_totales": f.heures_travaillees,
            "salaire_brut": f.salaire_brut,
            "net_a_payer": f.net_a_payer,
            "statut_paiement": f.statut_paiement,
            "montant_paye": f.montant_paye,
            "reste_a_payer": f.reste_a_payer,
            "date_paiement": f.date_paiement.strftime("%d/%m/%Y") if f.date_paiement else None,
            "mode_paiement": f.mode_paiement,
            "print_url": url_for('paie_personnel.print_bulletin', fiche_id=f.id)
        }
        for f in fiches
    ]
    return jsonify({"success": True, "fiches": data})

