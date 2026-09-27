from types import SimpleNamespace


def niveaux_depuis_classes(classes):
    niveaux_par_id = {}
    for classe in classes:
        niveau = getattr(classe, "niveau_scolaire", None)
        if niveau and niveau.id not in niveaux_par_id:
            niveaux_par_id[niveau.id] = niveau
            continue
        niveau_nom = (getattr(classe, "niveau", None) or "").strip()
        if niveau_nom and f"legacy:{niveau_nom}" not in niveaux_par_id:
            niveaux_par_id[f"legacy:{niveau_nom}"] = SimpleNamespace(
                id=niveau_nom,
                nom=niveau_nom,
                ordre=999,
            )
    return sorted(niveaux_par_id.values(), key=lambda niveau: (niveau.ordre, niveau.nom))
