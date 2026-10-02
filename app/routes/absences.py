from . import main
import pandas as pd
from .common import (
    abort,
    Absence,
    AbsenceForm,
    Classe,
    Cours,
    Eleve,
    Inscription,
    current_app,
    current_user,
    date,
    datetime,
    db,
    flash,
    io,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    selectinload,
    send_file,
    url_for,
)
from flask import g, jsonify
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import joinedload
from app.authorization import tenant_required
from app.services.annees_scolaires import get_annee_consultee
from app.services.scolaire import niveaux_depuis_classes
from app.services.structure_annuelle import get_niveaux_annee
from app.services.absences_annuelles import (
    _parent_enfant_ids,
    _professeur_classe_ids,
    absences_modifiables,
    get_absences_annee,
    get_classes_absences,
    get_cours_choices_absences,
    get_eleves_choices_absences,
    get_palmares_absences_annuel,
    statut_annee_absences,
    verifier_mutation_absence,
)
from app.services.whatsapp_queue import enqueue_message
from app.services.phone_numbers import normaliser_numero_whatsapp


def _ecole_id_courante():
    return getattr(g, "ecole_id", getattr(current_user, "ecole_id", None))


def _remplir_choix_absence(form, ecole_id, annee):
    form.eleve_id.choices = get_eleves_choices_absences(ecole_id, annee, current_user)
    form.cours_id.choices = get_cours_choices_absences(ecole_id, annee, current_user)


def _notifier_whatsapp_absence(absence, ecole=None, eleve=None, cours=None):
    try:
        eleve = eleve or getattr(absence, "eleve", None)
        cours = cours or getattr(absence, "cours", None)
        if not eleve:
            return None

        ecole = getattr(eleve, "ecole", None) or ecole
        if not ecole or not getattr(ecole, "whatsapp_enabled", False):
            return None

        tel_parent = normaliser_numero_whatsapp(
            getattr(eleve, "contact_parent", None)
            or getattr(getattr(eleve, "parent", None), "telephone", None)
        )

        if not tel_parent:
            current_app.logger.warning(
                "Notification WhatsApp absence ignoree: aucun telephone parent valide pour eleve_id=%s",
                getattr(eleve, "id", None),
            )
            return None

        date_absence = absence.date_absence.strftime("%d/%m/%Y") if absence.date_absence else "ce jour"
        matiere = getattr(cours, "nom", None) or "la matiere"
        message = (
            f"Bonjour, KLASORA vous informe que votre enfant {eleve.prenom} {eleve.nom} "
            f"a ete marque(e) absent(e) ce jour {date_absence} au cours de {matiere}. "
            f"Merci de contacter l'administration de {ecole.nom}."
        )
        return enqueue_message(
            ecole_id=ecole.id,
            destinataire=tel_parent,
            message=message,
            type_message='absence',
            duree_validite_heures=12,
            commit=True,
        )
    except Exception as exc:
        current_app.logger.warning("Notification WhatsApp absence ignoree: %s", exc)
        return None


@main.route('/absences', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur')
@tenant_required
def absences():
    if request.method == 'POST':
        ecole_id = _ecole_id_courante()
        annee_consultee = get_annee_consultee(ecole_id)
        form = AbsenceForm()
        _remplir_choix_absence(form, ecole_id, annee_consultee)
        if not form.validate_on_submit():
            flash("Formulaire d'absence invalide.", "danger")
            return redirect(url_for('main.absences'))

        eleve, cours, inscription, error = verifier_mutation_absence(
            ecole_id,
            annee_consultee,
            current_user,
            form.eleve_id.data,
            form.cours_id.data,
            form.date_absence.data,
        )
        if error:
            flash(error, "danger")
            return redirect(url_for('main.absences'))

        absence = Absence(
            date_absence=form.date_absence.data,
            motif=form.motif.data,
            justifiee=bool(form.justifiee.data),
            eleve_id=eleve.id,
            cours_id=cours.id if cours else None,
            ecole_id=ecole_id,
            inscription_id=inscription.id if inscription else None,
        )
        db.session.add(absence)
        db.session.commit()

        if not absence.justifiee:
            _notifier_whatsapp_absence(absence, ecole=getattr(current_user, "ecole", None), eleve=eleve, cours=cours)

        flash("Absence enregistree avec succes.", "success")
        return redirect(url_for('main.absences'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    if per_page < 1 or per_page > 500:
        per_page = 50

    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    can_mutate = absences_modifiables(annee_consultee, current_user)
    message_annee = statut_annee_absences(annee_consultee)

    search = (request.args.get('search') or request.args.get('q') or '').strip()
    classe_param = request.args.get('classe_id') or request.args.get('classe')
    classe_id = None
    classe_nom = None
    if classe_param:
        if str(classe_param).isdigit():
            classe_id = int(classe_param)
        else:
            classe_nom = str(classe_param).strip()
    niveau_param = (request.args.get('niveau') or request.args.get('niveau_id') or '').strip()
    date_debut_str = request.args.get('date_debut')
    date_fin_str = request.args.get('date_fin')
    justifiee_param = request.args.get('justifiee')

    # Requête de base SQLAlchemy avec jointures et eager-loading optimisé
    query = (
        Absence.query.options(
            joinedload(Absence.eleve),
            joinedload(Absence.cours).joinedload(Cours.classe),
            joinedload(Absence.inscription).joinedload(Inscription.classe).joinedload(Classe.niveau_scolaire),
        )
        .outerjoin(Inscription, Absence.inscription_id == Inscription.id)
        .outerjoin(Classe, Inscription.classe_id == Classe.id)
        .join(Eleve, Absence.eleve_id == Eleve.id)
        .outerjoin(Cours, Absence.cours_id == Cours.id)
        .filter(Absence.ecole_id == ecole_id)
    )

    # Ancrage strict sur l'année scolaire consultée
    if annee_consultee:
        query = query.filter(
            or_(
                Inscription.annee_scolaire_id == annee_consultee.id,
                and_(
                    Absence.inscription_id.is_(None),
                    Absence.date_absence >= annee_consultee.date_debut,
                    Absence.date_absence <= annee_consultee.date_fin,
                ),
            )
        )
    else:
        query = query.filter(db.false())

    # Permissions et restrictions par rôle
    if current_user.role == "professeur":
        professeur = getattr(current_user, "professeur_rel", None)
        prof_id = professeur.id if professeur else -1
        classe_ids = _professeur_classe_ids(current_user, annee_consultee.id if annee_consultee else None)
        query = query.filter(
            or_(
                Cours.professeur_id == prof_id,
                Inscription.classe_id.in_(classe_ids) if classe_ids else db.false(),
            )
        )
    elif current_user.role == "parent":
        enfant_ids = _parent_enfant_ids(current_user)
        query = query.filter(Absence.eleve_id.in_(enfant_ids)) if enfant_ids else query.filter(db.false())

    # Filtre par classe
    if classe_id:
        query = query.filter(
            or_(
                Inscription.classe_id == classe_id,
                and_(Absence.inscription_id.is_(None), Cours.classe_id == classe_id),
            )
        )
    elif classe_nom and classe_nom.lower() != 'all':
        query = query.filter(func.lower(Classe.nom) == classe_nom.lower())

    # Filtre par niveau
    if niveau_param:
        if str(niveau_param).isdigit():
            query = query.filter(
                or_(
                    Classe.niveau_id == int(niveau_param),
                    Classe.niveau == str(niveau_param),
                )
            )
        else:
            query = query.filter(func.lower(Classe.niveau) == niveau_param.lower())

    # Filtre par recherche texte
    if search:
        search_pattern = f"%{search}%"
        words = search.split()
        if len(words) >= 2:
            query = query.filter(
                or_(
                    and_(Eleve.prenom.ilike(f"%{words[0]}%"), Eleve.nom.ilike(f"%{words[1]}%")),
                    and_(Eleve.nom.ilike(f"%{words[0]}%"), Eleve.prenom.ilike(f"%{words[1]}%")),
                    Eleve.code_parent.ilike(search_pattern),
                    Cours.nom.ilike(search_pattern),
                    Absence.motif.ilike(search_pattern),
                )
            )
        else:
            query = query.filter(
                or_(
                    Eleve.prenom.ilike(search_pattern),
                    Eleve.nom.ilike(search_pattern),
                    Eleve.code_parent.ilike(search_pattern),
                    Cours.nom.ilike(search_pattern),
                    Absence.motif.ilike(search_pattern),
                )
            )

    # Filtre justification
    if justifiee_param in ('1', 'true', 'yes', 'justifiee'):
        query = query.filter(Absence.justifiee == True)
    elif justifiee_param in ('0', 'false', 'no', 'non-justifiee'):
        query = query.filter(Absence.justifiee == False)

    # Filtres dates
    if date_debut_str:
        try:
            d_deb = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
            query = query.filter(Absence.date_absence >= d_deb)
        except ValueError:
            pass
    if date_fin_str:
        try:
            d_fin = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
            query = query.filter(Absence.date_absence <= d_fin)
        except ValueError:
            pass

    # Compteurs statistiques globaux via agrégation SQL native directe (zéro boucle ni saturation mémoire Python)
    stats_query = (
        query.with_entities(
            func.count(Absence.id).label('total'),
            func.sum(case((Absence.justifiee == True, 1), else_=0)).label('justifiees'),
            func.sum(case((or_(Absence.justifiee == False, Absence.justifiee.is_(None)), 1), else_=0)).label('non_justifiees'),
        )
        .order_by(None)
    )
    stats_row = stats_query.first()
    total = int(stats_row.total or 0) if stats_row else 0
    absences_justifiees = int(stats_row.justifiees or 0) if stats_row else 0
    absences_non_justifiees = int(stats_row.non_justifiees or 0) if stats_row else 0

    # Tri par date décroissante et pagination SQL native côté serveur
    pagination = query.order_by(Absence.date_absence.desc(), Absence.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    absences_page = pagination.items

    # Attacher contexte annuel uniquement pour les absences de la page courante
    for a in absences_page:
        if not hasattr(a, 'annee_classe') or a.annee_classe is None:
            a.annee_classe = a.inscription.classe if (a.inscription and a.inscription.classe) else (a.cours.classe if (a.cours and a.cours.classe) else None)
        if not hasattr(a, 'annee_scolaire') or a.annee_scolaire is None:
            a.annee_scolaire = annee_consultee

    show_form = False

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': total,
            'page': pagination.page,
            'pages': pagination.pages,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev,
            'absences': [
                {
                    'id': a.id,
                    'eleve_id': a.eleve_id,
                    'eleve_nom': f"{a.eleve.prenom} {a.eleve.nom}" if a.eleve else "",
                    'classe_nom': a.annee_classe.nom if getattr(a, 'annee_classe', None) else "",
                    'cours_nom': a.cours.nom if a.cours else "",
                    'date': a.date_absence.strftime('%Y-%m-%d') if a.date_absence else "",
                    'motif': a.motif or "",
                    'justifiee': bool(a.justifiee)
                }
                for a in absences_page
            ]
        })

    classes = get_classes_absences(ecole_id, annee_consultee, current_user)
    if current_user.role == "professeur":
        niveaux_annee = niveaux_depuis_classes(classes)
    else:
        niveaux_annee = get_niveaux_annee(ecole_id, annee_consultee.id) if (ecole_id and annee_consultee) else []

    palmares_absences = None
    if current_user.role in ('admin', 'super_admin'):
        palmares_absences = get_palmares_absences_annuel(ecole_id, annee_consultee)

    return render_template(
        'absences.html',
        pagination=pagination,
        absences=absences_page,
        absences_justifiees=absences_justifiees,
        absences_non_justifiees=absences_non_justifiees,
        show_form=show_form,
        total=total,
        classes=classes,
        niveaux_annee=niveaux_annee,
        annee_consultee=annee_consultee,
        can_mutate=can_mutate,
        message_annee=message_annee,
        search=search,
        classe_id=classe_id,
        classe_param=classe_param,
        niveau=niveau_param,
        date_debut=date_debut_str,
        date_fin=date_fin_str,
        justifiee=justifiee_param,
        per_page=per_page,
        palmares_absences=palmares_absences,
    )


@main.route('/api/absences/palmares')
@login_required
@role_required('admin', 'professeur')
@tenant_required
def api_absences_palmares():
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    return jsonify(get_palmares_absences_annuel(ecole_id, annee_consultee))


@main.route('/absences/export_excel')
@login_required
@role_required('admin')
@tenant_required
def export_absences_excel():
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    absences = get_absences_annee(ecole_id, annee_consultee, current_user)

    data = {
        'Date': [a.date_absence.strftime('%d/%m/%Y') for a in absences],
        'Eleve': [f"{a.eleve.prenom} {a.eleve.nom}" for a in absences],
        'Classe': [a.annee_classe.nom if getattr(a, 'annee_classe', None) else 'Sans classe' for a in absences],
        'Motif': [a.motif for a in absences],
        'Justifiee': ['Oui' if a.justifiee else 'Non' for a in absences],
        'Annee scolaire': [annee_consultee.nom if annee_consultee else '' for _ in absences],
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Absences', index=False)
    output.seek(0)

    suffix = f"_{annee_consultee.nom}" if annee_consultee else ""
    return send_file(
        output,
        as_attachment=True,
        download_name=f"liste_absences{suffix}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@main.route('/absences/edit/<int:absence_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur')
@tenant_required
def edit_absence(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    if absence.ecole_id != ecole_id:
        abort(403)

    _eleve, _cours, _inscription, access_error = verifier_mutation_absence(
        ecole_id,
        annee_consultee,
        current_user,
        absence.eleve_id,
        absence.cours_id,
        absence.date_absence,
        absence=absence,
    )
    if access_error:
        flash(access_error, "warning")
        return redirect(url_for('main.absences'))

    form = AbsenceForm(obj=absence)
    _remplir_choix_absence(form, ecole_id, annee_consultee)
    form.eleve_id.data = absence.eleve_id
    form.cours_id.data = absence.cours_id

    if form.validate_on_submit():
        eleve, cours, _inscription, error = verifier_mutation_absence(
            ecole_id,
            annee_consultee,
            current_user,
            form.eleve_id.data,
            form.cours_id.data,
            form.date_absence.data,
            absence=absence,
        )
        if error:
            flash(error, "danger")
            return redirect(url_for('main.absences'))

        absence.eleve_id = eleve.id
        absence.cours_id = cours.id if cours else None
        absence.date_absence = form.date_absence.data
        absence.motif = form.motif.data
        absence.justifiee = form.justifiee.data

        db.session.commit()
        flash("Absence mise a jour avec succes.", "success")
        return redirect(url_for('main.absences'))

    return render_template('edit_absence.html', form=form, annee_consultee=annee_consultee)


@main.route('/absences/delete/<int:absence_id>', methods=['POST'])
@login_required
@role_required('admin', 'professeur')
@tenant_required
def delete_absence(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    _eleve, _cours, _inscription, access_error = verifier_mutation_absence(
        ecole_id,
        annee_consultee,
        current_user,
        absence.eleve_id,
        absence.cours_id,
        absence.date_absence,
        absence=absence,
    )
    if access_error:
        flash(access_error, "warning")
        return redirect(url_for('main.absences'))

    try:
        db.session.delete(absence)
        db.session.commit()
        flash("Absence supprimee avec succes.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")

    return redirect(url_for('main.absences'))


@main.route("/presence", methods=["GET", "POST"])
@login_required
@role_required("professeur", "admin")
def presence():
    flash("KLASORA gère uniquement les absences scolaires.", "info")
    return redirect(url_for("main.absences"))


@main.route("/absences/appel", methods=["GET", "POST"])
@login_required
@role_required("professeur")
@tenant_required
def faire_appel():
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    can_mutate = absences_modifiables(annee_consultee, current_user)
    message_annee = statut_annee_absences(annee_consultee)

    classe_id = request.values.get("classe_id", type=int)
    cours_id = request.values.get("cours_id", type=int)
    date_appel_str = request.values.get("date_appel") or date.today().isoformat()
    try:
        date_appel = datetime.strptime(date_appel_str, "%Y-%m-%d").date()
    except ValueError:
        date_appel = date.today()

    if not annee_consultee:
        flash("Aucune annee scolaire active pour faire l'appel.", "warning")
        return redirect(url_for("main.absences"))

    cours = None
    classe = None
    if cours_id:
        cours = (
            Cours.query
            .join(Classe, Classe.id == Cours.classe_id)
            .filter(
                Cours.id == cours_id,
                Cours.ecole_id == ecole_id,
                Classe.ecole_id == ecole_id,
                Classe.annee_scolaire_id == annee_consultee.id,
            )
            .first()
        )
        if not cours:
            abort(404)
        classe = cours.classe
        classe_id = classe.id if classe else classe_id

    if classe_id and not classe:
        classe = Classe.query.filter_by(
            id=classe_id,
            ecole_id=ecole_id,
            annee_scolaire_id=annee_consultee.id,
        ).first_or_404()

    professeur = getattr(current_user, "professeur_rel", None)
    if not professeur:
        abort(403)
    if cours and cours.professeur_id != professeur.id:
        abort(403)
    if classe and not cours:
        cours = Cours.query.filter_by(
            classe_id=classe.id,
            professeur_id=professeur.id,
            ecole_id=ecole_id,
        ).order_by(Cours.nom.asc()).first()
        if not cours:
            abort(403)

    cours_disponibles = []
    if classe:
        cours_query = Cours.query.filter_by(
            classe_id=classe.id,
            ecole_id=ecole_id,
            professeur_id=professeur.id,
        )
        cours_disponibles = cours_query.order_by(Cours.nom.asc()).all()
        if not cours and cours_disponibles:
            cours = cours_disponibles[0]
            cours_id = cours.id

    if not classe or not cours:
        flash("Selectionnez une classe et un cours pour faire l'appel.", "warning")
        return redirect(url_for("main.absences"))

    inscriptions = (
        Inscription.query
        .options(selectinload(Inscription.eleve))
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_consultee.id,
            Inscription.classe_id == classe.id,
            Inscription.statut == "inscrit",
        )
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )
    inscription_ids = [ins.id for ins in inscriptions]

    absences_existantes = (
        Absence.query
        .filter(
            Absence.ecole_id == ecole_id,
            Absence.cours_id == cours.id,
            Absence.date_absence == date_appel,
            Absence.inscription_id.in_(inscription_ids),
        )
        .all()
        if inscription_ids
        else []
    )
    absences_par_inscription = {a.inscription_id: a for a in absences_existantes}

    if request.method == "POST":
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json

        if not can_mutate:
            msg = message_annee or "Les absences ne peuvent pas être modifiées pour cette année."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 403
            flash(msg, "warning")
            return redirect(url_for("main.faire_appel", classe_id=classe.id, cours_id=cours.id, date_appel=date_appel.isoformat()))

        if request.is_json:
            data = request.get_json() or {}
            raw_ids = data.get("absent_inscription_ids", [])
            absent_ids = {int(x) for x in raw_ids if str(x).isdigit()}
        else:
            absent_ids = {
                int(raw_id)
                for raw_id in request.form.getlist("absent_inscription_ids")
                if raw_id.isdigit()
            }
        absent_ids = absent_ids & set(inscription_ids)

        try:
            nouvelles_absences = []
            for absence in list(absences_existantes):
                if absence.inscription_id not in absent_ids:
                    db.session.delete(absence)

            for ins in inscriptions:
                if ins.id in absent_ids and ins.id not in absences_par_inscription:
                    absence = Absence(
                        date_absence=date_appel,
                        motif="Absence signalée pendant l'appel",
                        justifiee=False,
                        eleve_id=ins.eleve_id,
                        cours_id=cours.id,
                        ecole_id=ecole_id,
                        inscription_id=ins.id,
                    )
                    db.session.add(absence)
                    nouvelles_absences.append((absence, ins.eleve))

            db.session.commit()
            ecole = getattr(current_user, "ecole", None)
            for absence, eleve in nouvelles_absences:
                if not absence.justifiee:
                    _notifier_whatsapp_absence(absence, ecole=ecole, eleve=eleve, cours=cours)

            if is_ajax:
                return jsonify({
                    "success": True,
                    "absents_count": len(absent_ids),
                    "presents_count": max(len(inscriptions) - len(absent_ids), 0),
                    "message": "Appel enregistré avec succès."
                })
            flash("Appel enregistré avec succès.", "success")
            return redirect(url_for("main.faire_appel", classe_id=classe.id, cours_id=cours.id, date_appel=date_appel.isoformat()))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Erreur enregistrement appel professeur: %s", exc)
            if is_ajax:
                return jsonify({"success": False, "message": "Erreur lors de l'enregistrement de l'appel."}), 500
            flash("Erreur lors de l'enregistrement de l'appel.", "danger")

    absents_count = len(absences_par_inscription)
    effectif = len(inscriptions)

    return render_template(
        "faire_appel.html",
        classe=classe,
        cours=cours,
        cours_disponibles=cours_disponibles,
        inscriptions=inscriptions,
        absences_par_inscription=absences_par_inscription,
        date_appel=date_appel,
        effectif=effectif,
        absents_count=absents_count,
        presents_count=max(effectif - absents_count, 0),
        annee_consultee=annee_consultee,
        can_mutate=can_mutate,
        message_annee=message_annee,
    )
