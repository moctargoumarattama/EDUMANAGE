from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, g
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from app.authorization import role_required, tenant_required
from app import db
from app.models import Professeur, PointagePersonnel, AnneeScolaire
from app.services.annees_scolaires import get_annee_consultee, get_annee_active

pointage_personnel_bp = Blueprint('pointage_personnel', __name__, url_prefix='/pointage-personnel')

STATUTS_AUTORISES = {'present', 'retard', 'absent_justifie', 'absent_injustifie'}

NOMS_MOIS_FR = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
]


def _calculer_duree_heures(debut_str, fin_str):
    """Calcule une durée valide ; aucune durée fictive sur une heure erronée."""
    try:
        if not debut_str or not fin_str:
            return None
        t1 = datetime.strptime(debut_str.strip(), "%H:%M")
        t2 = datetime.strptime(fin_str.strip(), "%H:%M")
        diff = (t2 - t1).total_seconds() / 3600.0
        return round(diff, 2) if diff > 0 else None
    except (TypeError, ValueError):
        return None


def _generer_mois_annee_scolaire(annee_scolaire):
    """Génère la liste ordonnée des mois de l'année scolaire active pour le rapport."""
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


@pointage_personnel_bp.route('/', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def index():
    """Écran principal de pointage du personnel rattaché strictement à l'année scolaire active."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)

    if not annee_active:
        flash("Aucune année scolaire active. Veuillez d'abord configurer une année scolaire.", "warning")
        return redirect(url_for('main.gestion_annees'))

    date_param = request.args.get('date', '').strip()
    try:
        date_selectionnee = datetime.strptime(date_param, "%Y-%m-%d").date() if date_param else date.today()
    except ValueError:
        date_selectionnee = date.today()

    # Professeurs actifs de l'établissement
    professeurs = (
        Professeur.query
        .filter_by(ecole_id=ecole_id)
        .order_by(Professeur.nom.asc(), Professeur.prenom.asc())
        .all()
    )

    # Pointages existants pour cette date rattachés à l'année active
    pointages_existants = (
        PointagePersonnel.query
        .filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee_active.id,
            date_pointage=date_selectionnee
        )
        .all()
    )
    pointages_par_prof = {p.professeur_id: p for p in pointages_existants}

    # Préparation des lignes pour le template
    lignes_pointage = []
    for prof in professeurs:
        pointage = pointages_par_prof.get(prof.id)
        if pointage:
            debut = pointage.heure_arrivee or "08:00"
            fin = pointage.heure_depart or "16:00"
            lignes_pointage.append({
                "professeur": prof,
                "pointage_id": pointage.id,
                "statut": pointage.statut,
                "retard_minutes": pointage.retard_minutes or 0,
                "creneau_debut": debut,
                "creneau_fin": fin,
                "creneau": pointage.creneau or f"{debut} - {fin}",
                "heures_prevues": pointage.heures_prevues or 8.0,
                "heures_effectuees": pointage.heures_effectuees or 0.0,
                "motif": pointage.motif or "",
                "valide": pointage.valide,
                "est_enregistre": True
            })
        else:
            lignes_pointage.append({
                "professeur": prof,
                "pointage_id": None,
                "statut": "non_pointe",
                "retard_minutes": 0,
                "creneau_debut": "08:00",
                "creneau_fin": "16:00",
                "creneau": "08:00 - 16:00",
                "heures_prevues": 8.0,
                "heures_effectuees": 8.0,
                "motif": "",
                "valide": False,
                "est_enregistre": False
            })

    # Statistiques du jour
    total_profs = len(professeurs)
    presents_count = sum(1 for p in pointages_existants if p.statut == 'present')
    retards_count = sum(1 for p in pointages_existants if p.statut == 'retard')
    absents_injustifies_count = sum(1 for p in pointages_existants if p.statut == 'absent_injustifie')
    absents_justifies_count = sum(1 for p in pointages_existants if p.statut == 'absent_justifie')
    total_absents_count = absents_injustifies_count + absents_justifies_count
    non_pointes_count = max(0, total_profs - len(pointages_existants))
    journee_validee = len(pointages_existants) > 0 and all(p.valide for p in pointages_existants)

    # Onglet 2 : Les journées antérieures non scellées (dans l'année scolaire active)
    debut_recherche = max(annee_active.date_debut, date.today() - timedelta(days=45)) if annee_active.date_debut else (date.today() - timedelta(days=45))
    journees_non_scellees_query = (
        db.session.query(
            PointagePersonnel.date_pointage,
            db.func.count(PointagePersonnel.id).label('total'),
            db.func.sum(db.case((PointagePersonnel.valide == True, 1), else_=0)).label('valides')
        )
        .filter(
            PointagePersonnel.ecole_id == ecole_id,
            PointagePersonnel.annee_scolaire_id == annee_active.id,
            PointagePersonnel.date_pointage >= debut_recherche,
            PointagePersonnel.date_pointage <= date.today()
        )
        .group_by(PointagePersonnel.date_pointage)
        .order_by(PointagePersonnel.date_pointage.desc())
        .limit(20)
        .all()
    )

    journees_a_valider = []
    for row in journees_non_scellees_query:
        total = row.total or 0
        valides = row.valides or 0
        if valides < total:
            journees_a_valider.append({
                "date": row.date_pointage,
                "date_str": row.date_pointage.strftime("%Y-%m-%d"),
                "date_formatee": row.date_pointage.strftime("%d/%m/%Y"),
                "total_pointes": total,
                "restants_a_sceller": total - valides
            })

    # Navigation dates
    date_hier = (date_selectionnee - timedelta(days=1)).strftime("%Y-%m-%d")
    date_demain = (date_selectionnee + timedelta(days=1)).strftime("%Y-%m-%d")
    date_aujourdhui = date.today().strftime("%Y-%m-%d")

    # Mois de l'année scolaire active pour l'onglet rapport
    mois_annee_options = _generer_mois_annee_scolaire(annee_active)
    mois_selectionne_valeur = f"{date_selectionnee.year}-{date_selectionnee.month:02d}"

    return render_template(
        'pointage_personnel.html',
        annee_active=annee_active,
        date_selectionnee=date_selectionnee,
        date_str=date_selectionnee.strftime("%Y-%m-%d"),
        date_hier=date_hier,
        date_demain=date_demain,
        date_aujourdhui=date_aujourdhui,
        lignes_pointage=lignes_pointage,
        total_profs=total_profs,
        presents_count=presents_count,
        retards_count=retards_count,
        absents_injustifies_count=absents_injustifies_count,
        absents_justifies_count=absents_justifies_count,
        total_absents_count=total_absents_count,
        non_pointes_count=non_pointes_count,
        journee_validee=journee_validee,
        journees_a_valider=journees_a_valider[:15],
        mois_annee_options=mois_annee_options,
        mois_selectionne_valeur=mois_selectionne_valeur
    )


@pointage_personnel_bp.route('/marquer-tous-presents', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def marquer_tous_presents():
    """Action de masse AJAX : marque tous les professeurs actifs en 'présent' pour l'année active."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    if not annee_active or annee_active.statut != 'active':
        return jsonify({"success": False, "error": "Le pointage exige une année scolaire active."}), 409

    data = request.get_json() or {}
    date_str = data.get('date', '').strip()
    creneau_debut = data.get('creneau_debut', '08:00').strip() or '08:00'
    creneau_fin = data.get('creneau_fin', '16:00').strip() or '16:00'

    try:
        date_cible = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    except ValueError:
        return jsonify({"success": False, "error": "Format de date invalide."}), 400

    duree_heures = _calculer_duree_heures(creneau_debut, creneau_fin)
    if duree_heures is None:
        return jsonify({"success": False, "error": "Créneau horaire invalide : l'heure de fin doit suivre le début."}), 400
    if not (annee_active.date_debut <= date_cible <= annee_active.date_fin):
        return jsonify({"success": False, "error": "Date hors de l'année scolaire active."}), 400
    creneau_libelle = f"{creneau_debut} - {creneau_fin}"

    professeurs = Professeur.query.filter_by(ecole_id=ecole_id).all()
    if not professeurs:
        return jsonify({"success": False, "error": "Aucun professeur trouvé pour cette école."}), 404

    pointages_existants = {
        p.professeur_id: p
        for p in PointagePersonnel.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee_active.id,
            date_pointage=date_cible
        ).all()
    }
    if any(pointage.valide for pointage in pointages_existants.values()):
        return jsonify({"success": False, "error": "La journée contient déjà des pointages validés."}), 409

    count_modifies = 0
    for prof in professeurs:
        p = pointages_existants.get(prof.id)
        if not p:
            p = PointagePersonnel(
                professeur_id=prof.id,
                ecole_id=ecole_id,
                annee_scolaire_id=annee_active.id,
                date_pointage=date_cible,
                statut='present',
                retard_minutes=0,
                creneau=creneau_libelle,
                heure_arrivee=creneau_debut,
                heure_depart=creneau_fin,
                heures_prevues=duree_heures,
                heures_effectuees=duree_heures,
                pointe_par_id=current_user.id,
                valide=False
            )
            db.session.add(p)
            count_modifies += 1
        elif not p.valide and p.statut != 'present':
            p.statut = 'present'
            p.retard_minutes = 0
            p.creneau = creneau_libelle
            p.heure_arrivee = creneau_debut
            p.heure_depart = creneau_fin
            p.heures_prevues = duree_heures
            p.heures_effectuees = duree_heures
            p.pointe_par_id = current_user.id
            count_modifies += 1

    db.session.commit()
    return jsonify({
        "success": True,
        "message": f"{len(professeurs)} professeurs ont été marqués présents.",
        "count_modifies": count_modifies,
        "heures_effectuees": duree_heures
    })


@pointage_personnel_bp.route('/sauvegarder-ligne', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def sauvegarder_ligne():
    """Enregistrement instantané (AJAX) d'un pointage pour un enseignant."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    if not annee_active or annee_active.statut != 'active':
        return jsonify({"success": False, "error": "Le pointage exige une année scolaire active."}), 409

    data = request.get_json() or {}
    professeur_id = data.get('professeur_id')
    date_str = (data.get('date') or '').strip()
    statut = (data.get('statut') or '').strip().lower()
    retard_minutes = data.get('retard_minutes', 0)
    creneau_debut = (data.get('creneau_debut') or '08:00').strip()
    creneau_fin = (data.get('creneau_fin') or '16:00').strip()
    motif = (data.get('motif') or '').strip()

    if not professeur_id or not date_str or not statut:
        return jsonify({"success": False, "error": "Champs requis manquants."}), 400

    # VALIDATION STRICTE (JAMAIS DE CONGÉ DANS LE SYSTÈME)
    if statut not in STATUTS_AUTORISES:
        return jsonify({
            "success": False,
            "error": f"Statut non autorisé : '{statut}'. Statuts valides : {', '.join(STATUTS_AUTORISES)}."
        }), 400

    try:
        date_cible = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"success": False, "error": "Format de date invalide."}), 400
    if not (annee_active.date_debut <= date_cible <= annee_active.date_fin):
        return jsonify({"success": False, "error": "Date hors de l'année scolaire active."}), 400

    prof = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
    if not prof:
        return jsonify({"success": False, "error": "Professeur introuvable."}), 404

    duree_creneau = _calculer_duree_heures(creneau_debut, creneau_fin)
    if duree_creneau is None:
        return jsonify({"success": False, "error": "Créneau horaire invalide : l'heure de fin doit suivre le début."}), 400
    creneau_label = f"{creneau_debut} - {creneau_fin}"

    try:
        retard_min = max(0, int(retard_minutes or 0))
    except (ValueError, TypeError):
        retard_min = 0

    if statut in ('absent_injustifie', 'absent_justifie'):
        heures_effectuees = 0.0
        retard_min = 0
    elif statut == 'retard':
        deduction_h = round(retard_min / 60.0, 2)
        heures_effectuees = max(0.0, round(duree_creneau - deduction_h, 2))
    else:  # 'present'
        retard_min = 0
        heures_effectuees = duree_creneau

    pointage = PointagePersonnel.query.filter_by(
        professeur_id=prof.id,
        ecole_id=ecole_id,
        annee_scolaire_id=annee_active.id,
        date_pointage=date_cible
    ).first()
    if pointage and pointage.valide:
        return jsonify({"success": False, "error": "Ce pointage validé ne peut plus être modifié."}), 409

    if not pointage:
        pointage = PointagePersonnel(
            professeur_id=prof.id,
            ecole_id=ecole_id,
            annee_scolaire_id=annee_active.id,
            date_pointage=date_cible,
            statut=statut,
            retard_minutes=retard_min,
            creneau=creneau_label,
            heure_arrivee=creneau_debut,
            heure_depart=creneau_fin,
            heures_prevues=duree_creneau,
            heures_effectuees=heures_effectuees,
            motif=motif,
            pointe_par_id=current_user.id,
            valide=False
        )
        db.session.add(pointage)
    else:
        pointage.statut = statut
        pointage.retard_minutes = retard_min
        pointage.creneau = creneau_label
        pointage.heure_arrivee = creneau_debut
        pointage.heure_depart = creneau_fin
        pointage.heures_prevues = duree_creneau
        pointage.heures_effectuees = heures_effectuees
        pointage.motif = motif
        pointage.pointe_par_id = current_user.id

    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Pointage de {prof.prenom} {prof.nom} enregistré.",
        "pointage": pointage.to_dict(),
        "heures_effectuees": heures_effectuees,
        "statut": statut,
        "retard_minutes": retard_min
    })


@pointage_personnel_bp.route('/valider-journee', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def valider_journee():
    """Verrouille tous les pointages de la date sélectionnée pour l'année active."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    if not annee_active or annee_active.statut != 'active':
        return jsonify({"success": False, "error": "La validation exige une année scolaire active."}), 409

    if request.is_json:
        data = request.get_json() or {}
        date_str = (data.get('date') or '').strip()
    else:
        date_str = (request.form.get('date') or '').strip()

    try:
        date_cible = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    except ValueError:
        date_cible = date.today()

    pointages = PointagePersonnel.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_active.id,
        date_pointage=date_cible
    ).all()

    if not pointages:
        msg = f"Aucun pointage enregistré pour le {date_cible.strftime('%d/%m/%Y')}."
        if request.is_json:
            return jsonify({"success": False, "error": msg}), 404
        flash(msg, "warning")
        return redirect(url_for('pointage_personnel.index', date=date_cible.strftime("%Y-%m-%d")))

    maintenant = datetime.utcnow()
    for p in pointages:
        p.valide = True
        p.valide_par_user_id = current_user.id
        p.date_validation = maintenant

    db.session.commit()

    succes_msg = f"La journée du {date_cible.strftime('%d/%m/%Y')} a été validée avec succès ({len(pointages)} pointages scellés)."
    if request.is_json:
        return jsonify({"success": True, "message": succes_msg})
    flash(succes_msg, "success")
    return redirect(url_for('pointage_personnel.index', date=date_cible.strftime("%Y-%m-%d")))


@pointage_personnel_bp.route('/rapport-mensuel', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
@tenant_required
def rapport_mensuel():
    """Retourne la synthèse mensuelle des pointages par professeur pour l'année active."""
    ecole_id = g.ecole_id
    annee_active = get_annee_consultee(ecole_id) or get_annee_active(ecole_id)
    if not annee_active:
        return jsonify({"success": False, "error": "Aucune année scolaire active."}), 400

    mois = request.args.get('mois', type=int) or date.today().month
    annee_cal = request.args.get('annee', type=int) or date.today().year

    # Si passé sous forme 'periode=YYYY-MM'
    periode = request.args.get('periode', '').strip()
    if periode and '-' in periode:
        try:
            parts = periode.split('-')
            annee_cal = int(parts[0])
            mois = int(parts[1])
        except Exception:
            pass

    professeurs = Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.nom.asc(), Professeur.prenom.asc()).all()

    debut_mois = date(annee_cal, mois, 1)
    if mois == 12:
        fin_mois = date(annee_cal + 1, 1, 1) - timedelta(days=1)
    else:
        fin_mois = date(annee_cal, mois + 1, 1) - timedelta(days=1)

    pointages = (
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
    for p in pointages:
        pointages_par_prof.setdefault(p.professeur_id, []).append(p)

    rapport_profs = []
    total_ecole_heures = 0.0
    total_ecole_retards_min = 0
    total_ecole_absences_injustifiees = 0
    total_ecole_absences_justifiees = 0
    total_pointages_presences = 0
    total_pointages_possibles = 0

    for prof in professeurs:
        profs_pointages = pointages_par_prof.get(prof.id, [])
        jours_travailles = sum(1 for p in profs_pointages if p.statut in ('present', 'retard'))
        heures_totales = sum(p.heures_effectuees for p in profs_pointages)
        retards_minutes = sum(p.retard_minutes for p in profs_pointages if p.statut == 'retard')
        absences_injustifiees = sum(1 for p in profs_pointages if p.statut == 'absent_injustifie')
        absences_justifiees = sum(1 for p in profs_pointages if p.statut == 'absent_justifie')

        total_ecole_heures += heures_totales
        total_ecole_retards_min += retards_minutes
        total_ecole_absences_injustifiees += absences_injustifiees
        total_ecole_absences_justifiees += absences_justifiees
        total_pointages_presences += jours_travailles
        total_pointages_possibles += len(profs_pointages)

        rapport_profs.append({
            "professeur_id": prof.id,
            "nom_complet": f"{prof.prenom} {prof.nom}",
            "specialite": prof.specialite or prof.matieres_enseignees or "Enseignant",
            "jours_travailles": jours_travailles,
            "heures_totales": round(heures_totales, 2),
            "retards_minutes": retards_minutes,
            "absences_injustifiees": absences_injustifiees,
            "absences_justifiees": absences_justifiees
        })

    taux_presence = (
        round((total_pointages_presences / total_pointages_possibles) * 100, 1)
        if total_pointages_possibles > 0 else 100.0
    )

    nom_mois = NOMS_MOIS_FR[mois - 1]
    result = {
        "success": True,
        "mois": mois,
        "annee": annee_cal,
        "annee_scolaire_nom": annee_active.nom,
        "periode_nom": f"{nom_mois} {annee_cal}",
        "kpis": {
            "total_heures": round(total_ecole_heures, 2),
            "taux_presence": taux_presence,
            "total_retards_minutes": total_ecole_retards_min,
            "total_absences_injustifiees": total_ecole_absences_injustifiees,
            "total_absences_justifiees": total_ecole_absences_justifiees
        },
        "professeurs": rapport_profs
    }

    return jsonify(result)
