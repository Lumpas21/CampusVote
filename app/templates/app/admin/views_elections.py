"""Admin: create, edit, close and delete elections."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.auth.services import record_audit
from app.election_utils import election_phase
from app.extensions import db
from app.models import (
    Candidate, College, Election, ElectionPosition, Program, Section, Vote, YearLevel, utc_now,
)
from app.timeutil import parse_local_input

TYPES = {"department": "Department", "organization": "Organization", "classroom": "Classroom"}
DEFAULT_POSITIONS = {
    "department": ["Governor", "Vice Governor", "Secretary", "Treasurer", "Auditor", "PIO"],
    "organization": ["President", "Vice President", "Secretary", "Treasurer", "Auditor", "PIO"],
    "classroom": ["President", "Vice President", "Secretary", "Treasurer", "Auditor", "PIO"],
}
DATE_FIELDS = ("candidacy_start", "candidacy_end", "voting_start", "voting_end")
DATE_LABELS = {
    "candidacy_start": "Candidacy start", "candidacy_end": "Candidacy end",
    "voting_start": "Voting start", "voting_end": "Voting end",
}


def type_label(value):
    return TYPES.get(value, (value or "").replace("_", " ").title())


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_activity(election):
    """True once students applied or voted - then type, scope and used positions are frozen."""
    pos_ids = [p.id for p in ElectionPosition.query.filter_by(election_id=election.id).all()]
    if not pos_ids:
        return False
    return bool(
        Candidate.query.filter(Candidate.election_position_id.in_(pos_ids)).first()
        or Vote.query.filter(Vote.election_position_id.in_(pos_ids)).first()
    )


def _form_context(values, errors, election=None, locked=False):
    sections = (
        db.session.query(Section, YearLevel)
        .join(YearLevel, Section.year_level_id == YearLevel.id)
        .order_by(YearLevel.sort_order, Section.code).all()
    )
    return dict(
        values=values, errors=errors, election=election, locked=locked,
        types=TYPES, defaults=DEFAULT_POSITIONS,
        colleges=College.query.order_by(College.code).all(),
        programs=Program.query.order_by(Program.code).all(),
        sections=sections,
    )


def _values_from_election(e):
    from app.timeutil import local_input
    v = {
        "title": e.title, "type": e.type if e.type in TYPES else "department",
        "college_id": e.scope_college_id, "program_id": e.scope_program_id,
        "section_id": e.scope_section_id,
        "org_name": e.scope_label if e.type == "organization" else "",
        "rules": e.rules or "",
        "positions": [p.title for p in ElectionPosition.query.filter_by(election_id=e.id)
                      .order_by(ElectionPosition.sort_order, ElectionPosition.id)],
    }
    for key in DATE_FIELDS:
        v[key] = local_input(getattr(e, key))
    return v


def _read_form():
    f = request.form
    seen, positions = set(), []
    for raw in f.getlist("positions"):
        name = " ".join(raw.split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            positions.append(name)
    v = {
        "title": f.get("title", "").strip(),
        "type": f.get("type", ""),
        "college_id": _int(f.get("college_id")),
        "program_id": _int(f.get("program_id")),
        "section_id": _int(f.get("section_id")),
        "org_name": f.get("org_name", "").strip(),
        "rules": f.get("rules", "").strip(),
        "positions": positions,
    }
    for key in DATE_FIELDS:
        v[key] = f.get(key, "").strip()
    return v


def _validate(v, strict, lock_scope):
    """Returns (errors, parsed_dates, scope). `strict` = needed for a published election."""
    errors, dates, scope = {}, {}, {}
    if not v["title"]:
        errors["title"] = "Enter the election title."
    elif len(v["title"]) > 200:
        errors["title"] = "Keep the title under 200 characters."
    if len(v["rules"]) > 3000:
        errors["rules"] = "Keep the rules under 3000 characters."

    if not lock_scope:
        if v["type"] not in TYPES:
            errors["type"] = "Choose a type."
        elif v["type"] == "department":
            college = db.session.get(College, v["college_id"]) if v["college_id"] else None
            if college is None:
                errors["scope"] = "Choose the college."
            else:
                scope = dict(scope_college_id=college.id, scope_program_id=None,
                             scope_section_id=None, scope_label=college.name[:100])
        elif v["type"] == "classroom":
            section = db.session.get(Section, v["section_id"]) if v["section_id"] else None
            program = db.session.get(Program, v["program_id"]) if v["program_id"] else None
            if section is None:
                errors["scope"] = "Choose the section."
            else:
                label = f"{program.code} {section.code}" if program else f"Section {section.code}"
                scope = dict(scope_college_id=None, scope_program_id=program.id if program else None,
                             scope_section_id=section.id, scope_label=label)
        else:  # organization
            if not v["org_name"]:
                errors["scope"] = "Enter the organization name."
            elif len(v["org_name"]) > 100:
                errors["scope"] = "Keep the name under 100 characters."
            else:
                scope = dict(scope_college_id=None, scope_program_id=None,
                             scope_section_id=None, scope_label=v["org_name"])

    for key in DATE_FIELDS:
        try:
            dates[key] = parse_local_input(v[key])
        except ValueError:
            errors[key] = "Enter a valid date and time."
            dates[key] = None
        if strict and dates[key] is None and key not in errors:
            errors[key] = f"{DATE_LABELS[key]} is required."
    if not any(k in errors for k in DATE_FIELDS):
        cs, ce, vs, ve = (dates[k] for k in DATE_FIELDS)
        if cs and ce and ce <= cs:
            errors["candidacy_end"] = "Candidacy must end after it starts."
        if ce and vs and vs < ce:
            errors["voting_start"] = "Voting should start after candidacy ends."
        if vs and ve and ve <= vs:
            errors["voting_end"] = "Voting must end after it starts."

    if not v["positions"]:
        errors["positions"] = "Add at least one position."
    elif len(v["positions"]) > 20 or any(len(p) > 100 for p in v["positions"]):
        errors["positions"] = "Use up to 20 positions, each under 100 characters."
    return errors, dates, scope


# --------------------------------------------------------------------- list
@admin_bp.route("/elections")
def elections():
    kind = request.args.get("type", "all")
    query = Election.query
    if kind in TYPES:
        query = query.filter_by(type=kind)
    now = utc_now()
    rows = [(e, election_phase(e, now)) for e in query.order_by(Election.created_at.desc()).all()]
    return render_template("admin/elections.html", rows=rows, kind=kind,
                           types=TYPES, type_label=type_label)


# ---------------------------------------------------------------------- new
@admin_bp.route("/elections/new", methods=["GET", "POST"])
def new_election():
    values = {"title": "", "type": "department", "college_id": None, "program_id": None,
              "section_id": None, "org_name": "", "rules": "",
              "positions": list(DEFAULT_POSITIONS["department"]),
              **{k: "" for k in DATE_FIELDS}}
    errors = {}
    if request.method == "POST":
        values = _read_form()
        publish = request.form.get("action") == "publish"
        errors, dates, scope = _validate(values, strict=publish, lock_scope=False)
        if not errors:
            election = Election(title=values["title"], type=values["type"],
                                rules=values["rules"] or None,
                                status="published" if publish else "draft", **scope, **dates)
            db.session.add(election)
            db.session.flush()
            for order, name in enumerate(values["positions"], start=1):
                db.session.add(ElectionPosition(election_id=election.id, title=name, sort_order=order))
            record_audit("election_published" if publish else "election_created",
                         actor_id=current_user.id, entity_type="election", entity_id=election.id,
                         details=election.title)
            db.session.commit()
            flash("Election published." if publish else "Draft saved.", "success")
            return redirect(url_for("admin.elections"))
        flash("Please fix the highlighted fields.", "danger")
    return render_template("admin/election_form.html", **_form_context(values, errors))


# --------------------------------------------------------------------- edit
@admin_bp.route("/elections/<int:election_id>/edit", methods=["GET", "POST"])
def edit_election(election_id):
    election = db.session.get(Election, election_id) or abort(404)
    if election_phase(election) == "closed":
        flash("Closed elections cannot be edited. Open Results to see the counts.", "warning")
        return redirect(url_for("admin.elections"))
    locked = _has_activity(election)
    values = _values_from_election(election)
    errors = {}

    if request.method == "POST":
        values = _read_form()
        action = request.form.get("action")
        publishing = election.status == "draft" and action == "publish"
        strict = election.status != "draft" or publishing
        if locked:      # type and scope stay as they were
            values.update(type=election.type, college_id=election.scope_college_id,
                          program_id=election.scope_program_id, section_id=election.scope_section_id,
                          org_name=election.scope_label if election.type == "organization" else "")
        errors, dates, scope = _validate(values, strict=strict, lock_scope=locked)

        existing = {p.title.lower(): p for p in ElectionPosition.query.filter_by(election_id=election.id)}
        wanted = {name.lower() for name in values["positions"]}
        if "positions" not in errors:
            for key, pos in existing.items():
                if key not in wanted and (
                    Candidate.query.filter_by(election_position_id=pos.id).first()
                    or Vote.query.filter_by(election_position_id=pos.id).first()
                ):
                    errors["positions"] = f"{pos.title} already has applications or votes and cannot be removed."
                    break

        if not errors:
            election.title = values["title"]
            election.rules = values["rules"] or None
            for key, value in {**dates, **scope}.items():
                setattr(election, key, value)
            if not locked:
                election.type = values["type"]
            for key, pos in existing.items():
                if key not in wanted:
                    db.session.delete(pos)
            for order, name in enumerate(values["positions"], start=1):
                pos = existing.get(name.lower())
                if pos is None:
                    db.session.add(ElectionPosition(election_id=election.id, title=name, sort_order=order))
                else:
                    pos.sort_order = order
            if publishing:
                election.status = "published"
            record_audit("election_published" if publishing else "election_updated",
                         actor_id=current_user.id, entity_type="election", entity_id=election.id,
                         details=election.title)
            db.session.commit()
            flash("Election published." if publishing else "Changes saved.", "success")
            return redirect(url_for("admin.elections"))
        flash("Please fix the highlighted fields.", "danger")
    return render_template("admin/election_form.html", **_form_context(values, errors, election, locked))


# ------------------------------------------------------------ close / delete
@admin_bp.route("/elections/<int:election_id>/close", methods=["POST"])
def close_election(election_id):
    election = db.session.get(Election, election_id) or abort(404)
    if election.status == "draft":
        flash("Publish the election before closing it, or delete the draft.", "warning")
    elif election_phase(election) == "closed":
        flash("This election is already closed.", "warning")
    else:
        election.status = "closed"
        record_audit("election_closed", actor_id=current_user.id, entity_type="election",
                     entity_id=election.id, details=election.title)
        db.session.commit()
        flash(f"{election.title} is closed. Voting and applications have stopped.", "success")
    return redirect(url_for("admin.elections"))


@admin_bp.route("/elections/<int:election_id>/delete", methods=["POST"])
def delete_election(election_id):
    election = db.session.get(Election, election_id) or abort(404)
    phase = election_phase(election)
    if phase not in ("draft", "upcoming", "candidacy") or _has_activity(election):
        flash("Only elections with no applications or votes can be deleted. Close it instead.", "warning")
        return redirect(url_for("admin.elections"))
    title = election.title
    ElectionPosition.query.filter_by(election_id=election.id).delete()
    db.session.delete(election)
    record_audit("election_deleted", actor_id=current_user.id, entity_type="election",
                 entity_id=election_id, details=title)
    db.session.commit()
    flash("Election deleted.", "success")
    return redirect(url_for("admin.elections"))
