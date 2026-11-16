PRAGMA foreign_keys = ON;
CREATE TABLE clinics(id INTEGER PRIMARY KEY, name TEXT, community TEXT, phone TEXT, registered_only INTEGER);
CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, role TEXT, clinic INTEGER REFERENCES clinics);
CREATE TABLE registration(patient INTEGER REFERENCES users, clinic INTEGER REFERENCES clinics, PRIMARY KEY(patient,clinic));
CREATE TABLE slots(id INTEGER PRIMARY KEY, clinic INTEGER REFERENCES clinics, service TEXT, kind TEXT, starts INTEGER);
CREATE TABLE bookings(id INTEGER PRIMARY KEY, slot INTEGER REFERENCES slots, patient INTEGER REFERENCES users,
 status TEXT CHECK(status IN ('Requested','Confirmed','Cancelled','Declined','Withdrawn')));
CREATE UNIQUE INDEX active_booking ON bookings(slot) WHERE status IN ('Requested','Confirmed');
CREATE TABLE waitlist(id INTEGER PRIMARY KEY, patient INTEGER REFERENCES users, clinic INTEGER REFERENCES clinics,
 service TEXT, kind TEXT, earliest INTEGER, latest INTEGER, added INTEGER, active INTEGER DEFAULT 1);
CREATE UNIQUE INDEX active_waitlist ON waitlist(patient,clinic,service,kind) WHERE active=1;
CREATE TABLE releases(id INTEGER PRIMARY KEY, slot INTEGER REFERENCES slots, reviewed INTEGER DEFAULT 0);
CREATE UNIQUE INDEX pending_release ON releases(slot) WHERE reviewed=0;
CREATE TABLE offers(id INTEGER PRIMARY KEY, release INTEGER REFERENCES releases, slot INTEGER REFERENCES slots,
 entry INTEGER REFERENCES waitlist, patient INTEGER REFERENCES users, deadline INTEGER,
 status TEXT CHECK(status IN ('Offered','Accepted','Declined','Expired','Ineligible')), UNIQUE(release,entry));
CREATE UNIQUE INDEX active_offer_slot ON offers(slot) WHERE status='Offered';
CREATE UNIQUE INDEX active_offer_patient ON offers(patient) WHERE status='Offered';
CREATE TABLE audit(id INTEGER PRIMARY KEY, release INTEGER REFERENCES releases, entry INTEGER REFERENCES waitlist,
 reason TEXT, recorded INTEGER);
