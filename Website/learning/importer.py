"""Upsert parsed vault notes into the database.

THE RULE THIS FILE EXISTS TO HONOUR (scope §2.6): the importer owns the
vault-sourced models and MUST NEVER write to the user-owned ones. Nothing here
may touch UserNote, NoteStatus, TaskState or Question — not to clean up, not to
cascade, not to "fix" anything. Those rows hold thinking that exists nowhere
else, because nothing writes back to the vault.

Consequences that shape the code below:

* Notes are keyed on (path, vault_filename) and UPSERTED. Never delete-and-recreate:
  user rows FK to Note, and recreating would cascade them away.
* A vanished file ARCHIVES its note; it never deletes it.
* Change detection is CONTENT-based, never mtime-based. Saving a file in Obsidian
  without editing it bumps mtime, and treating that as a change would make the
  idempotency guarantee meaningless.
* StudyTask rows are replaced freely — user tick state lives in TaskState keyed on
  (note, text_hash), deliberately NOT on a StudyTask row id.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from django.db import transaction
from django.utils.text import slugify

from . import vault
from .models import LearningPath, Note, NoteLink, StudyTask, Tag


class _Rollback(Exception):
    """Internal sentinel used to abort a --dry-run transaction."""


@dataclass
class ImportReport:
    created: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    unchanged: list = field(default_factory=list)
    archived: list = field(default_factory=list)
    restored: list = field(default_factory=list)
    skipped_files: list = field(default_factory=list)
    slug_conflicts: list = field(default_factory=list)
    broken_links: list = field(default_factory=list)
    moc_links: int = 0
    unparsed: list = field(default_factory=list)
    tags_created: int = 0
    tags_removed: int = 0
    dry_run: bool = False
    path_created: bool = False

    @property
    def changed(self):
        return bool(self.created or self.updated or self.archived or self.restored)


def _read_source(source_dir):
    """Parse every .md under `source_dir`. Returns (notes, skipped, moc_filename, unparsed)."""
    notes, skipped, unparsed, moc_filename = [], [], [], ""
    for f in sorted(Path(source_dir).rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        meta, _, _ = vault.parse_frontmatter(text)
        if (meta.get("type") or "").strip() == "moc":
            moc_filename = f.stem
        parsed = vault.parse_note(f.stem, text)
        if parsed is None:
            skipped.append(f.stem)
            continue
        parsed.vault_modified = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        notes.append(parsed)
        unparsed.extend(f"{f.stem}: {line}" for line in parsed.unparsed)
    return notes, skipped, moc_filename, unparsed


def _sync_tags(path, parsed_notes, report):
    """Rebuild the Tag table for this path. Safe to do wholesale — tags hold no user data."""
    wanted = {t for n in parsed_notes for t in n.tags}
    existing = {t.name: t for t in path.tags.all()}

    for name in wanted - set(existing):
        leaf = name.rsplit("/", 1)[-1]
        existing[name] = Tag.objects.create(
            path=path, name=name, leaf=leaf, slug=slugify(name),
        )
        report.tags_created += 1

    orphans = [tag for name, tag in existing.items() if name not in wanted]
    for tag in orphans:
        tag.delete()
        report.tags_removed += 1
        existing.pop(tag.name, None)

    return existing


def _sync_tasks(note, parsed):
    """Replace this note's StudyTask rows if they differ. Returns True when changed."""
    current = [(t.kind, t.text, t.text_hash, t.order)
               for t in note.tasks.order_by("order")]
    wanted = [(t.kind, t.text, t.text_hash, t.order) for t in parsed.tasks]
    if current == wanted:
        return False
    note.tasks.all().delete()
    StudyTask.objects.bulk_create([
        StudyTask(note=note, kind=t.kind, text=t.text, text_hash=t.text_hash, order=t.order)
        for t in parsed.tasks
    ])
    return True


def _sync_links(note, parsed, by_filename, moc_filename, report):
    """Replace this note's NoteLink rows if they differ. Returns True when changed."""
    current = sorted((l.raw_target, l.display_text, l.to_note_id)
                     for l in note.links_out.all())
    wanted = sorted((l.raw_target, l.display_text,
                     by_filename[l.raw_target].pk if l.raw_target in by_filename else None)
                    for l in parsed.links)
    if current == wanted:
        # Still report link health on an unchanged run, so a broken link cannot
        # hide simply because nothing else about the note moved.
        _report_link_health(note, parsed, by_filename, moc_filename, report)
        return False

    note.links_out.all().delete()
    NoteLink.objects.bulk_create([
        NoteLink(from_note=note, to_note=by_filename.get(l.raw_target),
                 raw_target=l.raw_target, display_text=l.display_text)
        for l in parsed.links
    ])
    _report_link_health(note, parsed, by_filename, moc_filename, report)
    return True


def _report_link_health(note, parsed, by_filename, moc_filename, report):
    for l in parsed.links:
        if l.raw_target in by_filename:
            continue
        if moc_filename and l.raw_target == moc_filename:
            report.moc_links += 1        # resolves to the generated overview, not broken
        else:
            report.broken_links.append((parsed.vault_filename, l.raw_target))


@transaction.atomic
def import_path(slug, source_dir, title="", dry_run=False):
    """Import `source_dir` into the LearningPath `slug`. Returns an ImportReport.

    On dry_run the work is genuinely performed and then rolled back, rather than
    guarded by `if dry_run:` at each write site — a scattered guard can silently
    miss a write path, and the report would then describe operations that never ran.

    The LearningPath row is created HERE rather than in the command, so that it too
    falls inside the rolled-back transaction. Creating it in the command meant a
    --dry-run left a real row behind, which quietly broke the "nothing was written"
    promise the flag exists to make.
    """
    report = ImportReport(dry_run=dry_run)

    path, report.path_created = LearningPath.objects.get_or_create(
        slug=slug,
        defaults={"title": title or slug.replace("-", " ").title(),
                  "vault_folder": str(source_dir)},
    )
    if not report.path_created and path.vault_folder != str(source_dir):
        path.vault_folder = str(source_dir)
        path.save(update_fields=["vault_folder"])
    parsed_notes, skipped, moc_filename, unparsed = _read_source(source_dir)
    report.skipped_files = skipped
    report.unparsed = unparsed

    if moc_filename and path.moc_filename != moc_filename:
        path.moc_filename = moc_filename
        path.save(update_fields=["moc_filename"])

    tags_by_name = _sync_tags(path, parsed_notes, report)
    seen_filenames = set()

    # ---- Pass 1: notes themselves -------------------------------------------
    for parsed in parsed_notes:
        seen_filenames.add(parsed.vault_filename)
        note = Note.objects.filter(path=path, vault_filename=parsed.vault_filename).first()

        if note is None:
            note = Note.objects.create(
                path=path, kind=parsed.kind, slug=parsed.slug, title=parsed.title,
                vault_filename=parsed.vault_filename, body_markdown=parsed.body_markdown,
                part=parsed.part, order=parsed.order,
                vault_modified=parsed.vault_modified,
            )
            report.created.append(parsed.vault_filename)
        else:
            # Slug is NEVER recomputed: it is in the public URL, so a title tweak
            # in Obsidian must not silently break a published link (scope §2.c).
            if parsed.slug != note.slug:
                report.slug_conflicts.append((note.vault_filename, note.slug, parsed.slug))

            changed_fields = []
            for attr in ("kind", "title", "body_markdown", "part", "order"):
                if getattr(note, attr) != getattr(parsed, attr):
                    setattr(note, attr, getattr(parsed, attr))
                    changed_fields.append(attr)
            if note.is_archived:
                note.is_archived = False
                changed_fields.append("is_archived")
                report.restored.append(parsed.vault_filename)
            if changed_fields:
                note.vault_modified = parsed.vault_modified
                note.save(update_fields=changed_fields + ["vault_modified"])
            note._changed = bool(changed_fields)

        parsed._note = note

    by_filename = {n.vault_filename: n for n in Note.objects.filter(path=path)}

    # ---- Pass 2: relations, now that every note exists -----------------------
    # A wikilink or a `module:` FK may target a note created moments ago, so this
    # cannot be folded into pass 1.
    for parsed in parsed_notes:
        note = parsed._note
        changed = getattr(note, "_changed", False)

        target = by_filename.get(parsed.module_target) if parsed.module_target else None
        if note.module_id != (target.pk if target else None):
            note.module = target
            note.save(update_fields=["module"])
            changed = True

        wanted_tags = {tags_by_name[t] for t in parsed.tags if t in tags_by_name}
        if set(note.tags.all()) != wanted_tags:
            note.tags.set(wanted_tags)
            changed = True

        if _sync_tasks(note, parsed):
            changed = True
        if _sync_links(note, parsed, by_filename, moc_filename, report):
            changed = True

        name = parsed.vault_filename
        if name in report.created:
            pass
        elif changed:
            report.updated.append(name)
        else:
            report.unchanged.append(name)

    # ---- Archive notes whose files have vanished -----------------------------
    for note in Note.objects.filter(path=path, is_archived=False):
        if note.vault_filename not in seen_filenames:
            note.is_archived = True
            note.save(update_fields=["is_archived"])
            report.archived.append(note.vault_filename)

    if dry_run:
        raise _Rollback(report)
    return report


def run_import(slug, source_dir, title="", dry_run=False):
    """Wrapper that turns the dry-run rollback back into a normal return value."""
    try:
        return import_path(slug, source_dir, title=title, dry_run=dry_run)
    except _Rollback as exc:
        return exc.args[0]
