"""The PRIVATE study layer: notes, status, tasks, questions, dashboard.

Kept in its own module, separate from `views.py`, deliberately. `views.py` holds
the public reference pages and must never query a user-owned model; everything
that touches one lives here. That separation is what makes the "no private data
in a public context" rule STRUCTURAL rather than a discipline someone has to
remember on every future template edit (scope §4.c).

🔴 EVERY view in this module is a door. Each one carries `@study_gate`. A POST
handler that forgets its check is a hole even when the form calling it is hidden
— hiding a form is not access control (parent scope §4.8.1: "gate every door
separately"). `test_every_non_public_route_is_gated` walks the URLconf and fails
if a new route appears here without a gate, so the guarantee survives additions.
"""
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import NoteStatusForm, QuestionAnswerForm, QuestionForm, UserNoteForm
from .models import LearningPath, Note, NoteStatus, Question, TaskState, UserNote
from .rendering import note_url, render_in_note_context, render_note

# One constant, used by every view here, so no two doors can end up checking
# different things — the same single-source rule listening/views.py follows.
STUDY_PERMISSION = "learning.view_study_layer"


def study_gate(view):
    """Both decorators, always together, applied as ONE.

    `login_required` sends anonymous users to the login page (302); the
    `raise_exception=True` on `permission_required` then gives an authenticated
    but unpermitted user a 403 rather than bouncing them to a login form they
    are already past (parent scope §4.8.1).

    Bundled into a single decorator on purpose: applying two by hand to six
    endpoints is an invitation to apply one.
    """
    return login_required(
        permission_required(STUDY_PERMISSION, raise_exception=True)(view)
    )


KIND_FROM_SEGMENT = {
    "modules": Note.Kind.MODULE,
    "concepts": Note.Kind.CONCEPT,
    "sources": Note.Kind.SOURCE,
}


def _get_note(path_slug, kind_segment, slug):
    kind = KIND_FROM_SEGMENT[kind_segment]
    path = get_object_or_404(LearningPath, slug=path_slug, is_public=True)
    note = get_object_or_404(
        Note.objects.select_related("path", "module"),
        path=path, slug=slug, kind=kind, is_archived=False,
    )
    return path, note


SEGMENT_FROM_KIND = {v: k for k, v in KIND_FROM_SEGMENT.items()}


def _study_redirect(note):
    """Back to the study view after any write. POST-redirect-GET, so a refresh
    does not resubmit."""
    return redirect(reverse("learning-study", kwargs={
        "path_slug": note.path.slug,
        "kind_segment": SEGMENT_FROM_KIND[note.kind],
        "slug": note.slug,
    }))


# ---------------------------------------------------------------------
# The study view
# ---------------------------------------------------------------------


@study_gate
def study_note(request, path_slug, kind_segment, slug):
    """Reference content read-only, plus every editor, on one page.

    The reference half renders through the SAME partial the public page uses, so
    the definition stays visible while you write about it and there is no second
    copy of that markup to drift.
    """
    path, note = _get_note(path_slug, kind_segment, slug)

    status = NoteStatus.objects.filter(user=request.user, note=note).first()
    user_note = UserNote.objects.filter(user=request.user, note=note).first()
    tasks = list(note.tasks.order_by("order"))
    done_hashes = set(
        TaskState.objects.filter(user=request.user, note=note, done=True)
        .values_list("text_hash", flat=True)
    )
    for task in tasks:
        task.is_done = task.text_hash in done_hashes

    return render(request, "learning/study.html", {
        "path": path,
        "note": note,
        "body": render_note(note),
        "public_url": note_url(note),
        "kind_segment": kind_segment,
        "tasks": tasks,
        "tags": note.tags.all(),
        # Absence of a NoteStatus row MEANS not-started; rows are created lazily
        # so nobody carries 53 empty rows (scope §4.b.4).
        "status_form": NoteStatusForm(instance=status),
        "current_status": status.status if status else NoteStatus.Status.NOT_STARTED,
        "note_form": UserNoteForm(instance=user_note),
        "user_note": user_note,
        "user_note_html": render_user_note(user_note, note),
        "question_form": QuestionForm(),
        "questions": Question.objects.filter(user=request.user, note=note),
    })


def render_user_note(user_note, note):
    """Render the user's own markdown through the SAME pipeline as reference content."""
    if not user_note or not user_note.body_markdown.strip():
        return ""
    return render_in_note_context(user_note.body_markdown, note)


# ---------------------------------------------------------------------
# Write endpoints — one door each, all gated
# ---------------------------------------------------------------------


@study_gate
@require_POST
def save_user_note(request, path_slug, kind_segment, slug):
    _, note = _get_note(path_slug, kind_segment, slug)
    instance = UserNote.objects.filter(user=request.user, note=note).first()
    form = UserNoteForm(request.POST, instance=instance)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.note = note
        obj.save()
    return _study_redirect(note)


@study_gate
@require_POST
def set_status(request, path_slug, kind_segment, slug):
    _, note = _get_note(path_slug, kind_segment, slug)
    instance = NoteStatus.objects.filter(user=request.user, note=note).first()
    form = NoteStatusForm(request.POST, instance=instance)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.note = note
        obj.save()
    return _study_redirect(note)


@study_gate
@require_POST
def save_tasks(request, path_slug, kind_segment, slug):
    """One form, one Save, all checkboxes.

    State is keyed on `text_hash`, never on a StudyTask row id — the importer
    replaces those rows on every run (scope §2.6.2).
    """
    _, note = _get_note(path_slug, kind_segment, slug)
    ticked = set(request.POST.getlist("task"))
    # Only hashes that actually belong to this note, so a crafted POST cannot
    # invent state for arbitrary tasks.
    valid = set(note.tasks.values_list("text_hash", flat=True))
    for text_hash in valid:
        done = text_hash in ticked
        state, _created = TaskState.objects.get_or_create(
            user=request.user, note=note, text_hash=text_hash,
        )
        if state.done != done:
            state.done = done
            state.done_at = timezone.now() if done else None
            state.save(update_fields=["done", "done_at"])
    return _study_redirect(note)


@study_gate
@require_POST
def ask_question(request, path_slug, kind_segment, slug):
    _, note = _get_note(path_slug, kind_segment, slug)
    form = QuestionForm(request.POST)
    if form.is_valid():
        question = form.save(commit=False)
        question.user = request.user
        question.note = note
        question.save()
    return _study_redirect(note)


@study_gate
@require_POST
def answer_question(request, path_slug, question_id):
    """Resolve a question.

    Filtered on `user=request.user`, so one account cannot answer another's
    question by guessing an id. Only one user exists today; that is not a reason
    to write it wrongly.
    """
    question = get_object_or_404(
        Question, pk=question_id, user=request.user, note__path__slug=path_slug,
    )
    form = QuestionAnswerForm(request.POST, instance=question)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.status = (Question.Status.ANSWERED if obj.answer.strip()
                      else Question.Status.OPEN)
        obj.save()
    return _study_redirect(question.note)


# ---------------------------------------------------------------------
# The dashboard — replaces the vault's SEVEN Dataview/Tasks blocks
# ---------------------------------------------------------------------


@study_gate
def dashboard(request, path_slug):
    path = get_object_or_404(LearningPath, slug=path_slug, is_public=True)
    notes = path.notes.filter(is_archived=False)
    user = request.user

    status_by_note = dict(
        NoteStatus.objects.filter(user=user, note__path=path)
        .values_list("note_id", "status")
    )
    default = NoteStatus.Status.NOT_STARTED

    modules = list(notes.filter(kind=Note.Kind.MODULE).order_by("order"))
    for module in modules:
        module.user_status = status_by_note.get(module.pk, default)

    concepts = list(notes.filter(kind=Note.Kind.CONCEPT).order_by("title"))
    for concept in concepts:
        concept.user_status = status_by_note.get(concept.pk, default)

    done_hashes = set(
        TaskState.objects.filter(user=user, note__path=path, done=True)
        .values_list("text_hash", flat=True)
    )
    open_tasks = [
        task
        for owner in notes.prefetch_related("tasks")
        for task in owner.tasks.all()
        if task.text_hash not in done_hashes
    ]

    total = len(modules) + len(concepts)
    completed = sum(1 for n in modules + concepts if n.user_status == NoteStatus.Status.DONE)

    return render(request, "learning/dashboard.html", {
        "path": path,
        # 1. module progress · 2. concepts by status · 3. completed concepts
        "modules": modules,
        "concepts_open": [c for c in concepts if c.user_status != NoteStatus.Status.DONE],
        "concepts_done": [c for c in concepts if c.user_status == NoteStatus.Status.DONE],
        # 4. open study tasks · 5. open questions · 6. question notes
        "open_tasks": open_tasks,
        "open_questions": Question.objects.filter(
            user=user, note__path=path, status=Question.Status.OPEN),
        "answered_questions": Question.objects.filter(
            user=user, note__path=path, status=Question.Status.ANSWERED)[:10],
        # 7. recently touched — USER activity, not vault_modified, which only
        # moves when an import runs (scope §4.c).
        "recent_notes": UserNote.objects.filter(user=user, note__path=path)
                                        .select_related("note")
                                        .order_by("-updated_at")[:8],
        "recent_statuses": NoteStatus.objects.filter(user=user, note__path=path)
                                             .select_related("note")
                                             .order_by("-updated_at")[:8],
        "progress_done": completed,
        "progress_total": total,
        "progress_pct": round(100 * completed / total) if total else 0,
    })
