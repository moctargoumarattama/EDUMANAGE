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
from flask import url_for, make_response
from app.services import get_qr_cache_path
from app.services.bulletin_verification import (
    generer_token_eleve,
    decoder_token_eleve,
    generer_qr_code_buffer,
)


@main.route('/eleve/<int:id>/qrcode')
@login_required
@role_required('admin')
def generer_qrcode_eleve(id):
    eleve = Eleve.query.get_or_404(id)
    if eleve.ecole_id != current_user.ecole_id:
        abort(403)

    # Résoudre la classe via l'inscription de l'année active (indépendamment de annee_consultee)
    annee_active = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id, statut='active').first()
    ins = Inscription.query.filter_by(
        ecole_id=current_user.ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee_active.id
    ).first() if annee_active else None

    if ins:
        token = generer_token_eleve(current_user.ecole_id, ins.id)
        scan_url = url_for('main.verifier_eleve_public', token=token, _external=True)
    else:
        scan_url = url_for('main.voir_eleve', eleve_id=eleve.id, _external=True)

    cache_path = get_qr_cache_path(eleve)
    img_buf = generer_qr_code_buffer(scan_url)

    # Sauvegarder dans le cache pour rétrocompatibilité
    with open(cache_path, "wb") as f:
        f.write(img_buf.getvalue())

    return send_file(cache_path, mimetype='image/png',
                     download_name=f"qrcode_{eleve.prenom}_{eleve.nom}.png")


@main.route('/api/qr/info/<int:eleve_id>')
def api_qr_info(eleve_id):
    """Endpoint public minimal de résolution QR code."""
    eleve = Eleve.query.get_or_404(eleve_id)
    ecole_id = eleve.ecole_id

    # Toujours résoudre selon l'année ACTIVE
    annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut='active').first()
    ins = Inscription.query.filter_by(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee_active.id
    ).first() if annee_active else None

    return {
        'eleve_id': eleve.id,
        'nom': f"{eleve.prenom} {eleve.nom}",
        'ecole': eleve.ecole.nom if eleve.ecole else '',
        'annee_scolaire': annee_active.nom if annee_active else None,
        'classe': ins.classe.nom if (ins and ins.classe) else 'Aucune inscription active',
        'statut': ins.statut if ins else 'Non inscrit',
    }


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

    qrcodes_par_classe = {}

    for ins in inscriptions:
        e = ins.eleve
        if not e:
            continue
        classe_obj = ins.classe
        classe_nom = classe_obj.nom if classe_obj else 'Sans classe'

        token = generer_token_eleve(ecole_id, ins.id)
        scan_url = url_for('main.verifier_eleve_public', token=token, _external=True)

        # Génération du QR code en mémoire directement
        img_buf = generer_qr_code_buffer(scan_url)
        img_data = base64.b64encode(img_buf.getvalue()).decode()

        # Mettre à jour le fichier cache physique pour rétrocompatibilité
        cache_path = get_qr_cache_path(e)
        with open(cache_path, "wb") as f:
            f.write(img_buf.getvalue())

        if classe_nom not in qrcodes_par_classe:
            qrcodes_par_classe[classe_nom] = {
                'classe': classe_obj,
                'classe_nom': classe_nom,
                'classe_id': classe_obj.id if classe_obj else 0,
                'classe_niveau': getattr(classe_obj, 'niveau', None) if classe_obj else None,
                'qrcodes': []
            }

        qrcodes_par_classe[classe_nom]['qrcodes'].append({
            'eleve': e,
            'qr': img_data,
            'inscription': ins,
            'classe_nom': classe_nom,
            'annee_nom': annee_active.nom,
            'scan_url': scan_url,
        })

    return render_template(
        'qrcodes_etudiants.html',
        qrcodes_par_classe=qrcodes_par_classe,
        annee_active=annee_active,
    )


@main.route('/verifier/eleve/<token>')
@main.route('/verifier/etudiant/<token>')
def verifier_eleve_public(token):
    """
    Page publique d'authentification de l'identité scolaire d'un élève (carte scolaire / badge).
    Mobile-first, sans connexion.
    Affiche UNIQUEMENT l'identité minimale :
    - Nom et prénom
    - Matricule
    - École
    - Classe
    - Année scolaire
    Aucun contact, parent, note, absence, paiement ou autre donnée privée.
    Exclue de l'indexation (noindex, nofollow, noarchive) et du cache (Cache-Control: no-store, private).
    """
    ecole_id, inscription_id = decoder_token_eleve(token)

    if not ecole_id or not inscription_id:
        resp = make_response(render_template(
            'verifier_eleve.html',
            valide=False,
            message_erreur="Ce QR code étudiant est introuvable ou n'est pas valide."
        ), 404)
        resp.headers["Cache-Control"] = "no-store, private, must-revalidate"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return resp

    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=ecole_id).first()

    if not inscription or not inscription.eleve:
        resp = make_response(render_template(
            'verifier_eleve.html',
            valide=False,
            message_erreur="L'inscription de cet élève est introuvable ou n'est plus active."
        ), 404)
        resp.headers["Cache-Control"] = "no-store, private, must-revalidate"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return resp

    eleve = inscription.eleve
    classe = inscription.classe
    annee = inscription.annee_scolaire
    ecole = inscription.ecole or eleve.ecole

    matricule = eleve.code_parent or f"#{eleve.id}"

    resp = make_response(render_template(
        'verifier_eleve.html',
        valide=True,
        eleve=eleve,
        matricule=matricule,
        classe=classe,
        annee=annee,
        ecole=ecole,
        inscription=inscription,
    ), 200)

    resp.headers["Cache-Control"] = "no-store, private, must-revalidate"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return resp
