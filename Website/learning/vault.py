"""Pure parsing of Obsidian vault notes. NO database access, NO Django models.

Kept separate from `importer.py` so every parsing rule is testable without a
database and without the vault itself — see the scope's Phase 2.a. The management
command is a thin wrapper over both; logic must not accumulate there.

Deliberately does NOT use PyYAML. It is not a project dependency and is not worth
adding for frontmatter this simple. The parser instead REPORTS lines it cannot
read (`ParsedNote.unparsed`) rather than guessing — Phase 0 ran this rule over all
58 files and reported zero unparsed lines.
"""
import hashlib
import re
from dataclasses import dataclass, field

from django.utils.text import slugify

# Note kinds we import. Everything else in the vault — `info`, `dashboard`, and
# the `moc` (which Phase 3 GENERATES from the database) — is skipped. Selection is
# by frontmatter `type`, not by folder, which is what excludes the Questions
# README and the two Templater stubs.
IMPORTABLE_TYPES = {"module", "concept", "source"}

# Sections that belong to the user, not to the reference content (scope §2.8).
# Stripped from the imported body; the web layer owns these.
PERSONAL_SECTIONS = {"My Notes", "Questions"}

# Sections whose content is EXTRACTED into structured rows and re-rendered by the
# template, so leaving them in the body would show everything twice — and show it
# badly, as raw Obsidian checkbox syntax (`- [ ] #study …`), since Python-Markdown
# has no tasklist extension enabled.
#
# Verified safe before enabling: across all 12 notes that have a `## Study Tasks`
# section, there are ZERO non-checkbox lines in it. Nothing but tasks lives there,
# so nothing else is lost.
EXTRACTED_SECTIONS = {"Study Tasks"}

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
FM_KEY = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
FM_LIST_ITEM = re.compile(r"^\s+-\s*(.*)$")
H2 = re.compile(r"^##\s+(.*?)\s*$", re.M)
WIKILINK = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
TASK_LINE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.*)$", re.M)
TAG = re.compile(r"#([A-Za-z][\w/-]*)")
MODULE_NUMBER = re.compile(r"^Module\s+(\d+)")


@dataclass
class ParsedTask:
    kind: str          # "study" | "question"
    text: str
    text_hash: str
    order: int


@dataclass
class ParsedLink:
    raw_target: str    # the note name, with |display, #anchor and ^block removed
    display_text: str  # "" when the link is not piped


@dataclass
class ParsedNote:
    vault_filename: str
    kind: str
    title: str
    slug: str
    body_markdown: str
    part: str = ""
    order: int = 0
    module_target: str = ""            # vault filename this concept belongs to
    vault_modified: object = None      # file mtime; stored for information only.
                                       # NEVER used for change detection — an
                                       # untouched save in Obsidian bumps mtime.
    tags: list = field(default_factory=list)
    tasks: list = field(default_factory=list)
    links: list = field(default_factory=list)
    unparsed: list = field(default_factory=list)


def parse_frontmatter(text):
    """Return (meta, body, unparsed_lines).

    `meta` values are strings, except list-valued keys which become lists.
    Both `tags: [a, b]` and a YAML block sequence are handled.
    """
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text, []

    meta, unparsed, current = {}, [], None
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        m = FM_KEY.match(line)
        if m:
            current = m.group(1)
            value = m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                meta[current] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            else:
                meta[current] = value
            continue
        m = FM_LIST_ITEM.match(line)
        if m and current:
            meta.setdefault(current, [])
            if not isinstance(meta[current], list):
                meta[current] = [meta[current]] if meta[current] else []
            meta[current].append(m.group(1).strip())
            continue
        unparsed.append(line)

    return meta, text[match.end():], unparsed


def split_sections(body):
    """Split a body into (preamble, [(heading, content), ...])."""
    matches = list(H2.finditer(body))
    if not matches:
        return body, []
    preamble = body[: matches[0].start()]
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((m.group(1), body[m.end():end]))
    return preamble, sections


def strip_personal_sections(body):
    """Remove the personal and extracted sections wherever they appear.

    Removes BY HEADING rather than "everything after the first personal heading".
    Source notes carry `## My Notes` last and have no `## Questions` at all, and a
    positional rule would be one reordering away from silently eating real content.
    """
    preamble, sections = split_sections(body)
    kept = [preamble.rstrip()]
    for heading, content in sections:
        if heading in PERSONAL_SECTIONS or heading in EXTRACTED_SECTIONS:
            continue
        kept.append(f"## {heading}\n{content.rstrip()}")
    return "\n\n".join(part for part in kept if part.strip()) + "\n"


H1 = re.compile(r"^\s*#\s+(.+?)\s*$", re.M)
# Separators and labels that may appear in a module's navigation line alongside
# wikilinks: "[[MOC]] · Prev: [[…]] · Next: [[…]]".
#
# Must allow ANY NUMBER of prev/next labels: an earlier version permitted only
# one, so it stripped Modules 01 and 11 (which have a single neighbour) and
# silently left the other nine — a partial fix that looked like a working one.
NAV_RESIDUE = re.compile(r"^(?:[\s·•|,\-–—:]|prev|next)*$", re.I)


def split_title(body):
    """Pull the leading `# Heading` out of a body. Returns (title, remaining_body).

    The H1 is the author's DISPLAY title; the filename is only a filesystem-safe
    approximation of it. Measured across the corpus: all 53 notes have one, and
    the 17 that differ from the filename differ by being better —
    `Möbius Band as a Bundle` for `Mobius…`, `Aharonov–Bohm` with a real en dash,
    `Module 05 — Fiber Bundles: First Definitions` for the hyphenated filename.

    Removing it also stops the page rendering its title twice, since the template
    already prints one.
    """
    match = H1.search(body)
    if not match or body[: match.start()].strip():
        return "", body            # no H1, or prose before it — leave the body alone
    return match.group(1).strip(), body[match.end():].lstrip("\n")


def strip_nav_line(body):
    """Drop a module's `[[MOC]] · Prev: [[…]] · Next: [[…]]` line.

    Our own breadcrumb and pager already provide this, built from `Note.order`,
    so leaving it in renders the navigation twice.

    Detection is CONSERVATIVE: a line qualifies only if removing every wikilink
    leaves nothing but separators and the words Prev/Next. A line with any real
    prose in it is kept, so this cannot silently eat content. Exactly the 11
    modules match; no concept or source has such a line.
    """
    lines = body.split("\n")
    for i, line in enumerate(lines[:4]):
        if "[[" not in line:
            continue
        if NAV_RESIDUE.fullmatch(WIKILINK.sub("", line)):
            del lines[i]
            return "\n".join(lines).lstrip("\n")
    return body


def normalise_task_text(text):
    """Canonical form used for the hash that carries user tick state across imports."""
    without_tags = TAG.sub("", text)
    return re.sub(r"\s+", " ", without_tags).strip()


def task_hash(text):
    return hashlib.sha256(normalise_task_text(text).encode("utf-8")).hexdigest()


def extract_tasks(body):
    """Pull `- [ ] #study …` / `#question …` lines out of a body.

    SKIPS items whose body is only a tag. Phase 0 found 50 of 97 checkboxes are
    bare `- [ ] #question` template placeholders; importing them would fill the
    dashboard with blank rows.
    """
    tasks, order = [], 0
    for mark, raw in TASK_LINE.findall(body):
        tags = TAG.findall(raw)
        if not normalise_task_text(raw):
            continue  # template stub — tag only, no content
        kind = "question" if "question" in tags else "study"
        order += 1
        # Store the NORMALISED text: the `#study` / `#question` tag is redundant
        # once `kind` records it, and showing it on the page is just noise. This
        # also makes text and text_hash exactly correspond, since the hash is
        # taken over the same normalised form.
        tasks.append(ParsedTask(kind=kind, text=normalise_task_text(raw),
                                text_hash=task_hash(raw), order=order))
    return tasks


def extract_links(text):
    """Extract `[[wikilinks]]`, dropping `|display`, `#anchor` and `^block` from the target.

    Phase 0 found zero anchors and zero block refs in this corpus, but stripping
    them is one line and stops a future one silently becoming an unresolvable target.
    """
    links = []
    for raw in WIKILINK.findall(text):
        target, _, display = raw.partition("|")
        target = target.split("#")[0].split("^")[0].strip()
        if target:
            links.append(ParsedLink(raw_target=target, display_text=display.strip()))
    return links


def derive_order(vault_filename):
    """Modules sort by their filename number; everything else falls back to title order."""
    m = MODULE_NUMBER.match(vault_filename)
    return int(m.group(1)) if m else 0


def unwrap_wikilink(value):
    """`"[[Module 08 - Connections and Curvature]]"` -> `Module 08 - Connections and Curvature`."""
    if not value:
        return ""
    value = value.strip().strip('"').strip("'")
    m = WIKILINK.fullmatch(value)
    if m:
        return m.group(1).split("|")[0].split("#")[0].strip()
    return value


def parse_note(vault_filename, text):
    """Parse one note's raw text into a ParsedNote, or return None if it is not importable."""
    meta, body, unparsed = parse_frontmatter(text)
    kind = (meta.get("type") or "").strip()
    if kind not in IMPORTABLE_TYPES:
        return None

    # Tasks and links come from the FULL body, before personal sections are
    # stripped, so a real task written under `## Questions` is still captured.
    tasks = extract_tasks(body)
    stripped = strip_personal_sections(body)
    heading, stripped = split_title(stripped)
    stripped = strip_nav_line(stripped)
    links = extract_links(stripped)

    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags] if tags else []

    return ParsedNote(
        vault_filename=vault_filename,
        kind=kind,
        title=heading or vault_filename,
        slug=slugify(vault_filename),
        body_markdown=stripped,
        part=(meta.get("part") or "").strip(),
        order=derive_order(vault_filename),
        module_target=unwrap_wikilink(meta.get("module") or ""),
        tags=[t.strip() for t in tags if t.strip()],
        tasks=tasks,
        links=links,
        unparsed=unparsed,
    )
