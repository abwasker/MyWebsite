"""Import an Obsidian learning-path folder into the database.

    manage.py import_learning_path --path fiber-bundles \
        --source "G:/My Drive/Obsidian Data/Obsidian Vault/Projects/Fiber Bundles" \
        [--title "Fiber Bundles"] [--dry-run]

DELIBERATELY THIN. All parsing lives in learning/vault.py and all database work in
learning/importer.py, so both are testable without the command layer and neither
can quietly acquire a second implementation.

⚠️ This runs LOCALLY, never on the server: the vault is a Google Drive path the
Linode cannot reach (scope §2.5). Content reaches production via the parent
project's §10.11 push, not by running this there.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from learning.importer import run_import



class Command(BaseCommand):
    help = "Import an Obsidian learning path folder into the learning app."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True,
                            help="LearningPath slug, e.g. fiber-bundles")
        parser.add_argument("--source", required=True,
                            help="Path to the vault folder to import")
        parser.add_argument("--title", default="",
                            help="Title used only when creating the LearningPath row")
        parser.add_argument("--dry-run", action="store_true",
                            help="Parse and report, then roll back without writing")

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.is_dir():
            raise CommandError(f"--source is not a directory: {source}")

        report = run_import(
            options["path"], source,
            title=options["title"], dry_run=options["dry_run"],
        )
        if report.path_created:
            verb = "Would create" if report.dry_run else "Created"
            self.stdout.write(self.style.SUCCESS(f"{verb} learning path '{options['path']}'"))
        self._render(report)

    def _render(self, r):
        w, style = self.stdout.write, self.style

        if r.dry_run:
            w(style.WARNING("DRY RUN — everything below was rolled back, nothing was written."))

        w("")
        w(f"  created   : {len(r.created)}")
        w(f"  updated   : {len(r.updated)}")
        w(f"  unchanged : {len(r.unchanged)}")
        w(f"  archived  : {len(r.archived)}")
        w(f"  restored  : {len(r.restored)}")
        w(f"  skipped   : {len(r.skipped_files)} (not an importable type)")
        w(f"  tags      : +{r.tags_created} / -{r.tags_removed}")

        for label, items in (("created", r.created), ("updated", r.updated),
                             ("archived", r.archived), ("restored", r.restored)):
            for name in items:
                w(f"    {label}: {name}")

        if r.moc_links:
            w(f"  {r.moc_links} link(s) target the MOC — these resolve to the path overview.")

        if r.slug_conflicts:
            w(style.WARNING(
                "  Slug drift — the stored slug was KEPT so published URLs do not break:"))
            for name, stored, computed in r.slug_conflicts:
                w(style.WARNING(f"    {name}: stored '{stored}' vs computed '{computed}'"))

        if r.unparsed:
            w(style.WARNING(f"  {len(r.unparsed)} unparsed frontmatter line(s):"))
            for line in r.unparsed[:20]:
                w(style.WARNING(f"    {line}"))

        if r.broken_links:
            w(style.ERROR(f"  {len(r.broken_links)} BROKEN link(s):"))
            for src, target in r.broken_links[:30]:
                w(style.ERROR(f"    {src} -> [[{target}]]"))
        else:
            w(style.SUCCESS("  No broken links."))

        w("")
        if r.dry_run:
            w(style.WARNING("Nothing was written."))
        elif r.changed:
            w(style.SUCCESS("Import complete."))
        else:
            w(style.SUCCESS("Import complete — nothing changed (idempotent re-run)."))
