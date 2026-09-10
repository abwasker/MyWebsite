"""Models for the `learning` app — Obsidian learning paths published as site sections.

See the scope: [[Fiber Bundles Learning Path]] §4.1.

THE ONE INVARIANT THAT MATTERS (scope §2.6)
-------------------------------------------
The Obsidian vault is the source of truth for *reference* content, and it is
re-imported repeatedly. Everything in this file therefore falls into exactly one
of two groups, and the file is ordered to make that visible:

  1. VAULT-SOURCED  — the importer owns these. It upserts them on every run.
                      Never hand-edit; the next import silently overwrites you.
  2. USER-OWNED     — the importer MUST NEVER touch these. Notes, statuses,
                      question resolutions and task ticks live here and have to
                      survive every re-import.

Get that boundary wrong and the failure is silent: a re-import quietly discards
thinking that was never anywhere else, because nothing writes back to the vault.
"""
from django.conf import settings
from django.db import models


# =====================================================================
# GROUP 1 — VAULT-SOURCED.  Importer-owned. Upserted on every import.
# =====================================================================


class LearningPath(models.Model):
    """One imported vault folder. `Fiber Bundles` is the first row, not the schema."""

    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=200)
    goal = models.TextField(blank=True)

    # Reference pages are public; only the study layer is gated (see LearningAccess).
    is_public = models.BooleanField(default=False)

    # Absolute path to the vault folder on the machine that runs the importer.
    # NOT usable on the server: the vault lives on a local Google Drive path the
    # Linode cannot reach (scope §2.5), so imports run locally and content is
    # pushed with the parent project's §10.11 procedure.
    vault_folder = models.CharField(max_length=500, blank=True)

    # Vault filename of the note with `type: moc` — e.g. "Fiber Bundles MOC".
    #
    # The MOC is NOT imported as a Note: Phase 3 GENERATES the path overview from
    # the database, because the vault's copy is five Dataview queries that mean
    # nothing outside Obsidian (scope §2.7). But every module links back to it in
    # its breadcrumb, so without recording the name those links resolve nowhere
    # and render as broken. Storing it here lets link rendering send
    # `[[Fiber Bundles MOC]]` to this path's overview URL instead.
    moc_filename = models.CharField(max_length=200, blank=True)

    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class Tag(models.Model):
    """A vault tag, e.g. `fiber-bundles/bundles`.

    Stored FLAT, with the full string in `name` and the display half in `leaf`.
    The corpus has 15 tags one level deep (scope §2.9), so a self-referential
    parent FK would be over-engineering — and can be added later without
    touching any imported row.
    """

    path = models.ForeignKey(LearningPath, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=120)   # "fiber-bundles/bundles"
    leaf = models.CharField(max_length=120)   # "bundles"
    slug = models.SlugField(max_length=120)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["path", "name"], name="uniq_tag_name_per_path"),
        ]

    def __str__(self):
        return self.name


class Note(models.Model):
    """One imported markdown note: a module, a concept, or a source.

    ONE TABLE, NOT THREE (scope §4.1). Modules, concepts and sources share
    nearly every field, and the link graph is cross-kind — concepts link to
    modules, sources link to concepts. Separate tables would force a three-way
    union for both wikilink resolution and backlinks.

    Body is stored as free-form markdown deliberately. Phase 0 found module
    structure is NOT uniform (`## Core idea` in 9 of 11, five one-off headings),
    so there is nothing stable to model as fields.
    """

    class Kind(models.TextChoices):
        MODULE = "module", "Module"
        CONCEPT = "concept", "Concept"
        SOURCE = "source", "Source"

    path = models.ForeignKey(LearningPath, on_delete=models.CASCADE, related_name="notes")
    kind = models.CharField(max_length=20, choices=Kind.choices)

    slug = models.SlugField(max_length=200)
    title = models.CharField(max_length=200)

    # THE JOIN KEY (scope §2.6). The vault filename without `.md` — this is also
    # what `[[wikilinks]]` target. Stable across re-imports, which is exactly why
    # user-owned rows may safely hang off this Note.
    vault_filename = models.CharField(max_length=200)

    body_markdown = models.TextField(blank=True)

    # Nullable render cache, populated by the importer from Phase 2 onward.
    # `body_markdown` remains the source of truth; this exists so a 961 MB box
    # is not re-rendering 39 concept pages on demand.
    body_html = models.TextField(blank=True, null=True)

    # Modules only.
    part = models.CharField(max_length=80, blank=True)   # "III - Bundle Theory"
    order = models.PositiveIntegerField(default=0)

    # Concepts only — the owning module, from the `module:` frontmatter key.
    module = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="concepts",
    )

    tags = models.ManyToManyField(Tag, blank=True, related_name="notes")

    vault_modified = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    # Soft delete. The importer ARCHIVES rather than deletes (scope §2.6.3), so a
    # vault rename cannot orphan a user's notes without a human noticing.
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["kind", "order", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["path", "vault_filename"], name="uniq_note_vault_filename_per_path",
            ),
            models.UniqueConstraint(fields=["path", "slug"], name="uniq_note_slug_per_path"),
        ]
        indexes = [models.Index(fields=["path", "kind"])]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"


class NoteLink(models.Model):
    """A resolved `[[wikilink]]`. Gives backlinks cheaply and makes broken links queryable.

    `to_note` is null when the target resolves nowhere — Phase 0 found zero such
    links today, but §2.3 requires unresolvable links to be *visible* rather than
    silently rendered as literal text, which is the failure mode §4.12 already hit.
    """

    from_note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="links_out")
    to_note = models.ForeignKey(
        Note, on_delete=models.SET_NULL, null=True, blank=True, related_name="links_in",
    )
    raw_target = models.CharField(max_length=200)
    display_text = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["raw_target"]
        indexes = [
            models.Index(fields=["to_note"]),          # backlinks panel
            models.Index(fields=["from_note", "raw_target"]),
        ]

    def __str__(self):
        return f"{self.from_note.title} -> {self.raw_target}"


class StudyTask(models.Model):
    """A `- [ ] #study` / `#question` checkbox line lifted out of a note body.

    Extracted at import rather than rendered as text, because Python-Markdown has
    no tasklist extension enabled — `- [ ] #study …` otherwise renders as a literal
    `[ ]` (scope §2.4).

    IMPORTER RULE: skip items whose body is only a tag. Phase 0 found 50 of 97
    checkboxes are bare `- [ ] #question` template stubs; importing them fills the
    dashboard with blanks.
    """

    class Kind(models.TextChoices):
        STUDY = "study", "Study"
        QUESTION = "question", "Question"

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="tasks")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    text = models.TextField()

    # sha256 of the normalised text. This — not the row id — is what user tick
    # state joins on, so completion survives the importer replacing these rows.
    text_hash = models.CharField(max_length=64, db_index=True)

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["note", "order"]

    def __str__(self):
        return f"[{self.kind}] {self.text[:60]}"


# =====================================================================
# GROUP 2 — USER-OWNED.  The importer MUST NEVER write to these.
# =====================================================================


class NoteStatus(models.Model):
    """Per-user progress through a note.

    Status lives here rather than on `Note` because it is *state*, not reference —
    and a copy on `Note` would be overwritten by every import. The importer may
    seed an initial value on first import and must never touch it again.

    All four choices are kept even though the vault currently uses only
    `not-started` (×50) and `reading` (×1) — scope §2.9.
    """

    class Status(models.TextChoices):
        NOT_STARTED = "not-started", "Not started"
        READING = "reading", "Reading"
        REVIEW = "review", "Review"
        DONE = "done", "Done"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="learning_statuses")
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="statuses")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "note statuses"
        constraints = [
            models.UniqueConstraint(fields=["user", "note"], name="uniq_status_per_user_note"),
        ]

    def __str__(self):
        return f"{self.user}: {self.note.title} = {self.status}"


class UserNote(models.Model):
    """The `## My Notes` panel — the user's own thinking about a note.

    This is the content that exists NOWHERE ELSE. Nothing writes back to the
    vault (scope §2.8), so losing a row here is unrecoverable.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="learning_notes")
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="user_notes")
    body_markdown = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "note"], name="uniq_usernote_per_user_note"),
        ]

    def __str__(self):
        return f"{self.user}'s notes on {self.note.title}"


class TaskState(models.Model):
    """Whether a user has ticked a study task.

    DELIBERATELY NOT a FK to StudyTask. The importer replaces StudyTask rows on
    every run, so joining on a row id would silently untick everything each time
    (scope §2.6.2). Joining on `(note, text_hash)` means state survives as long as
    the task text does — and an edited task legitimately reads as a new one.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="learning_task_states")
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="task_states")
    text_hash = models.CharField(max_length=64)
    done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "note", "text_hash"], name="uniq_taskstate_per_user_note_hash",
            ),
        ]
        indexes = [models.Index(fields=["user", "done"])]

    def __str__(self):
        return f"{self.user}: {self.text_hash[:8]} = {'done' if self.done else 'open'}"


class Question(models.Model):
    """A question raised while studying, and its eventual answer.

    `note` is nullable so a question can be standalone, mirroring the vault's
    Questions folder alongside inline `#question` items.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ANSWERED = "answered", "Answered"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="learning_questions")
    note = models.ForeignKey(Note, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="questions")
    title = models.CharField(max_length=250)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    answer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-created_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return self.title


# =====================================================================
# Permission anchor — no table, no rows, never instantiated.
# =====================================================================


class LearningAccess(models.Model):
    """Permission anchor for the private study layer.

    Same pattern as `listening.ListeningAccess`: `managed = False` means Django
    creates no table, but `create_permissions` still walks every model in an
    installed app — managed or not — so the ContentType and Permission rows are
    created by the migration.

    Deliberately NOT registered in admin.py; there is nothing to list.

    HOW THIS DIFFERS FROM `listening.view_dashboard`
    ------------------------------------------------
    The listening dashboard is private in its entirety. Here the *reference*
    pages — modules, concepts, sources — are deliberately PUBLIC, and this
    permission gates only the study layer: `My Notes`, statuses, questions and
    the dashboard. So the gate is per-section, not per-page, and the private data
    must be kept out of the template CONTEXT for anonymous users rather than
    merely hidden in the template.

    Remember the other doors (scope §4.2): any future export endpoint, and
    Django's own /admin/ — a staff user holding `learning.view_usernote` would
    read every user's private notes there, which is why the user-owned models
    above are not registered in admin.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [("view_study_layer", "Can view the private learning study layer")]
