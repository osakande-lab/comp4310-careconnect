# CareConnect

COMP 4310 Web Health Informatics, Lakehead University  
P4 prototype by Oluwatolayomi Akande

CareConnect demonstrates cross-clinic appointment discovery and cancellation reuse for rural and remote communities. Its data is fictional. The application runs only on localhost and uses demo account switching. It is intended for a lab demonstration.

## Run

Requires Python 3.11 or newer. There are no third-party dependencies, environment variables or credentials.

```text
python app.py --reset
```

Open http://127.0.0.1:8000. The command resets the default fictional database to three clinics, three appointment slots, three patient accounts and two clinic staff accounts. The schema is in `schema.sql`, and the seed is in `scheduling.py`. Dates are displayed in UTC and start relative to the time the demo database is reset. Stop the server before resetting. Use `--port 8001` if port 8000 is occupied. The normal offer window is 30 minutes, capped at the appointment start. Use `--offer-seconds 60` for a separate expiry demonstration.

## Lab demo

1. **Patient A.** Filter search to Primary care and point out the Pine River in-person and Cedar Bay virtual slots. Request the Pine River opening. Show the Requested status.
2. **Pine River Staff.** Confirm Patient A’s booking. Switch to Patient A and show Confirmed.
3. **Patient B.** Under My waitlist, choose Pine River Clinic, Primary care, In person, and 7 days. Join.
4. **Patient A.** Cancel the confirmed appointment.
5. **Pine River Staff.** Open Released appointments. Patient C joined earlier but fails Pine River’s registration requirement. Patient B is next eligible. Issue the offer and show the recorded reasons in Offer history.
6. **Patient B.** Accept the offer. Show the Requested booking and explain that staff confirmation is still required.
7. **Pine River Staff.** Confirm Patient B. Show Patient B’s Confirmed booking and the earlier cancellation in the staff view.

The account picker works in a single browser window. Separate browser profiles are optional. Refresh any open page after an action in another session to see the updated state.

## Implementation and checks

`app.py` handles HTTP, forms, local sessions, demo-account access and role-specific pages. `scheduling.py` implements search, booking transitions, eligibility, offer lifecycle and audit records. SQLite write transactions and partial unique indexes protect slot and offer reservations. The app checks expiration on page loads and writes. The staff queue is refreshed manually.

```text
python -m unittest -v
```

Six HTTP integration tests cover cross-clinic search, the complete cancellation-to-rebooking flow, simultaneous booking requests, role and ownership checks, expired offers and offer replay. Tests exercise the HTTP handler in memory with a temporary database and a controllable clock.

## P5 work

Add staff slot creation and editing, staff-assisted booking, richer waitlist windows, and more comprehensive audit coverage. Expand the seed data and run the paired manual-versus-waitlist evaluation. Verify keyboard and narrow-screen usability, and measure search latency. The local demo account picker needs replacement if the application ever handles real identities.
