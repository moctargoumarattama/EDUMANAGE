# scripts/init_db.py
from app import create_app
from app.init_ecoles import init_ecoles_par_defaut

app = create_app()

with app.app_context():
    init_ecoles_par_defaut()
