from . import main
from .common import (
    abort,
    Absence,
    can_manage_absence,
    can_manage_cours,
    AbsenceForm,
    can_access_absence,
    can_access_cours,
    can_access_eleve,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Presence,
    ajouter_ecole_id,
    current_app,
    current_user,
    date,
    datetime,
    db,
    envoyer_email,
    filtre_par_ecole,
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
from flask import jsonify
from app.services.annees_scolaires import get_annee_consultee
from app.services.structure_annuelle import get_niveaux_annee
from app.services.absences_annuelles import (
    absences_modifiables,
    get_absences_annee,
    get_cours_choices_absences,
    get_eleves_choices_absences,
    statut_annee_absences,
    verifier_mutation_absence,
)


def _ecole_id_courante():
    return getattr(current_user, "ecole_id", None)


def _remplir_choix_absence(form, ecole_id, annee):
    form.eleve_id.choices = get_eleves_choices_absences(ecole_id, annee, current_user)
    form.cours_id.choices = get_cours_choices_absences(ecole_id, annee, current_user)


@main.route('/absences', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur', 'parent')
def absences():
    form = AbsenceForm()
    page = request.args.get('page', 1, type=int)
    per_page = 50
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    can_mutate = absences_modifiables(annee_consultee, current_user)
    message_annee = statut_annee_absences(annee_consultee)

    _remplir_choix_absence(form, ecole_id, annee_consultee)

    if form.validate_on_submit():
        if not can_mutate:
            flash(message_annee or "Les absences ne peuvent pas etre modifiees pour cette annee.", "warning")
            return redirect(url_for('main.absences'))

        try:
            eleve, cours, _inscription, error = verifier_mutation_absence(
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

            nouvelle_absence = Absence(
                date_absence=form.date_absence.data,
                motif=form.motif.data,
                justifiee=form.justifiee.data,
                eleve_id=form.eleve_id.data,
                cours_id=form.cours_id.data,
                ecole_id=ecole_id,
                inscription_id=_inscription.id if _inscription else None,
            )
            db.session.add(nouvelle_absence)
            db.session.commit()

            if eleve and eleve.email_parent and cours:
                sujet = f"Absence de {eleve.prenom} {eleve.nom}"
                message = f"""Bonjour,
Nous vous informons que {eleve.prenom} {eleve.nom} a ete absent(e) le {form.date_absence.data.strftime('%d/%m/%Y')}.
Motif: {form.motif.data}
Cours: {cours.nom}
Statut: {'Justifiee' if form.justifiee.data else 'Non justifiee'}

Cordialement,
L'equipe pedagogique"""
                try:
                    envoyer_email(eleve.email_parent, sujet, message)
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email absence: {e}")

            flash('Absence enregistree avec succes', 'success')
            return redirect(url_for('main.absences'))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'enregistrement de l'absence.", "danger")
            current_app.logger.error(f"Erreur ajout absence: {e}")

    absences_list = get_absences_annee(ecole_id, annee_consultee, current_user)

    search = (request.args.get('search') or request.args.get('q') or '').strip().lower()
    classe_id = request.args.get('classe_id', type=int) or request.args.get('classe', type=int)
    niveau_param = (request.args.get('niveau') or request.args.get('niveau_id') or '').strip()
    date_debut_str = request.args.get('date_debut')
    date_fin_str = request.args.get('date_fin')
    justifiee_param = request.args.get('justifiee')

    # Filtrage
    filtrees = []
    for a in absences_list:
        if classe_id:
            c = getattr(a, 'annee_classe', None)
            if not c or c.id != classe_id:
                continue
        if niveau_param:
            c = getattr(a, 'annee_classe', None)
            if not c:
                continue
            if str(niveau_param).isdigit():
                if getattr(c, 'niveau_id', None) != int(niveau_param) and str(c.niveau) != str(niveau_param):
                    continue
            elif str(c.niveau or '').strip().lower() != niveau_param.lower():
                continue
        if search:
            eleve_str = f"{a.eleve.prenom} {a.eleve.nom}".lower() if a.eleve else ""
            matricule = (getattr(a.eleve, 'code_parent', '') or '').lower() if a.eleve else ""
            cours_str = (a.cours.nom if a.cours else "").lower()
            motif_str = (a.motif or "").lower()
            if search not in eleve_str and search not in matricule and search not in cours_str and search not in motif_str:
                continue
        if justifiee_param in ('1', 'true', 'yes', 'justifiee'):
            if not a.justifiee:
                continue
        elif justifiee_param in ('0', 'false', 'no', 'non-justifiee'):
            if a.justifiee:
                continue
        if date_debut_str:
            try:
                d_deb = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
                if a.date_absence and a.date_absence < d_deb:
                    continue
            except ValueError:
                pass
        if date_fin_str:
            try:
                d_fin = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
                if a.date_absence and a.date_absence > d_fin:
                    continue
            except ValueError:
                pass
        filtrees.append(a)

    total = len(filtrees)
    start = (page - 1) * per_page
    end = start + per_page
    absences_paginated = filtrees[start:end]

    absences_justifiees = sum(1 for a in filtrees if a.justifiee)
    absences_non_justifiees = total - absences_justifiees
    show_form = can_mutate

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': total,
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
                for a in absences_paginated
            ]
        })

    niveaux_annee = get_niveaux_annee(ecole_id, annee_consultee.id) if (ecole_id and annee_consultee) else []
    classes = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_consultee.id).order_by(Classe.nom).all() if (ecole_id and annee_consultee) else []

    return render_template(
        'absences.html',
        form=form,
        absences=absences_paginated,
        absences_justifiees=absences_justifiees,
        absences_non_justifiees=absences_non_justifiees,
        show_form=show_form,
        page=page,
        per_page=per_page,
        total=total,
        classes=classes,
        niveaux_annee=niveaux_annee,
        annee_consultee=annee_consultee,
        can_mutate=can_mutate,
        message_annee=message_annee,
        search=search,
        classe_id=classe_id,
        niveau=niveau_param,
        date_debut=date_debut_str,
        date_fin=date_fin_str,
        justifiee=justifiee_param
    )
@main.route('/absences/export_excel')
@login_required
@role_required('admin')
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
