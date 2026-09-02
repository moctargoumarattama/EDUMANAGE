from . import main
from .common import (
    AnneeScolaire,
    CSRFForm,
    Ecole,
    current_user,
    datetime,
    db,
    flash,
    get_ecole_filter_query,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    url_for,
)


@main.route('/annees', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def gestion_annees():
    csrf_form = CSRFForm()

    # 🔹 Récupération des écoles accessibles
    if current_user.role == 'super_admin':
        ecoles = get_ecole_filter_query(Ecole).all()
        # Super admin peut tout voir
        annees = AnneeScolaire.query.options(
            db.selectinload(AnneeScolaire.ecole)
        ).order_by(AnneeScolaire.date_debut.desc()).all()
    else:
        # Si un admin gère une seule école
        ecoles = [current_user.ecole]
        # ⚠️ Si un admin gère plusieurs écoles, décommente :
        # ecoles = current_user.ecoles_gerees
        ecole_ids = [e.id for e in ecoles]

        annees = AnneeScolaire.query.options(
            db.selectinload(AnneeScolaire.ecole)
        ).filter(AnneeScolaire.ecole_id.in_(ecole_ids)).order_by(
            AnneeScolaire.date_debut.desc()
        ).all()

    if request.method == 'POST' and csrf_form.validate_on_submit():
        action = request.form.get('action')
        annee_id = request.form.get('annee_id')

        if action == 'activer' and annee_id:
            annee = AnneeScolaire.query.get(int(annee_id))
            if annee and annee.ecole_id in [e.id for e in ecoles]:
                # Désactiver uniquement les années de la même école
                AnneeScolaire.query.filter_by(ecole_id=annee.ecole_id).update({'statut': 'archivee'})
                annee.statut = 'active'
                db.session.commit()
                flash(f"L'année {annee.nom} est maintenant active.", "success")
            else:
                flash("Action non autorisée pour cette école.", "danger")

        elif action == 'ajouter':
            nom = request.form.get('nom')
            date_debut_str = request.form.get('date_debut')
            date_fin_str = request.form.get('date_fin')
            ecole_id = request.form.get('ecole_id')

            try:
                # ✅ Sécurisation : si l'admin n’a qu’une seule école, on force automatiquement
                if not ecole_id and len(ecoles) == 1:
                    ecole_id = ecoles[0].id

                if not (nom and date_debut_str and date_fin_str and ecole_id):
                    flash("Tous les champs sont obligatoires.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                ecole_id = int(ecole_id)
                if ecole_id not in [e.id for e in ecoles]:
                    flash("Vous ne pouvez pas créer une année pour cette école.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                date_debut = datetime.strptime(date_debut_str, "%Y-%m-%d").date()
                date_fin = datetime.strptime(date_fin_str, "%Y-%m-%d").date()

                nouvelle_annee = AnneeScolaire(
                    nom=nom,
                    date_debut=date_debut,
                    date_fin=date_fin,
                    statut='planifiee',
                    ecole_id=ecole_id  # ✅ Jamais None
                )
                db.session.add(nouvelle_annee)
                db.session.commit()
                flash(f"Nouvelle année {nom} ajoutée.", "success")

            except Exception as e:
                db.session.rollback()
                flash(f"Erreur lors de l'ajout: {str(e)}", "danger")

        return redirect(url_for('main.gestion_annees'))

    return render_template('gestion_annees.html', annees=annees, ecoles=ecoles, csrf_form=csrf_form)

@main.route('/changer_annee/<int:annee_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def changer_annee(annee_id):
    annee = AnneeScolaire.query.get_or_404(annee_id)
    try:
        # Désactiver toutes les années de la même école
        AnneeScolaire.query.filter_by(ecole_id=annee.ecole_id).update({'statut': 'archivee'})
        # Activer l'année sélectionnée
        annee.statut = 'active'
        db.session.commit()
        flash(f"L'année {annee.nom} est maintenant active.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de l'activation: {str(e)}", "danger")

    return redirect(request.referrer or url_for('main.gestion_annees'))
