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
from app.services.paiements_annuels import get_mois_scolaires
from app.services.annees_scolaires import get_annee_consultee, get_annees_ecole, get_classes_annee
from app.services.classes_annuelles import get_classes_ouvertes_annee
from app.services.inscriptions_annuelles import creer_inscription_annuelle, get_inscription_active, get_parcours_eleve, modifier_inscription_annuelle
from app.access_codes import generate_access_code, is_valid_access_code
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
import pandas as pd
import uuid
from app.services import check_ecole_access
from app.utils import sanitize_internal_url
from app.services.import_eleves_service import (
    generer_modele_excel_eleves,
    previsualiser_import_excel,
    executer_import_excel,
    stocker_preview_import,
    recuperer_preview_import,
    supprimer_preview_import,
)


def _url_with_args(endpoint, allowed_args, **values):
    args = {}
    for key in allowed_args:
        value = request.args.get(key)
        if value not in (None, ""):
            args[key] = value
    args.update({k: v for k, v in values.items() if v not in (None, "")})
    return url_for(endpoint, **args)


def _eleves_context_url():
    return _url_with_args(
        'main.eleves',
        ('search', 'classe_id', 'niveau', 'niveau_id', 'genre', 'page'),
    )


def _safe_return_url(fallback):
    return sanitize_internal_url(
        request.form.get('return_url') or request.args.get('return_url'),
        fallback,
    )


@main.route('/eleves')
@login_required
@role_required('admin', 'professeur')
def eleves():
    """Gestion et liste des élèves organisée par classe avec recherche et filtres"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    classe_id = request.args.get('classe_id', type=int)
    search = (request.args.get('search') or request.args.get('q') or '').strip()
    niveau_param = request.args.get('niveau_id') or request.args.get('niveau') or ''
    genre = (request.args.get('genre') or '').strip().upper()
    statut = (request.args.get('statut') or '').strip()
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        abort(403)
    annee_consultee = get_annee_consultee(ecole_id)
    annees_ecole = get_annees_ecole(ecole_id)

    from app.services.structure_annuelle import get_niveaux_annee
    niveaux_annee = get_niveaux_annee(ecole_id, annee_consultee.id) if annee_consultee else []

    # ---------------- Base query avec relations pour éviter N+1 ----------------
    base_query = Eleve.query.options(
        db.joinedload(Eleve.parent)
    )
    if annee_consultee:
        base_query = base_query.join(
            Inscription,
            db.and_(
                Inscription.eleve_id == Eleve.id,
                Inscription.ecole_id == ecole_id,
                Inscription.annee_scolaire_id == annee_consultee.id,
            )
        )
    else:
        base_query = base_query.filter(db.false())

    # ---------------- Filtrage multi-écoles selon rôle ----------------
    if current_user.role == 'admin':
        all_eleves_query = base_query.filter(Eleve.ecole_id == ecole_id).order_by(Eleve.nom.asc(), Eleve.prenom.asc())

    elif current_user.role == 'professeur':
        professeur_id = getattr(current_user.professeur_rel, 'id', None)
        all_eleves_query = (
            base_query.join(Classe, Classe.id == Inscription.classe_id)
            .filter(
                Classe.ecole_id == ecole_id,
                db.or_(
                    Classe.professeur_id == professeur_id,
                    Classe.id.in_(
                        db.session.query(professeur_classes.c.classe_id)
                        .filter(professeur_classes.c.professeur_id == professeur_id)
                    )
                ),
                Eleve.ecole_id == ecole_id
            )
            .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        )

    else:
        abort(403)

    # Application des filtres de recherche multi-critères
    eleves_query = all_eleves_query

    # Sécurité prof : vérification si la classe demandée lui est bien assignée
    if classe_id:
        if current_user.role == 'professeur':
            professeur_id = getattr(current_user.professeur_rel, 'id', None)
            prof_classe_ids = [row[0] for row in db.session.query(professeur_classes.c.classe_id).filter(professeur_classes.c.professeur_id == professeur_id).all()]
            own_classes = [c.id for c in Classe.query.filter_by(professeur_id=professeur_id, ecole_id=ecole_id).all()]
            if classe_id not in prof_classe_ids and classe_id not in own_classes:
                eleves_query = eleves_query.filter(db.false())
            else:
                eleves_query = eleves_query.filter(Inscription.classe_id == classe_id)
        else:
            eleves_query = eleves_query.filter(Inscription.classe_id == classe_id)

    if niveau_param:
        # Jointure Classe si pas déjà jointe
        if current_user.role != 'professeur':
            eleves_query = eleves_query.join(Classe, Classe.id == Inscription.classe_id)
        if str(niveau_param).isdigit():
            eleves_query = eleves_query.filter(db.or_(Classe.niveau_id == int(niveau_param), Classe.niveau == str(niveau_param)))
        else:
            eleves_query = eleves_query.filter(Classe.niveau == str(niveau_param))

    if genre:
        eleves_query = eleves_query.filter(db.func.upper(Eleve.genre) == genre)

    if statut:
        eleves_query = eleves_query.filter(db.or_(Inscription.statut.ilike(f"%{statut}%"), Eleve.statut.ilike(f"%{statut}%")))

    if search:
        like = f"%{search}%"
        eleves_query = eleves_query.filter(
            db.or_(
                Eleve.nom.ilike(like),
                Eleve.prenom.ilike(like),
                Eleve.code_parent.ilike(like),
                Eleve.contact_parent.ilike(like)
            )
        )

    # Récupération des élèves filtrés
    has_filters = bool(search or classe_id or niveau_param or genre or statut)
    all_eleves = eleves_query.all() if has_filters else all_eleves_query.all()
    eleves_pagination = eleves_query.paginate(page=page, per_page=per_page, error_out=False)

    # Classes autorisées
    classes_query = get_classes_annee(ecole_id, annee_consultee.id) if annee_consultee else Classe.query.filter_by(ecole_id=ecole_id).filter(db.false())
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
    inscriptions = []
    if annee_consultee and all_eleves:
        inscriptions = (
            Inscription.query
            .options(joinedload(Inscription.classe))
            .filter(
                Inscription.ecole_id == ecole_id,
                Inscription.annee_scolaire_id == annee_consultee.id,
                Inscription.eleve_id.in_([e.id for e in all_eleves]),
            )
            .all()
        )
    inscription_par_eleve = {ins.eleve_id: ins for ins in inscriptions}

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
        inscription = inscription_par_eleve.get(e.id)
        cid = inscription.classe_id if inscription else e.classe_id
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

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'total': total_eleves,
            'page': page,
            'eleves': [
                {
                    'id': e.id,
                    'nom': e.nom,
                    'prenom': e.prenom,
                    'genre': e.genre,
                    'code_parent': e.code_parent,
                    'contact_parent': e.contact_parent,
                    'classe_id': inscription_par_eleve[e.id].classe_id if e.id in inscription_par_eleve else e.classe_id,
                    'classe_nom': (inscription_par_eleve[e.id].classe.nom if (e.id in inscription_par_eleve and inscription_par_eleve[e.id].classe) else 'Sans classe'),
                    'parent': f"{e.parent.prenom} {e.parent.nom}" if e.parent else (e.contact_parent or 'Non assigné')
                } for e in all_eleves
            ]
        })

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
        niveau_id=niveau_param,
        genre=genre,
        statut=statut,
        niveaux_annee=niveaux_annee,
        eleves=eleves_pagination,
        all_eleves=all_eleves,
        annee_consultee=annee_consultee,
        annees_ecole=annees_ecole,
        return_url=_eleves_context_url()
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

    annee_consultee = get_annee_consultee(ecole_id)
    if annee_consultee and annee_consultee.statut == "archivee":
        flash("Impossible de creer un eleve dans une annee archivee.", "warning")
        return redirect(url_for('main.eleves'))

    # ---------------- Année scolaire active ----------------
    annees_ecole = AnneeScolaire.query.filter_by(ecole_id=ecole_id).order_by(AnneeScolaire.id.desc()).all()
    annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()
    if not annee_active and annees_ecole:
        annee_active = annees_ecole[0]

    # ---------------- Classes ----------------
    annee_classes = annee_consultee or annee_active
    annee_active = annee_classes
    classes_query = Classe.query.filter_by(ecole_id=ecole_id).filter(db.false())
    if annee_classes:
        classes_query = get_classes_ouvertes_annee(ecole_id, annee_classes.id)
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
            if not annee_consultee or classe_selectionnee.annee_scolaire_id != annee_consultee.id:
                flash("Classe invalide pour l'annee consultee.", "danger")
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

                # Vérifier si code_parent saisi est unique
                if code_parent_saisi:
                    if not is_valid_access_code(code_parent_saisi):
                        flash("Le code d'accès doit contenir exactement 8 chiffres.", "danger")
                        return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                                               annee_active=annee_active, classes=classes)
                    if Eleve.query.filter_by(code_parent=code_parent_saisi).first():
                        flash("Ce code parent est déjà utilisé par un autre élève.", "danger")
                        return render_template('ajouter_eleve.html', form=form, annees_ecole=annees_ecole,
                                               annee_active=annee_active, classes=classes)

                code_parent = code_parent_saisi or generate_access_code()
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
                        sujet = "Bienvenue sur KLASORA — Votre espace parent est prêt"
                        message = render_template(
                            'emails/bienvenue_parent.html',
                            parent=parent_utilisateur,
                            ecole=current_user.ecole,
                            mot_de_passe=code_parent
                        )
                        email_ok = envoyer_email(email_parent, sujet, message, context="welcome_parent")
                        if email_ok:
                            current_app.logger.info("EMAIL_SUCCESS_HANDLED type=welcome_parent recipient=%s", email_parent)
                        else:
                            current_app.logger.warning("EMAIL_FAILED_HANDLED type=welcome_parent recipient=%s", email_parent)
                            flash("Parent créé, mais l'email de bienvenue n'a pas pu être envoyé.", "warning")

                except Exception as e:
                    current_app.logger.error("Erreur préparation notification parent: %s", e)

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
    """Retourne la liste des élèves d'une classe filtrée par école et année consultée (JSON)"""
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        return jsonify({'eleves': []}), 403

    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return jsonify({'eleves': []}), 404

    annee_consultee = get_annee_consultee(ecole_id)
    annee = annee_consultee or AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()
    if not annee or (annee_consultee and classe.annee_scolaire_id != annee_consultee.id):
        return jsonify({'eleves': []}), 200

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

    inscriptions_query = (
        Inscription.query
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.classe_id == classe_id,
            Inscription.annee_scolaire_id == annee.id,
            Eleve.ecole_id == ecole_id,
        )
    )
    inscriptions = inscriptions_query.order_by(Eleve.nom, Eleve.prenom).all()
    return jsonify({'eleves': [
        {
            'id': inscription.eleve.id,
            'nom': inscription.eleve.nom,
            'prenom': inscription.eleve.prenom,
            'telephone': inscription.eleve.contact_parent or '-',
            'classe': inscription.classe.nom if inscription.classe else "Sans classe",
            'parent': f"{inscription.eleve.parent.prenom} {inscription.eleve.parent.nom}" if inscription.eleve.parent else "Non assigné"
        }
        for inscription in inscriptions
        if inscription.eleve
    ]})

@main.route('/eleve/<int:id>/export_notes_pdf') 
@login_required
@role_required('admin', 'professeur', 'parent')
def export_notes_eleve_pdf(id):
    """Génère et retourne le relevé de notes PDF d'un élève avec contrôle multi-écoles"""
    eleve = Eleve.query.get_or_404(id)
    if not can_access_eleve(eleve):
        flash("Accès non autorisé à cet élève.", "danger")
        return redirect(url_for('main.index'))

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
    inscription_active = (
        Inscription.query.filter_by(
            ecole_id=eleve.ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=annee_active.id,
        ).first()
        if annee_active else None
    )
    classe_pdf = inscription_active.classe if inscription_active else None
    elements.append(Paragraph(f"<b>Année scolaire :</b> {annee_text}", styles['Normal']))
    elements.append(Spacer(1, 10))

    # Informations élève
    premiere_annee = str(eleve.annee_premiere_ecole) if eleve.annee_premiere_ecole else "N/A"
    info_text = f"""
    <b>Élève :</b> {eleve.prenom} {eleve.nom}<br/>
    <b>Classe :</b> {classe_pdf.nom if classe_pdf else 'Non assignée'}<br/>
    <b>Date de naissance :</b> {eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else 'Non renseignée'}<br/>
    <b>Parent :</b> {eleve.parent.nom if eleve.parent else 'N/A'}<br/>
    <b>1ère année dans l'école :</b> {premiere_annee}<br/>
    <b>Date d'édition :</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}
    """
    elements.append(Paragraph(info_text, styles['Normal']))
    elements.append(Spacer(1, 20))

    # Notes filtrées par année active
    notes = (
        Note.query.filter_by(inscription_id=inscription_active.id, ecole_id=eleve.ecole_id).all()
        if inscription_active else []
    )
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
    ecole_id = current_user.ecole_id if current_user.role == "admin" else session.get("ecole_id")
    annee_consultee = get_annee_consultee(ecole_id)

    if annee_consultee:
        inscriptions = (
            Inscription.query
            .options(joinedload(Inscription.eleve), joinedload(Inscription.classe))
            .join(Eleve, Eleve.id == Inscription.eleve_id)
            .join(Classe, Classe.id == Inscription.classe_id, isouter=True)
            .filter(
                Inscription.ecole_id == ecole_id,
                Inscription.annee_scolaire_id == annee_consultee.id,
                Eleve.ecole_id == ecole_id,
            )
            .order_by(Classe.nom.asc(), Eleve.nom.asc(), Eleve.prenom.asc())
            .all()
        )
        eleves_rows = [(ins.eleve, ins.classe) for ins in inscriptions if ins.eleve]
    else:
        eleves = (
            Eleve.query
            .filter_by(ecole_id=ecole_id)
            .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
            .all()
        )
        eleves_rows = [(e, None) for e in eleves]

    data = {
        'ID': [e.id for e, _classe in eleves_rows],
        'Nom': [e.nom for e, _classe in eleves_rows],
        'Prenom': [e.prenom for e, _classe in eleves_rows],
        'Date de naissance': [e.date_naissance.strftime('%d/%m/%Y') if e.date_naissance else '' for e, _classe in eleves_rows],
        'Classe': [classe.nom if classe else "Non assignee" for _e, classe in eleves_rows],
        'Telephone parent': [e.contact_parent for e, _classe in eleves_rows],
        'Email parent': [e.email_parent for e, _classe in eleves_rows],
        'Date inscription': [e.date_inscription.strftime('%d/%m/%Y') if e.date_inscription else '' for e, _classe in eleves_rows],
        'Annee scolaire': [annee_consultee.nom if annee_consultee else '' for _e, _classe in eleves_rows],
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Eleves', index=False)

        ws = writer.sheets['Eleves']
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except Exception:
                    pass
            ws.column_dimensions[column].width = max_length + 2

        for cell in ws[1]:
            cell.font = cell.font.copy(bold=True)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="liste_eleves.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@main.route('/eleves/modele_excel')
@login_required
@role_required('admin')
def modele_excel_eleves():
    """Téléchargement du modèle Excel d'importation d'élèves."""
    buffer = generer_modele_excel_eleves()
    return send_file(
        buffer,
        as_attachment=True,
        download_name="modele_import_eleves.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@main.route('/eleves/import', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def import_excel_form():
    """Prévisualisation et validation de l'import Excel."""
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        abort(403)
    
    annee_consultee = get_annee_consultee(ecole_id)
    if not annee_consultee:
        flash("Veuillez configurer une année scolaire active ou planifiée.", "warning")
        return redirect(url_for('main.eleves'))
        
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not file.filename:
            flash("Veuillez sélectionner un fichier Excel (.xlsx).", "danger")
            return redirect(url_for('main.eleves'))
            
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext != 'xlsx':
            flash("Format non pris en charge. Veuillez utiliser un fichier modèle Excel (.xlsx).", "danger")
            return redirect(url_for('main.eleves'))
            
        res = previsualiser_import_excel(file.stream, ecole_id, annee_consultee)
        if not res['is_importable'] and res.get('erreur_globale'):
            flash(res['erreur_globale'], "danger")
            return redirect(url_for('main.eleves'))
            
        token = str(uuid.uuid4())
        # Stockage temporaire serveur hors session cookie
        stocker_preview_import(token, {
            'user_id': current_user.id,
            'ecole_id': ecole_id,
            'annee_id': annee_consultee.id,
            'lignes': res['lignes']
        })
        session['import_token'] = token
        return render_template('import_eleves_preview.html', data=res, annee_consultee=annee_consultee, import_token=token)

    return render_template('import_eleves.html', annee_consultee=annee_consultee)

@main.route('/eleves/import_confirm', methods=['POST'])
@login_required
@role_required('admin')
def import_excel_confirm():
    """Exécution effective de l'importation après prévisualisation."""
    token = request.form.get('token') or session.get('import_token')
    if not token:
        flash("La session d'importation a expiré. Veuillez téléverser à nouveau le fichier.", "warning")
        return redirect(url_for('main.eleves'))
        
    cached = recuperer_preview_import(token)
    if not cached:
        flash("La session d'importation a expiré. Veuillez téléverser à nouveau le fichier.", "warning")
        session.pop('import_token', None)
        return redirect(url_for('main.eleves'))
        
    ecole_id = cached['ecole_id']
    if current_user.role != 'super_admin' and current_user.ecole_id != ecole_id:
        supprimer_preview_import(token)
        session.pop('import_token', None)
        abort(403)
        
    annee_consultee = get_annee_consultee(ecole_id)
    if not annee_consultee or annee_consultee.statut == "archivee":
        flash("Importation impossible dans une année scolaire archivée.", "danger")
        supprimer_preview_import(token)
        session.pop('import_token', None)
        return redirect(url_for('main.eleves'))
        
    lignes = cached['lignes']
    succes, msg, crees, reinscrits, ignores = executer_import_excel(lignes, ecole_id, annee_consultee)
    
    supprimer_preview_import(token)
    session.pop('import_token', None)
    
    if succes:
        flash(f"✅ {msg} ({crees} créé(s), {reinscrits} réinscrit(s), {ignores} ignoré(s)).", "success")
    else:
        flash(f"❌ {msg}", "danger")
        
    return redirect(url_for('main.eleves'))

@main.route('/voir_eleve/<int:eleve_id>')
@main.route('/eleve/<int:eleve_id>')
@login_required
@role_required('admin', 'professeur', 'parent')
@parent_access_required
def voir_eleve(eleve_id):
    if getattr(current_user, 'role', None) == 'parent':
        return redirect(url_for('main.parent_dashboard', enfant_id=eleve_id))
    eleve = Eleve.query.options(
        joinedload(Eleve.notes).joinedload(Note.cours),
        joinedload(Eleve.absences).joinedload(Absence.cours),
        joinedload(Eleve.paiements),
        joinedload(Eleve.parent)
    ).get_or_404(eleve_id)

    if not can_access_eleve(eleve):
        abort(403)

    # 1. Notes & Performances académiques de l'année
    inscription_active = get_inscription_active(eleve)
    parcours_scolaire = get_parcours_eleve(eleve)
    eleve.inscription_active = inscription_active
    eleve.parcours_scolaire = parcours_scolaire
    eleve.classe_actuelle = inscription_active.classe if inscription_active else None

    from app.services.evaluations import calculer_completude_inscription, STATUS_COMPLETE, STATUS_PROVISOIRE

    annee_id = inscription_active.annee_scolaire_id if inscription_active else None

    if inscription_active and annee_id:
        eval_info = calculer_completude_inscription(eleve.ecole_id, annee_id, inscription_active)
        notes = sorted([n for n in eleve.notes if n.annee_id == annee_id], key=lambda n: n.date_evaluation or datetime.min, reverse=True)
    else:
        eval_info = {"status": "non_evalue", "average": 0, "evaluated_subjects": 0, "expected_subjects": 0}
        notes = []

    moyenne_generale = eval_info["average"] if eval_info["average"] is not None else 0

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

    if eval_info["status"] == "non_evalue":
        mention = 'Non évalué'
        mention_badge = 'secondary'
    elif moyenne_generale >= 16:
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
    if current_user.role == 'professeur':
        paiements = []
        total_frais = 0.0
        total_paye = 0.0
        reste_a_payer = 0.0
        pourcentage_paye = 0.0
        echeancier = []
        mois_impayes_list = []
    else:
        paiements = sorted(eleve.paiements, key=lambda p: p.date_paiement or datetime.min, reverse=True)
        total_frais = float(eleve.frais_annuels or 150000.0)
        total_paye = float(sum(p.montant or 0 for p in paiements))
        reste_a_payer = max(0.0, total_frais - total_paye)
        pourcentage_paye = round((total_paye / total_frais) * 100, 1) if total_frais > 0 else 0.0

        mois_scolaires = get_mois_scolaires(inscription_active.annee_scolaire if inscription_active else None)
        mois_payes_set = set(p.mois for p in paiements if p.mois)
        echeancier = [{'mois': m, 'paye': (m in mois_payes_set)} for m in mois_scolaires]
        mois_impayes_list = [m for m in mois_scolaires if m not in mois_payes_set]

    # 4. ?ge calculé
    age = None
    if eleve.date_naissance:
        today = datetime.now().date()
        age = today.year - eleve.date_naissance.year - ((today.month, today.day) < (eleve.date_naissance.month, eleve.date_naissance.day))

    return_url = _safe_return_url(url_for('main.eleves'))
    detail_url = url_for('main.voir_eleve', eleve_id=eleve.id, return_url=return_url)

    return render_template('voir_eleve.html',
                           eleve=eleve,
                           age=age,
                           notes=notes,
                           matieres_stats=matieres_stats,
                           moyennes_par_matiere=moyennes_par_matiere,
                           eval_info=eval_info,
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
                           mois_impayes_list=mois_impayes_list,
                           return_url=return_url,
                           detail_url=detail_url)

@main.route('/eleve/<int:eleve_id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_eleve(eleve_id):
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=eleve_id).first_or_404()
    return_url = _safe_return_url(url_for('main.eleves'))
    detail_url = url_for('main.voir_eleve', eleve_id=eleve.id, return_url=return_url)
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
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id, return_url=return_url))

        classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first()
        if not classe:
            flash("❌ Classe invalide pour cette école.", "danger")
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id, return_url=return_url))

        parent = Utilisateur.query.filter_by(id=parent_id, ecole_id=current_user.ecole_id, role='parent').first() if parent_id else None
        if parent_id and not parent:
            flash("❌ Parent invalide pour cette école.", "danger")
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id, return_url=return_url))

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
            return redirect(url_for('main.modifier_eleve', eleve_id=eleve.id, return_url=return_url))

        eleve.parent_id = parent.id if parent else None
        eleve.email_parent = parent.email if parent else request.form.get('email_parent') or eleve.email_parent
        eleve.contact_parent = parent.telephone if parent else request.form.get('telephone_parent') or eleve.contact_parent
        db.session.commit()
        flash("Élève modifié avec succès.", "success")
        return redirect(detail_url)

    inscription_active = (
        Inscription.query.filter_by(
            ecole_id=current_user.ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=annee_active.id
        ).first()
        if annee_active else None
    )
    return render_template('edit_eleve.html', eleve=eleve, classes=classes, parents=parents, inscription_active=inscription_active, return_url=return_url, detail_url=detail_url)


@main.route('/eleve/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_eleve(id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=id).first()
    if not eleve:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Élève introuvable.'}), 404
        abort(404)

    # 🛡️ Sécurité multi-écoles : empêche la suppression inter-écoles
    if current_user.role != 'super_admin' and eleve.ecole_id != current_user.ecole_id:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Action non autorisée.'}), 403
        flash("Action non autorisée : cet élève appartient à une autre école.", "danger")
        return redirect(url_for('main.eleves'))

    # Vérifier s'il y a des données liées
    if eleve.notes or eleve.paiements or eleve.absences:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Impossible de supprimer cet élève car il a des données associées.'}), 409
        flash("Impossible de supprimer cet élève car il a des données associées.", "danger")
        return redirect(url_for('main.eleves'))

    try:
        deleted_id = eleve.id
        nom_eleve = eleve.nom
        db.session.delete(eleve)
        db.session.commit()
        current_app.logger.info(f"Élève supprimé : {nom_eleve} (ID={deleted_id}) par {current_user.email}")
        if is_ajax:
            return jsonify({'success': True, 'message': 'Élève supprimé.', 'deleted_id': deleted_id})
        flash("Élève supprimé avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur lors de la suppression de l’élève {eleve.id} : {e}")
        if is_ajax:
            return jsonify({'success': False, 'message': 'Suppression impossible. Veuillez réessayer.'}), 500
        flash("Erreur lors de la suppression de l’élève.", "danger")

    return redirect(url_for('main.eleves'))

@main.route('/eleve/<int:id>/supprimer-cascade', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_eleve_cascade(id):
    """Supprime un élève et toutes ses données associées, avec journalisation."""
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=id).first_or_404()

    try:
        ancienne_valeur = f"{eleve.nom} {eleve.prenom} (ID: {eleve.id})"

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
