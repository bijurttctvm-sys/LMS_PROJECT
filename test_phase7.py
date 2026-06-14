"""
Phase 7 -- Doubt Sessions test suite
Run: venv\\Scripts\\python.exe test_phase7.py

Steps:
  1.  Instructor adds 3 slots via slot management → InstructorSlot records created
  2.  Confirm all 3 slots have is_available=True in DB
  3.  Student visits slot selection → 3 slots displayed
  4.  Student selects a slot → DoubtSession created (status=CONFIRMED)
  5.  meet_url copied from instructor.google_meet_link to session
  6.  Student confirmation email received with Meet link
  7.  Instructor notification email received
  8.  Student tries to book another slot → 'already booked' shown
  9.  Instructor marks session ATTENDED → last_attended_at set
  10. 30-day countdown shown when student tries to re-book
  11. NEW SESSION marked NO_SHOW → student eligible again immediately
"""
import os
import re
import sys
import traceback
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("Standalone diagnostic script; run explicitly.")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lms_project.test_settings")
import django
django.setup()

from django.contrib.auth import get_user_model
from django.core import mail as _mail
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

User = get_user_model()
PASS = "PASS"
FAIL = "FAIL"
failures = []

call_command("migrate", "--run-syncdb", verbosity=0)


def check(label, cond, detail=""):
    sym = PASS if cond else FAIL
    if not cond:
        failures.append(label)
    suffix = f"  ({detail})" if detail else ""
    print(f"  {sym}: {label}{suffix}")
    return cond


def skip(label, reason):
    print(f"  SKIP: {label}  -- {reason}")


# ── Models ────────────────────────────────────────────────────────────────────
from doubt_sessions.models import DoubtSession, InstructorSlot

# ── Fixtures ──────────────────────────────────────────────────────────────────
MEET_URL = "https://meet.google.com/test-p7-lms"

p7_instructor, _ = User.objects.get_or_create(
    username="p7_instructor",
    defaults={"role": "instructor", "email": "p7_instructor@test.com"},
)
p7_instructor.set_password("pass123")
p7_instructor.role = "instructor"
p7_instructor.email = "p7_instructor@test.com"
p7_instructor.google_meet_link = MEET_URL
p7_instructor.save()

p7_student, _ = User.objects.get_or_create(
    username="p7_student",
    defaults={"role": "student", "email": "p7_student@test.com"},
)
p7_student.set_password("pass123")
p7_student.role = "student"
p7_student.email = "p7_student@test.com"
p7_student.save()

p7_student2, _ = User.objects.get_or_create(
    username="p7_student2",
    defaults={"role": "student", "email": "p7_student2@test.com"},
)
p7_student2.set_password("pass123")
p7_student2.role = "student"
p7_student2.email = "p7_student2@test.com"
p7_student2.save()

# Clean up previous test data for idempotency
DoubtSession.objects.filter(
    student__in=[p7_student, p7_student2]
).delete()
InstructorSlot.objects.filter(instructor=p7_instructor).delete()


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 1. Instructor adds 3 slots via slot management ===")
# ─────────────────────────────────────────────────────────────────────────────

now = timezone.now()
SLOT_DATES = [
    (now + timedelta(days=8)).strftime("%Y-%m-%d"),
    (now + timedelta(days=9)).strftime("%Y-%m-%d"),
    (now + timedelta(days=10)).strftime("%Y-%m-%d"),
]
SLOT_TIMES = ["10:00", "11:00", "14:00"]

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_instructor = Client()
    cl_instructor.login(username="p7_instructor", password="pass123")

    cl_student_gate = Client()
    cl_student_gate.login(username="p7_student", password="pass123")

    # Access control: student cannot access slot management
    r = cl_student_gate.get("/doubt/slots/")
    check("Student GET /doubt/slots/ -> redirect (not instructor)", r.status_code == 302)

    # Instructor can access slot management
    r = cl_instructor.get("/doubt/slots/")
    check("Instructor GET /doubt/slots/ -> 200", r.status_code == 200)
    if r.status_code == 200:
        content = r.content.decode()
        check("Slot management page renders correctly", "Slot Management" in content)

    # Add 3 slots
    add_ok = 0
    for date_str, time_str in zip(SLOT_DATES, SLOT_TIMES):
        r = cl_instructor.post("/doubt/slots/", {
            "action": "add",
            "slot_date": date_str,
            "slot_time": time_str,
        })
        if r.status_code == 302:
            add_ok += 1

    check("All 3 slot-add POSTs returned 302 redirect", add_ok == 3, f"got {add_ok}")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 2. InstructorSlot records in DB — is_available=True ===")
# ─────────────────────────────────────────────────────────────────────────────

all_slots = list(InstructorSlot.objects.filter(instructor=p7_instructor).order_by("slot_datetime"))
check("3 InstructorSlot records created in DB",
      len(all_slots) == 3, f"count={len(all_slots)}")
check("All slots have is_available=True",
      all(s.is_available for s in all_slots),
      f"flags={[s.is_available for s in all_slots]}")

# Verify slot times stored correctly (as future datetimes)
if all_slots:
    check("All slot datetimes are in the future",
          all(s.slot_datetime > now for s in all_slots))


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 3. Student visits slot selection — 3 slots displayed ===")
# ─────────────────────────────────────────────────────────────────────────────

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_student = Client()
    cl_student.login(username="p7_student", password="pass123")

    # Access control: instructor cannot access student booking page
    r = cl_instructor.get("/doubt/book/")
    check("Instructor GET /doubt/book/ -> redirect (not student)", r.status_code == 302)

    # Unauthenticated user gets redirected
    cl_anon = Client()
    r = cl_anon.get("/doubt/book/")
    check("Unauthenticated GET /doubt/book/ -> redirect to login", r.status_code == 302)

    # Student can access slot selection
    r = cl_student.get("/doubt/book/")
    check("Student GET /doubt/book/ -> 200", r.status_code == 200)

    if r.status_code == 200:
        content = r.content.decode()
        check("Page shows 'Book a Doubt Session' heading",
              "Book a Doubt Session" in content)
        slot_ids_found = re.findall(r'name="slot_id"\s+value="(\d+)"', content)
        check("3 slot radio buttons displayed",
              len(slot_ids_found) == 3, f"found {len(slot_ids_found)}")
        check("'Confirm Booking' submit button present",
              "Confirm Booking" in content)
        check("Instructor name shown on slot cards",
              p7_instructor.username in content)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 4. Student selects a slot — DoubtSession created (CONFIRMED) ===")
# ─────────────────────────────────────────────────────────────────────────────

first_slot = all_slots[0] if all_slots else None
session_obj = None

if not first_slot:
    skip("Session booking tests", "no slots created")
else:
    with override_settings(ALLOWED_HOSTS=["*"]):
        cl_student = Client()
        cl_student.login(username="p7_student", password="pass123")

        # POST to book — Celery unavailable in test env, view catches the error silently
        r = cl_student.post("/doubt/book/", {"slot_id": str(first_slot.id)})

        check("POST /doubt/book/ -> 302 redirect", r.status_code == 302)
        if r.status_code == 302:
            check("Redirects to /doubt/sessions/ (my-sessions)",
                  "/doubt/sessions/" in r["Location"])

    session_obj = DoubtSession.objects.filter(student=p7_student).first()
    check("DoubtSession record exists in DB", session_obj is not None)

    if session_obj:
        check("session.status == CONFIRMED",
              session_obj.status == DoubtSession.Status.CONFIRMED,
              f"got {session_obj.status!r}")
        check("session.instructor == p7_instructor",
              session_obj.instructor_id == p7_instructor.id)
        check("session.slot == first_slot",
              session_obj.slot_id == first_slot.id)

    # Slot must be marked unavailable
    first_slot.refresh_from_db()
    check("first_slot.is_available = False after booking",
          not first_slot.is_available)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 5. meet_url copied from instructor.google_meet_link ===")
# ─────────────────────────────────────────────────────────────────────────────

if session_obj:
    check("session.meet_url matches instructor google_meet_link",
          session_obj.meet_url == MEET_URL,
          f"expected {MEET_URL!r}, got {session_obj.meet_url!r}")
else:
    skip("meet_url check", "no session object")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 6 & 7. Confirmation emails — student + instructor ===")
# ─────────────────────────────────────────────────────────────────────────────

if session_obj:
    from doubt_sessions.tasks import send_confirmation_emails

    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="lms@test.com",
    ):
        _mail.outbox = []
        try:
            send_confirmation_emails.apply(args=[session_obj.id])
            task_ok = True
        except Exception as exc:
            task_ok = False
            check("send_confirmation_emails task runs without error", False, str(exc)[:80])
            traceback.print_exc()

    if task_ok:
        check("2 emails sent (student + instructor)",
              len(_mail.outbox) == 2, f"outbox size={len(_mail.outbox)}")

        all_recipients = [m.to[0] for m in _mail.outbox]

        # ── Step 6: student email ─────────────────────────────────────────────
        student_msg = next((m for m in _mail.outbox if p7_student.email in m.to), None)
        check("Step 6: Student confirmation email received",
              student_msg is not None, f"recipients={all_recipients}")
        if student_msg:
            check("Student email subject contains 'confirmed'",
                  "confirmed" in student_msg.subject.lower(), student_msg.subject)
            check("Student email plain-text body contains Meet URL",
                  MEET_URL in student_msg.body, student_msg.body[:100])
            has_html = bool(getattr(student_msg, "alternatives", []))
            if has_html:
                html_body = student_msg.alternatives[0][0]
                check("Student HTML email contains Meet URL",
                      MEET_URL in html_body)
                check("Student HTML email shows 'Doubt Session Confirmed'",
                      "Doubt Session Confirmed" in html_body)

        # ── Step 7: instructor email ──────────────────────────────────────────
        instr_msg = next((m for m in _mail.outbox if p7_instructor.email in m.to), None)
        check("Step 7: Instructor notification email received",
              instr_msg is not None, f"recipients={all_recipients}")
        if instr_msg:
            check("Instructor email subject contains 'booked'",
                  "booked" in instr_msg.subject.lower(), instr_msg.subject)
            check("Instructor email body mentions student",
                  p7_student.username in instr_msg.body or
                  p7_student.get_full_name() in instr_msg.body)
            check("Instructor email body contains Meet URL",
                  MEET_URL in instr_msg.body)
else:
    skip("Email confirmation tests", "no session object")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 8. Student tries to book again — 'already booked' message ===")
# ─────────────────────────────────────────────────────────────────────────────

second_slot = all_slots[1] if len(all_slots) >= 2 else None

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_student = Client()
    cl_student.login(username="p7_student", password="pass123")

    # GET: template should show the "upcoming session" card, not the booking form
    r = cl_student.get("/doubt/book/")
    check("GET /doubt/book/ -> 200 (student has active booking)",
          r.status_code == 200)
    if r.status_code == 200:
        content = r.content.decode()
        check("Page shows 'You have an upcoming session' card",
              "You have an upcoming session" in content)
        check("Page shows Join Google Meet link",
              "Join Google Meet" in content)
        check("Booking form NOT shown (eligible=False)",
              "Confirm Booking" not in content)

    # POST: should redirect back with error (not eligible)
    if second_slot:
        r = cl_student.post("/doubt/book/", {"slot_id": str(second_slot.id)})
        check("POST with another slot_id -> 302 redirect (not eligible)",
              r.status_code == 302)
        # Second slot must remain untouched
        second_slot.refresh_from_db()
        check("Second slot is still is_available=True",
              second_slot.is_available)
        # No new session created
        session_count = DoubtSession.objects.filter(student=p7_student).count()
        check("No duplicate session created",
              session_count == 1, f"count={session_count}")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 9. Instructor marks session ATTENDED ===")
# ─────────────────────────────────────────────────────────────────────────────

if not session_obj:
    skip("Mark attended tests", "no session object")
else:
    with override_settings(ALLOWED_HOSTS=["*"]):
        cl_instructor = Client()
        cl_instructor.login(username="p7_instructor", password="pass123")

        # Instructor sessions page shows the session
        r = cl_instructor.get("/doubt/instructor/sessions/")
        check("Instructor GET /doubt/instructor/sessions/ -> 200",
              r.status_code == 200)
        if r.status_code == 200:
            content = r.content.decode()
            check("Sessions page shows student username",
                  p7_student.username in content)
            check("Sessions page shows 'Mark Outcome' button",
                  "Mark Outcome" in content)
            check("Sessions page shows 'Student Attended' button",
                  "Student Attended" in content)

        # POST mark attended
        r = cl_instructor.post(
            f"/doubt/sessions/{session_obj.id}/outcome/",
            {"outcome": "attended"},
        )
        check("POST mark attended -> 302 redirect", r.status_code == 302)

    session_obj.refresh_from_db()
    check("session.status == ATTENDED",
          session_obj.status == DoubtSession.Status.ATTENDED,
          f"got {session_obj.status!r}")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 10. last_attended_at set on DoubtSession ===")
# ─────────────────────────────────────────────────────────────────────────────

if session_obj:
    session_obj.refresh_from_db()
    check("last_attended_at is not None",
          session_obj.last_attended_at is not None)
    if session_obj.last_attended_at:
        age_secs = (timezone.now() - session_obj.last_attended_at).total_seconds()
        check("last_attended_at set within the last 60 seconds",
              age_secs < 60, f"age={age_secs:.1f}s")

    # Verify is_eligible returns cooldown state
    eligible, next_eligible_date, active = DoubtSession.is_eligible(p7_student)
    check("is_eligible() returns False (attended within 30 days)", not eligible)
    check("next_eligible_date is returned", next_eligible_date is not None)
    check("active_session is None (no active booking)", active is None)
    if next_eligible_date:
        days_left = (next_eligible_date - timezone.now()).days
        check(f"next_eligible_date is ~30 days away ({days_left}d)",
              28 <= days_left <= 31, f"got {days_left}d")
else:
    skip("last_attended_at tests", "no session object")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 11. 30-day countdown displayed in slot selection ===")
# ─────────────────────────────────────────────────────────────────────────────

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_student = Client()
    cl_student.login(username="p7_student", password="pass123")

    r = cl_student.get("/doubt/book/")
    check("Student GET /doubt/book/ after ATTENDED -> 200", r.status_code == 200)

    if r.status_code == 200:
        content = r.content.decode()
        check("Cooldown card shown: 'recently attended a session'",
              "recently attended" in content)
        check("Cooldown message: '30-day cooldown after attendance'",
              "30-day cooldown" in content)
        check("Next booking date displayed",
              "can book your next session on" in content)
        check("JavaScript countdown ring element present",
              "countdown-display" in content)
        check("Booking form NOT shown during cooldown",
              "Confirm Booking" not in content)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 12. NO_SHOW: student eligible again immediately ===")
# ─────────────────────────────────────────────────────────────────────────────

# Use p7_student2 (clean state) and one of the remaining available slots
ns_slot = InstructorSlot.objects.filter(
    instructor=p7_instructor, is_available=True
).first()

if not ns_slot:
    # All slots booked somehow — create a fresh one
    ns_slot = InstructorSlot.objects.create(
        instructor=p7_instructor,
        slot_datetime=timezone.now() + timedelta(days=15),
    )

check("Available slot found for NO_SHOW scenario", ns_slot is not None)

if ns_slot:
    # Create CONFIRMED session for p7_student2 directly (bypasses eligibility view)
    ns_session = DoubtSession.objects.create(
        student=p7_student2,
        instructor=p7_instructor,
        slot=ns_slot,
        meet_url=MEET_URL,
        status=DoubtSession.Status.CONFIRMED,
    )
    ns_slot.is_available = False
    ns_slot.save(update_fields=["is_available"])

    check("NO_SHOW test: session created with CONFIRMED status",
          ns_session.status == DoubtSession.Status.CONFIRMED)

    # Verify student2 is currently NOT eligible (has active session)
    elig2, next2, active2 = DoubtSession.is_eligible(p7_student2)
    check("student2 not eligible (active CONFIRMED session)", not elig2)
    check("active_session returned for student2", active2 is not None)

    # Instructor marks it NO_SHOW
    with override_settings(ALLOWED_HOSTS=["*"]):
        cl_instructor = Client()
        cl_instructor.login(username="p7_instructor", password="pass123")

        r = cl_instructor.post(
            f"/doubt/sessions/{ns_session.id}/outcome/",
            {"outcome": "no_show"},
        )
        check("POST mark no_show -> 302 redirect", r.status_code == 302)

    ns_session.refresh_from_db()
    ns_slot.refresh_from_db()

    check("Session status = NO_SHOW",
          ns_session.status == DoubtSession.Status.NO_SHOW,
          f"got {ns_session.status!r}")
    check("Slot restored to is_available=True after NO_SHOW",
          ns_slot.is_available)

    # Student2 should now be immediately eligible
    elig2_after, next2_after, active2_after = DoubtSession.is_eligible(p7_student2)
    check("student2 eligible again immediately after NO_SHOW",
          elig2_after,
          f"eligible={elig2_after}, next={next2_after}, active={active2_after}")
    check("No cooldown date set (NO_SHOW never triggers 30-day wait)",
          next2_after is None)
    check("No active session after NO_SHOW", active2_after is None)

    # Confirm via the slot-selection view
    with override_settings(ALLOWED_HOSTS=["*"]):
        cl_s2 = Client()
        cl_s2.login(username="p7_student2", password="pass123")

        r = cl_s2.get("/doubt/book/")
        check("student2 GET /doubt/book/ after NO_SHOW -> 200", r.status_code == 200)
        if r.status_code == 200:
            content = r.content.decode()
            check("student2 sees 'Confirm Booking' (eligible to book)",
                  "Confirm Booking" in content)
            check("student2 does NOT see 'upcoming session' card",
                  "You have an upcoming session" not in content)
            check("student2 does NOT see cooldown message",
                  "recently attended" not in content)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== manage.py check ===")
# ─────────────────────────────────────────────────────────────────────────────

out = StringIO()
call_command("check", stdout=out, stderr=out)
output = out.getvalue()
clean = "no issues" in output or "0 issues" in output or output.strip() == ""
check("manage.py check reports 0 issues",
      clean, output.strip()[:120] if not clean else "clean")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Summary ===")
# ─────────────────────────────────────────────────────────────────────────────

if failures:
    print(f"\n  {len(failures)} FAIL(s):")
    for f in failures:
        print(f"    - {f}")
    sys.exit(1)
else:
    print("  All checks PASSED.")
    print()
    print("  Infrastructure notes (require real services):")
    print("    * Celery worker   -> send_confirmation_emails.delay() tested via .apply()")
    print("    * Email backend   -> locmem used; set EMAIL_BACKEND in .env for real sends")
    print("    * 30-day cooldown -> tested by marking ATTENDED and checking is_eligible()")
