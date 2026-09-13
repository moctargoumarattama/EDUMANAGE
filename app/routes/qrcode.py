from . import main
from .common import (
    AnneeScolaire,
    Classe,
    Eleve,
    Inscription,
    abort,
    current_user,
    db,
    flash,
    login_required,
    os,
    render_template,
    role_required,
    send_file,
    professeur_classes,
)
import qrcode
from app.services import get_qr_cache_path


@main.route('/eleve/<int:id>/qrcode')
@login_required
@role_required('admin')
def generer_qrcode_eleve(id):
    eleve = Eleve.query.get_or_404(id)
    if eleve.ecole_id != current_user.ecole_id:
        abort(403)

    # Résoudre la classe via l'inscription de l'année active
    annee_active = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id, statut='active').first()
    ins = Inscription.query.filter_by(
        ecole_id=current_user.ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee_active.id
    ).first() if annee_active else None

    if not ins or not ins.classe:
        abort(404)
    classe_nom = ins.classe.nom

    cache_path = get_qr_cache_path(eleve)

    # → Si existe → renvoyer directement
    if os.path.exists(cache_path):
        return send_file(cache_path, mimetype='image/png',
                         download_name=f"qrcode_{eleve.prenom}_{eleve.nom}.png")

    # Sinon générer
    data = (
        f"ÉLÈVE: {eleve.prenom} {eleve.nom}\n"
        f"CLASSE: {classe_nom}\n"
        f"DATE NAISSANCE: {eleve.date_naissance.strftime('%d/%m/%Y') if eleve.date_naissance else 'Non renseignée'}\n"
        f"TÉLÉPHONE: {eleve.telephone or 'Non renseigné'}\n"
        f"EMAIL: {eleve.email or 'Non renseigné'}\n"
    )

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image()

    img.save(cache_path)

    return send_file(cache_path, mimetype='image/png',
                     download_name=f"qrcode_{eleve.prenom}_{eleve.nom}.png")


@main.route('/qrcodes_etudiants')
@login_required
@role_required('admin', 'professeur')
def qrcodes_etudiants():
    from collections import defaultdict
    import base64
    from app.models import Cours

    ecole_id = current_user.ecole_id

    # 1. Vérification stricte de l'année ACTIVE de l'école (indépendamment de la session)
    annees_actives = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut='active').all()

    if not annees_actives:
        flash("Aucune année scolaire active.", "warning")
        return render_template('qrcodes_etudiants.html', qrcodes_par_classe={}, annee_active=None)

    if len(annees_actives) > 1:
        flash("Incohérence détectée : plusieurs années scolaires sont marquées comme actives.", "danger")
        return render_template('qrcodes_etudiants.html', qrcodes_par_classe={}, annee_active=None)

    annee_active = annees_actives[0]

    # 2. Population : Inscription de l'année ACTIVE uniquement
    ins_query = (
        Inscription.query
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .options(
            db.joinedload(Inscription.eleve),
            db.joinedload(Inscription.classe),
            db.joinedload(Inscription.annee_scolaire),
        )
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_active.id,
        )
    )

    # 3. Restriction Professeur : uniquement ses classes dans l'année active
    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        if not professeur:
            abort(403)
        classes_prof_query = Classe.query.filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee_active.id,
            db.or_(
                Classe.professeur_id == professeur.id,
                Classe.id.in_(
                    db.session.query(professeur_classes.c.classe_id)
                    .filter(professeur_classes.c.professeur_id == professeur.id)
                ),
                Classe.id.in_(
                    db.session.query(Cours.classe_id)
                    .filter(Cours.professeur_id == professeur.id, Cours.ecole_id == ecole_id)
                )
            )
        )
        classes_prof_ids = [c.id for c in classes_prof_query.all()]
        ins_query = ins_query.filter(Inscription.classe_id.in_(classes_prof_ids))

    inscriptions = ins_query.order_by(Inscription.classe_id, Eleve.nom, Eleve.prenom).all()

    qrcodes_par_classe = defaultdict(list)

    for ins in inscriptions:
        e = ins.eleve
        if not e:
            continue
        classe_nom = ins.classe.nom if ins.classe else 'Sans classe'

        cache_path = get_qr_cache_path(e)

        # Génère si manquant
        if not os.path.exists(cache_path):
            data = f"{e.prenom} {e.nom}\nClasse: {classe_nom}"
            qr = qrcode.make(data)
            qr.save(cache_path)

        # Charger en base64
        with open(cache_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()

        qrcodes_par_classe[classe_nom].append({
            'eleve': e,
            'qr': img_data,
            'inscription': ins,
            'classe_nom': classe_nom,
            'annee_nom': annee_active.nom,
        })

    return render_template(
        'qrcodes_etudiants.html',
        qrcodes_par_classe=qrcodes_par_classe,
        annee_active=annee_active,
    )
