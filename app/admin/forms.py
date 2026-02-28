from flask_wtf import FlaskForm
from app.utils import get_ecole_filter_query
from wtforms import SelectField, SubmitField
from wtforms.validators import DataRequired
from app.models import Ecole


class BackupSchoolForm(FlaskForm):
    ecole_id = SelectField("École", coerce=int, validators=[DataRequired()])
    submit = SubmitField("Créer la sauvegarde")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remplir dynamiquement la liste des écoles
        self.ecole_id.choices = [(e.id, e.nom) for e in get_ecole_filter_query(Ecole).all()]
