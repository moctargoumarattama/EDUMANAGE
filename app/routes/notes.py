import io
import pandas as pd
from datetime import datetime
from flask import abort, flash, redirect, render_template, send_file, url_for, current_app
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from . import main
from .common import (
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
    NoteForm,
    Professeur,
    can_manage_note,
    db,
    get_ecole_courante,
    role_required,
)
from app.services.annees_scolaires import get_annee_consultee
from app.services.notes_annuelles import (
    MESSAGE_ANNEE_ARCHIVEE,
    MESSAGE_ANNEE_PLANIFIEE,
    calculer_statistiques_notes,
    creer_note,
    get_cours_annee,
    get_cours_choices_notes,
    get_eleves_choices_notes,
    get_inscriptions_notes,
    get_notes_annee,
    modifier_note as service_modifier_note,
    notes_modifiables,
    statut_annee_notes,
    supprimer_note as service_supprimer_note,
)


@main.route('/notes', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur', 'parent')
def notes():
    form = NoteForm()
    ecole_id = current_user.ecole_id

    # ------------------- Contexte annuel unique (Règle 2C-5D) -------------------
    annee_consultee = get_annee_consultee(ecole_id)
    message_annee = statut_annee_notes(annee_consultee)
    peut_modifier = notes_modifiables(annee_consultee, current_user)

    # ------------------- Choix élèves et cours -------------------
    if annee_consultee:
        eleve_choices = get_eleves_choices_notes(ecole_id, annee_consultee, user=current_user)
        form.eleve_id.choices = eleve_choices or [(0, "--- Aucun élève disponible ---")]

        cours_choices = get_cours_choices_notes(ecole_id, annee_consultee, user=current_user)
        form.cours_id.choices = cours_choices or [(0, "--- Aucun cours disponible ---")]

        if hasattr(form, 'annee_id'):
            form.annee_id.choices = [(annee_consultee.id, annee_consultee.nom)]
            form.annee_id.data = annee_consultee.id
    else:
        form.eleve_id.choices = [(0, "--- Aucun élève disponible ---")]
        form.cours_id.choices = [(0, "--- Aucun cours disponible ---")]
        if hasattr(form, 'annee_id'):
            form.annee_id.choices = [(0, "--- Aucune année disponible ---")]

    # ------------------- Ajout d'une note (POST) -------------------
    if form.validate_on_submit():
        if not peut_modifier:
            if annee_consultee and annee_consultee.statut == 'archivee':
                flash(MESSAGE_ANNEE_ARCHIVEE, "warning")
            elif annee_consultee and annee_consultee.statut == 'planifiee':
                flash(MESSAGE_ANNEE_PLANIFIEE, "warning")
            else:
                flash("Action non autorisée pour cette année scolaire.", "danger")
            return redirect(url_for('main.notes'))

        nouvelle_note, err = creer_note(
            ecole_id=ecole_id,
            annee=annee_consultee,
            user=current_user,
            eleve_id=form.eleve_id.data,
            cours_id=form.cours_id.data,
            valeur=form.valeur.data,
            coefficient=form.coefficient.data,
            type_evaluation=form.type_evaluation.data,
            periode=form.periode.data,
            date_evaluation=datetime.utcnow(),
        )

        if err:
            flash(err, "danger")
        else:
            flash("Note ajoutée avec succès", "success")
        return redirect(url_for('main.notes'))

    # ------------------- Récupération des notes et statistiques -------------------
    toutes_notes = get_notes_annee(
        ecole_id=ecole_id,
        annee=annee_consultee,
        user=current_user,
    )

    stats = calculer_statistiques_notes(toutes_notes)

    # Inscriptions pour accordéons / structure annuelle
    inscriptions = get_inscriptions_notes(ecole_id, annee_consultee, user=current_user)

    # Extraction des élèves uniques pour compatibilité templates
    eleves_uniques = []
    seen_eleves = set()
    for ins in inscriptions:
        if ins.eleve and ins.eleve.id not in seen_eleves:
            seen_eleves.add(ins.eleve.id)
            eleves_uniques.append(ins.eleve)

    tous_les_cours = get_cours_annee(ecole_id, annee_consultee, user=current_user)

    return render_template(
        'notes.html',
        form=form if peut_modifier else None,
        notes=toutes_notes,
        moyenne_generale=stats["moyenne_generale"],
        taux_reussite=stats["taux_reussite"],
        matieres_evaluees=stats["matieres_evaluees"],
        eleves=eleves_uniques,
        inscriptions=inscriptions,
        tous_les_cours=tous_les_cours,
        annee_active=annee_consultee,
        annee_consultee=annee_consultee,
        message_annee=message_annee,
        notes_modifiables=peut_modifier,
    )


@main.route('/notes/export_excel')
@login_required
@role_required('admin')
def export_notes_excel():
    """Export Excel de toutes les notes avec jointures élèves/cours pour l'année consultée."""
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)

    notes = get_notes_annee(
        ecole_id=ecole_id,
        annee=annee_consultee,
        user=current_user,
    )

    data = {
        'Date': [n.date_evaluation.strftime('%d/%m/%Y') if n.date_evaluation else '' for n in notes],
        'Élève': [f"{n.eleve.prenom} {n.eleve.nom}" if n.eleve else '' for n in notes],
        'Classe': [
            n.inscription.classe.nom if (n.inscription and n.inscription.classe)
            else (n.cours.classe.nom if (n.cours and n.cours.classe) else 'Sans classe')
            for n in notes
        ],
        'Cours': [n.cours.nom if n.cours else '' for n in notes],
        'Note': [n.valeur for n in notes],
        'Coefficient': [n.coefficient for n in notes],
        'Type': [n.type_evaluation for n in notes],
        'Période': [n.periode for n in notes],
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Notes', index=False)

    output.seek(0)
    suffix = f"_{annee_consultee.nom.replace('/', '-')}" if annee_consultee else ""
    return send_file(
        output,
        as_attachment=True,
        download_name=f"liste_notes{suffix}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@main.route('/note/<int:note_id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur')
def modifier_note(note_id):
    note = Note.query.get_or_404(note_id)
    if not can_manage_note(note):
        abort(403)

    ecole_id = current_user.ecole_id
    if note.ecole_id != ecole_id:
        abort(403)

    # Récupérer l'année scolaire de la note
    annee_note = note.annee
    if not annee_note and note.inscription:
        annee_note = note.inscription.annee_scolaire
    if not annee_note and note.annee_id:
        annee_note = AnneeScolaire.query.get(note.annee_id)

    # Contrôle de cycle de vie annuel
    if not annee_note or annee_note.statut != 'active':
        if annee_note and annee_note.statut == 'archivee':
            flash(MESSAGE_ANNEE_ARCHIVEE, "warning")
        elif annee_note and annee_note.statut == 'planifiee':
            flash(MESSAGE_ANNEE_PLANIFIEE, "warning")
        else:
            flash("Vous ne pouvez modifier une note que pour une année scolaire active.", "warning")
        return redirect(url_for('main.notes'))

    # Permissions professeurs
    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        if not professeur:
            professeur = Professeur.query.filter_by(
                utilisateur_id=current_user.id,
                ecole_id=ecole_id
            ).first()
        if not professeur or (note.cours and note.cours.professeur_id != professeur.id):
            abort(403)

    form = NoteForm(obj=note)

    # Choix limités à l'année de la note
    eleve_choices = get_eleves_choices_notes(ecole_id, annee_note, user=current_user)
    form.eleve_id.choices = eleve_choices or [(note.eleve_id, f"{note.eleve.prenom} {note.eleve.nom}" if note.eleve else "Élève")]

    cours_choices = get_cours_choices_notes(ecole_id, annee_note, user=current_user)
    form.cours_id.choices = cours_choices or [(note.cours_id, note.cours.nom if note.cours else "Cours")]

    if hasattr(form, 'annee_id'):
        form.annee_id.choices = [(annee_note.id, annee_note.nom)]
        form.annee_id.data = annee_note.id

    if form.validate_on_submit():
        _, err = service_modifier_note(
            ecole_id=ecole_id,
            annee=annee_note,
            user=current_user,
            note_id=note.id,
            valeur=form.valeur.data,
            coefficient=form.coefficient.data,
            type_evaluation=form.type_evaluation.data,
            periode=form.periode.data,
            eleve_id=form.eleve_id.data,
            cours_id=form.cours_id.data,
        )

        if err:
            flash(err, "danger")
        else:
            flash("Note modifiée avec succès", "success")
            return redirect(url_for('main.notes'))

    return render_template(
        'modifier_note.html',
        form=form,
        note=note,
        annee_note=annee_note,
    )


@main.route('/notes/supprimer/<int:note_id>', methods=['POST'])
@login_required
@role_required('admin', 'professeur')
def supprimer_note(note_id):
    note = Note.query.get_or_404(note_id)
    if not can_manage_note(note):
        abort(403)

    ecole_id = current_user.ecole_id
    if note.ecole_id != ecole_id:
        abort(403)

    annee_note = note.annee
    if not annee_note and note.inscription:
        annee_note = note.inscription.annee_scolaire
    if not annee_note and note.annee_id:
        annee_note = AnneeScolaire.query.get(note.annee_id)

    succes, err = service_supprimer_note(
        ecole_id=ecole_id,
        annee=annee_note,
        user=current_user,
        note_id=note.id,
    )

    if not succes:
        flash(err or "Erreur lors de la suppression de la note.", "danger")
    else:
        flash("Note supprimée avec succès.", "success")

    return redirect(url_for('main.notes'))
