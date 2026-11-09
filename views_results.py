"""Admin: vote counts per position, publish, CSV export."""
import csv
import io

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.auth.services import record_audit
from app.election_utils import election_phase, results_published
from app.extensions import db
from app.models import Candidate, Election, ElectionPosition, Setting, User, Vote, VoteReceipt


def _tally(election):
    """One block per position: candidates with counts, abstain count and total."""
    positions = (ElectionPosition.query.filter_by(election_id=election.id)
                 .order_by(ElectionPosition.sort_order, ElectionPosition.id).all())
    blocks = []
    for pos in positions:
        counts = dict(
            db.session.query(Vote.candidate_id, db.func.count(Vote.id))
            .filter(Vote.election_position_id == pos.id).group_by(Vote.candidate_id).all()
        )
        people = (
            db.session.query(Candidate, User)
            .join(User, Candidate.user_id == User.id)
            .filter(Candidate.election_position_id == pos.id, Candidate.status == "approved").all()
        )
        entries = [{"name": u.full_name, "party": c.party_group, "votes": counts.get(c.id, 0)}
                   for c, u in people]
        entries.sort(key=lambda e: (-e["votes"], e["name"] or ""))
        abstain = counts.get(None, 0)
        total = sum(e["votes"] for e in entries) + abstain
        top = entries[0]["votes"] if entries else 0
        for e in entries:
            e["pct"] = round(e["votes"] * 100 / total) if total else 0
            e["leading"] = top > 0 and e["votes"] == top
        blocks.append({"position": pos, "entries": entries, "abstain": abstain, "total": total,
                       "abstain_pct": round(abstain * 100 / total) if total else 0})
    voters = (
        db.session.query(db.func.count(VoteReceipt.id))
        .filter(VoteReceipt.election_id == election.id).scalar() or 0
    )
    return blocks, voters


@admin_bp.route("/results")
def results():
    elections = (Election.query.filter(Election.status != "draft")
                 .order_by(Election.voting_start.desc()).all())
    election_id = request.args.get("election", type=int)
    election = next((e for e in elections if e.id == election_id), None) or (elections[0] if elections else None)
    blocks, voters, phase, published = [], 0, None, False
    if election:
        blocks, voters = _tally(election)
        phase = election_phase(election)
        published = results_published(election.id)
    return render_template("admin/results.html", elections=elections, election=election,
                           blocks=blocks, voters=voters, phase=phase, published=published)


@admin_bp.route("/results/<int:election_id>/publish", methods=["POST"])
def publish_results(election_id):
    election = db.session.get(Election, election_id) or abort(404)
    row = Setting.query.filter_by(key=f"results_published_{election.id}").first()
    currently = bool(row and row.value == "1")
    if not currently and election_phase(election) != "closed":
        flash("Results can only be published after the election is closed.", "warning")
        return redirect(url_for("admin.results", election=election.id))
    if row is None:
        row = Setting(key=f"results_published_{election.id}", value="0")
        db.session.add(row)
    row.value = "0" if currently else "1"
    record_audit("results_unpublished" if currently else "results_published",
                 actor_id=current_user.id, entity_type="election", entity_id=election.id,
                 details=election.title)
    db.session.commit()
    flash("Results are hidden from students again." if currently else "Results are now visible to students.", "success")
    return redirect(url_for("admin.results", election=election.id))


@admin_bp.route("/results/<int:election_id>/export.csv")
def export_results(election_id):
    election = db.session.get(Election, election_id) or abort(404)
    blocks, voters = _tally(election)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([election.title])
    writer.writerow(["Voters who took part", voters])
    writer.writerow([])
    writer.writerow(["Position", "Candidate", "Party or group", "Votes", "Share (%)"])
    for block in blocks:
        for e in block["entries"]:
            writer.writerow([block["position"].title, e["name"], e["party"] or "", e["votes"], e["pct"]])
        writer.writerow([block["position"].title, "Abstain", "", block["abstain"], block["abstain_pct"]])
    record_audit("results_exported", actor_id=current_user.id, entity_type="election",
                 entity_id=election.id, details=election.title)
    db.session.commit()
    name = "".join(ch if ch.isalnum() else "-" for ch in election.title).strip("-").lower() or "results"
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}-results.csv"'})
