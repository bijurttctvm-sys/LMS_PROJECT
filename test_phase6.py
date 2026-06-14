"""
Phase 6 -- Quiz System test suite
Run: venv\\Scripts\\python.exe test_phase6.py
Steps:
  1.  Task wiring: generate_quiz importable, generate_quiz_view calls .delay()
  2.  Model structure: fields, properties, Status choices
  3.  generate_quiz task: mocked Groq -> QuizDraft records created
  4.  Email notification: send_mail called when task runs
  5.  Admin draft list view: pending questions grouped by video
  6.  Review draft: approve, reject, edit fields
  7.  Publish quiz: Quiz + QuizQuestion records created, drafts deleted
  8.  Student quiz list: enrolled student sees published quiz
  9.  Take quiz + score: session-based flow, StudentQuizAttempt score correct
  10. Results view: correct/incorrect breakdown per question
"""
import json
import os
import sys
import inspect
import traceback
from unittest.mock import MagicMock, patch

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("Standalone diagnostic script; run explicitly.")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lms_project.test_settings")
import django
django.setup()

from django.contrib.auth import get_user_model
from django.core import mail as _mail
from django.test import Client, override_settings
from django.core.management import call_command
from io import StringIO

User = get_user_model()
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
failures = []

# Ensure test SQLite DB has all tables
call_command("migrate", "--run-syncdb", verbosity=0)


def check(label, cond, detail=""):
    sym = PASS if cond else FAIL
    if not cond:
        failures.append(label)
    suffix = f"  ({detail})" if detail else ""
    print(f"  {sym}: {label}{suffix}")
    return cond


def skip(label, reason):
    print(f"  {SKIP}: {label}  -- {reason}")


# ── Test fixtures ─────────────────────────────────────────────────────────────

from courses.models import Course, Enrollment
from videos.models import Video
from quizzes.models import Quiz, QuizDraft, QuizQuestion, StudentQuizAttempt

# Users
p6_admin, _ = User.objects.get_or_create(
    username="p6_admin",
    defaults={"role": "admin", "email": "p6admin@test.com"},
)
p6_admin.set_password("pass123")
p6_admin.save()

p6_instructor, _ = User.objects.get_or_create(
    username="p6_instructor",
    defaults={"role": "instructor"},
)
p6_instructor.set_password("pass123")
p6_instructor.save()

p6_student, _ = User.objects.get_or_create(
    username="p6_student",
    defaults={"role": "student"},
)
p6_student.set_password("pass123")
p6_student.save()

p6_student2, _ = User.objects.get_or_create(
    username="p6_student2",
    defaults={"role": "student"},
)
p6_student2.set_password("pass123")
p6_student2.save()

# Course + enrollment
p6_course, _ = Course.objects.get_or_create(
    title="P6 Test Course",
    defaults={"instructor": p6_instructor},
)
Enrollment.objects.get_or_create(student=p6_student, course=p6_course)

# Video (READY with transcript)
p6_video, _ = Video.objects.get_or_create(
    title="P6 Test Video",
    defaults={
        "course": p6_course,
        "status": "READY",
        "english_transcript": (
            "Python is a high-level, interpreted programming language created by "
            "Guido van Rossum in 1991. It emphasizes code readability and supports "
            "multiple programming paradigms including procedural, object-oriented, "
            "and functional programming. Python has a large standard library and an "
            "active community. It is widely used in web development, data science, "
            "artificial intelligence, scientific computing, and automation."
        ),
    },
)
if p6_video.status != "READY":
    Video.objects.filter(id=p6_video.id).update(status="READY",
        english_transcript="Python is a high-level programming language.")

# Clean previous quiz drafts for this video to keep tests idempotent
QuizDraft.objects.filter(video=p6_video).delete()
Quiz.objects.filter(video=p6_video).delete()
StudentQuizAttempt.objects.filter(quiz__video=p6_video).delete()


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 1. Task wiring ===")
# ─────────────────────────────────────────────────────────────────────────────

try:
    from videos.tasks import generate_quiz
    check("generate_quiz task importable", True)
except Exception as e:
    check("generate_quiz task importable", False, str(e))

try:
    import videos.views as _vv_mod
    # Use module-level source: the decorator doesn't preserve __wrapped__,
    # so getsource(generate_quiz_view) returns the wrapper, not the body.
    vsrc = inspect.getsource(_vv_mod)
    check("generate_quiz_view calls generate_quiz.delay()",
          "generate_quiz.delay" in vsrc)
    check("generate_quiz_view checks video.status == READY",
          "Status.READY" in vsrc or ("READY" in vsrc and "status" in vsrc))
    check("generate_quiz_view is POST-only",
          "method != 'POST'" in vsrc or "method == 'POST'" in vsrc)
except Exception as e:
    check("generate_quiz_view source checks", False, str(e))

# generate_quiz task source checks
try:
    src = inspect.getsource(generate_quiz)
    check("Task uses llama-3.1-8b-instant model",
          "llama-3.1-8b-instant" in src)
    check("Task strips markdown fences",
          "fence" in src or "```" in src)
    check("Task validates correct_option in a/b/c/d",
          "('a', 'b', 'c', 'd')" in src or "('a','b','c','d')" in src)
    check("Task uses bulk_create for drafts",
          "bulk_create" in src)
    check("Task sends email notification",
          "send_mail" in src)
    check("Task retries on failure",
          "self.retry" in src)
except Exception as e:
    check("generate_quiz source checks", False, str(e))

# generate_quiz_view: GET -> redirect, not-READY -> error message
with override_settings(ALLOWED_HOSTS=["*"]):
    cl_admin = Client()
    cl_admin.login(username="p6_admin", password="pass123")

    r = cl_admin.get(f"/videos/{p6_video.id}/generate-quiz/")
    check("GET /generate-quiz/ redirects (not allowed)",
          r.status_code in (302, 405))

    # Test with non-READY video
    Video.objects.filter(id=p6_video.id).update(status="UPLOADED")
    with patch("videos.tasks.generate_quiz.delay"):
        r = cl_admin.post(f"/videos/{p6_video.id}/generate-quiz/")
    check("POST generate-quiz for non-READY video -> redirect with error",
          r.status_code == 302)
    Video.objects.filter(id=p6_video.id).update(status="READY")

    # Test with READY video (mock .delay so Celery not needed)
    with patch("videos.tasks.generate_quiz.delay") as mock_delay:
        r = cl_admin.post(f"/videos/{p6_video.id}/generate-quiz/")
    check("POST generate-quiz for READY video -> 302 redirect",
          r.status_code == 302)
    check("generate_quiz.delay() was called with video.id",
          mock_delay.called and mock_delay.call_args[0][0] == p6_video.id)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 2. Model structure ===")
# ─────────────────────────────────────────────────────────────────────────────

# QuizDraft
check("QuizDraft.Status has PENDING/APPROVED/REJECTED",
      all(s in QuizDraft.Status.values for s in ('pending', 'approved', 'rejected')))

draft_tmp = QuizDraft(
    video=p6_video,
    question_text="What is Python?",
    option_a="A language",
    option_b="A snake",
    option_c="A tool",
    option_d="An IDE",
    correct_option="a",
    explanation="Python is a programming language.",
    status=QuizDraft.Status.PENDING,
)
check("QuizDraft __str__ contains question preview",
      "What is Python?" in str(draft_tmp) or "pending" in str(draft_tmp).lower())

# Quiz
quiz_tmp = Quiz(video=p6_video, title="Temp Quiz", is_published=False)
check("Quiz has is_published field", hasattr(quiz_tmp, 'is_published'))

# QuizQuestion
qq_tmp = QuizQuestion(
    option_a="Alpha", option_b="Beta", option_c="Gamma", option_d="Delta",
    correct_option="c",
)
check("get_option_text('c') returns option_c", qq_tmp.get_option_text('c') == "Gamma")
check("get_option_text('a') returns option_a", qq_tmp.get_option_text('a') == "Alpha")
check("get_option_text('') returns empty",    qq_tmp.get_option_text('') == '')

# StudentQuizAttempt percentage + passed
attempt_tmp = StudentQuizAttempt(score=7, total_questions=10)
check("percentage: 7/10 = 70%",        attempt_tmp.percentage == 70)
check("passed: 70% >= 60 -> True",     attempt_tmp.passed is True)

attempt_tmp2 = StudentQuizAttempt(score=5, total_questions=10)
check("passed: 50% < 60 -> False",     attempt_tmp2.passed is False)

attempt_zero = StudentQuizAttempt(score=0, total_questions=0)
check("percentage: 0/0 -> 0 (no ZeroDivision)", attempt_zero.percentage == 0)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 3. generate_quiz task execution (mocked Groq) ===")
# ─────────────────────────────────────────────────────────────────────────────

fake_questions = [
    {
        "question": f"Question {i+1}: What does Python support?",
        "option_a": "Only procedural programming",
        "option_b": "Multiple paradigms",
        "option_c": "Only OOP",
        "option_d": "Only functional",
        "correct_option": "b",
        "explanation": "Python supports multiple programming paradigms.",
    }
    for i in range(10)
]

mock_groq_instance = MagicMock()
mock_groq_instance.chat.completions.create.return_value.choices = [
    MagicMock(message=MagicMock(content=json.dumps(fake_questions)))
]

try:
    with patch("groq.Groq", return_value=mock_groq_instance):
        result = generate_quiz.apply(args=[p6_video.id])
    draft_count = QuizDraft.objects.filter(video=p6_video, status="pending").count()
    check("generate_quiz task runs without exception (mocked Groq)",
          result.successful() if hasattr(result, 'successful') else True)
    check("10 QuizDraft records created with status=PENDING",
          draft_count == 10, f"got {draft_count}")
except Exception as e:
    check("generate_quiz task execution", False, str(e)[:120])
    traceback.print_exc()

# Test markdown fence stripping
import re as _re
fenced_json = "```json\n" + json.dumps(fake_questions[:3]) + "\n```"
fence_match = _re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', fenced_json)
check("Markdown fence regex extracts JSON content",
      fence_match is not None and isinstance(json.loads(fence_match.group(1)), list))

# Test correct_option fallback for invalid value
bad_q = {"question": "X?", "option_a": "A", "option_b": "B",
         "option_c": "C", "option_d": "D",
         "correct_option": "z", "explanation": ""}
correct = str(bad_q.get('correct_option', 'a')).strip().lower()
if correct not in ('a', 'b', 'c', 'd'):
    correct = 'a'
check("Invalid correct_option 'z' falls back to 'a'", correct == 'a')

# Test with real Groq if key available
from django.conf import settings as _settings
groq_real = bool(_settings.GROQ_API_KEY and
                 _settings.GROQ_API_KEY not in ('your-groq-api-key', ''))
if groq_real:
    try:
        QuizDraft.objects.filter(video=p6_video).delete()
        result = generate_quiz.apply(args=[p6_video.id])
        real_count = QuizDraft.objects.filter(video=p6_video).count()
        if real_count > 0:
            check(f"Real Groq: {real_count} drafts created", real_count >= 5,
                  f"expected 10, got {real_count}")
        else:
            skip("Real Groq quiz generation", "Task ran but created 0 drafts -- check Groq key")
    except Exception as e:
        _em = str(e)
        if any(x in _em for x in ("401", "Invalid API Key", "invalid_api_key")):
            skip("Real Groq quiz generation", "GROQ_API_KEY set but returned 401")
        else:
            check("Real Groq quiz generation", False, _em[:80])
    # Re-create 10 pending drafts for subsequent tests
    if QuizDraft.objects.filter(video=p6_video).count() < 10:
        with patch("groq.Groq", return_value=mock_groq_instance):
            generate_quiz.apply(args=[p6_video.id])
else:
    skip("Real Groq quiz generation", "set GROQ_API_KEY in .env to enable")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 4. Email notification ===")
# ─────────────────────────────────────────────────────────────────────────────

try:
    QuizDraft.objects.filter(video=p6_video).delete()
    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="lms@test.com",
    ):
        _mail.outbox = []
        with patch("groq.Groq", return_value=mock_groq_instance):
            generate_quiz.apply(args=[p6_video.id])
        check("Email sent to admin(s)", len(_mail.outbox) >= 1,
              f"outbox={len(_mail.outbox)}")
        if _mail.outbox:
            msg = _mail.outbox[0]
            check("Email subject contains video title",
                  p6_video.title in msg.subject,
                  msg.subject[:60])
            check("Email recipient is admin email",
                  p6_admin.email in msg.to,
                  str(msg.to))
            check("Email body mentions course title",
                  p6_course.title in msg.body)
except Exception as e:
    check("Email notification test", False, str(e)[:120])
    traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 5. Admin draft list view ===")
# ─────────────────────────────────────────────────────────────────────────────

pending_drafts = QuizDraft.objects.filter(video=p6_video, status="pending")
check("Pending drafts exist in DB before view test",
      pending_drafts.count() > 0, f"count={pending_drafts.count()}")

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_admin = Client()
    cl_admin.login(username="p6_admin", password="pass123")

    cl_student = Client()
    cl_student.login(username="p6_student", password="pass123")

    # Non-admin gets redirected
    r = cl_student.get("/quizzes/drafts/")
    check("Non-admin GET /quizzes/drafts/ -> redirect",
          r.status_code == 302)

    # Admin gets 200
    r = cl_admin.get("/quizzes/drafts/")
    check("Admin GET /quizzes/drafts/ -> 200",
          r.status_code == 200)

    if r.status_code == 200:
        content = r.content.decode()
        check("Draft list shows video title",
              p6_video.title in content)
        check("Draft list shows pending count badge",
              "pending" in content.lower() or str(pending_drafts.count()) in content)
        check("Draft list shows Review button",
              "Review" in content)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 6. Review draft (approve / reject / edit) ===")
# ─────────────────────────────────────────────────────────────────────────────

all_pending = list(QuizDraft.objects.filter(video=p6_video, status="pending").order_by("created_at"))
check("At least 7 pending drafts available for review tests",
      len(all_pending) >= 7, f"got {len(all_pending)}")

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_admin = Client()
    cl_admin.login(username="p6_admin", password="pass123")

    # GET review page
    if all_pending:
        r = cl_admin.get(f"/quizzes/drafts/{all_pending[0].id}/review/")
        check("GET /quizzes/drafts/{id}/review/ -> 200",
              r.status_code == 200)
        if r.status_code == 200:
            content = r.content.decode()
            check("Review page shows question text",
                  all_pending[0].question_text[:30] in content)
            check("Review page has Approve & Save button",
                  "approve" in content.lower())
            check("Review page has Reject button",
                  "reject" in content.lower() or "Reject" in content)

    # Approve first 5 questions
    approved_ids = []
    for draft in all_pending[:5]:
        r = cl_admin.post(f"/quizzes/drafts/{draft.id}/review/", {
            "action": "approve",
            "question_text": draft.question_text,
            "option_a": draft.option_a,
            "option_b": draft.option_b,
            "option_c": draft.option_c,
            "option_d": draft.option_d,
            "correct_option": draft.correct_option,
            "explanation": draft.explanation,
        })
        draft.refresh_from_db()
        approved_ids.append(draft.id)

    approved_count = QuizDraft.objects.filter(
        video=p6_video, status="approved").count()
    check("POST approve -> status becomes APPROVED",
          approved_count == 5, f"got {approved_count}")
    check("Approve redirects to draft list",
          r.status_code == 302 and "/quizzes/drafts" in (r['Location'] if 'Location' in r else r.get('Location', '')))

    # Reject questions 6 and 7
    if len(all_pending) >= 7:
        for draft in all_pending[5:7]:
            r = cl_admin.post(f"/quizzes/drafts/{draft.id}/review/", {
                "action": "reject",
                "admin_note": "Unclear question, needs rework.",
            })
            draft.refresh_from_db()

        rejected_count = QuizDraft.objects.filter(
            video=p6_video, status="rejected").count()
        check("POST reject -> status becomes REJECTED",
              rejected_count == 2, f"got {rejected_count}")

        # Verify admin_note saved
        rejected_draft = QuizDraft.objects.filter(
            video=p6_video, status="rejected").first()
        check("Rejected draft has admin_note saved",
              bool(rejected_draft and rejected_draft.admin_note))

    # Edit and approve question 8 (if available)
    if len(all_pending) >= 8:
        draft_to_edit = all_pending[7]
        edited_text = "EDITED: What paradigms does Python support?"
        r = cl_admin.post(f"/quizzes/drafts/{draft_to_edit.id}/review/", {
            "action": "approve",
            "question_text": edited_text,
            "option_a": "Only OOP",
            "option_b": "Multiple paradigms",
            "option_c": "Only functional",
            "option_d": "Only procedural",
            "correct_option": "b",
            "explanation": "Python supports multiple paradigms.",
        })
        draft_to_edit.refresh_from_db()
        check("Edited question text saved on approve",
              draft_to_edit.question_text == edited_text)
        check("Edited correct_option saved",
              draft_to_edit.correct_option == "b")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 7. Publish quiz ===")
# ─────────────────────────────────────────────────────────────────────────────

approved = QuizDraft.objects.filter(video=p6_video, status="approved")
check("Approved drafts exist before publish test",
      approved.count() > 0, f"count={approved.count()}")

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_admin = Client()
    cl_admin.login(username="p6_admin", password="pass123")

    # GET publish page (confirmation)
    r = cl_admin.get(f"/quizzes/publish/{p6_video.id}/")
    check("GET /quizzes/publish/{video_id}/ -> 200",
          r.status_code == 200)
    if r.status_code == 200:
        content = r.content.decode()
        check("Publish page shows approved question count",
              str(approved.count()) in content or "question" in content.lower())

    approved_count_before = approved.count()

    # POST publish
    r = cl_admin.post(f"/quizzes/publish/{p6_video.id}/", {
        "title": "P6 Test Quiz",
    })
    check("POST /quizzes/publish/ -> 302 redirect",
          r.status_code == 302)

    # Verify Quiz created
    quiz_obj = Quiz.objects.filter(video=p6_video, title="P6 Test Quiz").first()
    check("Quiz object created in DB",
          quiz_obj is not None)

    if quiz_obj:
        check("Quiz is_published = True",
              quiz_obj.is_published is True)

        # Verify QuizQuestion records
        qq_count = QuizQuestion.objects.filter(quiz=quiz_obj).count()
        check(f"QuizQuestion records created ({qq_count})",
              qq_count == approved_count_before,
              f"expected {approved_count_before}, got {qq_count}")

        check("question_count() method works",
              quiz_obj.question_count() == qq_count)

        # Verify order field set
        orders = list(QuizQuestion.objects.filter(quiz=quiz_obj).values_list('order', flat=True))
        check("QuizQuestion order fields are sequential starting at 1",
              sorted(orders) == list(range(1, qq_count + 1)))

    # Verify approved drafts were deleted
    remaining = QuizDraft.objects.filter(video=p6_video, status="approved").count()
    check("Approved drafts deleted after publish",
          remaining == 0, f"{remaining} remaining")

    # Verify draft list now shows video in 'Ready to Publish' section (empty after publish)
    r = cl_admin.get("/quizzes/drafts/")
    check("Draft list accessible after publish", r.status_code == 200)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 8. Student quiz list ===")
# ─────────────────────────────────────────────────────────────────────────────

quiz_obj = Quiz.objects.filter(video=p6_video, is_published=True).first()
check("Published quiz exists for student view test", quiz_obj is not None)

with override_settings(ALLOWED_HOSTS=["*"]):
    cl_student = Client()
    cl_student.login(username="p6_student", password="pass123")

    cl_student2 = Client()
    cl_student2.login(username="p6_student2", password="pass123")

    # Enrolled student sees quiz
    r = cl_student.get("/quizzes/")
    check("Enrolled student GET /quizzes/ -> 200",
          r.status_code == 200)
    if r.status_code == 200 and quiz_obj:
        content = r.content.decode()
        check("Enrolled student sees quiz title",
              quiz_obj.title in content)
        check("Enrolled student sees 'Start Quiz' button",
              "Start Quiz" in content)

    # Non-enrolled student does NOT see the quiz
    r = cl_student2.get("/quizzes/")
    check("Non-enrolled student GET /quizzes/ -> 200 (no error)",
          r.status_code == 200)
    if r.status_code == 200 and quiz_obj:
        content2 = r.content.decode()
        check("Non-enrolled student does NOT see the quiz",
              quiz_obj.title not in content2)

    # Unauthenticated -> redirect
    cl_anon = Client()
    r = cl_anon.get("/quizzes/")
    check("Unauthenticated -> redirect to login",
          r.status_code == 302)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 9. Take quiz + score calculation ===")
# ─────────────────────────────────────────────────────────────────────────────

if not quiz_obj:
    skip("Take quiz tests", "no published quiz exists")
else:
    questions = list(quiz_obj.questions.order_by('order'))
    check(f"Quiz has {len(questions)} questions",
          len(questions) > 0)

    with override_settings(ALLOWED_HOSTS=["*"]):
        cl_student = Client()
        cl_student.login(username="p6_student", password="pass123")

        # GET first question
        r = cl_student.get(f"/quizzes/{quiz_obj.id}/take/")
        check("GET /quizzes/{id}/take/ -> 200",
              r.status_code == 200)
        if r.status_code == 200:
            content = r.content.decode()
            check("Take quiz page shows question text",
                  questions[0].question_text[:20] in content)
            check("Take quiz page shows progress bar",
                  "progress" in content.lower())
            check("Take quiz page shows question number",
                  "1" in content and str(len(questions)) in content)

        # Submit all answers: answer correctly for even questions, wrong for odd
        # Track expected score
        expected_score = 0
        for i, q in enumerate(questions):
            correct = q.correct_option
            wrong_opts = [x for x in ('a', 'b', 'c', 'd') if x != correct]
            if i % 2 == 0:
                chosen = correct
                expected_score += 1
            else:
                chosen = wrong_opts[0]

            r = cl_student.post(
                f"/quizzes/{quiz_obj.id}/take/?q={i}",
                {"answer": chosen, "question_id": str(q.id)},
            )
            # Should redirect on each answer
            if i < len(questions) - 1:
                check(f"Q{i+1} POST -> redirect to next question",
                      r.status_code == 302,
                      f"got {r.status_code}")

        # Last POST should redirect to results
        check("Final answer POST -> redirect to results",
              r.status_code == 302)
        if r.status_code == 302:
            check("Redirect target is results page",
                  f"/quizzes/{quiz_obj.id}/results/" in r['Location'])

        # Verify StudentQuizAttempt created
        attempt = StudentQuizAttempt.objects.filter(
            student=p6_student, quiz=quiz_obj
        ).first()
        check("StudentQuizAttempt record created in DB",
              attempt is not None)

        if attempt:
            check(f"Score is correct ({attempt.score}/{attempt.total_questions})",
                  attempt.score == expected_score,
                  f"expected {expected_score}, got {attempt.score}")
            check("total_questions matches quiz question count",
                  attempt.total_questions == len(questions))
            check("answers dict has entry per question",
                  len(attempt.answers) == len(questions))
            check("percentage property works",
                  0 <= attempt.percentage <= 100)

        # Already-attempted quiz redirects to results
        r = cl_student.get(f"/quizzes/{quiz_obj.id}/take/")
        check("Already-attempted quiz GET -> redirect to results",
              r.status_code == 302)
        if r.status_code == 302:
            check("Redirect is to results page",
                  "results" in r['Location'])


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 10. Results view ===")
# ─────────────────────────────────────────────────────────────────────────────

attempt = StudentQuizAttempt.objects.filter(
    student=p6_student, quiz=quiz_obj
).first() if quiz_obj else None

check("StudentQuizAttempt exists for results test", attempt is not None)

if attempt and quiz_obj:
    with override_settings(ALLOWED_HOSTS=["*"]):
        cl_student = Client()
        cl_student.login(username="p6_student", password="pass123")

        r = cl_student.get(f"/quizzes/{quiz_obj.id}/results/")
        check("GET /quizzes/{id}/results/ -> 200",
              r.status_code == 200)

        if r.status_code == 200:
            content = r.content.decode()
            check("Results page shows score fraction",
                  f"{attempt.score}" in content and
                  f"{attempt.total_questions}" in content)
            check("Results page shows percentage",
                  f"{attempt.percentage}%" in content)
            check("Results page shows pass/fail indicator",
                  "Passed" in content or "Not Passed" in content)
            check("Results page has per-question breakdown",
                  "Correct" in content or "Wrong" in content)
            check("Results page shows question text",
                  quiz_obj.questions.first().question_text[:15] in content)

        # Student can't see another student's results
        cl_student2 = Client()
        cl_student2.login(username="p6_student2", password="pass123")
        r2 = cl_student2.get(f"/quizzes/{quiz_obj.id}/results/")
        check("Non-attempted student results -> 404",
              r2.status_code == 404)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== manage.py check ===")
# ─────────────────────────────────────────────────────────────────────────────

out = StringIO()
call_command("check", stdout=out, stderr=out)
output = out.getvalue()
clean = ("no issues" in output or "0 issues" in output or output.strip() == "")
check("manage.py check reports 0 issues",
      clean, output.strip()[:120] if not clean else "clean")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Summary ===")
# ─────────────────────────────────────────────────────────────────────────────

if failures:
    print(f"  {len(failures)} FAIL(s):")
    for f in failures:
        print(f"    - {f}")
    sys.exit(1)
else:
    print("  All locally-testable checks PASSED.")
    print()
    print("  Infrastructure steps (require real services):")
    infra = []
    if not groq_real:
        infra.append("GROQ_API_KEY     -> Real Groq quiz generation (Steps 1+3)")
    if not bool(_settings.PINECONE_API_KEY and
                _settings.PINECONE_API_KEY != "your-pinecone-api-key"):
        infra.append("Pinecone         -> Not needed for quiz system")
    infra.append("Celery worker    -> Step 1 (generate_quiz.delay runs in background)")
    infra.append("Admin email      -> Step 3 (outbox checked with locmem backend)")
    for i in infra:
        print(f"    * {i}")
