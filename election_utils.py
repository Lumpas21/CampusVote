"""Small helpers shared by the admin panel and the student pages."""
from app.models import Setting, utc_now

PHASE_LABELS = {
    "draft": "Draft",
    "upcoming": "Upcoming",
    "candidacy": "Applications open",
    "open": "Voting open",
    "closed": "Closed",
}


def election_phase(election, now=None):
    """draft | upcoming | candidacy | open | closed - worked out from status + dates."""
    now = now or utc_now()
    if election.status == "draft":
        return "draft"
    if election.status == "closed":
        return "closed"
    if election.voting_end and now > election.voting_end:
        return "closed"
    if election.voting_start and election.voting_end and election.voting_start <= now <= election.voting_end:
        return "open"
    if election.candidacy_start and election.candidacy_end and election.candidacy_start <= now <= election.candidacy_end:
        return "candidacy"
    return "upcoming"


def phase_label(phase):
    return PHASE_LABELS.get(phase, phase.title())


def results_published(election_id):
    row = Setting.query.filter_by(key=f"results_published_{election_id}").first()
    return bool(row and row.value == "1")
