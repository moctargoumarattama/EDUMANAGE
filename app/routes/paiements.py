from . import main
from .common import (
    Classe,
    Eleve,
    Inscription,
    Paiement,
    PaiementForm,
    ajouter_ecole_id,
    aliased,
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
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    send_file,
    url_for,
)
from flask import jsonify
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from app.services.annees_scolaires import get_annee_consultee
from app.services.structure_annuelle import get_niveaux_annee
from app.services.paiements_annuels import (
    get_inscriptions_paiements,
    get_finances_inscription,
    get_paiements_annee,
    enregistrer_paiement,
    supprimer_paiement_securise,
)


@main.route('/paiements', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def paiements():
    annee = get_annee_consultee(current_user.ecole_id)
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

    # --- TRAITEMENT DU POST (ENCAISSEMENT) ---
    if form.validate_on_submit():
        if annee.statut == 'archivee':
            flash("L'année scolaire est archivée : les paiements sont en lecture seule stricte.", "danger")
            return redirect(url_for('main.paiements'))
        if annee.statut == 'planifiee':
            flash("Les paiements pourront être enregistrés lorsque cette année sera active.", "warning")
            return redirect(url_for('main.paiements'))
        if annee.statut != 'active':
            flash("Seule l'année active autorise l'encaissement de paiements.", "danger")
            return redirect(url_for('main.paiements'))

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
            return redirect(url_for('main.paiements'))

        try:
            db.session.commit()
            flash("Paiement enregistré avec succès !", "success")
            return redirect(url_for('main.paiements'))
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'enregistrement du paiement: {e}", "danger")

    # Classes de l'année consultée
    classes = filtre_par_ecole(
        Classe.query.filter_by(annee_scolaire_id=annee.id).order_by(Classe.nom),
        Classe
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
            .order_by(Paiement.date_paiement.desc(), Paiement.id.desc())
        )
    else:
        query_paiements = Paiement.query.filter(Paiement.id == -1)

    paiements_pagination = query_paiements.paginate(
        page=page_paiements, per_page=per_page_paiements, error_out=False
    )

    all_eleves = [ins.eleve for ins in inscriptions_filtrees if ins.eleve]
    niveaux = get_niveaux_annee(current_user.ecole_id, annee.id) if annee else []

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
    paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

    if current_user.role == 'parent' and not check_parent_access(paiement.eleve_id):
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    return render_template('recu_paiement.html', paiement=paiement, now=datetime.now())


@main.route('/paiement/<int:id>/pdf')
@main.route('/paiements/<int:id>/pdf')
@login_required
@role_required('admin', 'parent')
def generer_recu_pdf(id):
    paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

    if current_user.role == 'parent' and not check_parent_access(paiement.eleve_id):
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    eleve = paiement.eleve
    # --- Infos école dynamiques ---
    if eleve and eleve.ecole:
        ecole = eleve.ecole
        nom_ecole = ecole.nom
        adresse_ecole = ecole.adresse or ""
        contact_ecole = f"Tél: {ecole.telephone or '-'}"
    else:
        nom_ecole = "ÉCOLE INCONNUE"
        adresse_ecole = "Non renseignée"
        contact_ecole = "-"

    # Classe et année scolaire historiques depuis Inscription
    classe_nom = "Sans classe"
    annee_scolaire_nom = ""
    if paiement.inscription:
        if paiement.inscription.classe:
            classe_nom = paiement.inscription.classe.nom
        if paiement.inscription.annee_scolaire:
            annee_scolaire_nom = paiement.inscription.annee_scolaire.nom
    elif eleve and eleve.classe:
        classe_nom = eleve.classe.nom

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # --- En-tête ---
    p.setFont("Helvetica-Bold", 16)
    p.drawString(100, height - 100, nom_ecole)
    p.setFont("Helvetica", 12)
    p.drawString(100, height - 120, adresse_ecole)
    p.drawString(100, height - 140, contact_ecole)

    p.setFont("Helvetica-Bold", 14)
    p.drawString(100, height - 180, "REÇU DE PAIEMENT")
    p.line(100, height - 185, 300, height - 185)

    # --- Infos paiement ---
    y = height - 220
    p.setFont("Helvetica", 12)
    p.drawString(100, y, f"Référence: {paiement.id:06d}")
    y -= 25
    p.drawString(100, y, f"Date: {paiement.date_paiement.strftime('%d/%m/%Y %H:%M') if paiement.date_paiement else '-'}")
    if annee_scolaire_nom:
        y -= 25
        p.drawString(100, y, f"Année scolaire: {annee_scolaire_nom}")
    y -= 25
    p.drawString(100, y, f"Élève: {eleve.prenom if eleve else ''} {eleve.nom if eleve else ''}")
    y -= 25
    p.drawString(100, y, f"Classe: {classe_nom}")
    y -= 25
    p.drawString(100, y, f"Mois payé: {paiement.mois} {paiement.annee}")
    y -= 25
    p.drawString(100, y, f"Montant: {paiement.montant:,.0f} FCFA")
    y -= 25
    p.drawString(100, y, f"Mode de paiement: {paiement.mode_paiement}")
    if paiement.reference:
        y -= 25
        p.drawString(100, y, f"Référence: {paiement.reference}")

    # --- Signature & cachet ---
    p.line(50, 120, 250, 120)
    p.drawString(70, 100, "Signature du Caissier")

    p.line(300, 120, 500, 120)
    p.drawString(320, 100, "Signature du Parent")

    p.drawString(100, 60, "Cachet de l'Établissement")
    p.drawString(100, 40, f"Édition du: {datetime.utcnow().strftime('%d/%m/%Y %H:%M')}")

    p.showPage()
    p.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"reçu_paiement_{paiement.id}.pdf",
        mimetype='application/pdf'
    )


@main.route('/paiements/export_excel')
@main.route('/paiements/export/excel')
@login_required
@role_required('admin')
def export_paiements_excel():
    annee = get_annee_consultee(current_user.ecole_id)
    if not annee:
        flash("Aucune année scolaire configurée.", "warning")
        return redirect(url_for('main.paiements'))

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
            p.inscription.classe.nom if (p.inscription and p.inscription.classe)
            else (p.eleve.classe.nom if (p.eleve and p.eleve.classe) else 'Sans classe')
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
@login_required
@role_required('admin')
def supprimer_paiement(id):
    annee = get_annee_consultee(current_user.ecole_id)
    if not annee or annee.statut == 'archivee':
        flash("L'année scolaire est archivée : suppression de paiement interdite (lecture seule).", "danger")
        return redirect(url_for('main.paiements'))
    if annee.statut == 'planifiee':
        flash("Opération non autorisée sur une année planifiée.", "danger")
        return redirect(url_for('main.paiements'))

    paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

    if paiement.inscription and paiement.inscription.annee_scolaire_id != annee.id:
        flash("Ce paiement n'appartient pas à l'année scolaire consultée.", "danger")
        return redirect(url_for('main.paiements'))

    try:
        ancienne_valeur = f"Paiement ID {paiement.id} (Élève: {paiement.eleve_id}, Montant: {paiement.montant})"
        db.session.delete(paiement)
        db.session.commit()

        current_app.log_correction(
            action="suppression_paiement",
            description=f"Paiement supprimé ID {paiement.id}",
            ecole_id=paiement.ecole_id,
            cible_type="paiement",
            cible_id=id,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=None,
            niveau="info"
        )
        flash("Paiement supprimé avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression paiement {id}: {e}")
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")

    return redirect(url_for('main.paiements'))
