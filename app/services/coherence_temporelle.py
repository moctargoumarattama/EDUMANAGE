from __future__ import annotations


MESSAGE_HEURE_FIN_APRES_DEBUT = "L'heure de fin doit être postérieure à l'heure de début."
MESSAGE_DATE_FIN_APRES_DEBUT = "La date de fin doit être postérieure à la date de début."


def valider_intervalle_dates(date_debut, date_fin, *, autoriser_meme_date=False):
    if not date_debut or not date_fin:
        return False, "Les dates de début et de fin sont obligatoires."
    if autoriser_meme_date:
        if date_fin < date_debut:
            return False, MESSAGE_DATE_FIN_APRES_DEBUT
    elif date_fin <= date_debut:
        return False, MESSAGE_DATE_FIN_APRES_DEBUT
    return True, None


def valider_intervalle_heures(heure_debut, heure_fin):
    if not heure_debut or not heure_fin:
        return False, "Les heures de début et de fin sont obligatoires."
    if heure_fin <= heure_debut:
        return False, MESSAGE_HEURE_FIN_APRES_DEBUT
    return True, None


def intervalles_se_chevauchent(debut_a, fin_a, debut_b, fin_b):
    return debut_a < fin_b and fin_a > debut_b
