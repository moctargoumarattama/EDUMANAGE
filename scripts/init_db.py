# scripts/init_db.py
from app import create_app, db
from app.services.niveaux import ensure_standard_niveaux
from app.init_superadmin import ensure_canonical_superadmin

app = create_app()

with app.app_context():
    niveaux = ensure_standard_niveaux(commit=True)
    sa = ensure_canonical_superadmin()
    print(f"[OK] Initialisation technique : {len(niveaux)} niveaux scolaires, Super Admin : {getattr(sa, 'email', 'N/A')}")


