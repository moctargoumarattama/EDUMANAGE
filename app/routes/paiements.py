from . import main
from .common import (
    Classe,
    Eleve,
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
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    send_file,
    url_for,
)
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfgen import canvas
import pandas as pd


@main.route('/paiements', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def paiements():
    form = PaiementForm()
    page_eleves = request.args.get('page', 1, type=int)
    page_paiements = request.args.get('page_paiements', 1, type=int)
    per_page_eleves = 50
    per_page_paiements = 20
    classe_id = request.args.get('classe', type=int)
    recherche = request.args.get('recherche', '', type=str)

    # --- TRAITEMENT DU POST ---
    if form.validate_on_submit():
        try:
            eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=form.eleve_id.data).first()
            if not eleve:
                flash("Eleve invalide pour votre ecole.", "danger")
                return redirect(url_for('main.paiements'))
            paiement = Paiement(
                eleve_id=form.eleve_id.data,
                montant=form.montant.data,
                mois=form.mois.data,
                annee=form.annee.data,
                mode_paiement=form.mode_paiement.data,
                reference=form.reference.data
            )
            ajouter_ecole_id(paiement)
            db.session.add(paiement)
            db.session.commit()
            flash("Paiement enregistré avec succès !", "success")
            return redirect(url_for('main.paiements'))
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'enregistrement du paiement: {e}", "danger")

    # --- QUERY DE BASE POUR LES ÉLÈVES ---
    query_base = filtre_par_ecole(Eleve.query.outerjoin(Classe), Eleve)
    if classe_id:
        query_base = query_base.filter(Eleve.classe_id == classe_id)
    if recherche:
        query_base = query_base.filter(
            (Eleve.nom.ilike(f"%{recherche}%")) | (Eleve.prenom.ilike(f"%{recherche}%"))
        )

    # --- PAGINATION DES ÉLÈVES POUR L'AFFICHAGE ---
    ClasseAlias = aliased(Classe)
    eleves_pagination = query_base.outerjoin(ClasseAlias, Eleve.classe_id == ClasseAlias.id)\
                                  .order_by(ClasseAlias.nom, Eleve.nom)\
                                  .paginate(page=page_eleves, per_page=per_page_eleves, error_out=False)
    eleves = eleves_pagination.items

    # --- STATISTIQUES RÉELLES (OPTIMISÉES) ---
    eleve_ids = [e.id for e in query_base.with_entities(Eleve.id)]
    paiements_totaux = db.session.query(
        Paiement.eleve_id,
        func.sum(Paiement.montant).label('total_paye')
    ).filter(Paiement.eleve_id.in_(eleve_ids)).group_by(Paiement.eleve_id).all()
    paiements_dict = {p.eleve_id: p.total_paye for p in paiements_totaux}

    stats = {'total_eleves': len(eleve_ids), 'complet': 0, 'partiel': 0, 'aucun': 0}
    for e in eleves:
        total_paye = paiements_dict.get(e.id, 0)
        reste = max(e.frais_annuels - total_paye, 0)
        if reste <= 0:
            stats['complet'] += 1
        elif total_paye == 0:
            stats['aucun'] += 1
        else:
            stats['partiel'] += 1

    # --- Données pour affichage paginé ---
    paiements_par_eleve = {}
    for e in eleves:
        total_paye = paiements_dict.get(e.id, 0)
        reste = max(e.frais_annuels - total_paye, 0)
        paiements_par_eleve[e.id] = {
            'total_paye': total_paye,
            'reste_a_payer': reste,
            'frais_annuels': e.frais_annuels,
            'eleve': e,
            'pourcentage_paye': round((total_paye / e.frais_annuels) * 100, 2) if e.frais_annuels else 0
        }

    # --- PAGINATION DES PAIEMENTS ---
    query_paiements = filtre_par_ecole(
        Paiement.query.order_by(Paiement.date_paiement.desc(), Paiement.annee.desc(), Paiement.mois.desc()),
        Paiement
    )
    paiements_pagination = query_paiements.paginate(
        page=page_paiements, per_page=per_page_paiements, error_out=False
    )

    # --- CLASSES ---
    classes = filtre_par_ecole(Classe.query.order_by(Classe.nom), Classe).all()

    return render_template(
        "paiements.html",
        form=form,
        paiements_pagination=paiements_pagination,
        paiements_par_eleve=paiements_par_eleve,
        eleves_pagination=eleves_pagination,
        stats=stats,
        classes=classes,
        classe_id=classe_id,
        recherche=recherche
    )

@main.route('/parent/paiements')
@login_required
@role_required('parent')
def paiements_parent():
    paiements = []
    for enfant in filtre_par_ecole(current_user.enfants, Eleve):
        paiements.extend(
            filtre_par_ecole(
                Paiement.query.filter_by(eleve_id=enfant.id)
                .order_by(Paiement.annee.desc(), Paiement.mois.desc()),
                Paiement
            ).all()
        )
    
    # Calcul du montant total
    total_amount = sum(p.montant for p in paiements) if paiements else 0

    return render_template("paiements_parent.html", paiements=paiements, total_amount=total_amount)

@main.route('/paiement/<int:id>/recu')
@login_required
@role_required('admin', 'parent')
def recu_paiement(id):
    # Récupère la query filtrée par école puis l'objet
    paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

    if current_user.role == 'parent' and not check_parent_access(paiement.eleve_id):
        flash("Accès non autorisé.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    return render_template('recu_paiement.html', paiement=paiement, now=datetime.now())

@main.route('/paiement/<int:id>/pdf')
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
    p.drawString(100, y, f"Date: {paiement.date_paiement.strftime('%d/%m/%Y %H:%M')}")
    y -= 25
    p.drawString(100, y, f"Élève: {eleve.prenom} {eleve.nom}")
    y -= 25
    p.drawString(100, y, f"Classe: {eleve.classe.nom if eleve.classe else 'Sans classe'}")
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
@login_required
@role_required('admin')
def export_paiements_excel():
    # Récupère tous les paiements filtrés par école
    paiements = filtre_par_ecole(
        Paiement.query.join(Eleve).order_by(Paiement.date_paiement.desc()), Paiement
    ).all()

    data = {
        'Date': [p.date_paiement.strftime('%d/%m/%Y') for p in paiements],
        'Élève': [f"{p.eleve.prenom} {p.eleve.nom}" for p in paiements],
        'Classe': [p.eleve.classe.nom if p.eleve.classe else 'Sans classe' for p in paiements],
        'Mois': [p.mois for p in paiements],
        'Année': [p.annee for p in paiements],
        'Montant': [p.montant for p in paiements],
        'Mode': [p.mode_paiement for p in paiements],
        'Statut': [p.statut for p in paiements],
        'Référence': [p.reference for p in paiements]
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Paiements', index=False)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="liste_paiements.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@main.route('/paiement/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_paiement(id):
    try:
        # ✅ Sécurisation multi-écoles
        paiement = filtre_par_ecole(Paiement.query, Paiement).filter_by(id=id).first_or_404()

        ancienne_valeur = f"Paiement ID {paiement.id} (Élève: {paiement.eleve_id}, Montant: {paiement.montant})"

        db.session.delete(paiement)
        db.session.commit()

        # ✅ Journalisation
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
