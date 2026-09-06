# scripts/init_db.py
from app import create_app, db
from app.init_ecoles import init_ecoles_par_defaut
from app.init_superadmin import ensure_canonical_superadmin

app = create_app()

with app.app_context():
    init_ecoles_par_defaut(db)
    ensure_canonical_superadmin()

