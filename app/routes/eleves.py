from . import main
from .common import (
    Absence,
    AnneeScolaire,
    can_access_class,
    can_access_eleve,
    Classe,
    Eleve,
    EleveForm,
    Inscription,
    Note,
    Paiement,
    Utilisateur,
    abort,
    check_parent_access,
    current_app,
    current_user,
    datetime,
    db,
    ecole_required,
    filtre_par_ecole,
    flash,
    get_ecole_filter_query,
    io,
    joinedload,
    jsonify,
    login_required,
    parent_access_required,
    professeur_classes,
    redirect,
    render_template,
    request,
    role_required,
    send_file,
    session,
    url_for,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle, modifier_inscription_annuelle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
import pandas as pd
from app.services import check_ecole_access


@main.route('/eleves')
@login_required
@role_required('admin', 'professeur')
def eleves():
    """Gestion et liste des élèves organisée par classe avec recherche et filtres"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    classe_id = request.args.get('classe_id', type=int)
    search = (request.args.get('search') or '').strip()

    # ---------------- Base query avec relations pour éviter N+1 ----------------
    base_query = Eleve.query.options(
        db.joinedload(Eleve.classe),
        db.joinedload(Eleve.parent)
    )

    # ---------------- Filtrage multi-écoles selon rôle ----------------
    if current_user.role == 'admin':
        all_eleves_query = filtre_par_ecole(base_query, Eleve).order_by(Eleve.nom.asc(), Eleve.prenom.asc())

    elif current_user.role == 'professeur':
        professeur_id = getattr(current_user.professeur_rel, 'id', None)
        all_eleves_query = (
            base_query.join(Classe)
            .filter(
                Classe.ecole_id == current_user.ecole_id,
                db.or_(
                    Classe.professeur_id == professeur_id,
                    Classe.id.in_(
                        db.session.query(professeur_classes.c.classe_id)
                        .filter(professeur_classes.c.professeur_id == professeur_id)
                    )
                ),
                Eleve.ecole_id == current_user.ecole_id
            )
            .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        )

    else:
        abort(403)

    # Récupération de tous les élèves accessibles
    all_eleves = all_eleves_query.all()

    # Query pour la pagination rétrocompatible
    eleves_query = all_eleves_query
    if classe_id:
        eleves_query = eleves_query.filter(Eleve.classe_id == classe_id)
    if search:
        like = f"%{search}%"
        eleves_query = eleves_query.filter(db.or_(Eleve.nom.ilike(like), Eleve.prenom.ilike(like)))
    eleves_pagination = eleves_query.paginate(page=page, per_page=per_page, error_out=False)

    # Classes autorisées
    classes_query = Classe.query.filter_by(ecole_id=current_user.ecole_id)
    if current_user.role == 'professeur':
        professeur_id = getattr(current_user.professeur_rel, 'id', None)
        classes_query = classes_query.filter(
            db.or_(
                Classe.professeur_id == professeur_id,
                Classe.id.in_(
                    db.session.query(professeur_classes.c.classe_id)
                    .filter(professeur_classes.c.professeur_id == professeur_id)
                )
            )
        )
    classes = classes_query.order_by(Classe.nom.asc()).all()

    # Organisation des élèves par classe
    classes_dict = {}
    for c in classes:
        classes_dict[c.id] = {
            'classe': c,
            'id': c.id,
            'nom': c.nom,
            'niveau': getattr(c, 'niveau', '') or '',
            'salle': getattr(c, 'salle', '') or '',
            'capacite': getattr(c, 'capacite', 30) or 30,
            'eleves': [],
            'garcons_count': 0,
            'filles_count': 0
        }

    sans_classe_group = {
        'classe': None,
        'id': 'sans-classe',
        'nom': 'Élèves non assignés / Sans classe',
        'niveau': '',
        'salle': '',
        'capacite': 0,
        'eleves': [],
        'garcons_count': 0,
        'filles_count': 0
    }

    for e in all_eleves:
        cid = e.classe_id
        grp = classes_dict.get(cid, sans_classe_group)
        grp['eleves'].append(e)
        if (e.genre or '').upper() == 'F':
            grp['filles_count'] += 1
        else:
            grp['garcons_count'] += 1

    classes_eleves = list(classes_dict.values())
    if sans_classe_group['eleves']:
        classes_eleves.append(sans_classe_group)

    total_eleves = len(all_eleves)
    total_classes = len(classes)
    total_garcons = sum(1 for e in all_eleves if (e.genre or '').upper() != 'F')
    total_filles = sum(1 for e in all_eleves if (e.genre or '').upper() == 'F')
    total_sans_classe = len(sans_classe_group['eleves'])
    total_assignes = total_eleves - total_sans_classe

    stats = {
        'total_eleves': total_eleves,
        'total_classes': total_classes,
        'total_garcons': total_garcons,
        'total_filles': total_filles,
        'total_assignes': total_assignes,
        'total_sans_classe': total_sans_classe
    }

    return render_template(
        'eleves.html',
        classes=classes,
        classes_eleves=classes_eleves,
        sans_classe=sans_classe_group['eleves'],
        stats=stats,
        total_eleves=total_eleves,
        total_classes=total_classes,
        total_assignes=total_assignes,
        total_sans_classe=total_sans_classe,
        classe_id=classe_id,
        search=search,
        eleves=eleves_pagination,
        all_eleves=all_eleves
    )

@main.route('/ajouter_eleve', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_eleve():
    """Ajout d’un élève avec contrôle de cohérence, sécurité multi-écoles et notifications parent."""
    form = EleveForm()

    # ---------------- École courante ----------------
    if current_user.role == 'super_admin':
        ecole_id = session.get('ecole_id')
        if not ecole_id:
            flash("⚠️ Aucune école sélectionnée pour le super-admin.", "danger")
            return redirect(url_for('main.eleves'))
    else:
        ecole_id = current_user.ecole_id

    # ---------------- Année scolaire active ----------------
    annees_ecole = AnneeScolaire.query.filter_by(ecole_id=ecole_id).order_by(AnneeScolaire.id.desc()).all()
    annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()
    if not annee_active and annees_ecole:
        annee_active = annees_ecole[0]

    # ---------------- Classes ----------------
    classes_query = Classe.query.filter_by(ecole_id=ecole_id)
    if annee_active:
        classes_query = classes_query.filter_by(annee_scolaire_id=annee_active.id)
    classes = classes_query.order_by(Classe.nom).all()
    form.classe_id.choices = [(c.id, c.nom_complet) for c in classes]
    if not classes:
        flash("⚠️ Aucune classe disponible dans votre établissement. Un élève doit obligatoirement être inscrit dans une classe. Veuillez d'abord créer une classe.", "warning")

    # ---------------- Parents ----------------
    form.parent_id.choices = [(0, "--- Aucun parent ---")]
    parents = Utilisateur.query.filter_by(role='parent', ecole_id=ecole_id).order_by(Utilisateur.nom).all()
    form.parent_id.choices += [(p.id, f"{p.prenom or ''} {p.nom} ({p.email})") for p in parents]

    # ---------------- Soumission du formulaire ----------------
    if form.validate_on_submit():
        try:
            # 🔹 Vérif classe obligatoire et valide avec filtre multi-écoles
            if not form.classe_id.data:
                flash("❌ La sélection d'une classe est obligatoire. Un élève doit obligatoirement être inscrit dans une classe.", "danger")
                return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                                       annee_active=annee_active, classes=classes)

            classe_selectionnee = filtre_par_ecole(Classe.query, Classe).filter_by(id=form.classe_id.data).first()
            if not classe_selectionnee or classe_selectionnee.ecole_id != ecole_id:
                flash("❌ Classe invalide ou non autorisée pour cette école.", "danger")
                return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                                       annee_active=annee_active, classes=classes)

            # ---------------- Gestion parent ----------------
            parent_id_final = None
            code_parent = None
            email_parent = request.form.get("parent_email")
            telephone_parent = request.form.get("parent_telephone")
            code_parent_saisi = (request.form.get("code_parent") or "").strip()

            # 🔸 Nouveau parent
            if form.parent_id.data == 0 and any([
                request.form.get("parent_nom"),
                email_parent,
                telephone_parent,
                code_parent_saisi
            ]):
                # Vérifie doublon parent par email
                if email_parent and Utilisateur.query.filter_by(email=email_parent, role='parent', ecole_id=ecole_id).first():
                    flash("❌ Cet email est déjà utilisé par un autre parent.", "danger")
                    return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                                           annee_active=annee_active, classes=classes)

                code_parent = code_parent_saisi or Eleve.generer_code_parent()
                parent_utilisateur = Utilisateur(
                    nom=request.form.get("parent_nom"),
                    prenom=None,
                    email=email_parent,
                    telephone=telephone_parent,
                    role='parent',
                    ecole_id=ecole_id
                )
                parent_utilisateur.set_mot_de_passe(code_parent)
                db.session.add(parent_utilisateur)
                db.session.flush()  # Pour récupérer l'ID
                parent_id_final = parent_utilisateur.id

                email_parent = parent_utilisateur.email
                telephone_parent = parent_utilisateur.telephone

            else:
                # 🔸 Parent existant avec filtre multi-écoles
                parent_id_final = form.parent_id.data or None
                parent_obj = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=parent_id_final).first() if parent_id_final else None
                if parent_obj:
                    email_parent = parent_obj.email
                    telephone_parent = parent_obj.telephone
                elif parent_obj is None and parent_id_final:
                    flash("❌ Ce parent n'appartient pas à votre école.", "danger")
                    return redirect(url_for('main.ajouter_eleve'))

            # ---------------- Création élève ----------------
            nouvel_eleve = Eleve(
                nom=form.nom.data.strip(),
                prenom=form.prenom.data.strip(),
                genre=form.genre.data or 'M',
                date_naissance=form.date_naissance.data,
                lieu_naissance=form.lieu_naissance.data.strip() if form.lieu_naissance.data else None,
                adresse=form.adresse.data.strip() if form.adresse.data else None,
                
                # Suppression des champs email/téléphone élève
                contact_parent=telephone_parent,
                email_parent=email_parent.lower() if email_parent else None,
                
                classe_id=form.classe_id.data,
                frais_annuels=form.frais_annuels.data or 0.0,
                code_parent=code_parent,
                parent_id=parent_id_final,
                ecole_id=ecole_id
            )
            db.session.add(nouvel_eleve)
            db.session.flush()

            inscription, inscription_error = creer_inscription_annuelle(
                ecole_id=ecole_id,
                eleve_id=nouvel_eleve.id,
                annee_scolaire_id=classe_selectionnee.annee_scolaire_id,
                classe_id=classe_selectionnee.id,
            )
            if inscription_error:
                raise ValueError(inscription_error)

            db.session.commit()

            # ---------------- Notifications après commit ----------------
            if parent_id_final and code_parent:
                try:
                    import qrcode, io, base64
                    qr_data = f"Parent: {parent_utilisateur.prenom} {parent_utilisateur.nom}\nEmail: {email_parent}\nMot de passe: {code_parent}"
                    qr = qrcode.QRCode(
                        version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4
                    )
                    qr.add_data(qr_data)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    buffer.seek(0)
                    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

                    # Envoi email
                    if email_parent:
                        from app.notifications import envoyer_email
                        sujet = "Création de votre compte parent"
                        message = f"""
                        <html>
                        <body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:20px;">
                            <div style="max-width:600px; margin:auto; background:#fff; border-radius:10px; padding:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);">
                                <h2 style="color:#4CAF50;">Bonjour {parent_utilisateur.prenom or ''} {parent_utilisateur.nom},</h2>
                                <p>Un compte parent a été créé pour suivre la scolarité de votre enfant.</p>
                                <h3>Vos identifiants :</h3>
                                <ul>
                                    <li><b>Email :</b> {email_parent}</li>
                                    <li><b>Mot de passe :</b> {code_parent}</li>
                                </ul>
                                <p>
                                    <a href="{request.host_url}login_parent" style="display:inline-block; padding:10px 20px; background:#4CAF50; color:#fff; text-decoration:none; border-radius:5px;">Se connecter</a>
                                </p>
                                <img src="data:image/png;base64,{qr_base64}" width="150" height="150"/><br>
                                <p style="font-size:12px; color:#555;">Cordialement,<br>L’administration</p>
                            </div>
                        </body>
                        </html>
                        """
                        envoyer_email(email_parent, sujet, message)

                except Exception as e:
                    current_app.logger.error(f"Erreur QR/Email : {e}")

            flash("✅ Élève ajouté avec succès et inscrit à tous les cours de sa classe.", "success")
            return redirect(url_for('main.eleves'))

        except Exception as e:
            db.session.rollback()
            import traceback
            current_app.logger.error(f"Erreur ajout élève: {e}\n{traceback.format_exc()}")
            flash("❌ Erreur lors de l'ajout de l'élève. Veuillez vérifier les informations saisies.", "danger")

        
    # ---------------- Affichage du formulaire ----------------
    return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                           annee_active=annee_active, classes=classes)

@main.route('/api/eleves/classe/<int:classe_id>')
@login_required
@role_required('admin', 'professeur')
@ecole_required
def api_eleves_par_classe(classe_id):
    """Retourne la liste des élèves d'une classe filtrée par école et année active (JSON)"""

    ecole_id = current_user.ecole_id
    if not ecole_id:
        return jsonify({'eleves': []}), 403

    annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()

    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return jsonify({'eleves': []}), 404
    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        professeur_id = getattr(professeur, 'id', None)
        is_assigned = bool(
            professeur_id
            and (
                classe.professeur_id == professeur_id
                or db.session.query(professeur_classes).filter(
                    professeur_classes.c.professeur_id == professeur_id,
                    professeur_classes.c.classe_id == classe.id
                ).first()
            )
        )
        if not is_assigned:
            return jsonify({'eleves': []}), 403
    query = Eleve.query.filter(Eleve.classe_id == classe_id, Eleve.ecole_id == ecole_id)
    if annee_active:
        query = query.join(Classe).filter(Classe.annee_scolaire_id == annee_active.id)

    # --- Récupération des élèves ---
    eleves = query.order_by(Eleve.nom, Eleve.prenom).all()

    # --- Construction du JSON CORRIGÉ ---
    eleves_list = [
        {
            'id': e.id,
            'nom': e.nom,
            'prenom': e.prenom,
            'telephone': e.contact_parent or '-',  # ← CORRECTION ICI : utiliser contact_parent au lieu de telephone
            'classe': e.classe.nom if e.classe else "Sans classe",
            'parent': f"{e.parent.prenom} {e.parent.nom}" if e.parent else "Non assigné"
        } for e in eleves
    ]

    return jsonify({'eleves': eleves_list})

@main.route('/eleve/<int:id>/export_notes_pdf') 
@login_required
@role_required('admin', 'professeur', 'parent')
def export_notes_eleve_pdf(id):
    """Génère et retourne le relevé de notes PDF d'un élève avec contrôle multi-écoles"""
    eleve = Eleve.query.get_or_404(id)
    if not can_access_eleve(eleve):
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.index'))

    # Vérification des accès selon rôle
    if current_user.role == 'parent' and not check_parent_access(id):
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    if current_user.role in ['admin', 'professeur'] and eleve.ecole_id != current_user.ecole_id:
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.eleves'))

    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        professeur_id = getattr(professeur, 'id', None)
        classe_autorisee = (
            professeur_id and eleve.classe and (
                eleve.classe.professeur_id == professeur_id
                or db.session.query(professeur_classes).filter(
                    professeur_classes.c.professeur_id == professeur_id,
                    professeur_classes.c.classe_id == eleve.classe.id
                ).first()
            )
        )
        if not classe_autorisee:
            flash("Accès non autorisé à cet élève.", "danger")
            return redirect(url_for('main.eleves'))

    if current_user.role == 'parent' and eleve.ecole_id != current_user.ecole_id:
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.parent_dashboard'))

    # Création PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
    styles = getSampleStyleSheet()
    elements = []

    # Nom de l'école et titre
    ecole_nom = eleve.ecole.nom if eleve.ecole else "N/A"
    elements.append(Paragraph(f"{ecole_nom}", ParagraphStyle('SchoolTitle', fontSize=18, alignment=1, spaceAfter=5, fontName='Helvetica-Bold')))
    elements.append(Paragraph("RELEVÉ DE NOTES", ParagraphStyle('Title', fontSize=16, alignment=1, spaceAfter=10, fontName='Helvetica-Bold')))

    # Année scolaire active
    annee_active = AnneeScolaire.query.filter_by(
        ecole_id=eleve.ecole_id,
        statut="active"
    ).first()
    annee_text = annee_active.nom if annee_active else "N/A"
    elements.append(Paragraph(f"<b>Année scolaire :</b> {annee_text}", styles['Normal']))
    elements.append(Spacer(1, 10))

    # Informations élève
    premiere_annee = str(eleve.annee_premiere_ecole) if eleve.annee_premiere_ecole else "N/A"
    info_text = f"""
    <b>Élève :</b> {eleve.prenom} {eleve.nom}<br/>
    <b>Classe :</b> {eleve.classe.nom if eleve.classe else 'Non assignée'}<br/>
    <b>Date de naissance :</b> {eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else 'Non renseignée'}<br/>
    <b>Parent :</b> {eleve.parent.nom if eleve.parent else 'N/A'}<br/>
    <b>1ère année dans l'école :</b> {premiere_annee}<br/>
    <b>Date d'édition :</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}
    """
    elements.append(Paragraph(info_text, styles['Normal']))
    elements.append(Spacer(1, 20))

    # Notes filtrées par année active
    notes = [n for n in eleve.notes if not annee_active or n.annee_id == annee_active.id]
    notes = sorted(notes, key=lambda n: (n.cours.nom if n.cours else "", n.date_evaluation))

    # Création d'un tableau unique
    data = [['Matière', 'Date', 'Type d\'évaluation', 'Note', 'Coefficient']]
    total_pondere_global = 0
    total_coefficients_global = 0

    for note in notes:
        cours_nom = note.cours.nom if note.cours else "N/A"
        data.append([
            cours_nom,
            note.date_evaluation.strftime('%d/%m/%Y') if note.date_evaluation else "N/A",
            note.type_evaluation or "N/A",
            str(note.valeur),
            str(note.coefficient)
        ])
        total_pondere_global += note.valeur * note.coefficient
        total_coefficients_global += note.coefficient

    # Moyenne générale
    moyenne_generale = round(total_pondere_global / total_coefficients_global, 2) if total_coefficients_global > 0 else 0
    data.append(['', '', '', '', ''])
    data.append(['', '', 'Moyenne générale', str(moyenne_generale), str(total_coefficients_global)])

    table = Table(data, colWidths=[100, 70, 150, 60, 60])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#4B8BBE")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, -2), (-1, -1), colors.HexColor("#FFE699")),
        ('FONTNAME', (0, -2), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
    ]))

    elements.append(table)
    doc.build(elements)
    buffer.seek(0)

    # Logging export
    current_app.logger.info(f"Export PDF notes élève {eleve.id} ({eleve.prenom} {eleve.nom}) par {current_user.id}")

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"releve_notes_{eleve.prenom}_{eleve.nom}.pdf",
        mimetype='application/pdf'
    )

@main.route('/eleves/export_excel')
@login_required
@role_required('admin')
def export_eleves_excel():
    # Année scolaire active
    annee_active = AnneeScolaire.query.filter_by(
        statut="active",
        ecole_id=current_user.ecole_id if current_user.role == "admin" else None
    ).first()

    # Filtrage selon rôle et année
    if current_user.role == 'super-admin':
        eleves = get_ecole_filter_query(Eleve).options(joinedload(Eleve.classe)).all()
    else:
        eleves = Eleve.query.options(joinedload(Eleve.classe)).filter_by(ecole_id=current_user.ecole_id).all()

    # Filtrer seulement élèves inscrits dans l'année active
    if annee_active:
        eleves = [e for e in eleves if e.date_inscription.year <= int(annee_active.nom.split('-')[0])]

    data = {
        'ID': [e.id for e in eleves],
        'Nom': [e.nom for e in eleves],
        'Prénom': [e.prenom for e in eleves],
        'Date de naissance': [e.date_naissance.strftime('%d/%m/%Y') if e.date_naissance else '' for e in eleves],
        'Classe': [e.classe.nom if e.classe else "Non assignée" for e in eleves],
        'Téléphone': [e.telephone for e in eleves],
        'Email': [e.email for e in eleves],
        'Téléphone parent': [e.contact_parent for e in eleves],
        'Email parent': [e.email_parent for e in eleves],
        'Date inscription': [e.date_inscription.strftime('%d/%m/%Y') for e in eleves]
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Élèves', index=False)

        # Mise en forme Excel : largeur automatique et en-têtes en gras
        ws = writer.sheets['Élèves']
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except (TypeError, ValueError) as e:
                    current_app.logger.debug(f"Impossible d'ajuster la largeur Excel eleves: {e}")
            adjusted_width = max_length + 2
            ws.column_dimensions[column].width = adjusted_width
        for cell in ws[1]:
            cell.font = cell.font.copy(bold=True)



    output.seek(0)

    # Log de l'export
    current_app.logger.info(f"Export Excel élèves par {current_user.id} ({current_user.role})")

    return send_file(
        output,
        as_attachment=True,
        download_name="liste_eleves.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@main.route('/voir_eleve/<int:eleve_id>')  # Au lieu de '/eleve/<int:eleve_id>'
@login_required
@role_required('admin', 'professeur', 'parent')
@parent_access_required
def voir_eleve(eleve_id):
    eleve = Eleve.query.options(
        joinedload(Eleve.notes).joinedload(Note.cours),
        joinedload(Eleve.absences).joinedload(Absence.cours),
        joinedload(Eleve.paiements),
        joinedload(Eleve.classe),
        joinedload(Eleve.parent)
    ).get_or_404(eleve_id)

    if not can_access_eleve(eleve):
        abort(403)

    # 1. Notes & Performances académiques de l'année
    notes = sorted(eleve.notes, key=lambda n: n.date_evaluation or datetime.min, reverse=True)
    total_pondere = sum((n.valeur or 0) * (n.coefficient or 1) for n in notes)
    total_coefficients = sum((n.coefficient or 1) for n in notes)
    moyenne_generale = round(total_pondere / total_coefficients, 2) if total_coefficients else 0

    matieres_stats = {}
    for n in notes:
        mat = n.cours.nom if n.cours else "Matière générale"
        if mat not in matieres_stats:
            matieres_stats[mat] = {
                'nom': mat,
                'total': 0,
                'coef': 0,
                'count': 0,
                'notes': [],
                'min': 20.0,
                'max': 0.0
            }
        val = float(n.valeur or 0)
        coef = float(n.coefficient or 1)
        matieres_stats[mat]['total'] += val * coef
        matieres_stats[mat]['coef'] += coef
        matieres_stats[mat]['count'] += 1
        matieres_stats[mat]['notes'].append(n)
        if val < matieres_stats[mat]['min']:
            matieres_stats[mat]['min'] = val
        if val > matieres_stats[mat]['max']:
            matieres_stats[mat]['max'] = val

    moyennes_par_matiere = {}
    for mat, data in matieres_stats.items():
        moy = round(data['total'] / data['coef'], 2) if data['coef'] else (round(data['total'] / data['count'], 2) if data['count'] else 0)
        data['moyenne'] = moy
        moyennes_par_matiere[mat] = moy
        if data['min'] > data['max']:
            data['min'] = moy
            data['max'] = moy

    if moyenne_generale >= 16:
        mention = 'Très Bien'
        mention_badge = 'success'
    elif moyenne_generale >= 14:
        mention = 'Bien'
        mention_badge = 'primary'
    elif moyenne_generale >= 12:
        mention = 'Assez Bien'
        mention_badge = 'info'
    elif moyenne_generale >= 10:
        mention = 'Passable'
        mention_badge = 'warning'
    else:
        mention = 'Insuffisant'
        mention_badge = 'danger'

    # 2. Absences & Assiduité de l'année
    absences = sorted(eleve.absences, key=lambda a: a.date_absence or datetime.min.date(), reverse=True)
    total_absences = len(absences)
    absences_injustifiees = sum(1 for a in absences if not a.justifiee)
    absences_justifiees = total_absences - absences_injustifiees

    # 3. Paiements & Scolarité de l'année
    paiements = sorted(eleve.paiements, key=lambda p: p.date_paiement or datetime.min, reverse=True)
    total_frais = float(eleve.frais_annuels or 150000.0)
    total_paye = float(sum(p.montant or 0 for p in paiements))
    reste_a_payer = max(0.0, total_frais - total_paye)
    pourcentage_paye = round((total_paye / total_frais) * 100, 1) if total_frais > 0 else 0.0

    mois_scolaires = ['Octobre', 'Novembre', 'Décembre', 'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin']
    mois_payes_set = set(p.mois for p in paiements if p.mois)
    echeancier = [{'mois': m, 'paye': (m in mois_payes_set)} for m in mois_scolaires]
    mois_impayes_list = [m for m in mois_scolaires if m not in mois_payes_set]

    # 4. Âge calculé
    age = None
    if eleve.date_naissance:
        today = datetime.now().date()
        age = today.year - eleve.date_naissance.year - ((today.month, today.day) < (eleve.date_naissance.month, eleve.date_naissance.day))

    return render_template('voir_eleve.html',
                           eleve=eleve,
                           age=age,
                           notes=notes,
                           matieres_stats=matieres_stats,
                           moyennes_par_matiere=moyennes_par_matiere,
                           moyenne_generale=moyenne_generale,
                           mention=mention,
                           mention_badge=mention_badge,
                           absences=absences,
                           total_absences=total_absences,
                           absences_injustifiees=absences_injustifiees,
                           absences_justifiees=absences_justifiees,
                           paiements=paiements,
                           total_frais=total_frais,
                           total_paye=total_paye,
                           reste_a_payer=reste_a_payer,
                           pourcentage_paye=pourcentage_paye,
                           echeancier=echeancier,
                           mois_impayes_list=mois_impayes_list)

@main.route('/eleve/<int:eleve_id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_eleve(eleve_id):
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=eleve_id).first_or_404()
    annee_active = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id, statut="active").first()
    classes_query = Classe.query.filter_by(ecole_id=current_user.ecole_id)
    if annee_active:
        classes_query = classes_query.filter_by(annee_scolaire_id=annee_active.id)
    classes = classes_query.order_by(Classe.nom).all()
    parents = Utilisateur.query.filter_by(ecole_id=current_user.ecole_id, role='parent').order_by(Utilisateur.nom).all()

    if request.method == 'POST':
        classe_id = request.form.get('classe_id', type=int)
        parent_id = request.form.get('parent_id', type=int)

        if not classe_id:
            flash("❌ La classe est obligatoire. Un élève doit toujours être inscrit dans une classe.", "danger")
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id))

        classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first()
        if not classe:
            flash("❌ Classe invalide pour cette école.", "danger")
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id))

        parent = Utilisateur.query.filter_by(id=parent_id, ecole_id=current_user.ecole_id, role='parent').first() if parent_id else None
        if parent_id and not parent:
            flash("❌ Parent invalide pour cette école.", "danger")
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id))

        eleve.nom = request.form.get('nom', eleve.nom).strip()
        eleve.prenom = request.form.get('prenom', eleve.prenom).strip()
        if request.form.get('genre'):
            eleve.genre = request.form.get('genre')
        date_naissance = request.form.get('date_naissance')
        if date_naissance:
            eleve.date_naissance = datetime.strptime(date_naissance, '%Y-%m-%d').date()
        inscription, inscription_error = modifier_inscription_annuelle(
            ecole_id=current_user.ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=classe.annee_scolaire_id,
            classe_id=classe.id,
        )
        if inscription_error:
            flash(inscription_error, "danger")
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id))

        eleve.parent_id = parent.id if parent else None
        eleve.email_parent = parent.email if parent else request.form.get('email_parent') or eleve.email_parent
        eleve.contact_parent = parent.telephone if parent else request.form.get('telephone_parent') or eleve.contact_parent
        db.session.commit()
        flash("Élève modifié avec succès.", "success")
        return redirect(url_for('main.voir_eleve', eleve_id=eleve.id))

    return render_template('edit_eleve.html', eleve=eleve, classes=classes, parents=parents)

@main.route('/eleve/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_eleve(id):
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=id).first_or_404()

    # 🛡️ Sécurité multi-écoles : empêche la suppression inter-écoles
    if current_user.role != 'super_admin' and eleve.ecole_id != current_user.ecole_id:
        flash("Action non autorisée : cet élève appartient à une autre école.", "danger")
        return redirect(url_for('main.eleves'))

    # Vérifier s'il y a des données liées
    if eleve.notes or eleve.paiements or eleve.absences:
        flash("Impossible de supprimer cet élève car il a des données associées.", "danger")
        return redirect(url_for('main.eleves'))

    try:
        db.session.delete(eleve)
        db.session.commit()
        current_app.logger.info(f"Élève supprimé : {eleve.nom} (ID={eleve.id}) par {current_user.email}")
        flash("Élève supprimé avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur lors de la suppression de l’élève {eleve.id} : {e}")
        flash("Erreur lors de la suppression de l’élève.", "danger")

    return redirect(url_for('main.eleves'))

@main.route('/eleve/<int:id>/supprimer-cascade', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_eleve_cascade(id):
    """Supprime un élève et toutes ses données associées, avec journalisation."""
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=id).first_or_404()

    try:
        ancienne_valeur = f"{eleve.nom} {eleve.prenom} (Classe: {eleve.classe_id})"

        # Supprimer toutes les données associées
        Note.query.filter_by(eleve_id=id).delete(synchronize_session=False)
        Paiement.query.filter_by(eleve_id=id).delete(synchronize_session=False)
        Absence.query.filter_by(eleve_id=id).delete(synchronize_session=False)
        Inscription.query.filter_by(eleve_id=id).delete(synchronize_session=False)  # <-- Ajouté

        db.session.delete(eleve)
        db.session.commit()

        # ✅ Journalisation complète
        current_app.log_correction(
            action="suppression_cascade",
            description=f"Élève et données associées supprimés : {eleve.nom} {eleve.prenom}",
            ecole_id=eleve.ecole_id,
            cible_type="eleve",
            cible_id=id,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=None,
            niveau="info"
        )

        flash("Élève et toutes ses données associées supprimés avec succès.", "success")
        return redirect(url_for('main.eleves'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression cascade élève {id}: {e}")
        flash("Erreur inattendue lors de la suppression.", "danger")
        return redirect(url_for('main.eleves'))

@login_required
@role_required('admin')
def supprimer_eleve_route(id):
    """Supprimer un élève"""
    eleve = Eleve.query.get_or_404(id)
    
    # Vérifier s'il y a des données liées
    if eleve.notes or eleve.paiements or eleve.absences:
        flash("Impossible de supprimer cet élève car il a des données associées.", "danger")
        return redirect(url_for('main.profile'))
    
    db.session.delete(eleve)
    db.session.commit()
    flash("Élève supprimé avec succès.", "success")
    return redirect(url_for('main.profile'))

@main.route('/api/eleves/<int:eleve_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def supprimer_eleve_api(eleve_id):
    """Supprimer un élève via API"""
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=eleve_id).first_or_404()
    
    # Vérifier que l'élève appartient à l'école de l'admin
    if current_user.role == 'admin' and eleve.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403
    
    # Vérifier s'il y a des données liées
    if eleve.notes or eleve.paiements or eleve.absences:
        return jsonify({
            'success': False, 
            'message': 'Impossible de supprimer cet élève car il a des données associées'
        }), 400
    
    db.session.delete(eleve)
    db.session.commit()
    
    return jsonify({'success': True})
