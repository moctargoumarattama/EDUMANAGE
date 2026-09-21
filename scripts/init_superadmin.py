# scripts/init_superadmin.py
import sys, os
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.abspath('.'))

from app import create_app
from app.init_superadmin import ensure_canonical_superadmin

app = create_app()

with app.app_context():
    sa = ensure_canonical_superadmin()
    if sa:
        print(f"[OK] Super Administrateur verifie : {sa.email} (ID: {sa.id}, Role: {sa.role})")
    else:
        print("[ERREUR] Impossible d'initialiser le super administrateur")
