from . import main
from .common import (
    AnneeScolaire,
    can_access_cours,
    can_access_eleve,
    can_access_note,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
    NoteForm,
    Professeur,
    current_app,
    current_user,
    datetime,
    db,
    envoyer_email,
    filtre_par_ecole,
    flash,
    get_ecole_courante,
    io,
    login_required,
    redirect,
    render_template,
    role_required,
    send_file,
    url_for,
)
import pandas as pd
from app.utils import get_annee_active


@main.route('/notes', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'enseignant', 'professeur', 'parent')
def notes():
    form = NoteForm()
    ecole_courante = get_ecole_courante()

    # ------------------- Récupération de l'année active -------------------
    annee_active = get_annee_active(current_user.ecole_id)

    # ------------------- Gestion des élèves selon rôle -------------------
    if current_user.role == 'parent':
        enfants = filtre_par_ecole(Eleve.query.filter_by(parent_id=current_user.id), Eleve).all()
        eleves = enfants
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]
        form.eleve_id.render_kw = {'disabled': True} if len(enfants) == 1 else {}

    elif current_user.role in ['enseignant', 'professeur']:
        professeur = filtre_par_ecole(Professeur.query.filter_by(utilisateur_id=current_user.id), Professeur).first()
        if professeur:
            cours_prof = filtre_par_ecole(Cours.query.filter_by(professeur_id=professeur.id), Cours).all()
            cours_ids = [c.id for c in cours_prof]
            eleves = (
                filtre_par_ecole(Eleve.query.join(Inscription), Eleve)
                .filter(Inscription.cours_id.in_(cours_ids)).all()
            )
            form.eleve_id.choices = [
                (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
                for e in eleves
            ]
            form.cours_id.choices = [(c.id, c.nom) for c in cours_prof]
        else:
            eleves = []
            form.eleve_id.choices = []
            form.cours_id.choices = []
            flash("Aucun professeur n'est associé à votre compte.", "warning")

    else:  # admin
        eleves = filtre_par_ecole(Eleve.query.outerjoin(Classe).order_by(Classe.nom, Eleve.nom), Eleve).all()
        form.eleve_id.choices = [(e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}") for e in eleves]
        form.cours_id.choices = [(c.id, c.nom) for c in filtre_par_ecole(Cours.query.order_by(Cours.nom), Cours).all()]

    # ------------------- Pré-remplissage année scolaire -------------------
    if hasattr(form, 'annee_id'):
        annees = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).order_by(AnneeScolaire.nom.desc()).all()
        form.annee_id.choices = [(a.id, a.nom) for a in annees]
        if annee_active:
            form.annee_id.data = annee_active.id
        elif annees:
            form.annee_id.data = annees[0].id

    # ------------------- Ajout d'une note -------------------
    if form.validate_on_submit():
        if current_user.role == 'parent':
            flash("Vous n'êtes pas autorisé à ajouter des notes.", "danger")
            return redirect(url_for('main.notes'))

        cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()
        if not cours:
            flash("Cours introuvable pour cette école.", "danger")
            return redirect(url_for('main.notes'))

        eleve = filtre_par_ecole(Eleve.query.filter_by(id=form.eleve_id.data), Eleve).first()
        if not eleve or not can_access_eleve(eleve) or not can_access_cours(cours):
            flash("Acces non autorise pour cet eleve ou ce cours.", "danger")
            return redirect(url_for('main.notes'))
        if cours.classe_id and eleve.classe_id != cours.classe_id:
            flash("Cet eleve n'appartient pas a la classe de ce cours.", "danger")
            return redirect(url_for('main.notes'))

        if cours.classe_id:
            annee = cours.classe.annee_scolaire if cours.classe else None
            if not annee or annee.ecole_id != cours.ecole_id:
                flash("La classe de ce cours n'est associée à aucune année scolaire valide.", "danger")
                return redirect(url_for('main.notes'))
        else:
            annee = get_annee_active(cours.ecole_id)
            if not annee:
                flash("Aucune année scolaire active n'est disponible pour ce cours.", "danger")
                return redirect(url_for('main.notes'))

        annee_id = annee.id

        # Vérifier le droit de l'enseignant sur le cours
        if current_user.role in ['enseignant', 'professeur']:
            professeur = filtre_par_ecole(Professeur.query.filter_by(utilisateur_id=current_user.id), Professeur).first()
            if not cours or cours.professeur_id != professeur.id:
                flash("Vous ne pouvez pas ajouter de notes pour ce cours.", "danger")
                return redirect(url_for('main.notes'))

        # Création et sauvegarde
        try:
            nouvelle_note = Note(
                valeur=form.valeur.data,
                coefficient=form.coefficient.data,
                type_evaluation=form.type_evaluation.data,
                periode=form.periode.data,
                eleve_id=form.eleve_id.data,
                cours_id=form.cours_id.data,
                date_evaluation=datetime.utcnow(),
                ecole_id=current_user.ecole_id,
                annee_id=annee_id
            )
            db.session.add(nouvelle_note)
            db.session.commit()

            # Journalisation JSON pour SQLite
            import json
            current_app.log_correction(
                action="ajout",
                description=f"Note ajoutée pour l'élève {nouvelle_note.eleve_id} en cours {nouvelle_note.cours_id}",
                ecole_id=nouvelle_note.ecole_id,
                cible_type="note",
                cible_id=nouvelle_note.id,
                ancienne_valeur=None,
                nouvelle_valeur=json.dumps({
                    "valeur": nouvelle_note.valeur,
                    "coefficient": nouvelle_note.coefficient,
                    "type_evaluation": nouvelle_note.type_evaluation,
                    "periode": nouvelle_note.periode,
                    "eleve_id": nouvelle_note.eleve_id,
                    "cours_id": nouvelle_note.cours_id
                }),
                niveau="info"
            )

            # Notification email
            eleve = filtre_par_ecole(Eleve.query.filter_by(id=form.eleve_id.data), Eleve).first()
            cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()
            if eleve and eleve.email_parent and cours:
                sujet = f"Nouvelle note en {cours.nom}"
                message = f"""Bonjour,

Une nouvelle note a été ajoutée pour {eleve.prenom} {eleve.nom} en {cours.nom}:
- Note: {form.valeur.data}/20
- Type: {form.type_evaluation.data}
- Coefficient: {form.coefficient.data}
- Période: {form.periode.data}

Connectez-vous au portail parent pour plus de détails.

Cordialement,
L'équipe pédagogique"""
                try:
                    envoyer_email(eleve.email_parent, sujet, message)
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email note: {e}")

            flash('Note ajoutée avec succès', 'success')
            return redirect(url_for('main.notes'))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'ajout de la note.", "danger")
            current_app.logger.error(f"Erreur ajout note: {e}")

    # ------------------- Filtrage affichage notes selon année -------------------
    if current_user.role == 'parent':
        enfants_ids = [e.id for e in filtre_par_ecole(Eleve.query.filter_by(parent_id=current_user.id), Eleve).all()]
        query_notes = Note.query.filter(Note.eleve_id.in_(enfants_ids))
        if annee_active:
            query_notes = query_notes.filter_by(annee_id=annee_active.id)
        toutes_notes = filtre_par_ecole(query_notes.order_by(Note.date_evaluation.desc()), Note).all()

    elif current_user.role in ['enseignant', 'professeur']:
        professeur = filtre_par_ecole(Professeur.query.filter_by(utilisateur_id=current_user.id), Professeur).first()
        if professeur:
            cours_ids = [c.id for c in filtre_par_ecole(Cours.query.filter_by(professeur_id=professeur.id), Cours).all()]
            query_notes = Note.query.filter(Note.cours_id.in_(cours_ids))
            if annee_active:
                query_notes = query_notes.filter_by(annee_id=annee_active.id)
            toutes_notes = filtre_par_ecole(query_notes.order_by(Note.date_evaluation.desc()), Note).all()
        else:
            toutes_notes = []

    else:  # admin
        query_notes = Note.query
        if annee_active:
            query_notes = query_notes.filter_by(annee_id=annee_active.id)
        toutes_notes = filtre_par_ecole(query_notes.order_by(Note.date_evaluation.desc()), Note).all()

    # ------------------- Statistiques -------------------
    if toutes_notes:
        total_pondere = sum(n.valeur * n.coefficient for n in toutes_notes)
        total_coefficients = sum(n.coefficient for n in toutes_notes)
        moyenne_generale = round(total_pondere / total_coefficients, 2) if total_coefficients else 0
        notes_reussites = sum(1 for n in toutes_notes if n.valeur >= 10)
        taux_reussite = round((notes_reussites / len(toutes_notes)) * 100, 1)
        matieres_evaluees = len(set(n.cours_id for n in toutes_notes))
    else:
        moyenne_generale = taux_reussite = matieres_evaluees = 0

    return render_template(
        'notes.html',
        form=form,
        notes=toutes_notes,
        moyenne_generale=moyenne_generale,
        taux_reussite=taux_reussite,
        matieres_evaluees=matieres_evaluees,
        eleves=eleves,
        tous_les_cours=filtre_par_ecole(Cours.query.order_by(Cours.nom), Cours).all(),
        annee_active=annee_active
    )

@main.route('/notes/export_excel')
@login_required
@role_required('admin')
def export_notes_excel():
    """Export Excel de toutes les notes avec jointures élèves/cours"""
    notes = filtre_par_ecole(Note.query.join(Eleve).join(Cours).order_by(Note.date_evaluation.desc()), Note).all()
    
    data = {
        'Date': [n.date_evaluation.strftime('%d/%m/%Y') for n in notes],
        'Élève': [f"{n.eleve.prenom} {n.eleve.nom}" for n in notes],
        'Classe': [n.eleve.classe.nom if n.eleve.classe else 'Sans classe' for n in notes],
        'Cours': [n.cours.nom for n in notes],
        'Note': [n.valeur for n in notes],
        'Coefficient': [n.coefficient for n in notes],
        'Type': [n.type_evaluation for n in notes]
    }
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Notes', index=False)
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name="liste_notes.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@main.route('/note/<int:note_id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'enseignant')
def modifier_note(note_id):
    note = Note.query.get_or_404(note_id)
    if not can_access_note(note):
        flash("Acces non autorise a cette note.", "danger")
        return redirect(url_for('main.notes'))

    # --- Récupérer l'école courante ---
    ecole = get_ecole_courante()

    # --- Vérification de l'année active de l'école ---
    annee_active = AnneeScolaire.query.filter_by(id=note.annee_id, ecole_id=ecole.id, statut='active').first()
    if not annee_active:
        flash("Vous ne pouvez modifier une note que pour une année scolaire active de votre école.", "warning")
        return redirect(url_for('main.notes'))

    # --- Vérification permissions enseignants ---
    if current_user.role == 'enseignant':
        professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
        if not professeur or (note.cours and note.cours.professeur_id != professeur.id):
            flash("Vous n'êtes pas autorisé à modifier cette note.", "danger")
            return redirect(url_for('main.notes'))

    form = NoteForm(obj=note)

    # --- Forcer l'année existante pour l'élève et le cours ---
    if form.eleve_id.data is None:
        form.eleve_id.data = note.eleve_id
    if form.cours_id.data is None:
        form.cours_id.data = note.cours_id
    if form.annee_id.data is None:
        form.annee_id.data = note.annee_id

    # --- Soumission formulaire ---
    if form.validate_on_submit():
        if current_user.role == 'enseignant':
            cours = Cours.query.get(form.cours_id.data)
            if not cours or cours.professeur_id != professeur.id:
                flash("Vous ne pouvez pas modifier cette note.", "danger")
                return redirect(url_for('main.notes'))

        # Mise à jour
        eleve = filtre_par_ecole(Eleve.query.filter_by(id=form.eleve_id.data), Eleve).first()
        cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()
        if not eleve or not cours or not can_access_eleve(eleve) or not can_access_cours(cours):
            flash("Acces non autorise pour cet eleve ou ce cours.", "danger")
            return redirect(url_for('main.notes'))
        if cours.classe_id and eleve.classe_id != cours.classe_id:
            flash("Cet eleve n'appartient pas a la classe de ce cours.", "danger")
            return redirect(url_for('main.notes'))

        note.valeur = form.valeur.data
        note.coefficient = form.coefficient.data
        note.type_evaluation = form.type_evaluation.data
        note.periode = form.periode.data
        note.eleve_id = form.eleve_id.data
        note.cours_id = form.cours_id.data
        note.annee_id = form.annee_id.data

        db.session.commit()
        flash("Note modifiée avec succès", "success")
        return redirect(url_for('main.notes'))

    return render_template('modifier_note.html', form=form, note=note)

@main.route('/notes/supprimer/<int:note_id>', methods=['POST'])
@login_required
@role_required('admin', 'enseignant', 'professeur')
def supprimer_note(note_id):
    # Récupérer la note et la supprimer
    note = Note.query.get_or_404(note_id)
    if not can_access_note(note):
        flash("Acces non autorise a cette note.", "danger")
        return redirect(url_for('main.notes'))
    db.session.delete(note)
    db.session.commit()
    flash("Note supprimée avec succès.", "success")
    return redirect(url_for('main.notes'))
