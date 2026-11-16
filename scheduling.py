"""SQLite scheduling operations for the local CareConnect demonstration."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import time

SERVICES = ("Primary care", "Follow-up")
KINDS = ("In person", "Virtual")


class ScheduleError(ValueError):
    pass


def connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@contextmanager
def transaction(path):
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def seed(path, now):
    with closing(connect(path)) as db, db:
        db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        db.executemany("INSERT INTO clinics VALUES (?,?,?,?,?)", [
            (1, "Pine River Clinic", "Pine River", "807-555-0101", 1),
            (2, "Cedar Bay Clinic", "Cedar Bay", "807-555-0102", 0),
            (3, "North Ridge Clinic", "North Ridge", "807-555-0103", 0),
        ])
        db.executemany("INSERT INTO users VALUES (?,?,?,?)", [
            (1, "Patient A", "patient", None), (2, "Patient B", "patient", None),
            (3, "Patient C", "patient", None), (4, "Pine River Staff", "staff", 1),
            (5, "Cedar Bay Staff", "staff", 2),
        ])
        db.executemany("INSERT INTO registration VALUES (?,?)", [(1, 1), (2, 1)])
        db.executemany("INSERT INTO slots VALUES (?,?,?,?,?)", [
            (1, 1, SERVICES[0], KINDS[0], now + 86400),
            (2, 2, SERVICES[0], KINDS[1], now + 90000),
            (3, 3, SERVICES[1], KINDS[0], now + 93600),
        ])
        db.execute("INSERT INTO waitlist VALUES (1,3,1,?,?,?,?,?,1)",
                   (SERVICES[0], KINDS[0], now, now + 7 * 86400, now - 60))


def one(db, query, *params):
    return db.execute(query, params).fetchone()


def require(condition, message):
    if not condition:
        raise ScheduleError(message)


def get_slot(db, slot_id):
    slot = one(db, """SELECT slots.*, clinics.registered_only FROM slots
                     JOIN clinics ON clinics.id=slots.clinic WHERE slots.id=?""", slot_id)
    require(slot, "Appointment slot was not found.")
    return slot


def eligibility(db, patient, slot, ignore_booking=0):
    if slot["registered_only"] and not one(db, "SELECT 1 FROM registration WHERE patient=? AND clinic=?",
                                          patient, slot["clinic"]):
        return "Registration required"
    conflict = one(db, """SELECT 1 FROM bookings b JOIN slots s ON s.id=b.slot
        WHERE b.patient=? AND b.id<>? AND b.status IN ('Requested','Confirmed')
        AND (ABS(s.starts-?)<1800 OR s.service=?)""",
        patient, ignore_booking, slot["starts"], slot["service"])
    return "Existing booking conflict" if conflict else ""


def audit(db, release, entry, reason, now):
    db.execute("INSERT INTO audit(release,entry,reason,recorded) VALUES (?,?,?,?)",
               (release, entry, reason, now))


def reconsider(db, offer, outcome, now):
    db.execute("UPDATE offers SET status=? WHERE id=?", (outcome, offer["id"]))
    slot = get_slot(db, offer["slot"])
    if slot["starts"] > now:
        db.execute("UPDATE releases SET reviewed=0 WHERE id=?", (offer["release"],))
    audit(db, offer["release"], offer["entry"], outcome, now)


def expire(db, now):
    for offer in db.execute("SELECT * FROM offers WHERE status='Offered' AND deadline<=?", (now,)).fetchall():
        reconsider(db, offer, "Expired", now)


def available(db, filters, now):
    query = """SELECT s.*, c.name, c.community, c.phone, c.registered_only FROM slots s
        JOIN clinics c ON c.id=s.clinic WHERE s.starts>?
        AND NOT EXISTS(SELECT 1 FROM bookings b WHERE b.slot=s.id AND b.status IN ('Requested','Confirmed'))
        AND NOT EXISTS(SELECT 1 FROM offers o WHERE o.slot=s.id AND o.status='Offered')
        AND NOT EXISTS(SELECT 1 FROM releases r WHERE r.slot=s.id AND r.reviewed=0)"""
    params = [now]
    for key, column in (("community", "c.community"), ("service", "s.service"), ("kind", "s.kind")):
        if filters.get(key):
            query += f" AND {column}=?"
            params.append(filters[key])
    if filters.get("day"):
        query += " AND date(s.starts,'unixepoch')=?"
        params.append(filters["day"])
    return db.execute(query + " ORDER BY s.starts,s.id", params).fetchall()


def request(db, actor, slot_id, now):
    slot = get_slot(db, slot_id)
    allowed = {row["id"] for row in available(db, {}, now)}
    require(slot_id in allowed, "This appointment is no longer available.")
    reason = eligibility(db, actor["id"], slot)
    require(not reason, reason)
    db.execute("INSERT INTO bookings(slot,patient,status) VALUES (?,?,'Requested')", (slot_id, actor["id"]))
    return "Appointment requested."


def review_booking(db, actor, booking_id, action, now):
    booking = one(db, "SELECT * FROM bookings WHERE id=?", booking_id)
    require(booking, "Booking was not found.")
    slot = get_slot(db, booking["slot"])
    status = booking["status"]
    if action in ("confirm", "decline"):
        require(actor["role"] == "staff" and actor["clinic"] == slot["clinic"], "Access denied.")
        require(status == "Requested", "This request has already been reviewed.")
        if action == "confirm":
            require(slot["starts"] > now, "The appointment has already started.")
            reason = eligibility(db, booking["patient"], slot, booking_id)
            require(not reason, reason)
        outcome = "Confirmed" if action == "confirm" else "Declined"
    else:
        require(actor["role"] == "patient" and actor["id"] == booking["patient"], "Access denied.")
        require(status in ("Confirmed", "Requested") and slot["starts"] > now, "This booking cannot be released.")
        outcome = "Cancelled" if status == "Confirmed" else "Withdrawn"
    db.execute("UPDATE bookings SET status=? WHERE id=?", (outcome, booking_id))
    if outcome != "Confirmed" and slot["starts"] > now:
        previous = one(db, """SELECT o.* FROM offers o JOIN releases r ON r.id=o.release
            WHERE o.slot=? AND o.patient=? AND o.status='Accepted'
            AND r.id=(SELECT MAX(id) FROM releases WHERE slot=?) ORDER BY o.id DESC LIMIT 1""",
            slot["id"], booking["patient"], slot["id"])
        if outcome == "Declined" and previous:
            db.execute("UPDATE releases SET reviewed=0 WHERE id=?", (previous["release"],))
            audit(db, previous["release"], previous["entry"], "Staff declined", now)
        else:
            db.execute("INSERT INTO releases(slot) VALUES (?)", (slot["id"],))
    return f"Booking {outcome.lower()}."


def join(db, actor, form, now):
    clinic = int(form["clinic"])
    service, kind = form["service"], form["kind"]
    require(one(db, "SELECT 1 FROM clinics WHERE id=?", clinic) and service in SERVICES and kind in KINDS,
            "Choose a valid clinic, service and visit type.")
    days = int(form["days"])
    require(1 <= days <= 30, "Choose a date range of 1 to 30 days.")
    require(not one(db, """SELECT 1 FROM waitlist WHERE patient=? AND clinic=? AND service=?
                          AND kind=? AND active=1""", actor["id"], clinic, service, kind),
            "You already have this waitlist entry.")
    db.execute("""INSERT INTO waitlist(patient,clinic,service,kind,earliest,latest,added)
                  VALUES (?,?,?,?,?,?,?)""", (actor["id"], clinic, service, kind, now, now + days * 86400, now))
    return "Joined the waitlist."


def candidates(db, release, slot):
    entries = db.execute("""SELECT w.*, u.name FROM waitlist w JOIN users u ON u.id=w.patient
        WHERE w.active=1 AND w.clinic=? ORDER BY w.added,w.id""", (slot["clinic"],)).fetchall()
    for entry in entries:
        if (entry["service"] != slot["service"] or entry["kind"] != slot["kind"]
                or not entry["earliest"] <= slot["starts"] <= entry["latest"]):
            reason = "Preferences do not match"
        elif one(db, "SELECT 1 FROM offers WHERE release=? AND entry=?", release, entry["id"]):
            reason = "Already offered this opening"
        elif one(db, "SELECT 1 FROM offers WHERE patient=? AND status='Offered'", entry["patient"]):
            reason = "Already has an active offer"
        else:
            reason = eligibility(db, entry["patient"], slot)
        yield entry, reason


def issue(db, actor, release_id, now, offer_seconds):
    release = one(db, "SELECT * FROM releases WHERE id=? AND reviewed=0", release_id)
    require(release, "This release has already been reviewed.")
    slot = get_slot(db, release["slot"])
    require(actor["clinic"] == slot["clinic"] and slot["starts"] > now, "This slot cannot be reviewed.")
    for entry, reason in candidates(db, release_id, slot):
        audit(db, release_id, entry["id"], reason or "Selected and offered", now)
        if not reason:
            db.execute("""INSERT INTO offers(release,slot,entry,patient,deadline,status)
                VALUES (?,?,?,?,?,'Offered')""", (release_id, slot["id"], entry["id"], entry["patient"],
                                                  min(slot["starts"], now + offer_seconds)))
            db.execute("UPDATE releases SET reviewed=1 WHERE id=?", (release_id,))
            return f"Offer issued to {entry['name']}."
    audit(db, release_id, None, "No eligible patient; slot returned to search", now)
    db.execute("UPDATE releases SET reviewed=1 WHERE id=?", (release_id,))
    return "No eligible patients. The slot is available again."


def respond(db, actor, offer_id, action, now):
    offer = one(db, "SELECT * FROM offers WHERE id=? AND patient=?", offer_id, actor["id"])
    require(offer and offer["status"] == "Offered" and offer["deadline"] > now,
            "This offer is no longer available.")
    if action == "reject_offer":
        reconsider(db, offer, "Declined", now)
        return "Offer declined."
    slot = get_slot(db, offer["slot"])
    entry = one(db, "SELECT active FROM waitlist WHERE id=?", offer["entry"])
    reason = eligibility(db, actor["id"], slot)
    if reason or not entry["active"]:
        reconsider(db, offer, "Ineligible", now)
        return "Offer released because eligibility changed."
    db.execute("UPDATE offers SET status='Accepted' WHERE id=?", (offer_id,))
    db.execute("INSERT INTO bookings(slot,patient,status) VALUES (?,?,'Requested')", (slot["id"], actor["id"]))
    audit(db, offer["release"], offer["entry"], "Accepted; awaiting staff review", now)
    return "Offer accepted. Staff confirmation is pending."
