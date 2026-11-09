"""Student voting: guidelines -> face check -> ballot -> review -> face check -> receipt.

Ballot secrecy: VoteReceipt says THAT a student voted, Vote says WHAT was chosen,
and nothing connects the two rows.
"""
import hashlib
import hmac
import secrets
import time

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.auth.services import record_audit, require_face_verification
from app.election_utils import election_phase, results_published
from app.extensions import db
from app.main import main_bp
from app.models import Candidate, Election, ElectionPosition, User, Vote, VoteReceipt, utc_now

FLOW_KEY = "vote_flow"
FLOW_MAX_SECONDS = 30 * 60      # the whole voting session
FACE2_MAX_SECONDS = 5 * 60      # time allowed between the 2nd face check and pressing "Cast"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I


# ------------------------------------------------------------------ helpers
def is_eligible(user, election):
    if election.scope_college_id and (user.program is None or user.program.college_id != election.scope_college_id):
        return False
    if election.scope_program_id and user.program_id != election.scope_program_id:
        return False
    if election.scope_section_id and user.section_id != election.scope_section_id:
        return False
    return True


def _election_or_404(election_id):
    election = db.session.get(Election, election_id)
    if election is None or election.status != "published" or not is_eligible(current_user, election):
        abort(404)
    return election


def _receipt_for(user_id, election_id):
    return VoteReceipt.query.filter_by(voter_id=user_id, election_id=election_id).first()


def _ballot_positions(election):
    """Each position with its approved candidates: [{position, candidates:[(Candidate, User)]}]."""
    positions = (ElectionPosition.query.filter_by(election_id=election.id)
                 .order_by(ElectionPosition.sort_order, ElectionPosition.id).all())
    rows = []
    for pos in positions:
        people = (db.session.query(Candidate, User)
                  .join(User, Candidate.user_id == User.id)
                  .filter(Candidate.election_position_id == pos.id, Candidate.status == "approved")
                  .order_by(User.full_name).all())
        rows.append({"position": pos, "candidates": people})
    return rows


def _gate(election):
    """Returns a redirect response if this student may not vote right now, else None."""
    if current_user.role != "student":
        flash("Only student accounts can vote.", "warning")
        return redirect(url_for("main.elections"))
    if _receipt_for(current_user.id, election.id):
        return redirect(url_for("main.vote_receipt", election_id=election.id))
    if election_phase(election) != "open":
        flash("Voting for this election is not open.", "warning")
        return redirect(url_for("main.elections"))
    if current_user.is_locked:
        flash("Your account is locked. Please contact the election committee.", "danger")
        return redirect(url_for("main.elections"))
    if not current_user.email_verified:
        flash("Verify your school email before voting.", "warning")
        return redirect(url_for("main.settings"))
    if current_user.face_status != "enrolled":
        flash("Set up face verification before you vote.", "warning")
        return redirect(url_for("auth.face_setup"))
    return None


def _flow(election, key=None):
    """The student's current voting session for this election (or None)."""
    flow = session.get(FLOW_KEY)
    if (not flow or flow.get("user_id") != current_user.id or flow.get("election_id") != election.id
            or time.time() - flow.get("started", 0) > FLOW_MAX_SECONDS):
        return None
    if key and key not in flow:
        return None
    return flow


def _save_flow(flow):
    session[FLOW_KEY] = flow
    session.modified = True


def _new_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"CV-{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _hash_code(code):
    key = current_app.config["SECRET_KEY"]
    key = key.encode() if isinstance(key, str) else key
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------- pages
@main_bp.route("/elections/<int:election_id>/vote")
@login_required
def vote_guidelines(election_id):
    election = _election_or_404(election_id)
    blocked = _gate(election)
    if blocked:
        return blocked
    ballot = _ballot_positions(election)
    return render_template("vote_guidelines.html", election=election, ballot=ballot)


@main_bp.route("/elections/<int:election_id>/vote/start")
@login_required
@require_face_verification
def vote_start(election_id):
    """Reached only after the FIRST face check passes (the decorator sends students to it)."""
    election = _election_or_404(election_id)
    blocked = _gate(election)
    if blocked:
        return blocked
    _save_flow({"user_id": current_user.id, "election_id": election.id,
                "started": time.time(), "face1": True})
    return redirect(url_for("main.vote_ballot", election_id=election.id))


@main_bp.route("/elections/<int:election_id>/vote/ballot")
@login_required
def vote_ballot(election_id):
    election = _election_or_404(election_id)
    blocked = _gate(election)
    if blocked:
        return blocked
    flow = _flow(election, "face1")
    if not flow:
        return redirect(url_for("main.vote_guidelines", election_id=election.id))
    chosen = {int(k): v for k, v in (flow.get("choices") or {}).items()}
    return render_template("vote_ballot.html", election=election,
                           ballot=_ballot_positions(election), chosen=chosen)


@main_bp.route("/elections/<int:election_id>/vote/review", methods=["GET", "POST"])
@login_required
def vote_review(election_id):
    election = _election_or_404(election_id)
    blocked = _gate(election)
    if blocked:
        return blocked
    flow = _flow(election, "face1")
    if not flow:
        return redirect(url_for("main.vote_guidelines", election_id=election.id))
    ballot = _ballot_positions(election)

    if request.method == "POST":
        choices = {}
        for row in ballot:
            pos = row["position"]
            raw = (request.form.get(f"p{pos.id}") or "").strip()
            valid = {str(c.id) for c, _ in row["candidates"]}
            if raw == "abstain" or (not row["candidates"] and raw in ("", "abstain")):
                choices[str(pos.id)] = None
            elif raw in valid:
                choices[str(pos.id)] = int(raw)
            else:
                flash(f"Please choose a candidate (or abstain) for {pos.title}.", "warning")
                return redirect(url_for("main.vote_ballot", election_id=election.id))
        flow["choices"] = choices
        flow.pop("face2_at", None)
        _save_flow(flow)
        return redirect(url_for("main.vote_review", election_id=election.id))

    choices = flow.get("choices")
    if not choices:
        return redirect(url_for("main.vote_ballot", election_id=election.id))
    summary = []
    for row in ballot:
        pos = row["position"]
        picked = choices.get(str(pos.id))
        match = next(((c, u) for c, u in row["candidates"] if c.id == picked), None) if picked else None
        summary.append({"position": pos, "candidate": match})
    return render_template("vote_review.html", election=election, summary=summary)


@main_bp.route("/elections/<int:election_id>/vote/submit")
@login_required
@require_face_verification
def vote_submit(election_id):
    """Reached only after the SECOND face check passes. Shows the final 'Cast my vote' button."""
    election = _election_or_404(election_id)
    blocked = _gate(election)
    if blocked:
        return blocked
    flow = _flow(election, "choices")
    if not flow:
        flash("Your voting session expired. Please start again.", "warning")
        return redirect(url_for("main.vote_guidelines", election_id=election.id))
    flow["face2_at"] = time.time()
    _save_flow(flow)
    return render_template("vote_submit.html", election=election)


@main_bp.route("/elections/<int:election_id>/vote/cast", methods=["POST"])
@login_required
def vote_cast(election_id):
    election = _election_or_404(election_id)
    blocked = _gate(election)
    if blocked:
        return blocked
    flow = _flow(election, "choices")
    if not flow or time.time() - flow.get("face2_at", 0) > FACE2_MAX_SECONDS:
        flash("Please verify your face again before casting your vote.", "warning")
        return redirect(url_for("main.vote_review", election_id=election.id)
                        if flow else url_for("main.vote_guidelines", election_id=election.id))

    ballot = _ballot_positions(election)
    choices = flow["choices"]
    rows = []
    for row in ballot:
        pos = row["position"]
        picked = choices.get(str(pos.id), "missing")
        if picked == "missing":
            flash("Your ballot is incomplete. Please review it again.", "warning")
            return redirect(url_for("main.vote_ballot", election_id=election.id))
        if picked is not None and picked not in {c.id for c, _ in row["candidates"]}:
            flash("A candidate on your ballot is no longer available. Please choose again.", "warning")
            return redirect(url_for("main.vote_ballot", election_id=election.id))
        rows.append((pos.id, picked))

    code = _new_code()
    stamp = utc_now().replace(minute=0, second=0, microsecond=0)
    try:
        db.session.add(VoteReceipt(voter_id=current_user.id, election_id=election.id,
                                   receipt_code=code, receipt_hash=_hash_code(code)))
        db.session.flush()           # the unique (voter, election) rule stops a second vote
        for pos_id, cand_id in rows:
            db.session.add(Vote(election_position_id=pos_id, candidate_id=cand_id, cast_at=stamp))
        record_audit("vote_cast", actor_id=current_user.id, entity_type="election",
                     entity_id=election.id, details="vote submitted")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("You have already voted in this election.", "warning")
        return redirect(url_for("main.vote_receipt", election_id=election.id))

    session.pop(FLOW_KEY, None)
    return redirect(url_for("main.vote_receipt", election_id=election.id))


@main_bp.route("/elections/<int:election_id>/vote/receipt")
@login_required
def vote_receipt(election_id):
    election = _election_or_404(election_id)
    receipt = _receipt_for(current_user.id, election.id)
    if receipt is None:
        return redirect(url_for("main.vote_guidelines", election_id=election.id))
    return render_template("vote_receipt.html", election=election, receipt=receipt,
                           results_open=results_published(election.id))


@main_bp.route("/elections/<int:election_id>/results")
@login_required
def student_results(election_id):
    election = _election_or_404(election_id)
    if not results_published(election.id):
        flash("Results have not been published yet.", "warning")
        return redirect(url_for("main.elections"))
    from app.admin.views_results import _tally   # same numbers the committee sees
    blocks, voters = _tally(election)
    return render_template("student_results.html", election=election, blocks=blocks, voters=voters)


@main_bp.app_context_processor
def _elections_page_context():
    """Gives the Elections list the 'already voted' and 'results published' sets."""
    if request.endpoint != "main.elections" or not current_user.is_authenticated:
        return {}
    voted = {r.election_id for r in VoteReceipt.query.filter_by(voter_id=current_user.id)}
    shown = {e.id for e in Election.query.filter_by(status="published")
             if results_published(e.id)}
    return {"voted_ids": voted, "results_ids": shown}
