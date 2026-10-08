from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, g
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
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


def _get_bornes_mois(annee, mois):
    """Retourne la date de début et de fin pour un mois donné."""
    debut_mois = date(annee, mois, 1)
    if mois == 12:
        fin_mois = date(annee + 1, 1, 1) - timedelta(days=1)
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

    # Rattacher automatiquement les anciennes fiches sans annee_scolaire_id
    a_commiter = False
    for f in fiches:
        if not f.annee_scolaire_id:
            f.annee_scolaire_id = annee_active.id
            a_commiter = True
    if a_commiter:
        db.session.commit()

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
                "reste_a_payer": fiche.reste_a_payer
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
                "reste_a_payer": 0.0
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

    data = request.get_json() or {}
    mois = int(data.get('mois') or date.today().month)
    annee = int(data.get('annee') or date.today().year)

    if mois < 1 or mois > 12:
        return jsonify({"success": False, "error": "Mois invalide."}), 400

    debut_mois, fin_mois = _get_bornes_mois(annee, mois)
    nom_mois = NOMS_MOIS_FR[mois - 1]
    periode_nom = f"{nom_mois} {annee}"

    professeurs = Professeur.query.filter_by(ecole_id=ecole_id).all()
    if not professeurs:
        return jsonify({"success": False, "error": "Aucun professeur trouvé pour cet établissement."}), 404

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
        fiche = FichePaiePersonnel.query.filter(
            FichePaiePersonnel.professeur_id == prof.id,
            FichePaiePersonnel.ecole_id == ecole_id,
            (FichePaiePersonnel.annee_scolaire_id == annee_active.id) | (FichePaiePersonnel.annee_scolaire_id.is_(None)),
            FichePaiePersonnel.mois == mois,
            FichePaiePersonnel.annee == annee
        ).first()

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
            if annee_active and not fiche.annee_scolaire_id:
                fiche.annee_scolaire_id = annee_active.id

        # Recalcul des montants bruts et nets
        fiche.recalculer_montants()

        # Ajuster le statut selon le montant déjà payé
        if fiche.montant_paye >= fiche.salaire_net and fiche.salaire_net > 0:
            fiche.statut_paiement = 'paye'
        elif fiche.montant_paye > 0:
            fiche.statut_paiement = 'partiel'
        else:
            fiche.statut_paiement = 'en_attente'

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
    """Enregistre un paiement complet ou partiel pour une fiche de paie."""
    ecole_id = g.ecole_id
    data = request.get_json() or {}

    fiche_id = data.get('fiche_id')
    montant_verse = float(data.get('montant_verse') or 0.0)
    mode_reglement = (data.get('mode_reglement') or 'especes').strip()
    reference_recu = (data.get('reference_recu') or '').strip()

    if not fiche_id:
        return jsonify({"success": False, "error": "Identifiant de la fiche de paie manquant."}), 400

    fiche = FichePaiePersonnel.query.filter_by(id=fiche_id, ecole_id=ecole_id).first()
    if not fiche:
        return jsonify({"success": False, "error": "Fiche de paie introuvable."}), 404

    if montant_verse < 0:
        return jsonify({"success": False, "error": "Le montant versé ne peut pas être négatif."}), 400

    fiche.montant_paye = montant_verse
    fiche.mode_paiement = mode_reglement
    fiche.reference_paiement = reference_recu
    fiche.date_paiement = date.today()

    if montant_verse >= fiche.net_a_payer and fiche.net_a_payer > 0:
        fiche.statut_paiement = 'paye'
    elif montant_verse > 0:
        fiche.statut_paiement = 'partiel'
    else:
        fiche.statut_paiement = 'en_attente'

    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Règlement enregistré ({montant_verse:,.0f} FCFA). Statut: {fiche.statut_paiement}.",
        "fiche": fiche.to_dict()
    })


@paie_personnel_bp.route('/ajuster-ligne', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def ajuster_ligne():
    """Ajuste les primes, déductions et commentaire d'une fiche de paie."""
    ecole_id = g.ecole_id
    data = request.get_json() or {}

    fiche_id = data.get('fiche_id')
    primes = float(data.get('primes') or 0.0)
    retenues = float(data.get('retenues') or 0.0)
    commentaire = (data.get('commentaire') or '').strip()

    if not fiche_id:
        return jsonify({"success": False, "error": "Identifiant de fiche manquant."}), 400

    fiche = FichePaiePersonnel.query.filter_by(id=fiche_id, ecole_id=ecole_id).first()
    if not fiche:
        return jsonify({"success": False, "error": "Fiche de paie introuvable."}), 404

    fiche.primes = max(0.0, primes)
    fiche.deductions = max(0.0, retenues)
    if commentaire:
        fiche.note = commentaire

    fiche.recalculer_montants()

    # Réévaluation du statut
    if fiche.montant_paye >= fiche.salaire_net and fiche.salaire_net > 0:
        fiche.statut_paiement = 'paye'
    elif fiche.montant_paye > 0:
        fiche.statut_paiement = 'partiel'
    else:
        fiche.statut_paiement = 'en_attente'

    db.session.commit()

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
    data = request.get_json() or {}

    professeur_id = data.get('professeur_id')
    type_remuneration = (data.get('type_remuneration') or 'fixe').strip()
    salaire_base = float(data.get('salaire_base') or 0.0)
    taux_horaire = float(data.get('taux_horaire') or 0.0)

    if not professeur_id:
        return jsonify({"success": False, "error": "Professeur non spécifié."}), 400

    if type_remuneration not in ('fixe', 'horaire'):
        return jsonify({"success": False, "error": "Type de rémunération invalide (doit être 'fixe' ou 'horaire')."}), 400

    prof = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
    if not prof:
        return jsonify({"success": False, "error": "Professeur introuvable."}), 404

    prof.type_remuneration = type_remuneration
    prof.salaire_base = max(0.0, salaire_base)
    prof.taux_horaire = max(0.0, taux_horaire)

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

    return render_template(
        'bulletin_paie_print.html',
        fiche=fiche,
        prof=prof,
        ecole=ecole,
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

