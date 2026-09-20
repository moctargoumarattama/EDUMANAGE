from datetime import datetime, timedelta


SCHOOL_DELETION_DELAY_DAYS = 30
SCHOOL_DISABLED_STATUSES = {"bloque", "suspendu", "inactive"}
SCHOOL_ACTIVE_STATUS = "actif"
SCHOOL_DELETE_CONFIRMATION_PHRASE = "SUPPRIMER DÉFINITIVEMENT"


def utcnow():
    return datetime.utcnow()


def is_school_disabled(ecole):
    return bool(ecole and (ecole.statut or "").lower() in SCHOOL_DISABLED_STATUSES)


def school_deletion_available_at(ecole):
    disabled_at = getattr(ecole, "disabled_at", None)
    if not is_school_disabled(ecole) or not disabled_at:
        return None
    return disabled_at + timedelta(days=SCHOOL_DELETION_DELAY_DAYS)


def is_school_deletion_eligible(ecole, now=None):
    available_at = school_deletion_available_at(ecole)
    if not available_at:
        return False
    return (now or utcnow()) >= available_at


def days_until_school_deletion(ecole, now=None):
    available_at = school_deletion_available_at(ecole)
    if not available_at:
        return None
    remaining = available_at - (now or utcnow())
    if remaining.total_seconds() <= 0:
        return 0
    return max(1, remaining.days + (1 if remaining.seconds or remaining.microseconds else 0))
