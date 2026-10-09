"""Gestion administrative des dispenses de matière."""
from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import main
from .common import role_required
from app.authorization import tenant_required
from app.models import Cours, DispenseMatiere, Inscription
from app.services.dispenses import ANNEE_ENTIERE, accorder_dispense, revoquer_dispense
from app.services.notes_annuelles import periodes_notes_classe


@main.route('/inscriptions/<int:inscription_id>/dispenses', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@tenant_required
def gerer_dispenses(inscription_id):
    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=g.ecole_id).first_or_404()
    annee = inscription.annee_scolaire
    if request.method == 'POST':
        if not annee or annee.statut != 'active':
            abort(403)
        dispense, erreur = accorder_dispense(
            ecole_id=g.ecole_id,
            inscription_id=inscription.id,
            cours_id=request.form.get('cours_id', type=int),
            periode=request.form.get('periode', ANNEE_ENTIERE),
            reference=request.form.get('reference_justificatif', ''),
            utilisateur_id=current_user.id,
        )
        flash(erreur or 'Dispense médicale enregistrée.', 'warning' if erreur else 'success')
        return redirect(url_for('main.gerer_dispenses', inscription_id=inscription.id))

    cours = Cours.query.filter_by(ecole_id=g.ecole_id, classe_id=inscription.classe_id).order_by(Cours.nom).all()
    dispenses = DispenseMatiere.query.filter_by(
        ecole_id=g.ecole_id, inscription_id=inscription.id
    ).order_by(DispenseMatiere.date_creation.desc()).all()
    return render_template(
        'dispenses_eleve.html', inscription=inscription, cours=cours,
        dispenses=dispenses,
        periodes=periodes_notes_classe(inscription.classe, annee),
        annee_modifiable=bool(annee and annee.statut == 'active'),
        annee_entiere=ANNEE_ENTIERE,
    )


@main.route('/dispenses/<int:dispense_id>/annuler', methods=['POST'])
@login_required
@role_required('admin')
@tenant_required
def annuler_dispense(dispense_id):
    dispense = DispenseMatiere.query.filter_by(id=dispense_id, ecole_id=g.ecole_id).first_or_404()
    _, erreur = revoquer_dispense(g.ecole_id, dispense.id, current_user.id)
    flash(erreur or 'Dispense annulée.', 'warning' if erreur else 'success')
    return redirect(url_for('main.gerer_dispenses', inscription_id=dispense.inscription_id))
