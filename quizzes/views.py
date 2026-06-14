import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from users.decorators import role_home, role_required
from users.models import User
from .constants import QUIZ_TARGET_QUESTION_COUNT
from .models import Quiz, QuizDraft, QuizQuestion, StudentQuizAttempt

logger = logging.getLogger(__name__)


# ── Access helpers ─────────────────────────────────────────────────────────────

def _reviewer_home(user):
    return role_home(user)


def _can_review_video(user, video):
    if user.role == User.Role.ADMIN:
        return True
    return (
        user.role == User.Role.INSTRUCTOR
        and video.course.instructor_id == user.id
    )


def _reviewable_drafts(user):
    drafts = QuizDraft.objects.select_related('video', 'video__course')
    if user.role == User.Role.ADMIN:
        return drafts
    if user.role == User.Role.INSTRUCTOR:
        return drafts.filter(video__course__instructor=user)
    return drafts.none()


def _first_pending_video_id(user):
    return (
        _reviewable_drafts(user)
        .filter(status=QuizDraft.Status.PENDING)
        .order_by('created_at')
        .values_list('video_id', flat=True)
        .first()
    )


def _apply_draft_approval_fields(draft, data, prefix=''):
    draft.question_text = data.get(f'{prefix}question_text', draft.question_text).strip()
    draft.option_a = data.get(f'{prefix}option_a', draft.option_a).strip()
    draft.option_b = data.get(f'{prefix}option_b', draft.option_b).strip()
    draft.option_c = data.get(f'{prefix}option_c', draft.option_c).strip()
    draft.option_d = data.get(f'{prefix}option_d', draft.option_d).strip()
    correct = data.get(f'{prefix}correct_option', draft.correct_option).strip().lower()
    draft.correct_option = correct if correct in ('a', 'b', 'c', 'd') else draft.correct_option
    draft.explanation = data.get(f'{prefix}explanation', draft.explanation).strip()
    draft.status = QuizDraft.Status.APPROVED
    draft.admin_note = ''


def _publish_approved_drafts(video, title):
    approved = QuizDraft.objects.filter(video=video, status=QuizDraft.Status.APPROVED)
    quiz = Quiz.objects.create(video=video, title=title, is_published=True)
    questions_to_create = [
        QuizQuestion(
            quiz=quiz,
            question_text=d.question_text,
            option_a=d.option_a,
            option_b=d.option_b,
            option_c=d.option_c,
            option_d=d.option_d,
            correct_option=d.correct_option,
            explanation=d.explanation,
            order=i,
        )
        for i, d in enumerate(approved.order_by('created_at'), start=1)
    ]
    QuizQuestion.objects.bulk_create(questions_to_create)
    approved.delete()
    return quiz, len(questions_to_create)


def _quiz_review_progress(video):
    pending_count = QuizDraft.objects.filter(
        video=video,
        status=QuizDraft.Status.PENDING,
    ).count()
    approved_count = QuizDraft.objects.filter(
        video=video,
        status=QuizDraft.Status.APPROVED,
    ).count()
    return {
        'pending_count': pending_count,
        'approved_count': approved_count,
        'target_count': QUIZ_TARGET_QUESTION_COUNT,
        'can_publish': (
            pending_count == 0
            and approved_count == QUIZ_TARGET_QUESTION_COUNT
        ),
    }


def _quiz_workflow_state(video):
    progress = _quiz_review_progress(video)
    has_published_quiz = Quiz.objects.filter(video=video, is_published=True).exists()
    total_reviewable = progress['pending_count'] + progress['approved_count']
    return {
        **progress,
        'has_published_quiz': has_published_quiz,
        'total_reviewable': total_reviewable,
        'remaining_after_review': max(0, QUIZ_TARGET_QUESTION_COUNT - total_reviewable),
        'missing_generation_count': max(0, QUIZ_TARGET_QUESTION_COUNT - progress['approved_count']),
    }


# ── Admin views ────────────────────────────────────────────────────────────────

@role_required(User.Role.ADMIN, User.Role.INSTRUCTOR, message='Quiz review access required.')
def review_pending_quiz_drafts(request):
    video_id = _first_pending_video_id(request.user)
    if video_id:
        return redirect('bulk-review-quiz-drafts', video_id=video_id)
    messages.info(request, 'No pending quiz questions are waiting for approval.')
    return redirect('quiz-draft-list')


@role_required(User.Role.ADMIN, User.Role.INSTRUCTOR, message='Quiz review access required.')
def continue_quiz_workflow(request, video_id):
    from videos.models import Video

    video = get_object_or_404(Video.objects.select_related('course'), id=video_id)
    if not _can_review_video(request.user, video):
        messages.error(request, 'You do not have access to manage quiz workflow for that course.')
        return redirect(_reviewer_home(request.user))

    if request.method != 'POST':
        return redirect('video-detail', video_id=video.id)

    transcript = (video.english_transcript or '').strip()
    if not transcript:
        messages.error(request, 'Quiz generation requires study material first.')
        return redirect('video-detail', video_id=video.id)

    workflow = _quiz_workflow_state(video)
    if workflow['has_published_quiz']:
        messages.info(request, 'This content already has a published quiz.')
        return redirect('video-detail', video_id=video.id)

    if workflow['pending_count']:
        messages.info(
            request,
            f'{workflow["pending_count"]} pending quiz question(s) are ready for approval.'
        )
        return redirect('bulk-review-quiz-drafts', video_id=video.id)

    if workflow['can_publish']:
        messages.info(
            request,
            f'All {QUIZ_TARGET_QUESTION_COUNT} quiz questions are approved and ready to publish.'
        )
        return redirect('publish-quiz', video_id=video.id)

    if workflow['approved_count']:
        missing_count = workflow['missing_generation_count']
        try:
            from videos.tasks import generate_missing_quiz_drafts

            generated = generate_missing_quiz_drafts(
                video.id,
                missing_count,
                note='Generate the remaining unique quiz questions needed to complete this quiz set.',
            )
        except Exception as exc:
            logger.warning(
                'Missing quiz draft generation failed for video %s: %s',
                video.id,
                exc,
            )
            messages.warning(
                request,
                'The remaining quiz questions could not be generated right now. Please try again shortly.'
            )
            return redirect('quiz-draft-list')

        if not generated:
            messages.warning(
                request,
                'The remaining quiz questions could not be generated right now. Please try again shortly.'
            )
            return redirect('quiz-draft-list')

        messages.success(
            request,
            f'{len(generated)} additional quiz question(s) were generated for approval.'
        )
        return redirect('bulk-review-quiz-drafts', video_id=video.id)

    try:
        from videos.tasks import queue_quiz_generation

        queued = queue_quiz_generation(video.id)
    except Exception as exc:
        logger.exception('Quiz generation queue failed for video %s: %s', video.id, exc)
        messages.warning(
            request,
            'Quiz generation is temporarily unavailable. Please try again shortly.'
        )
        return redirect('video-detail', video_id=video.id)

    if queued is None:
        messages.warning(
            request,
            'Quiz generation is temporarily unavailable. Please try again shortly.'
        )
        return redirect('video-detail', video_id=video.id)

    if queued:
        messages.success(
            request,
            'Quiz generation started. The draft will appear in the review queue shortly.'
        )
        return redirect('video-detail', video_id=video.id)

    messages.info(
        request,
        'Quiz workflow is already in progress for this content.'
    )
    return redirect('quiz-draft-list')


@role_required(User.Role.ADMIN, User.Role.INSTRUCTOR, message='Quiz review access required.')
def quiz_draft_list(request):
    """Pending quiz drafts grouped by video."""
    pending = (
        _reviewable_drafts(request.user)
        .filter(status=QuizDraft.Status.PENDING)
        .order_by('video', 'created_at')
    )
    groups = {}
    pending_counts = {}
    first_pending_video_id = _first_pending_video_id(request.user)
    for draft in pending:
        groups.setdefault(draft.video, []).append(draft)
        pending_counts[draft.video_id] = pending_counts.get(draft.video_id, 0) + 1

    # Also show videos that have approved (not yet published) drafts
    approved_progress = []
    approved_by_video = {}
    approved = (
        _reviewable_drafts(request.user)
        .filter(status=QuizDraft.Status.APPROVED)
    )
    for draft in approved:
        approved_by_video.setdefault(draft.video, 0)
        approved_by_video[draft.video] += 1
    for video, approved_count in approved_by_video.items():
        pending_count = pending_counts.get(video.id, 0)
        if pending_count:
            continue
        approved_progress.append({
            'video': video,
            'approved_count': approved_count,
            'pending_count': pending_count,
            'can_publish': (
                pending_count == 0
                and approved_count == QUIZ_TARGET_QUESTION_COUNT
            ),
            'missing_count': max(0, QUIZ_TARGET_QUESTION_COUNT - approved_count),
        })

    return render(request, 'quizzes/draft_list.html', {
        'groups': groups,
        'approved_progress': approved_progress,
        'first_pending_video_id': first_pending_video_id,
        'quiz_target_count': QUIZ_TARGET_QUESTION_COUNT,
        'total_pending': pending.count(),
    })


@role_required(User.Role.ADMIN, User.Role.INSTRUCTOR, message='Quiz review access required.')
def review_quiz_draft(request, draft_id):
    """Approve (optionally edit fields), or reject a single draft question."""
    draft = get_object_or_404(QuizDraft, id=draft_id)
    if not _can_review_video(request.user, draft.video):
        messages.error(request, 'You do not have access to review that draft.')
        return redirect(_reviewer_home(request.user))

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        if action == 'approve':
            _apply_draft_approval_fields(draft, request.POST)
            draft.save()
            progress = _quiz_review_progress(draft.video)
            if progress['can_publish']:
                messages.success(
                    request,
                    f'Question approved. All {QUIZ_TARGET_QUESTION_COUNT} questions are approved and ready to publish.'
                )
            else:
                messages.success(
                    request,
                    f'Question approved. {progress["approved_count"]}/{QUIZ_TARGET_QUESTION_COUNT} questions are approved so far.'
                )
            return redirect('quiz-draft-list')

        elif action == 'reject':
            draft.status     = QuizDraft.Status.REJECTED
            draft.admin_note = request.POST.get('admin_note', '').strip()
            rejected_question = draft.question_text
            rejection_note = draft.admin_note
            draft.save(update_fields=['status', 'admin_note'])

            try:
                from videos.tasks import generate_replacement_quiz_drafts

                replacements = generate_replacement_quiz_drafts(
                    draft.video_id,
                    rejected_questions=[rejected_question],
                    rejection_note=rejection_note,
                )
            except Exception as exc:
                logger.warning(
                    'Replacement quiz draft generation failed for draft %s: %s',
                    draft.id,
                    exc,
                )
                messages.warning(
                    request,
                    'Question rejected. A replacement question could not be generated right now.'
                )
                return redirect('quiz-draft-list')

            if replacements:
                messages.warning(
                    request,
                    'Question rejected. A new replacement question was generated for review.'
                )
                return redirect('review-quiz-draft', draft_id=replacements[0].id)

            messages.warning(
                request,
                'Question rejected. A replacement question could not be generated right now.'
            )
            return redirect('quiz-draft-list')

    # Next/prev navigation within same video
    siblings = list(
        QuizDraft.objects
        .filter(video=draft.video, status=QuizDraft.Status.PENDING)
        .order_by('created_at')
        .values_list('id', flat=True)
    )
    try:
        idx      = siblings.index(draft.id)
        prev_id  = siblings[idx - 1] if idx > 0 else None
        next_id  = siblings[idx + 1] if idx < len(siblings) - 1 else None
    except ValueError:
        prev_id = next_id = None

    return render(request, 'quizzes/review_draft.html', {
        'draft':   draft,
        'prev_id': prev_id,
        'next_id': next_id,
        'total':   len(siblings),
        'index':   siblings.index(draft.id) + 1 if draft.id in siblings else '?',
        'quiz_target_count': QUIZ_TARGET_QUESTION_COUNT,
        'approved_count': QuizDraft.objects.filter(
            video=draft.video,
            status=QuizDraft.Status.APPROVED,
        ).count(),
    })


@role_required(User.Role.ADMIN, User.Role.INSTRUCTOR, message='Quiz review access required.')
def bulk_review_quiz_drafts(request, video_id):
    """Review all pending draft questions for a video in one screen."""
    from videos.models import Video

    video = get_object_or_404(Video.objects.select_related('course'), id=video_id)
    if not _can_review_video(request.user, video):
        messages.error(request, 'You do not have access to review drafts for that course.')
        return redirect(_reviewer_home(request.user))

    drafts = list(
        QuizDraft.objects
        .filter(video=video, status=QuizDraft.Status.PENDING)
        .order_by('created_at')
    )
    if not drafts:
        messages.info(request, 'No pending quiz drafts are available for bulk review.')
        return redirect('quiz-draft-list')

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        if action == 'approve_all':
            for draft in drafts:
                _apply_draft_approval_fields(draft, request.POST, prefix=f'{draft.id}_')
                draft.save()
            progress = _quiz_review_progress(video)
            if progress['can_publish']:
                messages.success(
                    request,
                    f'Approved {len(drafts)} question(s). All {QUIZ_TARGET_QUESTION_COUNT} questions are approved and ready to publish.'
                )
            else:
                messages.success(
                    request,
                    f'Approved {len(drafts)} question(s). {progress["approved_count"]}/{QUIZ_TARGET_QUESTION_COUNT} questions are approved so far.'
                )
            return redirect('quiz-draft-list')

        if action == 'approve_and_publish':
            for draft in drafts:
                _apply_draft_approval_fields(draft, request.POST, prefix=f'{draft.id}_')
                draft.save()
            progress = _quiz_review_progress(video)
            if not progress['can_publish']:
                messages.warning(
                    request,
                    f'Publishing is available only after exactly {QUIZ_TARGET_QUESTION_COUNT} questions are approved. Current progress: {progress["approved_count"]}/{QUIZ_TARGET_QUESTION_COUNT} approved.'
                )
                return redirect('quiz-draft-list')
            title = request.POST.get('title', f'Quiz: {video.title}').strip() or f'Quiz: {video.title}'
            _, question_count = _publish_approved_drafts(video, title)
            messages.success(
                request,
                f'Quiz "{title}" published with {question_count} questions. Enrolled students can access it now.'
            )
            return redirect('quiz-draft-list')

        if action == 'reject_all':
            note = request.POST.get('admin_note', '').strip()
            rejected_questions = []
            for draft in drafts:
                rejected_questions.append(draft.question_text)
                draft.status = QuizDraft.Status.REJECTED
                draft.admin_note = note
                draft.save(update_fields=['status', 'admin_note'])
            try:
                from videos.tasks import generate_replacement_quiz_drafts

                replacements = generate_replacement_quiz_drafts(
                    video.id,
                    rejected_questions=rejected_questions,
                    rejection_note=note,
                )
            except Exception as exc:
                logger.warning(
                    'Bulk replacement quiz draft generation failed for video %s: %s',
                    video.id,
                    exc,
                )
                messages.warning(
                    request,
                    'Questions rejected. Replacement questions could not be generated right now.'
                )
                return redirect('quiz-draft-list')

            if len(replacements) == len(rejected_questions):
                messages.warning(
                    request,
                    f'Rejected {len(drafts)} question(s). {len(replacements)} new question(s) were generated for review.'
                )
            elif replacements:
                messages.warning(
                    request,
                    f'Rejected {len(drafts)} question(s). Only {len(replacements)} replacement question(s) were generated this time.'
                )
            else:
                messages.warning(
                    request,
                    'Questions rejected. Replacement questions could not be generated right now.'
                )
                return redirect('quiz-draft-list')
            return redirect('bulk-review-quiz-drafts', video_id=video.id)

    return render(request, 'quizzes/bulk_review.html', {
        'video': video,
        'drafts': drafts,
        'total': len(drafts),
        'quiz_target_count': QUIZ_TARGET_QUESTION_COUNT,
        'approved_count': QuizDraft.objects.filter(
            video=video,
            status=QuizDraft.Status.APPROVED,
        ).count(),
    })


@role_required(User.Role.ADMIN, User.Role.INSTRUCTOR, message='Quiz review access required.')
def publish_quiz(request, video_id):
    """Publish all approved questions for a video as a Quiz."""
    from videos.models import Video
    video    = get_object_or_404(Video, id=video_id)
    if not _can_review_video(request.user, video):
        messages.error(request, 'You do not have access to publish quizzes for that course.')
        return redirect(_reviewer_home(request.user))
    approved = QuizDraft.objects.filter(video=video, status=QuizDraft.Status.APPROVED)
    progress = _quiz_review_progress(video)

    if not approved.exists():
        messages.error(request, 'No approved questions to publish for this video.')
        return redirect('quiz-draft-list')
    if not progress['can_publish']:
        messages.warning(
            request,
            f'Publishing is available only after exactly {QUIZ_TARGET_QUESTION_COUNT} questions are approved. Current progress: {progress["approved_count"]}/{QUIZ_TARGET_QUESTION_COUNT} approved and {progress["pending_count"]} pending.'
        )
        return redirect('quiz-draft-list')

    if request.method == 'POST':
        title = request.POST.get('title', f'Quiz: {video.title}').strip() or f'Quiz: {video.title}'
        _, question_count = _publish_approved_drafts(video, title)
        messages.success(request, f'Quiz "{title}" published with {question_count} questions.')
        return redirect('quiz-draft-list')

    return render(request, 'quizzes/publish_quiz.html', {
        'video':    video,
        'approved': approved,
        'count':    approved.count(),
        'quiz_target_count': QUIZ_TARGET_QUESTION_COUNT,
    })


# ── Student views ──────────────────────────────────────────────────────────────

@role_required(User.Role.STUDENT, message='Trainee access required.')
def student_quiz_list(request):
    """Published quizzes for the courses a student is enrolled in."""
    from courses.models import Enrollment
    enrolled_ids = Enrollment.objects.filter(
        student=request.user, is_active=True
    ).values_list('course_id', flat=True)

    quizzes = (
        Quiz.objects
        .filter(is_published=True, video__course_id__in=enrolled_ids)
        .select_related('video', 'video__course')
        .order_by('-created_at')
    )

    attempted_ids = set(
        StudentQuizAttempt.objects
        .filter(student=request.user)
        .values_list('quiz_id', flat=True)
    )

    quiz_list = [
        {'quiz': q, 'attempted': q.id in attempted_ids}
        for q in quizzes
    ]
    return render(request, 'quizzes/quiz_list.html', {'quiz_list': quiz_list})


@role_required(User.Role.STUDENT, message='Trainee access required.')
def take_quiz(request, quiz_id):
    """One question at a time; answers stored in session until final submit."""
    quiz      = get_object_or_404(Quiz, id=quiz_id, is_published=True)

    from courses.models import Enrollment
    enrolled = Enrollment.objects.filter(
        student=request.user, course=quiz.video.course, is_active=True
    ).exists()
    if not enrolled:
        messages.error(request, 'You are not enrolled in this course.')
        return redirect('student-quiz-list')

    questions = list(quiz.questions.order_by('order'))
    total     = len(questions)

    if total == 0:
        messages.error(request, 'This quiz has no questions yet.')
        return redirect('student-quiz-list')

    if StudentQuizAttempt.objects.filter(student=request.user, quiz=quiz).exists():
        messages.info(request, 'You have already completed this quiz.')
        return redirect('quiz-results', quiz_id=quiz.id)

    session_key = f'quiz_{quiz_id}_answers'
    answers     = request.session.get(session_key, {})

    try:
        q_index = int(request.GET.get('q', 0))
    except (ValueError, TypeError):
        q_index = 0
    q_index = max(0, min(q_index, total - 1))

    if request.method == 'POST':
        chosen = request.POST.get('answer', '').strip().lower()
        q_id   = request.POST.get('question_id', '').strip()
        if chosen in ('a', 'b', 'c', 'd') and q_id:
            answers[q_id] = chosen
            request.session[session_key] = answers
            request.session.modified = True

        next_index = q_index + 1
        if next_index >= total:
            # Final question answered — score and persist
            score = sum(
                1 for q in questions
                if answers.get(str(q.id)) == q.correct_option
            )
            StudentQuizAttempt.objects.create(
                student         = request.user,
                quiz            = quiz,
                score           = score,
                total_questions = total,
                answers         = answers,
            )
            request.session.pop(session_key, None)
            return redirect('quiz-results', quiz_id=quiz.id)

        return redirect(f"{request.path}?q={next_index}")

    current_q   = questions[q_index]
    prior_answer = answers.get(str(current_q.id), '')

    return render(request, 'quizzes/take_quiz.html', {
        'quiz':         quiz,
        'question':     current_q,
        'q_index':      q_index,
        'total':        total,
        'q_number':     q_index + 1,
        'progress_pct': round(q_index / total * 100),
        'prior_answer': prior_answer,
        'options': [
            ('a', current_q.option_a),
            ('b', current_q.option_b),
            ('c', current_q.option_c),
            ('d', current_q.option_d),
        ],
    })


@role_required(User.Role.STUDENT, message='Trainee access required.')
def quiz_results(request, quiz_id):
    """Score card with per-question correct/incorrect breakdown."""
    quiz    = get_object_or_404(Quiz, id=quiz_id)

    from courses.models import Enrollment
    enrolled = Enrollment.objects.filter(
        student=request.user, course=quiz.video.course, is_active=True
    ).exists()
    if not enrolled:
        messages.error(request, 'You are not enrolled in this course.')
        return redirect('student-quiz-list')

    attempt = get_object_or_404(StudentQuizAttempt, student=request.user, quiz=quiz)

    breakdown = [
        {
            'question':        q,
            'chosen':          attempt.answers.get(str(q.id), ''),
            'chosen_text':     q.get_option_text(attempt.answers.get(str(q.id), '')),
            'is_correct':      attempt.answers.get(str(q.id)) == q.correct_option,
            'correct_text':    q.get_option_text(q.correct_option),
        }
        for q in quiz.questions.order_by('order')
    ]

    return render(request, 'quizzes/results.html', {
        'quiz':      quiz,
        'attempt':   attempt,
        'breakdown': breakdown,
    })
