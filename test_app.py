"""End-to-end checks against the HTTP request handler and a temporary SQLite database."""
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from threading import Lock
import unittest
from urllib.parse import urlencode

from app import Handler
from scheduling import seed


class MemoryConnection:
    """Carry HTTP request and response bytes without requiring network sockets."""
    def __init__(self, payload):
        self.input = BytesIO(payload)
        self.output = BytesIO()

    def makefile(self, *_):
        return self.input

    def sendall(self, payload):
        self.output.write(payload)


class Client:
    def __init__(self, server, actor):
        self.server = server
        self.cookie = ""
        self.post("login", account=actor)

    def request(self, method, path, body=b""):
        headers = (f"{method} {path} HTTP/1.0\r\nCookie: {self.cookie}\r\n"
                   f"Content-Length: {len(body)}\r\n\r\n")
        connection = MemoryConnection(headers.encode() + body)
        Handler(connection, ("127.0.0.1", 0), self.server)
        head, content = connection.output.getvalue().decode().split("\r\n\r\n", 1)
        for line in head.split("\r\n"):
            if line.startswith("Set-Cookie: "):
                jar = SimpleCookie(line.removeprefix("Set-Cookie: "))
                self.cookie = "careconnect=" + jar["careconnect"].value
        return int(head.split()[1]), content

    def get(self, path="/"):
        code, page = self.request("GET", path)
        assert code == 200, code
        return page

    def post(self, action, target=0, **fields):
        token = re.search(r'name="csrf" value="([^"]+)', self.get())[1]
        data = urlencode(dict(csrf=token, action=action, id=target, **fields)).encode()
        code, _ = self.request("POST", "/action", data)
        assert code == 303, code
        return self.get()


class CareConnectTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        database = Path(directory) / "test.sqlite3"
        self.now = 1800000000
        seed(database, self.now)
        server = SimpleNamespace(db_path=database, clock=lambda: self.now, offer_seconds=60,
                                 sessions={}, lock=Lock())
        self.a, self.b, self.c, self.staff, self.other_staff = [Client(server, i) for i in range(1, 6)]

    def release(self):
        self.a.post("request", 1)
        self.staff.post("confirm", 1)
        self.b.post("join", clinic=1, service="Primary care", kind="In person", days=7)
        self.a.post("cancel", 1)

    def test_cross_clinic_search(self):
        page = self.a.get("/?service=Primary+care")
        self.assertIn("Pine River Clinic", page.split("My bookings")[0])
        self.assertIn("Cedar Bay Clinic", page.split("My bookings")[0])
        self.assertNotIn("North Ridge Clinic", page.split("My bookings")[0])
        self.assertIn("No matching open appointments", self.a.get("/?day=2000-01-01"))

    def test_rebook_cancellation(self):
        self.release()
        page = self.staff.get()
        self.assertRegex(page, "Patient C</td><td>Registration required")
        self.assertRegex(page, "Patient B</td><td>Next eligible")
        self.assertIn("Offer issued to Patient B", self.staff.post("issue", 1))
        self.assertNotIn("Pine River Clinic", self.c.get().split("My bookings")[0])
        self.assertIn("Staff confirmation is pending", self.b.post("accept_offer", 1))
        self.assertIn("Booking confirmed", self.staff.post("confirm", 2))
        self.assertIn("Confirmed", self.b.get())
        self.assertIn("Selected and offered", self.staff.get())

    def test_concurrent_requests(self):
        with ThreadPoolExecutor(2) as workers:
            results = list(workers.map(lambda c: c.post("request", 1), (self.a, self.b)))
        self.assertEqual(sum("Appointment requested." in page for page in results), 1)
        self.assertEqual(sum("no longer available" in page for page in results), 1)

    def test_staff_and_patient_access(self):
        self.a.post("request", 1)
        self.assertIn("Access denied", self.other_staff.post("confirm", 1))
        self.assertIn("Access denied", self.b.post("cancel", 1))
        self.assertNotIn("Patient A", self.other_staff.get())
        self.assertNotIn("Pine River Clinic", self.b.get().split("My bookings")[1].split("My waitlist")[0])
        code, _ = self.b.request("POST", "/action", b"action=cancel&id=1")
        self.assertEqual(code, 403)

    def test_expired_offer_returns_to_review(self):
        self.release()
        self.staff.post("issue", 1)
        self.now += 60
        self.assertIn("no longer available", self.b.post("accept_offer", 1))
        self.assertIn("Already offered this opening", self.staff.get())
        self.assertIn("The slot is available again", self.staff.post("issue", 1))
        self.assertIn("Pine River Clinic", self.a.get().split("My bookings")[0])

    def test_offer_replay_cannot_create_duplicate_request(self):
        self.release()
        self.staff.post("issue", 1)
        self.b.post("accept_offer", 1)
        self.assertIn("no longer available", self.b.post("accept_offer", 1))
        self.assertEqual(self.b.get().split("My bookings")[1].split("My waitlist")[0].count(">Requested<"), 1)


if __name__ == "__main__":
    unittest.main()
