"""Export the listening table outside the app (scope Phase 3c).

    python manage.py export_listening                          # CSV to stdout
    python manage.py export_listening --format markdown
    python manage.py export_listening --output listening.csv
    python manage.py export_listening --type episode --format markdown

The rendering itself lives in :mod:`listening.export`, shared with the download
buttons on the page (Phase 3d). This module is the shell that turns a run into a
file, terminal output and an exit code.

The command is not made redundant by those buttons: it is the automation path —
a cron job, a backup, piping into a script — which a browser download cannot be.

ENCODING IS NOT AN AFTERTHOUGHT ON WINDOWS
------------------------------------------
The real data contains ``ć`` (Bukvić), a Cyrillic ``С`` (Сomfort) and ``é``
(Trouvé). Python on Windows encodes both files and the console as cp1252 by
default, which raises ``UnicodeEncodeError`` on exactly those rows — so writes
here are explicitly UTF-8, and stdout is reconfigured before use. CSV *files*
additionally use ``utf-8-sig``: Excel assumes the local codepage for a plain
UTF-8 CSV and mojibakes the accents, and that byte-order mark is what tells it
otherwise.
"""

from django.core.management.base import BaseCommand, CommandError

from listening import export


class Command(BaseCommand):
    help = "Export the listening table as CSV or Markdown."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=tuple(export.RENDERERS),
            default="csv",
            help="Output format. CSV (default) is machine-readable; markdown is for reading.",
        )
        parser.add_argument(
            "--output",
            help="Write to this file instead of stdout. UTF-8; CSV gets a BOM so Excel "
                 "reads the accents correctly.",
        )
        parser.add_argument(
            "--type",
            choices=tuple(export.TYPE_FILTERS),
            default="all",
            help="Limit to songs or podcast episodes. Default: all.",
        )
        parser.add_argument(
            "--search",
            default="",
            help="Limit to items whose name or creator contains this text "
                 "(same matching as the page's search box).",
        )

    def handle(self, *args, **options):
        items = list(export.filtered_items(
            item_type=options["type"],
            query=options["search"],
        ))

        if not items:
            # Not an error: an empty library is a legitimate state, and a timer
            # or script wrapping this shouldn't treat it as a failure.
            self.stderr.write(self.style.WARNING("No listening items matched — nothing exported."))
            return

        output_format = options["format"]
        payload = export.render(items, output_format)

        destination = options["output"]
        if destination:
            # utf-8-sig for CSV (Excel), plain utf-8 for Markdown. newline=""
            # keeps csv from emitting \r\r\n on Windows.
            encoding = "utf-8-sig" if output_format == "csv" else "utf-8"
            try:
                with open(destination, "w", encoding=encoding, newline="") as handle:
                    handle.write(payload)
            except OSError as error:
                raise CommandError(f"Could not write {destination}: {error}") from error

            self.stdout.write(
                self.style.SUCCESS(f"Wrote {len(items)} item(s) to {destination}")
            )
        else:
            self.force_utf8_stdout()
            self.stdout.write(payload)

    def force_utf8_stdout(self):
        """Make the console accept non-ASCII before writing to it.

        A Windows console encodes stdout as cp1252, which cannot represent
        characters genuinely present in this data, so piping the export to the
        terminal died with ``UnicodeEncodeError`` while writing to a file worked.

        Handled here rather than by telling the operator to set ``PYTHONUTF8=1``,
        because a command that only works with the right environment variable is
        one that will fail the time nobody remembers. Guarded because
        ``self.stdout`` is a StringIO under test, which has no reconfigure().
        """
        stream = getattr(self.stdout, "_out", None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            return
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # Already-detached or otherwise unreconfigurable stream: let the
            # write fail loudly rather than silently mangling characters.
            pass
