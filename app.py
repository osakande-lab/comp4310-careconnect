"""Run a local, synthetic-data-only CareConnect demo."""
import argparse
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import time
from datetime import datetime, UTC
from urllib.parse import parse_qs, urlsplit

import scheduling as s


def h(value):
    return escape(str(value or ""), quote=True)


def date(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M UTC")


def options(values, selected="", blank=None):
    entries = ([("", blank)] if blank else []) + list(values)
    return "".join(f'<option value="{h(key)}"{" selected" if str(key) == selected else ""}>{h(label)}</option>'
                   for key, label in entries)


def form(token, action, content, label, target=0):
    return (f'<form method="post" action="/action"><input type="hidden" name="csrf" value="{h(token)}">'
            f'<input type="hidden" name="action" value="{action}"><input type="hidden" name="id" value="{target}">'
            f'{content}<button>{h(label)}</button></form>')


def table(headers, rows, empty):
    if not rows:
        return f"<p>{h(empty)}</p>"
    headings = "".join(f"<th scope='col'>{h(title)}</th>" for title in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<div class='table'><table><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table></div>"


def patient_view(db, actor, token, query, now):
    clinics = db.execute("SELECT * FROM clinics ORDER BY name").fetchall()
    sections = [f"""<section><h2>Find an appointment</h2><form method="get" class="filters">
        <label>Community<select name="community">{options([(c['community'],c['community']) for c in clinics], query.get('community',''), 'All communities')}</select></label>
        <label>Service<select name="service">{options(zip(s.SERVICES,s.SERVICES), query.get('service',''), 'All services')}</select></label>
        <label>Visit type<select name="kind">{options(zip(s.KINDS,s.KINDS), query.get('kind',''), 'All types')}</select></label>
        <label>Date (UTC)<input type="date" name="day" value="{h(query.get('day'))}"></label>
        <button>Search</button></form>"""]
    rows = []
    for slot in s.available(db, query, now):
        policy = "Registered patients only" if slot["registered_only"] else "Accepts new patients"
        rows.append((f"{h(slot['name'])}<br>{h(slot['phone'])}<br>{policy}",
                     f"{h(slot['service'])}<br>{h(slot['kind'])}<br>{date(slot['starts'])}",
                     form(token, "request", "", "Request", slot["id"])))
    sections.append(table(("Clinic", "Appointment", "Action"), rows, "No matching open appointments.") + "</section>")
    bookings = db.execute("""SELECT b.*, c.name, s.starts FROM bookings b JOIN slots s ON s.id=b.slot
        JOIN clinics c ON c.id=s.clinic WHERE patient=? ORDER BY b.id DESC""", (actor["id"],)).fetchall()
    rows = [(h(b["name"]), date(b["starts"]), h(b["status"]),
             form(token, "cancel", "", "Cancel" if b["status"] == "Confirmed" else "Withdraw", b["id"])
             if b["status"] in ("Requested", "Confirmed") and b["starts"] > now else "") for b in bookings]
    sections.append("<section><h2>My bookings</h2>" + table(
        ("Clinic", "Start", "Status", "Action"), rows, "No bookings yet.") + "</section>")
    fields = f"""<label>Clinic<select name="clinic">{options([(c['id'], c['name']) for c in clinics])}</select></label>
        <label>Service<select name="service">{options(zip(s.SERVICES,s.SERVICES))}</select></label>
        <label>Visit type<select name="kind">{options(zip(s.KINDS,s.KINDS))}</select></label>
        <label>Days I can accept<input type="number" name="days" min="1" max="30" value="7" required></label>"""
    entries = db.execute("""SELECT w.*, c.name FROM waitlist w JOIN clinics c ON c.id=w.clinic
        WHERE patient=? AND active=1 ORDER BY w.id""", (actor["id"],)).fetchall()
    rows = [(h(w["name"]), h(w["service"]), h(w["kind"]), date(w["latest"]),
             form(token,"leave","","Leave",w["id"])) for w in entries]
    sections.append("<section><h2>My waitlist</h2>" + form(token, "join", fields, "Join waitlist") + table(
        ("Clinic","Service","Visit type","Through","Action"), rows, "No active entries.") + "</section>")
    offers = db.execute("""SELECT o.*, c.name, s.starts FROM offers o JOIN slots s ON s.id=o.slot
        JOIN clinics c ON c.id=s.clinic WHERE o.patient=? ORDER BY o.id DESC""", (actor["id"],)).fetchall()
    rows = [(h(o["name"]), date(o["starts"]), date(o["deadline"]), h(o["status"]),
             form(token,"accept_offer","","Accept",o["id"]) + form(token,"reject_offer","","Decline",o["id"])
             if o["status"] == "Offered" else "") for o in offers]
    sections.append("<section><h2>My cancellation offers</h2>" + table(
        ("Clinic","Start","Respond by","Status","Action"), rows, "No offers yet.") + "</section>")
    return "".join(sections)


def staff_view(db, actor, token, now):
    clinic = actor["clinic"]
    bookings = db.execute("""SELECT b.*, u.name, s.starts FROM bookings b JOIN slots s ON s.id=b.slot
        JOIN users u ON u.id=b.patient WHERE s.clinic=? ORDER BY b.id DESC""", (clinic,)).fetchall()
    rows = [(h(b["name"]), date(b["starts"]), h(b["status"]),
             form(token,"confirm","","Confirm",b["id"]) + form(token,"decline","","Decline",b["id"])
             if b["status"] == "Requested" and b["starts"] > now else "") for b in bookings]
    sections = ["<section><h2>Clinic booking requests</h2>" + table(
        ("Patient","Start","Status","Review"), rows, "No requests yet.") + "</section>"]
    sections.append("<section><h2>Released appointments</h2>")
    releases = db.execute("""SELECT r.id, r.slot FROM releases r JOIN slots s ON s.id=r.slot
        WHERE r.reviewed=0 AND s.clinic=? AND s.starts>? ORDER BY s.starts,r.id""", (clinic,now)).fetchall()
    if not releases:
        sections.append("<p>No slots waiting for review.</p>")
    for release in releases:
        slot = s.get_slot(db, release["slot"])
        sections.append(f"<h3>{h(slot['service'])} · {date(slot['starts'])}</h3>")
        rows = []
        found = False
        for entry, reason in s.candidates(db, release["id"], slot):
            decision = reason or ("Eligible" if found else "Next eligible")
            found = found or not reason
            rows.append((h(entry["name"]), h(decision)))
        sections.append(table(("Waitlist entry","Review"), rows, "No entries for this clinic."))
        label = "Issue offer to next eligible patient" if found else "Return slot to search"
        sections.append(form(token, "issue", "", label, release["id"]))
    sections.append("</section><section><h2>Offer history</h2>")
    audit = db.execute("""SELECT a.*,u.name,r.slot FROM audit a JOIN releases r ON r.id=a.release
        JOIN slots s ON s.id=r.slot LEFT JOIN waitlist w ON w.id=a.entry LEFT JOIN users u ON u.id=w.patient
        WHERE s.clinic=? ORDER BY a.id DESC""", (clinic,)).fetchall()
    sections.append(table(("Slot","Patient","Decision","Recorded"),
        [(h(a["slot"]), h(a["name"]), h(a["reason"]), date(a["recorded"])) for a in audit],
        "No offer activity yet.") + "</section>")
    return "".join(sections)


def dispatch(db, actor, data, now, window):
    action = data["action"]
    target = int(data["id"])
    if action in ("confirm", "decline", "cancel"):
        return s.review_booking(db, actor, target, action, now)
    if actor["role"] == "staff":
        s.require(action == "issue", "Action unavailable for staff.")
        return s.issue(db, actor, target, now, window)
    if action == "request":
        return s.request(db, actor, target, now)
    if action == "join":
        return s.join(db, actor, data, now)
    if action == "leave":
        updated = db.execute("UPDATE waitlist SET active=0 WHERE id=? AND patient=? AND active=1", (target,actor["id"]))
        s.require(updated.rowcount == 1, "Waitlist entry not found.")
        offer = s.one(db, "SELECT * FROM offers WHERE entry=? AND status='Offered'", target)
        if offer:
            s.reconsider(db, offer, "Ineligible", now)
        return "Left the waitlist."
    s.require(action in ("accept_offer", "reject_offer"), "Action unavailable.")
    return s.respond(db, actor, target, action, now)


class CareConnect(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, db_path, clock=time.time, offer_seconds=1800):
        super().__init__(address, Handler)
        self.db_path = db_path
        self.clock = clock
        self.offer_seconds = offer_seconds
        self.sessions = {}
        self.lock = threading.Lock()
        self.css = Path(__file__).with_name("style.css").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def session(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            pass
        token = jar["careconnect"].value if "careconnect" in jar else ""
        with self.server.lock:
            if token in self.server.sessions:
                return token, self.server.sessions[token], False
            token = secrets.token_urlsafe(24)
            state = {"csrf": secrets.token_urlsafe(24), "actor": None, "message": ""}
            self.server.sessions[token] = state
            return token, state, True

    def reply(self, code, content=b"", types="text/html; charset=utf-8", token="", location=""):
        self.send_response(code)
        self.send_header("Content-Type", types)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if token:
            self.send_header("Set-Cookie", f"careconnect={token}; Path=/; HttpOnly; SameSite=Strict")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/style.css":
            self.reply(200, self.server.css, "text/css; charset=utf-8")
            return
        if url.path != "/":
            self.reply(404, b"Page not found.")
            return
        token, state, new = self.session()
        now = int(self.server.clock())
        with s.transaction(self.server.db_path) as db:
            s.expire(db, now)
            actor = s.one(db, "SELECT * FROM users WHERE id=?", state["actor"])
            if actor:
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                content = patient_view(db, actor, state["csrf"], query, now) if actor["role"] == "patient" else staff_view(db, actor, state["csrf"], now)
                title = h(actor["name"])
                controls = form(state["csrf"],"logout","","Switch demo account")
            else:
                users = db.execute("SELECT * FROM users ORDER BY id").fetchall()
                fields = f'<label>Demo account<select name="account">{options([(u["id"],u["name"]) for u in users])}</select></label>'
                content = "<section>" + form(state["csrf"],"login",fields,"Open demo account") + "</section>"
                title = "Appointment access"
                controls = ""
        message = state.pop("message", "")
        status = f'<p class="status" role="status">{h(message)}</p>' if message else ""
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <title>{title} | CareConnect</title><link href="/style.css" rel="stylesheet"></head>
          <body><header><div><strong>CareConnect</strong><p>Local demo · Fictional records</p></div>{controls}</header>
          <main><h1>{title}</h1>{status}{content}</main></body></html>"""
        self.reply(200, page.encode(), token=token if new else "")

    def do_POST(self):
        if self.path != "/action":
            self.reply(404, b"Page not found.")
            return
        _, state, _ = self.session()
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 8192:
                raise ValueError()
            data = {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode()).items()}
        except (ValueError, UnicodeError):
            self.reply(400, b"Invalid form.")
            return
        if not secrets.compare_digest(data.get("csrf", "").encode(), state["csrf"].encode()):
            self.reply(403, b"Invalid session form token.")
            return
        now = int(self.server.clock())
        with s.transaction(self.server.db_path) as db:
            s.expire(db, now)
        try:
            with s.transaction(self.server.db_path) as db:
                if data["action"] == "login":
                    actor = s.one(db, "SELECT * FROM users WHERE id=?", int(data["account"]))
                    s.require(actor, "Choose a demo account.")
                    state["actor"] = actor["id"]
                    state["csrf"] = secrets.token_urlsafe(24)
                elif data["action"] == "logout":
                    state["actor"] = None
                    state["csrf"] = secrets.token_urlsafe(24)
                else:
                    actor = s.one(db, "SELECT * FROM users WHERE id=?", state["actor"])
                    s.require(actor, "Choose a demo account first.")
                    state["message"] = dispatch(db, actor, data, now, self.server.offer_seconds)
        except s.ScheduleError as error:
            state["message"] = str(error)
        except (KeyError, ValueError):
            state["message"] = "Check the form values and try again."
        self.reply(303, location="/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("careconnect.sqlite3"))
    parser.add_argument("--reset", action="store_true", help="Reset the default fictional demo database.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--offer-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.reset:
        if args.db != Path("careconnect.sqlite3"):
            parser.error("--reset applies only to the default demo database.")
        args.db.unlink(missing_ok=True)
    if not args.db.exists():
        s.seed(args.db, int(time.time()))
    if args.offer_seconds < 1:
        parser.error("--offer-seconds must be positive.")
    with CareConnect(("127.0.0.1", args.port), args.db, offer_seconds=args.offer_seconds) as server:
        print(f"CareConnect: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
