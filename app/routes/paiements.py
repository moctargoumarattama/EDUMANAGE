import pandas as pd
from . import main
from .common import (
    Classe,
    Eleve,
    Inscription,
    Paiement,
    PaiementForm,
    check_parent_access,
    current_app,
    current_user,
    datetime,
    db,
    filtre_par_ecole,
    flash,
    func,
    io,
    joinedload,
    limiter,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    send_file,
    url_for,
)
from flask import abort, jsonify, make_response
from sqlalchemy import or_
from app.models import JournalCorrection, Utilisateur
from app.services.annees_scolaires import get_annee_consultee
from app.utils_classes import classes_triees_pedagogique
from app.utils import sanitize_internal_url
from app.services.structure_annuelle import get_niveaux_annee
from app.services.paiements_annuels import (
    get_inscriptions_paiements,
    get_finances_inscription,
    enregistrer_paiement,
    get_mois_scolaires,
)
from app.services.payment_receipts import (
    build_payment_receipt_context,
    build_public_receipt_verification_context,
    generate_payment_receipt_pdf,
)


_PAIEMENTS_CONTEXT_ARGS = ('classe', 'classe_id', 'search', 'recherche', 'statut_solde', 'niveau', 'page_paiements')


def _paiements_context_url():
    args = {}
    for key in _PAIEMENTS_CONTEXT_ARGS:
        value = request.args.get(key)
        if value not in (None, ""):
            args[key] = value
    return url_for('main.paiements', **args)


def _paiements_return_url():
    return sanitize_internal_url(
        request.form.get('return_url') or request.args.get('return_url'),
        _paiements_context_url(),
    )


@main.route('/paiements', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def paiements():
    annee = get_annee_consultee(current_user.ecole_id)
    context_url = _paiements_return_url()
    if not annee:
        flash("Aucune année scolaire configurée pour cet établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    form = PaiementForm()
    page_eleves = request.args.get('page', 1, type=int)
    page_paiements = request.args.get('page_paiements', 1, type=int)
    per_page_eleves = 50
    per_page_paiements = 20
    classe_id = request.args.get('classe', type=int) or request.args.get('classe_id', type=int)
    recherche = (request.args.get('recherche') or request.args.get('search') or '').strip()
    statut_solde = (request.args.get('statut_solde') or request.args.get('statut') or '').strip().lower()
    reste_a_payer = request.args.get('reste_a_payer')
    niveau_param = (request.args.get('niveau') or request.args.get('niveau_id') or '').strip()

    from app.services.structure_annuelle import get_niveaux_annee
    niveaux_annee = get_niveaux_annee(current_user.ecole_id, annee.id) if annee else []

    # Récupérer toutes les inscriptions de l'année scolaire consultée
    inscriptions_annee = get_inscriptions_paiements(current_user.ecole_id, annee, current_user)

    # Remplir les choix du formulaire d'encaissement avec les élèves inscrits dans l'année consultée
    form.eleve_id.choices = [
        (
            ins.eleve_id,
            f"{ins.eleve.prenom} {ins.eleve.nom} ({ins.classe.nom if ins.classe else 'Sans classe'})"
        )
        for ins in inscriptions_annee if ins.eleve
    ]
    mois_list = get_mois_scolaires(annee)
    form.mois.choices = [(m, m) for m in mois_list]

    # --- TRAITEMENT DU POST (ENCAISSEMENT) ---
    if form.validate_on_submit():
        if annee.statut == 'archivee':
            flash("L'année scolaire est archivée : les paiements sont en lecture seule stricte.", "danger")
            return redirect(context_url)
        if annee.statut == 'planifiee':
            flash("Les paiements pourront être enregistrés lorsque cette année sera active.", "warning")
            return redirect(context_url)
        if annee.statut != 'active':
            flash("Seule l'année active autorise l'encaissement de paiements.", "danger")
            return redirect(context_url)

        paiement, error = enregistrer_paiement(
            ecole_id=current_user.ecole_id,
            annee=annee,
            user=current_user,
            eleve_id=form.eleve_id.data,
            montant=form.montant.data,
            mois=form.mois.data,
            annee_civile=form.annee.data,
            mode_paiement=form.mode_paiement.data,
            reference=form.reference.data
        )
        if error:
            flash(error, "danger")
            return redirect(context_url)

        try:
            db.session.commit()
            flash("Paiement enregistré avec succès !", "success")
            return redirect(context_url)
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'enregistrement du paiement: {e}", "danger")

    # Classes de l'année consultée
    classes = classes_triees_pedagogique(
        filtre_par_ecole(
            Classe.query.filter_by(annee_scolaire_id=annee.id),
            Classe
        )
    ).all()

    # Inscriptions filtrées par classe, niveau, statut financier ou recherche
    inscriptions_filtrees = []
    for ins in inscriptions_annee:
        fin = get_finances_inscription(ins)
        if classe_id and ins.classe_id != classe_id:
            continue
        if niveau_param:
            cl = ins.classe
            if not cl:
                continue
            if str(niveau_param).isdigit():
                if getattr(cl, 'niveau_id', None) != int(niveau_param) and str(cl.niveau) != str(niveau_param):
                    continue
            elif str(cl.niveau or '').strip().lower() != niveau_param.lower():
                continue
        if recherche:
            r_lower = recherche.lower()
            nom_eleve = f"{ins.eleve.prenom} {ins.eleve.nom}".lower() if ins.eleve else ""
            matricule = (ins.eleve.code_parent or "").lower() if ins.eleve else ""
            refs_paiements = " ".join((p.reference or "").lower() for p in (ins.paiements or []))
            if r_lower not in nom_eleve and r_lower not in matricule and r_lower not in refs_paiements:
                continue
        if statut_solde:
            if statut_solde == 'complet' and fin['statut_solde'] != 'complet':
                continue
            elif statut_solde == 'partiel' and fin['statut_solde'] != 'partiel':
                continue
            elif statut_solde == 'aucun' and fin['statut_solde'] != 'aucun':
                continue
            elif statut_solde in ('reste_a_payer', 'impaye') and fin['reste_a_payer'] <= 0:
                continue
        if reste_a_payer and str(reste_a_payer).lower() in ('1', 'true', 'yes', 'on') and fin['reste_a_payer'] <= 0:
            continue

        inscriptions_filtrees.append(ins)

    # Données enrichies par élève / inscription
    paiements_par_eleve = {}
    eleves_par_classe = {c.id: [] for c in classes}
    eleves_sans_classe = []

    total_frais = 0.0
    total_recouvre = 0.0
    stats = {
        'total_eleves': len(inscriptions_annee),
        'complet': 0,
        'partiel': 0,
        'aucun': 0,
        'total_frais': 0.0,
        'total_recouvre': 0.0,
        'total_reste': 0.0,
        'taux_recouvrement': 0.0
    }

    # Calcul global des statistiques sur toutes les inscriptions de l'année consultée
    for ins in inscriptions_annee:
        fin = get_finances_inscription(ins)
        total_frais += fin['frais_annuels']
        total_recouvre += fin['total_paye']
        if fin['statut_solde'] == 'complet':
            stats['complet'] += 1
        elif fin['statut_solde'] == 'partiel':
            stats['partiel'] += 1
        else:
            stats['aucun'] += 1

    stats['total_frais'] = total_frais
    stats['total_recouvre'] = total_recouvre
    stats['total_reste'] = max(0.0, total_frais - total_recouvre)
    stats['taux_recouvrement'] = round((total_recouvre / total_frais) * 100, 1) if total_frais > 0 else 0.0

    # Données par élève filtré
    for ins in inscriptions_filtrees:
        fin = get_finances_inscription(ins)
        e = ins.eleve
        if not e:
            continue
        # Classe historique de l'année consultée
        e.annee_classe = ins.classe
        e.annee_paiements = ins.paiements
        paiements_par_eleve[e.id] = {
            'total_paye': fin['total_paye'],
            'reste_a_payer': fin['reste_a_payer'],
            'frais_annuels': fin['frais_annuels'],
            'eleve': e,
            'inscription': ins,
            'pourcentage_paye': fin['pourcentage_paye']
        }
        if ins.classe_id and ins.classe_id in eleves_par_classe:
            eleves_par_classe[ins.classe_id].append(e)
        else:
            eleves_sans_classe.append(e)

    # Statistiques par classe pour l'année consultée
    classe_finances = {}
    for c in classes:
        c_eleves = eleves_par_classe.get(c.id, [])
        c_frais = sum(paiements_par_eleve[e.id]['frais_annuels'] for e in c_eleves if e.id in paiements_par_eleve)
        c_paye = sum(paiements_par_eleve[e.id]['total_paye'] for e in c_eleves if e.id in paiements_par_eleve)
        c_reste = max(0.0, c_frais - c_paye)
        c_taux = round((c_paye / c_frais) * 100, 1) if c_frais > 0 else 0.0
        classe_finances[c.id] = {
            'eleves_count': len(c_eleves),
            'total_frais': c_frais,
            'total_paye': c_paye,
            'reste_a_payer': c_reste,
            'taux_recouvrement': c_taux
        }

    # Pagination des paiements pour l'année consultée
    ins_ids = [ins.id for ins in inscriptions_annee]
    if ins_ids:
        query_paiements = (
            Paiement.query.filter(
                Paiement.ecole_id == current_user.ecole_id,
                Paiement.inscription_id.in_(ins_ids)
            )
            .options(
                joinedload(Paiement.inscription).joinedload(Inscription.eleve),
                joinedload(Paiement.inscription).joinedload(Inscription.classe)
            )
            .order_by(Paiement.date_paiement.desc(), Paiement.id.desc())
        )
    else:
        query_paiements = Paiement.query.filter(Paiement.id == -1)

    paiements_pagination = query_paiements.paginate(
        page=page_paiements, per_page=per_page_paiements, error_out=False
    )

    all_eleves = [ins.eleve for ins in inscriptions_filtrees if ins.eleve]
    niveaux = niveaux_annee

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'count': len(inscriptions_filtrees),
            'inscriptions': [
                {
                    'id': ins.id,
                    'eleve_id': ins.eleve_id,
                    'eleve_nom': f"{ins.eleve.prenom} {ins.eleve.nom}" if ins.eleve else "",
                    'classe_id': ins.classe_id,
                    'classe_nom': ins.classe.nom if ins.classe else "",
                    'frais_annuels': paiements_par_eleve.get(ins.eleve_id, {}).get('frais_annuels', 0),
                    'total_paye': paiements_par_eleve.get(ins.eleve_id, {}).get('total_paye', 0),
                    'reste_a_payer': paiements_par_eleve.get(ins.eleve_id, {}).get('reste_a_payer', 0),
                    'statut_solde': 'complet' if paiements_par_eleve.get(ins.eleve_id, {}).get('reste_a_payer', 0) <= 0 else ('partiel' if paiements_par_eleve.get(ins.eleve_id, {}).get('total_paye', 0) > 0 else 'aucun')
                }
                for ins in inscriptions_filtrees if ins.eleve
            ],
            'stats': stats
        })

    return render_template(
        "paiements.html",
        form=form,
        paiements_pagination=paiements_pagination,
        paiements_par_eleve=paiements_par_eleve,
        eleves_pagination=None,
        all_eleves=all_eleves,
        eleves=all_eleves,
        eleves_par_classe=eleves_par_classe,
        eleves_sans_classe=eleves_sans_classe,
        classe_finances=classe_finances,
        stats=stats,
        classes=classes,
        niveaux=niveaux,
        classe_id=classe_id,
        recherche=recherche,
        annee_consultee=annee,
        return_url=context_url,
    )


@main.route('/parent/paiements')
@login_required
@role_required('parent')
def paiements_parent():
    """Route obsolète, redirige vers parent_dashboard car les paiements sont affichés dans voir_eleve"""
    return redirect(url_for('main.parent_dashboard'))



@main.route('/paiement/<int:id>/recu')
@main.route('/paiements/<int:id>')
@main.route('/paiements/<int:id>/recu')
@login_required
@role_required('admin', 'parent')
def recu_paiement(id):
    paiement = (
        filtre_par_ecole(Paiement.query, Paiement)
        .options(
            joinedload(Paiement.eleve),
            joinedload(Paiement.inscription).joinedload(Inscription.classe),
            joinedload(Paiement.inscription).joinedload(Inscription.annee_scolaire),
            joinedload(Paiement.inscription).joinedload(Inscription.ecole),
        )
        .filter_by(id=id)
        .first_or_404()
    )

    if current_user.role == 'parent' and not check_parent_access(paiement.eleve_id):
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    context = build_payment_receipt_context(paiement)
    db.session.commit()
    response = make_response(render_template('recu_paiement.html', **context, now=datetime.now()))
    response.headers["Cache-Control"] = "no-store, private, must-revalidate"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@main.route('/paiement/<int:id>/pdf')
@main.route('/paiements/<int:id>/pdf')
@login_required
@role_required('admin', 'parent')
def generer_recu_pdf(id):
    paiement = (
        filtre_par_ecole(Paiement.query, Paiement)
        .options(
            joinedload(Paiement.eleve),
            joinedload(Paiement.inscription).joinedload(Inscription.classe),
            joinedload(Paiement.inscription).joinedload(Inscription.annee_scolaire),
            joinedload(Paiement.inscription).joinedload(Inscription.ecole),
        )
        .filter_by(id=id)
        .first_or_404()
    )

    if current_user.role == 'parent' and not check_parent_access(paiement.eleve_id):
        flash("Acces non autorise.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    context = build_payment_receipt_context(paiement)
    db.session.commit()
    buffer = generate_payment_receipt_pdf(context)

    response = send_file(
        buffer,
        as_attachment=True,
        download_name=f"recu-paiement-{context['numero']}.pdf",
        mimetype='application/pdf'
    )
    response.headers["Cache-Control"] = "no-store, private, must-revalidate"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@main.route('/verifier/recu/<token>')
@main.route('/verifier-recu/<token>')
@limiter.limit("30 per minute")
def verifier_recu_public(token):
    paiement = Paiement.query.filter_by(verification_token=token).first()
    if not paiement:
        response = make_response(render_template(
            'verifier_recu.html',
            valide=False,
            message_erreur="Recu introuvable ou non valide."
        ), 404)
        response.headers["Cache-Control"] = "no-store, private, must-revalidate"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    context = build_public_receipt_verification_context(paiement)
    response = make_response(render_template('verifier_recu.html', **context), 200)
    response.headers["Cache-Control"] = "no-store, private, must-revalidate"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@main.route('/paiements/export_excel')
@main.route('/paiements/export/excel')
@login_required
@role_required('admin')
def export_paiements_excel():
    context_url = _paiements_return_url()
    annee = get_annee_consultee(current_user.ecole_id)
    if not annee:
        flash("Aucune année scolaire configurée.", "warning")
        return redirect(context_url)

    inscriptions = get_inscriptions_paiements(current_user.ecole_id, annee, current_user)
    ins_ids = [ins.id for ins in inscriptions]

    if ins_ids:
        paiements = (
            Paiement.query.options(
                joinedload(Paiement.inscription).joinedload(Inscription.classe),
                joinedload(Paiement.inscription).joinedload(Inscription.annee_scolaire),
                joinedload(Paiement.eleve),
            )
            .filter(
                Paiement.ecole_id == current_user.ecole_id,
                Paiement.inscription_id.in_(ins_ids)
            )
            .order_by(Paiement.date_paiement.desc(), Paiement.id.desc())
            .all()
        )
    else:
        paiements = []

    data = {
        'Date': [p.date_paiement.strftime('%d/%m/%Y') if p.date_paiement else '' for p in paiements],
        'Élève': [f"{p.eleve.prenom} {p.eleve.nom}" if p.eleve else '' for p in paiements],
        'Classe': [
            p.inscription.classe.nom if (p.inscription and p.inscription.classe) else 'Sans classe'
            for p in paiements
        ],
        'Année Scolaire': [
            p.inscription.annee_scolaire.nom if (p.inscription and p.inscription.annee_scolaire) else ''
            for p in paiements
        ],
        'Mois': [p.mois for p in paiements],
        'Année': [p.annee for p in paiements],
        'Montant': [p.montant for p in paiements],
        'Mode': [p.mode_paiement for p in paiements],
        'Statut': [p.statut for p in paiements],
        'Référence': [p.reference or '' for p in paiements]
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Paiements', index=False)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"liste_paiements_{annee.nom}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@main.route('/paiement/<int:id>/supprimer', methods=['POST'])
@main.route('/paiements/<int:id>/supprimer', methods=['POST'])
@main.route('/paiement/<int:id>/annuler', methods=['POST'])
@main.route('/paiements/<int:id>/annuler', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def supprimer_paiement(id):
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.is_json
        or request.accept_mimetypes.best == 'application/json'
    )
    context_url = _paiements_return_url()

    paiement = db.session.get(Paiement, id)
    if not paiement:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Paiement introuvable.'}), 404
        abort(404)

    # 🛡️ Protection multi-tenant stricte : 403 si cross-tenant
    if current_user.role != 'super_admin' and paiement.ecole_id != current_user.ecole_id:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Action non autorisée : ce paiement appartient à un autre établissement.'}), 403
        abort(403)

    annee = get_annee_consultee(current_user.ecole_id)
    if not annee or annee.statut == 'archivee':
        msg = "L'année scolaire est archivée : suppression/annulation de paiement interdite (lecture seule)."
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, "danger")
        return redirect(context_url)
    if annee.statut == 'planifiee':
        msg = "Opération non autorisée sur une année planifiée."
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, "danger")
        return redirect(context_url)

    if paiement.inscription and paiement.inscription.annee_scolaire_id != annee.id:
        msg = "Ce paiement n'appartient pas à l'année scolaire consultée."
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, "danger")
        return redirect(context_url)

    # Vérification double annulation
    if paiement.statut == 'annule':
        msg_deja = "Ce paiement a déjà été annulé."
        if is_ajax:
            return jsonify({'success': False, 'message': msg_deja}), 400
        flash(msg_deja, "warning")
        return redirect(context_url)

    # Récupération du motif
    motif = None
    if request.is_json and request.json:
        motif = request.json.get('motif')
    if not motif and request.form:
        motif = request.form.get('motif')
    motif = (motif or "").strip() or "Annulation administrative"

    try:
        ancienne_valeur = f"statut: {paiement.statut or 'payé'}, montant: {paiement.montant}"
        nouvelle_valeur = f"statut: annule, motif: {motif}"

        paiement.statut = 'annule'
        db.session.commit()

        if hasattr(current_app, "log_correction"):
            current_app.log_correction(
                action="annulation_paiement",
                description=f"Paiement #{paiement.id} de {paiement.montant} annulé. Motif: {motif}",
                ecole_id=paiement.ecole_id,
                cible_type="paiement",
                cible_id=paiement.id,
                ancienne_valeur=ancienne_valeur,
                nouvelle_valeur=nouvelle_valeur,
                niveau="info"
            )

        if is_ajax:
            return jsonify({'success': True, 'message': 'Paiement annulé avec succès'})
        flash('Le paiement a été annulé avec succès.', 'success')
        return redirect(context_url)

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur annulation paiement {id}: {e}")
        if is_ajax:
            return jsonify({'success': False, 'message': f"Erreur lors de l'annulation: {str(e)}"}), 500
        flash(f"Erreur lors de l'annulation: {str(e)}", "danger")
        return redirect(context_url)


@main.route('/paiements/configurer_mensualites', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def configurer_mensualites():
    context_url = _paiements_return_url()
    # Protection multi-tenant et rôles
    ecole_id = current_user.ecole_id
    annee = get_annee_consultee(ecole_id)
    
    if not annee:
        flash("Aucune année scolaire configurée.", "warning")
        return redirect(context_url)
        
    if annee.statut == 'archivee':
        flash("Impossible de modifier la configuration d'une année archivée.", "danger")
        return redirect(context_url)

    facturer_juillet = request.form.get('facturer_juillet') == 'on'
    
    try:
        annee.facturer_juillet = facturer_juillet
        db.session.commit()
        if facturer_juillet:
            flash(f"Mensualités mises à jour (Octobre - Juillet) pour l'année {annee.nom}.", "success")
        else:
            flash(f"Mensualités mises à jour (Octobre - Juin) pour l'année {annee.nom}.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur configuration mensualités : {e}")
        flash("Erreur lors de la sauvegarde.", "danger")

    return redirect(context_url)


@main.route('/paiements/historique', methods=['GET'])
@main.route('/paiements/tracabilite', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
def tracabilite_paiements():
    """Journal d'audit et de traçabilité des paiements et annulations en lecture seule."""
    annee = get_annee_consultee(current_user.ecole_id)
    if not annee:
        flash("Aucune année scolaire configurée.", "warning")
        return redirect(url_for('main.gestion_annees'))

    # Backfill idempotent des paiements existants de l'année sans trace d'audit
    try:
        paiements_sans_log = (
            db.session.query(Paiement)
            .join(Inscription, Paiement.inscription_id == Inscription.id)
            .outerjoin(
                JournalCorrection,
                (JournalCorrection.cible_type == 'paiement') & (JournalCorrection.cible_id == Paiement.id)
            )
            .filter(
                Paiement.ecole_id == current_user.ecole_id,
                Inscription.annee_scolaire_id == annee.id,
                JournalCorrection.id.is_(None)
            )
            .all()
        )
        if paiements_sans_log:
            for p in paiements_sans_log:
                correction = JournalCorrection(
                    action="PAIEMENT_CREE",
                    description=f"Paiement #{p.id} de {p.montant:,.0f} FCFA ({p.mois} {p.annee})",
                    ecole_id=p.ecole_id,
                    user_id=None,
                    cible_type="paiement",
                    cible_id=p.id,
                    ancienne_valeur=None,
                    nouvelle_valeur=f"Montant: {p.montant}, Statut: {p.statut}",
                    niveau="info",
                    date=p.date_paiement or datetime.utcnow()
                )
                db.session.add(correction)
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.warning(f"Backfill tracabilite paiements: {e}")

    search = (request.args.get('search') or request.args.get('q') or '').strip()
    action_filter = (request.args.get('action') or '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 25

    query = (
        db.session.query(
            JournalCorrection,
            Paiement,
            Eleve,
            Classe,
            Utilisateur
        )
        .join(Paiement, JournalCorrection.cible_id == Paiement.id)
        .join(Inscription, Paiement.inscription_id == Inscription.id)
        .join(Eleve, Paiement.eleve_id == Eleve.id)
        .outerjoin(Classe, Inscription.classe_id == Classe.id)
        .outerjoin(Utilisateur, JournalCorrection.user_id == Utilisateur.id)
        .filter(
            JournalCorrection.ecole_id == current_user.ecole_id,
            JournalCorrection.cible_type == 'paiement',
            Inscription.annee_scolaire_id == annee.id
        )
    )

    if action_filter == 'creation':
        query = query.filter(JournalCorrection.action.in_(['PAIEMENT_CREE', 'paiement_cree', 'creation_paiement']))
    elif action_filter == 'annulation':
        query = query.filter(JournalCorrection.action.in_(['annulation_paiement', 'annule', 'PAIEMENT_ANNULE']))

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Eleve.nom.ilike(search_pattern),
                Eleve.prenom.ilike(search_pattern),
                Paiement.reference.ilike(search_pattern),
                Paiement.mois.ilike(search_pattern),
                JournalCorrection.description.ilike(search_pattern),
                JournalCorrection.nouvelle_valeur.ilike(search_pattern),
                Utilisateur.nom.ilike(search_pattern),
                Utilisateur.prenom.ilike(search_pattern),
                Utilisateur.email.ilike(search_pattern),
            )
        )

    # Statistiques globales de l'année consultée
    base_stats_query = (
        db.session.query(JournalCorrection.action, func.count(JournalCorrection.id))
        .join(Paiement, JournalCorrection.cible_id == Paiement.id)
        .join(Inscription, Paiement.inscription_id == Inscription.id)
        .filter(
            JournalCorrection.ecole_id == current_user.ecole_id,
            JournalCorrection.cible_type == 'paiement',
            Inscription.annee_scolaire_id == annee.id
        )
        .group_by(JournalCorrection.action)
        .all()
    )
    stats_dict = {action: count for action, count in base_stats_query}
    nb_creations = stats_dict.get('PAIEMENT_CREE', 0) + stats_dict.get('paiement_cree', 0) + stats_dict.get('creation_paiement', 0)
    nb_annulations = stats_dict.get('annulation_paiement', 0) + stats_dict.get('annule', 0) + stats_dict.get('PAIEMENT_ANNULE', 0)
    total_operations = sum(stats_dict.values())

    pagination = query.order_by(JournalCorrection.date.desc(), JournalCorrection.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        'tracabilite_paiements.html',
        pagination=pagination,
        annee_consultee=annee,
        search=search,
        action_filter=action_filter,
        nb_creations=nb_creations,
        nb_annulations=nb_annulations,
        total_operations=total_operations,
    )
