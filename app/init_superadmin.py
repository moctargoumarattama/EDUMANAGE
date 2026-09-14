import os
import logging
from werkzeug.security import generate_password_hash

logger = logging.getLogger(__name__)

SUPERADMIN_EMAIL = os.getenv('SUPERADMIN_EMAIL', 'moctargoumarattama@gmail.com')


def _get_superadmin_password():
    password = os.getenv('SUPERADMIN_PASSWORD')
    if password:
        return password
    if os.getenv('APP_ENV', os.getenv('FLASK_ENV', 'development')).lower() in {'prod', 'production'}:
        raise RuntimeError("SUPERADMIN_PASSWORD est obligatoire en production.")
    return 'Alkaline0702'


def ensure_canonical_superadmin(db_session=None):
    """
    Assure la presence permanente du compte Super Administrateur canonique
    (moctargoumarattama@gmail.com).

    S'il est absent, il est cree immediatement.
    S'il est present mais inactif ou avec un role degrade, il est restaure en super_admin actif.
    """
    from app.models import Utilisateur, db

    session = db_session or db.session

    try:
        sa = session.query(Utilisateur).filter_by(email=SUPERADMIN_EMAIL).first()
        if not sa:
            sa = Utilisateur(
                nom='Attama',
                prenom='Moctar Goumar',
                email=SUPERADMIN_EMAIL,
                role='super_admin',
                mot_de_passe=generate_password_hash(_get_superadmin_password()),
                statut='actif',
                ecole_id=None
            )
            session.add(sa)
            session.commit()
            logger.info(f"[SUPER_ADMIN] Compte cree avec succes : {SUPERADMIN_EMAIL}")
        else:
            modifie = False
            if sa.role != 'super_admin':
                sa.role = 'super_admin'
                modifie = True
            if sa.statut != 'actif':
                sa.statut = 'actif'
                modifie = True
            if sa.ecole_id is not None:
                sa.ecole_id = None
                modifie = True
            if modifie:
                session.commit()
                logger.info(f"[SUPER_ADMIN] Compte restaure en super_admin : {SUPERADMIN_EMAIL}")
        return sa
    except Exception as e:
        session.rollback()
        logger.warning(f"[SUPER_ADMIN] Note initialisation: {e}")
        return None
