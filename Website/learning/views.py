"""Public reference views for learning paths.

PUBLIC BY DESIGN — unlike `listening`, these pages carry no gate. The private
study layer (notes, statuses, questions, dashboard) arrives in Phase 4 behind
`learning.view_study_layer`, and when it does the private data must be kept out
of the CONTEXT for anonymous users, not merely hidden in the template.

Nothing in this module may read a user-owned model. There is nothing to leak yet,
and keeping it that way is easier than auditing it later.
"""
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render

from .models import LearningPath, Note, NoteLink
from .rendering import render_note

VISIBLE = {"is_archived": False}


def path_index(request):
    """List the public learning paths.

    Carries the same per-kind counts as the portfolio card, so the two agree and
    this page is not a bare list of one link — it is a linked destination from
    /portfolio/, not just an internal index.
    """
    paths = (
        LearningPath.objects.filter(is_public=True)
        .annotate(
            module_count=Count(
                "notes", filter=Q(notes__kind=Note.Kind.MODULE, notes__is_archived=False),
                distinct=True,
            ),
            concept_count=Count(
                "notes", filter=Q(notes__kind=Note.Kind.CONCEPT, notes__is_archived=False),
                distinct=True,
            ),
        )
        .order_by("title")
    )
    return render(request, "learning/path-index.html", {"paths": paths})


def path_overview(request, path_slug):
    """The generated MOC.

    Built from the database rather than imported, because the vault's own MOC is
    five Dataview queries that mean nothing outside Obsidian (scope §2.7).
    """
    path = get_object_or_404(LearningPath, slug=path_slug, is_public=True)
    notes = path.notes.filter(**VISIBLE)

    modules = notes.filter(kind=Note.Kind.MODULE).order_by("order")
    parts = []
    for module in modules:
        if not parts or parts[-1]["name"] != module.part:
            parts.append({"name": module.part, "modules": []})
        parts[-1]["modules"].append(module)

    concepts = notes.filter(kind=Note.Kind.CONCEPT).prefetch_related("tags")
    by_tag = {}
    for concept in concepts:
        for tag in concept.tags.all():
            by_tag.setdefault(tag, []).append(concept)

    return render(request, "learning/path-overview.html", {
        "path": path,
        "parts": parts,
        "concept_groups": sorted(by_tag.items(), key=lambda kv: kv[0].name),
        "orphan_concepts": [c for c in concepts if not c.tags.exists()],
        "sources": notes.filter(kind=Note.Kind.SOURCE).order_by("title"),
        "module_count": modules.count(),
        "concept_count": concepts.count(),
    })


def _note_page(request, path_slug, slug, kind, template):
    path = get_object_or_404(LearningPath, slug=path_slug, is_public=True)
    note = get_object_or_404(
        Note.objects.select_related("path", "module"),
        path=path, slug=slug, kind=kind, is_archived=False,
    )

    backlinks = (
        NoteLink.objects.filter(to_note=note, from_note__is_archived=False)
        .select_related("from_note", "from_note__path")
        .order_by("from_note__title")
    )
    # One note may link to another several times; the panel wants each source once.
    seen, unique_backlinks = set(), []
    for link in backlinks:
        if link.from_note_id not in seen:
            seen.add(link.from_note_id)
            unique_backlinks.append(link.from_note)

    return render(request, template, {
        "path": path,
        "note": note,
        "body": render_note(note),
        "backlinks": unique_backlinks,
        "tasks": note.tasks.order_by("order"),
        "tags": note.tags.all(),
        "prev_note": _sibling(path, note, -1),
        "next_note": _sibling(path, note, +1),
        "concepts": (note.concepts.filter(is_archived=False).order_by("title")
                     if kind == Note.Kind.MODULE else None),
    })


def _sibling(path, note, offset):
    """Prev/Next within the module spine. Derived from `order`, not from the
    vault's inline Prev/Next links — the database already knows the sequence."""
    if note.kind != Note.Kind.MODULE:
        return None
    siblings = list(path.notes.filter(kind=Note.Kind.MODULE, is_archived=False).order_by("order"))
    index = siblings.index(note) + offset
    if 0 <= index < len(siblings):
        return siblings[index]
    return None


def module_detail(request, path_slug, slug):
    return _note_page(request, path_slug, slug, Note.Kind.MODULE, "learning/note.html")


def concept_detail(request, path_slug, slug):
    return _note_page(request, path_slug, slug, Note.Kind.CONCEPT, "learning/note.html")


def source_detail(request, path_slug, slug):
    return _note_page(request, path_slug, slug, Note.Kind.SOURCE, "learning/note.html")
