from flask import Blueprint


main = Blueprint("main", __name__)


from .common import envoyer_email, envoyer_email_smtp, envoyer_sms, limiter
from app.services import (
    check_ecole_access,
    generer_alertes_automatiques,
    generer_bulletin_pdf,
    get_cache,
    get_qr_cache_path,
    get_statistics,
    notifier_alertes,
    set_cache,
)

from . import auth
from . import eleves
from . import professeurs
from . import cours
from . import notes
from . import paiements
from . import absences
from . import dashboards
from . import rapports
from . import bulletins
from . import qrcode
from . import alertes
from . import emplois_temps
from . import utilisateurs
from . import classes
from . import sync
from . import ecoles
from . import annees
from . import errors
