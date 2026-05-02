"""local-issues — file-based, gh-shaped issue tracker.

Single-file, stdlib-only, Python 3.10+. Linux/macOS only.

Module structure (informal — there are no separate Python modules; functions
are grouped by section comments):

  Workspace  — discovery of `issues/` directory (CWD walk-up, worktree,
               sibling-shared).
  Task       — frontmatter parse/serialise, slug, snake<->camel.
  Repository — atomic on-disk task store (O_EXCL, rename, .next-id).
  Query      — filter composition, priority-then-ID sort key.
  Cli        — argparse + verb dispatch.
  Output     — text + JSON formatters.
  Editor     — $EDITOR invocation with TTY check.
"""

import argparse
import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ISSUES_DIRNAME = 'issues'
OPEN_DIRNAME = 'open'
CLOSED_DIRNAME = 'closed'
NEXT_ID_FILENAME = '.next-id'
README_FILENAME = 'README.md'

VALID_TYPES = ('task', 'prd')
VALID_STATES = ('open', 'closed')
VALID_STATE_REASONS = (None, 'completed', 'not_planned', 'reopened')

# Frontmatter field order — also defines the JSON schema field set.
TASK_FIELDS = (
    'number',
    'title',
    'type',
    'parent',
    'labels',
    'blockedBy',
    'priority',
    'state',
    'stateReason',
    'createdAt',
    'closedAt',
    'assignees',
)

DEFAULT_README = """# issues/

Local task store. Managed by the `issues` CLI.

Layout:

- `open/`   — open tasks, files named `NNN-slug.md`
- `closed/` — closed tasks, same naming

Each file has YAML frontmatter (camelCase fields) and a markdown body. Comments
are appended under a `## Comments` section.

Direct hand-edits are safe when no agent is running. Concurrent CLI invocations
are atomic on POSIX (O_EXCL on create, rename on state transition).
"""

EDITOR_TEMPLATE = """\
# Please enter the issue body. Lines starting with '#' will be kept as-is
# (this is markdown, '#' is a heading). Save and quit to submit. An empty
# body aborts the create.
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IssuesError(Exception):
    """User-facing error. Message is printed to stderr; exit code is 1."""


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
#
# Full discovery algorithm (slice 6):
#
#   1. Walk up from CWD looking for a `.git` (file or directory).
#   2. If `.git` is a directory: repo root = parent of the `.git/` dir.
#   3. If `.git` is a file: follow the `gitdir:` pointer (worktree
#      indirection).  The *repo root* for our purposes is the parent of the
#      `.git` file (i.e. the worktree's working directory) — NOT the common
#      dir's parent.
#   4. With repo root in hand, look for `issues/` at:
#        a. repo root first
#        b. parent of repo root (sibling-shared layout)
#   5. If no `.git` is found anywhere up the tree, fall back to plain
#      walk-up looking for `issues/` directly (slice 1 behaviour).
#   6. Auto-create-on-write rules still apply when nothing is found.


def _find_git_marker(start):
    """Walk up from `start` looking for a `.git` file or directory.

    Returns ``(path_to_.git, kind)`` where *kind* is ``'dir'`` or
    ``'file'``, or ``None`` if no ``.git`` is found before the filesystem
    root.  Pure (no writes, no git subprocess).
    """
    for candidate in (start, *start.parents):
        git = candidate / '.git'
        if git.is_dir():
            return git, 'dir'
        if git.is_file():
            return git, 'file'
    return None


def _resolve_worktree_gitdir(git_file):
    """Parse a worktree `.git` file and return the absolute gitdir Path.

    The file contains a single ``gitdir: <path>`` line.  The path may be
    relative (to the directory that contains the `.git` file) or absolute.
    Raises ``IssuesError`` if the file is malformed.
    """
    try:
        text = git_file.read_text(encoding='utf-8')
    except OSError as exc:
        raise IssuesError(f'cannot read {git_file}: {exc}') from exc
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('gitdir:'):
            raw = line[len('gitdir:'):].strip()
            if not raw:
                raise IssuesError(f'empty gitdir in {git_file}')
            path = Path(raw)
            if not path.is_absolute():
                path = (git_file.parent / path).resolve()
            return path
    raise IssuesError(f'no gitdir: line found in {git_file}')


def workspace_find(start=None):
    """Walk up from `start` (default CWD) looking for an `issues/` dir.

    Full algorithm (slice 6):
    - Find the nearest `.git` marker walking up from *start*.
    - For a ``.git`` directory: repo root = its parent.
    - For a ``.git`` file (worktree): repo root = parent of the `.git`
      file (the worktree's working-directory root).
    - Check for ``issues/`` at repo root, then at repo root's parent
      (sibling-shared layout).
    - If no ``.git`` is found: plain walk-up looking for ``issues/``
      directly (preserves slice 1 behaviour in non-git trees).

    Returns the absolute Path to the ``issues/`` directory, or ``None``.
    """
    cwd = Path(start).resolve() if start else Path.cwd().resolve()

    result = _find_git_marker(cwd)

    if result is not None:
        git_path, _ = result
        # Repo root: parent of .git dir, or parent of .git file (worktree).
        repo_root = git_path.parent

        # Two candidate locations: repo root, then its parent.
        for candidate_root in (repo_root, repo_root.parent):
            issues_dir = candidate_root / ISSUES_DIRNAME
            if issues_dir.is_dir():
                return issues_dir
        return None

    # No .git found anywhere — plain walk-up (no-git / bare filesystem).
    for candidate in (cwd, *cwd.parents):
        issues_dir = candidate / ISSUES_DIRNAME
        if issues_dir.is_dir():
            return issues_dir
    return None


def workspace_resolve(start=None, auto_create=False):
    """Find or auto-create the `issues/` dir.

    With `auto_create=False` (read commands), raises IssuesError if no
    `issues/` is found. With `auto_create=True` (write commands), bootstraps
    `issues/`, `issues/open/`, `issues/closed/` at CWD if none is found.
    """
    found = workspace_find(start)
    if found:
        return found
    if not auto_create:
        raise IssuesError(
            "no `issues/` directory found (walked up from CWD).\n"
            "Run `issues init` to create one here."
        )
    base = Path(start).resolve() if start else Path.cwd().resolve()
    issues_dir = base / ISSUES_DIRNAME
    workspace_init(base)
    return issues_dir


def workspace_init(base, with_gitignore=False):
    """Create `issues/`, `open/`, `closed/`, README at `base`.

    Idempotent: existing dirs/files are left alone (README is not overwritten).
    """
    issues_dir = base / ISSUES_DIRNAME
    open_dir = issues_dir / OPEN_DIRNAME
    closed_dir = issues_dir / CLOSED_DIRNAME
    open_dir.mkdir(parents=True, exist_ok=True)
    closed_dir.mkdir(parents=True, exist_ok=True)
    readme = issues_dir / README_FILENAME
    if not readme.exists():
        readme.write_text(DEFAULT_README, encoding='utf-8')
    if with_gitignore:
        _append_gitignore(base)
    return issues_dir


def _append_gitignore(base):
    """Append `issues/` to `<base>/.gitignore` if not already listed.

    Best effort — quietly does nothing if the entry already exists.
    """
    gitignore = base / '.gitignore'
    entry = f'{ISSUES_DIRNAME}/'
    existing = ''
    if gitignore.exists():
        existing = gitignore.read_text(encoding='utf-8')
        lines = [line.strip() for line in existing.splitlines()]
        if entry in lines or ISSUES_DIRNAME in lines:
            return
    sep = '' if (not existing) or existing.endswith('\n') else '\n'
    with open(gitignore, 'a', encoding='utf-8') as fh:
        fh.write(f'{sep}{entry}\n')


# ---------------------------------------------------------------------------
# Task — slug
# ---------------------------------------------------------------------------

SLUG_MAX_LEN = 50
DEFAULT_SLUG = 'untitled'


def task_slug(title):
    """Derive a slug: ASCII-fold, lowercase, [^a-z0-9]+ -> '-', truncate.

    Empty / all-non-ASCII inputs yield 'untitled'.
    """
    if not title:
        return DEFAULT_SLUG
    # NFKD then drop combining marks to ASCII-fold (handles é -> e, etc.).
    normalised = unicodedata.normalize('NFKD', title)
    ascii_only = normalised.encode('ascii', 'ignore').decode('ascii')
    lowered = ascii_only.lower()
    # Collapse runs of non-[a-z0-9] into single hyphens.
    slug = re.sub(r'[^a-z0-9]+', '-', lowered).strip('-')
    if not slug:
        return DEFAULT_SLUG
    if len(slug) <= SLUG_MAX_LEN:
        return slug
    # Truncate, then strip a trailing hyphen left by mid-word cut.
    return slug[:SLUG_MAX_LEN].rstrip('-') or DEFAULT_SLUG


# ---------------------------------------------------------------------------
# Task — snake<->camel
# ---------------------------------------------------------------------------


def _camel_to_snake(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


def _snake_to_camel(name):
    parts = name.split('_')
    return parts[0] + ''.join(p.title() for p in parts[1:])


def task_to_camel(snake_dict):
    """Translate snake_case keys to camelCase. Shallow."""
    return {_snake_to_camel(k): v for k, v in snake_dict.items()}


def task_to_snake(camel_dict):
    """Translate camelCase keys to snake_case. Shallow."""
    return {_camel_to_snake(k): v for k, v in camel_dict.items()}


# ---------------------------------------------------------------------------
# Task — frontmatter parse/serialise
# ---------------------------------------------------------------------------
#
# We use a minimal hand-rolled YAML subset: scalars (strings, ints, null,
# booleans), lists of scalars in flow form (`[a, b]`) and block form
# (one-per-line `- x`). Strings are quoted only when they need to be (contain
# special chars, leading/trailing whitespace, or look like a non-string).
# The parse/serialise pair is round-trip stable on its own subset.


_FRONTMATTER_FENCE = '---'
_QUOTE_NEEDED_RE = re.compile(
    r"""(^\s)|(\s$)|[:#{}\[\],&*!|>'"%@`]|(^-\s)|^$|^(true|false|null|yes|no|~)$""",
    re.IGNORECASE,
)
_INT_RE = re.compile(r'^-?\d+$')


def _yaml_dump_scalar(value):
    """Serialise a scalar (str / int / None / bool) to YAML."""
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, int):
        return str(value)
    s = str(value)
    if s == '':
        return "''"
    if _INT_RE.match(s) or _QUOTE_NEEDED_RE.search(s):
        # Use single quotes; escape embedded single quotes by doubling.
        escaped = s.replace("'", "''")
        return f"'{escaped}'"
    return s


def _yaml_dump_list(values):
    """Serialise a list of scalars in flow form."""
    if not values:
        return '[]'
    return '[' + ', '.join(_yaml_dump_scalar(v) for v in values) + ']'


def _yaml_load_scalar(token):
    """Parse a single YAML scalar token (already stripped)."""
    if token == '' or token == '~' or token.lower() == 'null':
        return None
    if token.lower() == 'true':
        return True
    if token.lower() == 'false':
        return False
    if _INT_RE.match(token):
        return int(token)
    # Quoted string?
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        inner = token[1:-1]
        if token[0] == "'":
            return inner.replace("''", "'")
        # Double-quoted: minimal escape handling (\\ \" \n).
        return (
            inner.replace('\\\\', '\\').replace('\\"', '"').replace('\\n', '\n')
        )
    return token


def _yaml_load_list(token):
    """Parse a flow-form list token like `[a, b, 'c d']`."""
    inner = token.strip()
    if not (inner.startswith('[') and inner.endswith(']')):
        raise IssuesError(f'expected flow-form list, got: {token!r}')
    inner = inner[1:-1].strip()
    if not inner:
        return []
    # Split on commas not inside quotes.
    items = []
    buf = ''
    quote = None
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf += ch
            continue
        if ch == ',':
            items.append(buf.strip())
            buf = ''
            continue
        buf += ch
    if buf.strip():
        items.append(buf.strip())
    return [_yaml_load_scalar(it) for it in items]


def task_parse(text):
    """Parse `text` (entire file contents) into a dict.

    Returns a dict with snake_case keys: number, title, type, parent, labels,
    blocked_by, priority, state, state_reason, created_at, closed_at,
    assignees, body, comments_raw.

    `comments_raw` is the substring of body following a `## Comments` heading,
    or '' if none. (Slice 1 does not parse individual comments.)
    """
    if not text.startswith(_FRONTMATTER_FENCE + '\n') and text != _FRONTMATTER_FENCE:
        raise IssuesError('file is missing YAML frontmatter')
    # Find the closing fence.
    rest = text[len(_FRONTMATTER_FENCE) + 1:]
    end = rest.find('\n' + _FRONTMATTER_FENCE + '\n')
    if end == -1:
        # Allow trailing fence at end-of-file with no following newline.
        if rest.endswith('\n' + _FRONTMATTER_FENCE):
            fm_text = rest[:-(len(_FRONTMATTER_FENCE) + 1)]
            body = ''
        else:
            raise IssuesError('unterminated YAML frontmatter')
    else:
        fm_text = rest[:end]
        body = rest[end + len('\n' + _FRONTMATTER_FENCE + '\n'):]

    fm_camel = _yaml_parse_frontmatter(fm_text)
    fm_snake = task_to_snake(fm_camel)

    # Split body / comments.
    body_main, comments_raw = _split_comments(body)
    fm_snake['body'] = body_main
    fm_snake['comments_raw'] = comments_raw
    return fm_snake


def _yaml_parse_frontmatter(fm_text):
    """Parse the YAML subset used in frontmatter. Returns dict (camelCase)."""
    out = {}
    lines = fm_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith('#'):
            i += 1
            continue
        # `key: value` or `key:` followed by a block list.
        if ':' not in line:
            raise IssuesError(f'malformed YAML line: {line!r}')
        key, _, rest = line.partition(':')
        key = key.strip()
        rest = rest.rstrip()
        if rest == '' or rest == ' ':
            # Either empty value or block list follows.
            block = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                stripped = nxt.lstrip()
                if not stripped:
                    j += 1
                    continue
                if stripped.startswith('- '):
                    block.append(_yaml_load_scalar(stripped[2:].strip()))
                    j += 1
                    continue
                if stripped.startswith('-') and len(stripped) == 1:
                    block.append(None)
                    j += 1
                    continue
                break
            if block:
                out[key] = block
                i = j
                continue
            out[key] = None
            i += 1
            continue
        value_token = rest.lstrip()
        if value_token.startswith('['):
            out[key] = _yaml_load_list(value_token)
        else:
            out[key] = _yaml_load_scalar(value_token)
        i += 1
    return out


_COMMENTS_HEADING_RE = re.compile(r'^##\s+Comments\s*$', re.MULTILINE)
_COMMENT_HEADER_RE = re.compile(
    r'^###\s+(\S+)\s+—\s+(.+?)\s*$', re.MULTILINE
)


def _split_comments(body):
    """Split body at the first `## Comments` heading.

    Returns (body_before, comments_raw_including_heading) — or (body, '') if
    no comments section.  `body_before` is normalised to end in exactly one
    `\\n` (or be empty) so that adding a comments section and re-parsing does
    not accumulate extra blank lines between the body and the heading.
    """
    m = _COMMENTS_HEADING_RE.search(body)
    if not m:
        return body, ''
    body_before = body[: m.start()].rstrip('\n')
    if body_before:
        body_before += '\n'
    return body_before, body[m.start():]


def task_parse_comments(comments_raw):
    """Parse the comments_raw string into a list of comment dicts.

    Each comment dict has keys: timestamp (str), author (str), body (str).
    Malformed `###` headers (those not matching `### TIMESTAMP — AUTHOR`)
    are silently skipped. An empty or missing comments_raw returns [].
    """
    if not comments_raw:
        return []
    comments = []
    headers = list(_COMMENT_HEADER_RE.finditer(comments_raw))
    for i, m in enumerate(headers):
        timestamp = m.group(1)
        author = m.group(2)
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(comments_raw)
        body = comments_raw[start:end].strip()
        comments.append({'timestamp': timestamp, 'author': author, 'body': body})
    return comments


def task_comments_to_raw(comments):
    """Serialise a list of comment dicts to a comments_raw string.

    Returns '' if `comments` is empty; otherwise returns a string starting
    with `## Comments` and containing one `### timestamp — author` h3 per
    comment. The round-trip `task_parse_comments(task_comments_to_raw(c))`
    is identity for well-formed inputs.
    """
    if not comments:
        return ''
    lines = ['## Comments']
    for c in comments:
        lines.append('')
        lines.append(f"### {c['timestamp']} — {c['author']}")
        lines.append('')
        body = c['body'].strip() if c.get('body') else ''
        if body:
            lines.append(body)
    return '\n'.join(lines) + '\n'


def task_serialise(task):
    """Serialise a task dict (snake_case) to file text.

    Required keys (any may be None except number/title/type/state/created_at):
    number, title, type, parent, labels, blocked_by, priority, state,
    state_reason, created_at, closed_at, assignees, body. `comments_raw` is
    appended after body.
    """
    camel = task_to_camel({k: task[k] for k in (
        'number', 'title', 'type', 'parent', 'labels', 'blocked_by',
        'priority', 'state', 'state_reason', 'created_at', 'closed_at',
        'assignees',
    )})
    lines = [_FRONTMATTER_FENCE]
    for field in TASK_FIELDS:
        value = camel.get(field)
        if isinstance(value, list):
            lines.append(f'{field}: {_yaml_dump_list(value)}')
        else:
            lines.append(f'{field}: {_yaml_dump_scalar(value)}')
    lines.append(_FRONTMATTER_FENCE)
    body = task.get('body', '') or ''
    comments_raw = task.get('comments_raw', '') or ''
    text = '\n'.join(lines) + '\n'
    if body:
        if not body.endswith('\n'):
            body = body + '\n'
        text += body
    if comments_raw:
        # Ensure exactly one blank line between body and comments heading.
        if not text.endswith('\n\n'):
            if text.endswith('\n'):
                text += '\n'
            else:
                text += '\n\n'
        text += comments_raw
        if not text.endswith('\n'):
            text += '\n'
    return text


def task_new(number, title, body='', task_type='task', parent=None,
             labels=None, blocked_by=None, priority=None, assignees=None,
             created_at=None):
    """Build a new task dict with defaults.

    `created_at` defaults to UTC now in ISO-8601 with 'Z' suffix (gh-style).
    Body is normalised to end with exactly one `\\n` (or be empty), so that
    parse(serialise(t)) is identity.
    """
    if created_at is None:
        created_at = _now_iso()
    return {
        'number': number,
        'title': title,
        'type': task_type,
        'parent': parent,
        'labels': list(labels or []),
        'blocked_by': list(blocked_by or []),
        'priority': priority,
        'state': 'open',
        'state_reason': None,
        'created_at': created_at,
        'closed_at': None,
        'assignees': list(assignees or []),
        'body': _normalise_body(body),
        'comments_raw': '',
    }


def _normalise_body(body):
    """Ensure body ends in exactly one \\n (or is empty)."""
    if not body:
        return ''
    return body if body.endswith('\n') else body + '\n'


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _get_git_author():
    """Derive comment author: git config user.name → $USER → 'unknown'."""
    try:
        result = subprocess.run(
            ['git', 'config', 'user.name'],
            capture_output=True, text=True, timeout=5,
        )
        name = result.stdout.strip()
        if name:
            return name
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.environ.get('USER') or 'unknown'


# ---------------------------------------------------------------------------
# Task — JSON view
# ---------------------------------------------------------------------------


def task_to_json_dict(task):
    """Build the gh-shaped JSON representation of a task.

    - camelCase keys
    - state uppercase
    - labels/assignees as plain string arrays
    - includes extensions: priority, parent, type, blockedBy, comments
    - body included
    - comments parsed to a list (slice 1: returns []; comment parsing arrives
      with the `comment` verb in slice 4)
    """
    out = task_to_camel({
        'number': task['number'],
        'title': task['title'],
        'state': (task['state'] or '').upper(),
        'state_reason': task['state_reason'],
        'created_at': task['created_at'],
        'closed_at': task['closed_at'],
        'labels': list(task.get('labels') or []),
        'assignees': list(task.get('assignees') or []),
        'body': task.get('body', '') or '',
        'priority': task.get('priority'),
        'parent': task.get('parent'),
        'type': task.get('type'),
        'blocked_by': list(task.get('blocked_by') or []),
    })
    out['comments'] = task_parse_comments(task.get('comments_raw', ''))
    return out


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
#
# Layout: <issues_dir>/open/NNN-slug.md, <issues_dir>/closed/NNN-slug.md,
# <issues_dir>/.next-id.
#
# All writes go through atomic_write (temp file in same dir + rename). New
# task creation uses O_EXCL with collision retry — if the target filename
# already exists (or .next-id is stale), we bump the ID and retry.

_FILENAME_RE = re.compile(r'^(\d+)-([a-z0-9-]+)\.md$')
_NUMBER_PREFIX_RE = re.compile(r'^(\d+)-')


def repo_open_dir(issues_dir):
    return issues_dir / OPEN_DIRNAME


def repo_closed_dir(issues_dir):
    return issues_dir / CLOSED_DIRNAME


def repo_next_id_path(issues_dir):
    return issues_dir / NEXT_ID_FILENAME


def repo_existing_numbers(issues_dir):
    """Return the set of in-use task numbers across open/ and closed/."""
    numbers = set()
    for sub in (OPEN_DIRNAME, CLOSED_DIRNAME):
        d = issues_dir / sub
        if not d.is_dir():
            continue
        for entry in os.listdir(d):
            m = _NUMBER_PREFIX_RE.match(entry)
            if m:
                numbers.add(int(m.group(1)))
    return numbers


def repo_read_next_id(issues_dir):
    p = repo_next_id_path(issues_dir)
    if not p.is_file():
        return 0
    try:
        return int(p.read_text(encoding='utf-8').strip())
    except (ValueError, OSError):
        return 0


def repo_write_next_id(issues_dir, value):
    """Atomically write `.next-id`."""
    _atomic_write_text(repo_next_id_path(issues_dir), f'{value}\n')


def repo_initial_next_id(issues_dir):
    """Compute the starting candidate for a new task ID.

    max(existing numbers, .next-id - 1) + 1, where `.next-id` is the *next*
    ID we should hand out (so subtract 1 when comparing to existing maxes).
    """
    existing = repo_existing_numbers(issues_dir)
    max_existing = max(existing) if existing else 0
    next_id_file = repo_read_next_id(issues_dir)
    return max(max_existing + 1, next_id_file)


def repo_format_filename(number, slug):
    return f'{number:03d}-{slug}.md'


def repo_find_path(issues_dir, number):
    """Find a task by number across open/ and closed/. Returns Path or None."""
    for sub in (OPEN_DIRNAME, CLOSED_DIRNAME):
        d = issues_dir / sub
        if not d.is_dir():
            continue
        for entry in os.listdir(d):
            m = _NUMBER_PREFIX_RE.match(entry)
            if m and int(m.group(1)) == number:
                return d / entry
    return None


def repo_read(issues_dir, number):
    """Read a task by number. Raises IssuesError if not found."""
    path = repo_find_path(issues_dir, number)
    if path is None:
        raise IssuesError(f'task #{number} not found')
    text = path.read_text(encoding='utf-8')
    task = task_parse(text)
    # Trust on-disk number — should match filename.
    return task, path


def repo_create(issues_dir, title, body='', task_type='task', parent=None,
                labels=None, blocked_by=None, priority=None, assignees=None,
                max_attempts=1000):
    """Create a new task atomically.

    Strategy:

    1. Compute candidate ID (max of existing numbers and `.next-id`).
    2. Atomically claim the number by `O_EXCL`-creating a number-only
       reservation file `NNN.md` (no slug — the filename is a function of
       the number alone, so two threads racing for the same N collide on
       the same path). On `EEXIST`, bump candidate and retry.
    3. Once claimed, write the task body to a temp file and `rename(2)` it
       over the reservation, then `rename(2)` to the final `NNN-slug.md`.
       (Both renames are atomic and intra-directory.)
    4. Update `.next-id` to candidate+1.

    This guarantees ID uniqueness under concurrent create even with varying
    slugs, without any locking primitives.
    """
    open_dir = repo_open_dir(issues_dir)
    open_dir.mkdir(parents=True, exist_ok=True)
    repo_closed_dir(issues_dir).mkdir(parents=True, exist_ok=True)

    slug = task_slug(title)
    candidate = repo_initial_next_id(issues_dir)
    attempts = 0
    while attempts < max_attempts:
        # Skip numbers visibly in use without bothering with O_EXCL.
        if repo_find_path(issues_dir, candidate) is not None:
            candidate += 1
            attempts += 1
            continue
        # Number-only reservation path. Same path for any racing thread.
        reservation = open_dir / f'{candidate:03d}.md'
        try:
            # Empty O_EXCL file — claims the number atomically.
            _excl_write_text(reservation, '')
        except FileExistsError:
            candidate += 1
            attempts += 1
            continue
        # Defensive recheck for the rename-window race: between another
        # thread's `os.rename(NNN.md, NNN-slug.md)` and our O_EXCL, NNN.md
        # is briefly absent — so we may have just claimed a number that's
        # already in use under its slug name. Drop the reservation and
        # bump if so. (Cannot rely on .next-id alone — a sibling process
        # may not have updated it yet.)
        existing = repo_find_path(issues_dir, candidate)
        if existing is not None and existing != reservation:
            try:
                os.unlink(reservation)
            except OSError:
                pass
            candidate += 1
            attempts += 1
            continue
        # Number is now ours. Build task, write body, rename into place.
        task = task_new(
            number=candidate,
            title=title,
            body=body,
            task_type=task_type,
            parent=parent,
            labels=labels,
            blocked_by=blocked_by,
            priority=priority,
            assignees=assignees,
        )
        text = task_serialise(task)
        final_path = open_dir / repo_format_filename(candidate, slug)
        try:
            # Write text to reservation (it exists, so plain write is fine),
            # then rename(2) reservation -> NNN-slug.md atomically.
            _atomic_write_text(reservation, text)
            os.rename(reservation, final_path)
        except Exception:
            # Best-effort cleanup; leave a `.next-id` bump so the dead ID
            # is never reused (PRD: IDs never reused).
            for stray in (reservation, final_path):
                try:
                    os.unlink(stray)
                except OSError:
                    pass
            repo_write_next_id(issues_dir, candidate + 1)
            raise
        repo_write_next_id(issues_dir, candidate + 1)
        return task, final_path
    raise IssuesError(
        f'failed to create task after {max_attempts} attempts (ID collision)'
    )


def repo_list(issues_dir):
    """Return a list of (task, path) for every task across open/ and closed/.

    Files that do not match the NNN-slug.md pattern are silently skipped
    (guards against temp files, .next-id, README, etc.).  Parse errors are
    also skipped (best-effort: a corrupted single file should not break list).
    Tasks are returned in arbitrary order — callers are expected to sort.
    """
    results = []
    for sub in (OPEN_DIRNAME, CLOSED_DIRNAME):
        d = issues_dir / sub
        if not d.is_dir():
            continue
        for entry in sorted(os.listdir(d)):
            if not _FILENAME_RE.match(entry):
                continue
            path = d / entry
            try:
                text = path.read_text(encoding='utf-8')
                task = task_parse(text)
            except (IssuesError, OSError):
                continue
            results.append((task, path))
    return results


def repo_close(issues_dir, number, *, reason='completed',
               comment_body=None, comment_author=None):
    """Close a task: update frontmatter + move open/→closed/ via rename(2).

    Writes the updated content to a temp file in `closed/`, renames it into
    place (atomic), then unlinks the original from `open/`.  Between the two
    syscalls the file briefly exists in both dirs — acceptable under last-
    write-wins semantics.

    Optional `comment_body` appends a comment in the same operation using the
    same timestamp as `closedAt`.  Raises IssuesError if already closed.
    """
    task, open_path = repo_read(issues_dir, number)
    if task['state'] == 'closed':
        raise IssuesError(f'task #{number} is already closed')
    now = _now_iso()
    task['state'] = 'closed'
    task['state_reason'] = reason
    task['closed_at'] = now
    if comment_body:
        comments = task_parse_comments(task.get('comments_raw', ''))
        comments.append({
            'timestamp': now,
            'author': comment_author or 'unknown',
            'body': comment_body,
        })
        task['comments_raw'] = task_comments_to_raw(comments)
    text = task_serialise(task)
    closed_dir = repo_closed_dir(issues_dir)
    closed_dir.mkdir(parents=True, exist_ok=True)
    final_path = closed_dir / open_path.name
    fd, tmp_name = tempfile.mkstemp(
        prefix=f'.{open_path.name}.', suffix='.tmp', dir=str(closed_dir),
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.rename(tmp_name, final_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:
        os.unlink(open_path)
    except OSError:
        pass
    return task, final_path


def repo_reopen(issues_dir, number):
    """Reopen a closed task: update frontmatter + move closed/→open/ via rename(2).

    Writes the updated content to a temp file in `open/`, renames it into
    place (atomic), then unlinks the original from `closed/`.  Sets
    `state: open`, `stateReason: reopened`, `closedAt: null`.  Raises
    IssuesError if already open.
    """
    task, closed_path = repo_read(issues_dir, number)
    if task['state'] == 'open':
        raise IssuesError(f'task #{number} is already open')
    task['state'] = 'open'
    task['state_reason'] = 'reopened'
    task['closed_at'] = None
    text = task_serialise(task)
    open_dir = repo_open_dir(issues_dir)
    open_dir.mkdir(parents=True, exist_ok=True)
    final_path = open_dir / closed_path.name
    fd, tmp_name = tempfile.mkstemp(
        prefix=f'.{closed_path.name}.', suffix='.tmp', dir=str(open_dir),
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.rename(tmp_name, final_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:
        os.unlink(closed_path)
    except OSError:
        pass
    return task, final_path


def repo_append_comment(issues_dir, number, *, body, author):
    """Append a comment to a task's body, writing atomically in-place.

    The task may be in open/ or closed/; the file stays in its current
    location.  Returns (updated_task, path).
    """
    task, path = repo_read(issues_dir, number)
    now = _now_iso()
    comments = task_parse_comments(task.get('comments_raw', ''))
    comments.append({'timestamp': now, 'author': author, 'body': body})
    task['comments_raw'] = task_comments_to_raw(comments)
    text = task_serialise(task)
    _atomic_write_text(path, text)
    return task, path


# Sentinel: distinguishes "caller didn't pass --priority" from "caller passed
# --priority none" (which sets priority to None/null).
_UNSET = object()


def repo_edit(issues_dir, number, *, title=None, body=None,
              add_labels=(), remove_labels=(), add_blocked_by=(),
              remove_blocked_by=(), priority=_UNSET):
    """Edit a task in-place atomically (write-to-temp + rename(2)).

    Only fields that are explicitly supplied are changed; everything else —
    including the comments section — is preserved exactly.

    `priority=_UNSET` (default) leaves priority unchanged.
    `priority=None` sets it to null.
    `priority=<int>` sets it to that integer.

    Returns (updated_task, path).
    """
    task, path = repo_read(issues_dir, number)

    if title is not None:
        task['title'] = title

    if body is not None:
        task['body'] = _normalise_body(body)
        # comments_raw is preserved as-is; we only replace body.

    if add_labels or remove_labels:
        current = list(task.get('labels') or [])
        for lbl in add_labels:
            if lbl not in current:
                current.append(lbl)
        remove_set = set(remove_labels)
        current = [lbl for lbl in current if lbl not in remove_set]
        task['labels'] = current

    if add_blocked_by or remove_blocked_by:
        current = list(task.get('blocked_by') or [])
        for n in add_blocked_by:
            if n not in current:
                current.append(n)
        remove_set = set(remove_blocked_by)
        current = [n for n in current if n not in remove_set]
        task['blocked_by'] = current

    if priority is not _UNSET:
        task['priority'] = priority

    text = task_serialise(task)
    _atomic_write_text(path, text)
    return task, path


def repo_delete(issues_dir, number):
    """Permanently delete a task file via unlink(2).

    `.next-id` is NOT decremented; the deleted ID is never reused.
    Raises IssuesError if the task does not exist.
    """
    path = repo_find_path(issues_dir, number)
    if path is None:
        raise IssuesError(f'task #{number} not found')
    try:
        os.unlink(path)
    except OSError as exc:
        raise IssuesError(f'cannot delete task #{number}: {exc}') from exc


def repo_cascade_delete(issues_dir, number):
    """Delete a task and every task whose `parent` field equals `number`.

    The parent task is deleted first; children are then unlinked in arbitrary
    order (each unlink is independent and atomic).  `.next-id` is NOT touched
    — deleted IDs are never reused.

    Raises IssuesError if the parent task does not exist.
    Returns a list of task numbers that were deleted (parent first, then
    children in discovery order).
    """
    # Verify the parent task exists before touching anything.
    parent_path = repo_find_path(issues_dir, number)
    if parent_path is None:
        raise IssuesError(f'task #{number} not found')

    # Collect children: tasks whose `parent` field == number.
    child_numbers = []
    for task, _path in repo_list(issues_dir):
        if task.get('parent') == number and task.get('number') != number:
            child_numbers.append(task['number'])

    # Delete parent first.
    try:
        os.unlink(parent_path)
    except OSError as exc:
        raise IssuesError(f'cannot delete task #{number}: {exc}') from exc

    deleted = [number]

    # Delete each child; skip gracefully if already gone (race-safe).
    for child_num in child_numbers:
        child_path = repo_find_path(issues_dir, child_num)
        if child_path is None:
            continue
        try:
            os.unlink(child_path)
            deleted.append(child_num)
        except OSError:
            pass  # best-effort; a concurrent delete is fine

    return deleted


# ---------------------------------------------------------------------------
# Atomic FS primitives
# ---------------------------------------------------------------------------


def _excl_write_text(path, text):
    """Write `text` to `path` using O_EXCL — fails if path exists.

    Raises FileExistsError on collision (caller retries). Other OSError is
    re-raised.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FileExistsError(str(path)) from exc
        raise
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
    except Exception:
        # Best-effort cleanup of partial file (write atop O_EXCL fd should
        # not normally fail; fdopen took ownership of fd).
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _atomic_write_text(path, text):
    """Atomic write: write to a temp file in the same dir, then rename(2).

    rename(2) is atomic on POSIX within a single filesystem; placing the temp
    file in the same dir guarantees that.
    """
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=str(parent),
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.rename(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
#
# Pure functions: no I/O.  Takes lists of task dicts (snake_case) and returns
# filtered/sorted subsets.  Designed for easy unit testing without touching
# the filesystem.


def query_is_ready(task, all_tasks_by_number):
    """Return True if `task` is open, not a PRD, and not blocked by any open task.

    A blocker that does not exist in `all_tasks_by_number` is treated as
    satisfied (closed-or-missing semantics — dangling references do not block).
    Pure: no I/O.
    """
    if task.get('state') != 'open':
        return False
    if task.get('type') == 'prd':
        return False
    for blocker_id in (task.get('blocked_by') or []):
        blocker = all_tasks_by_number.get(blocker_id)
        if blocker is not None and blocker.get('state') == 'open':
            return False
    return True


def query_detect_cycle(tasks):
    """DFS cycle detection on the blockedBy graph.

    Nodes are task numbers; edges follow `blocked_by` lists (task A -> B means
    A is blocked by B).  Only tasks present in `tasks` are traversed; dangling
    references (blockers that don't exist in the list) are silently skipped.

    Returns a list of task IDs representing the cycle path (e.g. [3, 5, 7, 3])
    with the start ID repeated at the end, or ``None`` if no cycle exists.
    """
    by_number = {t['number']: t for t in tasks}

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in by_number}
    # Current DFS path — used to reconstruct the cycle.
    path = []
    path_set = set()

    def _dfs(node):
        color[node] = GRAY
        path.append(node)
        path_set.add(node)
        for blocker_id in (by_number[node].get('blocked_by') or []):
            if blocker_id not in by_number:
                continue  # dangling reference — skip
            if blocker_id in path_set:
                # Closing edge found: blocker_id is already in current path.
                cycle_start = path.index(blocker_id)
                return path[cycle_start:] + [blocker_id]
            if color[blocker_id] == WHITE:
                result = _dfs(blocker_id)
                if result is not None:
                    return result
        color[node] = BLACK
        path.pop()
        path_set.remove(node)
        return None

    for node in list(by_number.keys()):
        if color[node] == WHITE:
            result = _dfs(node)
            if result is not None:
                return result
    return None


def query_would_introduce_cycle(tasks, source_id, new_blocker_id):
    """Return True if adding `source_id -> new_blocker_id` would create a cycle.

    The check is: can `new_blocker_id` already reach `source_id` via the
    existing `blocked_by` graph?  If so, adding the new edge would close a
    cycle.  Self-loops (source_id == new_blocker_id) also return True.

    Pure: no I/O.
    """
    if source_id == new_blocker_id:
        return True
    by_number = {t['number']: t for t in tasks}
    # BFS/DFS from new_blocker_id; if we reach source_id, a cycle would form.
    visited = set()
    stack = [new_blocker_id]
    while stack:
        node = stack.pop()
        if node == source_id:
            return True
        if node in visited:
            continue
        visited.add(node)
        if node in by_number:
            for blocker_id in (by_number[node].get('blocked_by') or []):
                if blocker_id not in visited:
                    stack.append(blocker_id)
    return False


def query_next(tasks, labels=()):
    """Return the next ready task or None.

    "Ready" = open + not PRD + all blockers closed-or-missing.  Applies the
    `labels` AND-filter on top.  Cycles in the graph raise IssuesError before
    any readiness check is performed.  Sorted priority-then-ID (same key as
    `query_sort_key`).
    """
    cycle = query_detect_cycle(tasks)
    if cycle is not None:
        path_str = ' -> '.join(str(n) for n in cycle)
        raise IssuesError(f'cycle detected in blockedBy: {path_str}')

    by_number = {t['number']: t for t in tasks}
    label_set = set(labels)
    candidates = []
    for task in tasks:
        if not query_is_ready(task, by_number):
            continue
        if label_set:
            task_labels = set(task.get('labels') or [])
            if not label_set.issubset(task_labels):
                continue
        candidates.append(task)
    if not candidates:
        return None
    return min(candidates, key=query_sort_key)


def query_ready_set(tasks, labels=()):
    """Return the full ready set sorted by priority-then-ID.

    Same semantics as `query_next` but returns all ready tasks (not just the
    first).  Cycles raise IssuesError before any readiness check.
    """
    cycle = query_detect_cycle(tasks)
    if cycle is not None:
        path_str = ' -> '.join(str(n) for n in cycle)
        raise IssuesError(f'cycle detected in blockedBy: {path_str}')

    by_number = {t['number']: t for t in tasks}
    label_set = set(labels)
    candidates = []
    for task in tasks:
        if not query_is_ready(task, by_number):
            continue
        if label_set:
            task_labels = set(task.get('labels') or [])
            if not label_set.issubset(task_labels):
                continue
        candidates.append(task)
    return sorted(candidates, key=query_sort_key)


def query_filter(tasks, state='open', task_type='task', labels=None):
    """Return the subset of `tasks` matching all supplied criteria.

    Parameters
    ----------
    tasks     : iterable of task dicts (snake_case keys)
    state     : 'open' | 'closed' | 'all'  (default 'open')
    task_type : 'task' | 'prd' | 'all'     (default 'task')
    labels    : list of label strings — AND semantics: task must carry ALL
                listed labels.  None / [] means no label filter.
    """
    out = []
    label_set = set(labels) if labels else set()
    for task in tasks:
        if state != 'all' and task.get('state') != state:
            continue
        if task_type != 'all' and task.get('type') != task_type:
            continue
        if label_set:
            task_labels = set(task.get('labels') or [])
            if not label_set.issubset(task_labels):
                continue
        out.append(task)
    return out


def query_sort_key(task):
    """Return a (priority_sort, number) tuple for stable priority-then-ID sort.

    Lower priority number = higher priority.  Tasks with priority=None sort
    after all tasks that have a numeric priority.
    """
    priority = task.get('priority')
    priority_sort = float('inf') if priority is None else priority
    return (priority_sort, task.get('number') or 0)


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------


def editor_invoke(template=EDITOR_TEMPLATE):
    """Open `$EDITOR` with `template`, return the user's edited text.

    Raises IssuesError if `$EDITOR` is unset or stdin/stdout is not a TTY.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise IssuesError(
            'cannot open editor: not a TTY. Pass --body or --body-file.'
        )
    editor = os.environ.get('EDITOR') or os.environ.get('VISUAL')
    if not editor:
        raise IssuesError(
            'no $EDITOR set. Pass --body / --body-file or set $EDITOR.'
        )
    fd, path = tempfile.mkstemp(prefix='issues-', suffix='.md')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(template)
        # Use shell=True only via shlex split — but stdlib's subprocess with
        # a list and shell=False is safer. EDITOR may contain args.
        argv = _split_editor_command(editor) + [path]
        rc = subprocess.call(argv)
        if rc != 0:
            raise IssuesError(f'editor exited with status {rc}')
        return Path(path).read_text(encoding='utf-8')
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _split_editor_command(editor):
    import shlex
    return shlex.split(editor)


def editor_strip_template(text, template=EDITOR_TEMPLATE):
    """Drop the template comment lines users left in.

    Lines starting with `# ` that are part of the template prefix are
    removed; everything else is preserved verbatim. (Conservative: only
    strips lines that exactly match a template line.)
    """
    template_lines = set(template.splitlines())
    out = []
    for line in text.splitlines():
        if line in template_lines:
            continue
        out.append(line)
    result = '\n'.join(out).strip()
    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def output_view_text(task):
    """Render a task as a human-readable text view, gh-style.

    Mirrors `gh issue view` shape loosely (no remote-only fields like url/
    author).
    """
    lines = []
    lines.append(f"#{task['number']} {task['title']}")
    lines.append(f"state:\t{task['state']}")
    lines.append(f"type:\t{task['type']}")
    if task.get('priority') is not None:
        lines.append(f"priority:\t{task['priority']}")
    if task.get('parent') is not None:
        lines.append(f"parent:\t#{task['parent']}")
    labels = task.get('labels') or []
    lines.append(f"labels:\t{', '.join(labels) if labels else '-'}")
    blocked = task.get('blocked_by') or []
    lines.append(
        f"blocked by:\t{', '.join(f'#{n}' for n in blocked) if blocked else '-'}"
    )
    assignees = task.get('assignees') or []
    lines.append(
        f"assignees:\t{', '.join(assignees) if assignees else '-'}"
    )
    lines.append(f"created:\t{task['created_at']}")
    if task.get('closed_at'):
        lines.append(f"closed:\t{task['closed_at']}")
        if task.get('state_reason'):
            lines.append(f"reason:\t{task['state_reason']}")
    lines.append('')
    lines.append('--')
    lines.append('')
    body = (task.get('body') or '').strip()
    lines.append(body if body else '(no description provided)')
    return '\n'.join(lines) + '\n'


def output_view_json(task):
    """Render a task as gh-shaped JSON (camelCase)."""
    return json.dumps(task_to_json_dict(task), indent=2, ensure_ascii=False) + '\n'


def output_create_summary(task, path):
    return f"Created task #{task['number']} at {path}\n"


def output_list_text(tasks):
    """Render a list of tasks as a padded text table.

    Columns: NUMBER | TITLE | TYPE | LABELS | PRI
    An empty list renders just the header row (no separator).
    """
    def _label_str(task):
        labels = task.get('labels') or []
        return ', '.join(labels) if labels else '-'

    def _pri_str(task):
        p = task.get('priority')
        return str(p) if p is not None else '-'

    rows = []
    for task in tasks:
        rows.append({
            'num': str(task.get('number') or ''),
            'title': task.get('title') or '',
            'type': task.get('type') or '',
            'labels': _label_str(task),
            'pri': _pri_str(task),
        })

    # Column widths: max of header and content.
    headers = {'num': '#', 'title': 'TITLE', 'type': 'TYPE',
               'labels': 'LABELS', 'pri': 'PRI'}
    widths = {col: len(headers[col]) for col in headers}
    for row in rows:
        for col in widths:
            widths[col] = max(widths[col], len(row[col]))

    def _fmt(row):
        return (
            f"{row['num']:<{widths['num']}}  "
            f"{row['title']:<{widths['title']}}  "
            f"{row['type']:<{widths['type']}}  "
            f"{row['labels']:<{widths['labels']}}  "
            f"{row['pri']}"
        )

    header_row = _fmt(headers)
    lines = [header_row]
    for row in rows:
        lines.append(_fmt(row))
    return '\n'.join(lines) + '\n'


def output_list_json(tasks):
    """Render a list of tasks as a gh-shaped JSON array (camelCase)."""
    return json.dumps(
        [task_to_json_dict(t) for t in tasks],
        indent=2,
        ensure_ascii=False,
    ) + '\n'


def output_status_text(tasks):
    """Render a count summary: open vs closed (and by type if useful)."""
    open_tasks = [t for t in tasks if t.get('state') == 'open'
                  and t.get('type') != 'prd']
    open_prds = [t for t in tasks if t.get('state') == 'open'
                 and t.get('type') == 'prd']
    closed_all = [t for t in tasks if t.get('state') == 'closed']

    lines = [
        f'Open tasks: {len(open_tasks)}',
        f'Open PRDs:  {len(open_prds)}',
        f'Closed:     {len(closed_all)}',
        f'Total:      {len(tasks)}',
    ]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _gh_compat_parent():
    """Return an ArgumentParser with gh-compat no-op flags for use as a parent.

    Pass this to subparsers via ``parents=[_gh_compat_parent()]`` so that
    every verb silently accepts ``--web`` and ``--repo R`` without any
    behavioural effect.  ``add_help=False`` is required by argparse to avoid
    duplicate ``-h`` flags when the parent is merged into a child parser.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument('--web', action='store_true', help='(no-op, gh compat)')
    p.add_argument('--repo', '-R', default=None, metavar='R',
                   help='(no-op, gh compat)')
    return p


def cli_build_parser():
    p = argparse.ArgumentParser(
        prog='issues',
        description='Local-first, file-based issue tracker (gh-shaped CLI).',
    )
    sub = p.add_subparsers(dest='cmd', required=True)

    # Shared parent carrying gh-compat no-op flags (--web, --repo).
    # Each subparser is created with parents=[gh] so it inherits both flags.
    gh = _gh_compat_parent()

    init_p = sub.add_parser('init', help='Bootstrap an issues/ directory.',
                             parents=[gh])
    init_p.add_argument(
        '--gitignore', action='store_true',
        help='Append `issues/` to repo .gitignore.',
    )

    create_p = sub.add_parser('create', help='Create a new task.',
                               parents=[gh])
    create_p.add_argument('--title', '-t', required=True)
    body_g = create_p.add_mutually_exclusive_group()
    body_g.add_argument('--body', '-b', default=None)
    body_g.add_argument(
        '--body-file', '-F', default=None,
        help='Path to body file. `-` reads from stdin.',
    )
    create_p.add_argument(
        '--label', '-l', action='append', default=[],
        help='Add a label (repeatable).',
    )
    create_p.add_argument('--priority', type=int, default=None)
    create_p.add_argument(
        '--type', dest='task_type', choices=VALID_TYPES, default='task',
    )
    create_p.add_argument('--parent', type=int, default=None)
    create_p.add_argument(
        '--blocked-by', action='append', type=int, default=[],
        help='Add a blocked-by dep (repeatable).',
    )
    create_p.add_argument(
        '--assignee', '-a', action='append', default=[],
        help='Add an assignee (repeatable).',
    )

    view_p = sub.add_parser('view', help='View a task.', parents=[gh])
    view_p.add_argument('id', type=int)
    view_p.add_argument('--json', action='store_true', dest='as_json')

    list_p = sub.add_parser('list', help='List tasks.', parents=[gh])
    list_p.add_argument(
        '--label', '-l', action='append', default=[],
        help='Filter by label (repeatable; AND semantics).',
    )
    list_p.add_argument(
        '--state', '-s', choices=('open', 'closed', 'all'), default='open',
        help='Filter by state (default: open).',
    )
    list_p.add_argument(
        '--type', dest='task_type', choices=('task', 'prd', 'all'),
        default='task',
        help='Filter by type (default: task — excludes PRDs).',
    )
    list_p.add_argument(
        '--ready', action='store_true', dest='ready',
        help='Show only ready tasks (open, not PRD, all blockers closed).',
    )
    list_p.add_argument(
        '--json', action='store_true', dest='as_json',
        help='Output as JSON array.',
    )

    sub.add_parser('status', help='Show open/closed task counts.', parents=[gh])

    close_p = sub.add_parser('close', help='Close a task.', parents=[gh])
    close_p.add_argument('id', type=int)
    close_p.add_argument(
        '--reason', choices=('completed', 'not_planned'), default='completed',
        help='State reason (default: completed).',
    )
    close_p.add_argument(
        '--comment', default=None,
        help='Optional comment to append in the same operation.',
    )

    reopen_p = sub.add_parser('reopen', help='Reopen a closed task.',
                               parents=[gh])
    reopen_p.add_argument('id', type=int)

    comment_p = sub.add_parser('comment', help='Add a comment to a task.',
                                parents=[gh])
    comment_p.add_argument('id', type=int)
    body_g2 = comment_p.add_mutually_exclusive_group()
    body_g2.add_argument('--body', '-b', default=None)
    body_g2.add_argument(
        '--body-file', '-F', default=None,
        help='Path to body file. `-` reads from stdin.',
    )

    edit_p = sub.add_parser('edit', help='Edit a task in-place.', parents=[gh])
    edit_p.add_argument('id', type=int)
    edit_p.add_argument('--title', '-t', default=None)
    body_g3 = edit_p.add_mutually_exclusive_group()
    body_g3.add_argument('--body', '-b', default=None)
    body_g3.add_argument(
        '--body-file', '-F', default=None,
        help='Path to body file. `-` reads from stdin.',
    )
    edit_p.add_argument(
        '--add-label', action='append', default=[], metavar='L',
        help='Add a label (repeatable).',
    )
    edit_p.add_argument(
        '--remove-label', action='append', default=[], metavar='L',
        help='Remove a label (repeatable; no-op if absent).',
    )
    edit_p.add_argument(
        '--add-blocked-by', action='append', type=int, default=[], metavar='N',
        help='Add a blocked-by dep (repeatable).',
    )
    edit_p.add_argument(
        '--remove-blocked-by', action='append', type=int, default=[], metavar='N',
        help='Remove a blocked-by dep (repeatable; no-op if absent).',
    )
    edit_p.add_argument(
        '--priority', default=None, metavar='N',
        help='Set priority (integer). Pass `none` to clear.',
    )

    delete_p = sub.add_parser('delete', help='Permanently delete a task.',
                               parents=[gh])
    delete_p.add_argument('id', type=int)
    delete_p.add_argument(
        '--yes', '-y', action='store_true',
        help='Skip confirmation prompt.',
    )
    delete_p.add_argument(
        '--cascade', action='store_true',
        help='Also delete all tasks whose parent field matches this ID.',
    )

    next_p = sub.add_parser(
        'next',
        help='Print the ID of the next ready task (exit 1 if none).',
        parents=[gh],
    )
    next_p.add_argument(
        '--label', '-l', action='append', default=[],
        help='Constrain candidate set by label (repeatable; AND semantics).',
    )

    return p


def cli_dispatch(argv=None):
    parser = cli_build_parser()
    args = parser.parse_args(argv)
    if args.cmd == 'init':
        return cli_cmd_init(args)
    if args.cmd == 'create':
        return cli_cmd_create(args)
    if args.cmd == 'view':
        return cli_cmd_view(args)
    if args.cmd == 'list':
        return cli_cmd_list(args)
    if args.cmd == 'status':
        return cli_cmd_status(args)
    if args.cmd == 'close':
        return cli_cmd_close(args)
    if args.cmd == 'reopen':
        return cli_cmd_reopen(args)
    if args.cmd == 'comment':
        return cli_cmd_comment(args)
    if args.cmd == 'edit':
        return cli_cmd_edit(args)
    if args.cmd == 'delete':
        return cli_cmd_delete(args)
    if args.cmd == 'next':
        return cli_cmd_next(args)
    parser.error(f'unknown command: {args.cmd}')


def cli_cmd_init(args):
    base = Path.cwd()
    issues_dir = workspace_init(base, with_gitignore=args.gitignore)
    sys.stdout.write(f'Initialised {issues_dir}\n')
    if args.gitignore:
        sys.stdout.write(f'Updated {base / ".gitignore"}\n')
    return 0


def cli_cmd_create(args):
    if not args.title.strip():
        raise IssuesError('--title must be non-empty')
    issues_dir = workspace_resolve(auto_create=True)
    body = _resolve_body(args)
    task, path = repo_create(
        issues_dir,
        title=args.title,
        body=body,
        task_type=args.task_type,
        parent=args.parent,
        labels=args.label,
        blocked_by=args.blocked_by,
        priority=args.priority,
        assignees=args.assignee,
    )
    sys.stdout.write(output_create_summary(task, path))
    return 0


def _resolve_body(args):
    """Resolve body source. Mirrors gh's four-mode precedence:

    1. --body  -> use directly
    2. --body-file - -> stdin
    3. --body-file PATH -> read file
    4. (none) on TTY -> open $EDITOR
    5. (none) non-TTY -> error
    """
    if args.body is not None:
        return args.body
    if args.body_file is not None:
        if args.body_file == '-':
            return sys.stdin.read()
        return Path(args.body_file).read_text(encoding='utf-8')
    if sys.stdin.isatty() and sys.stdout.isatty():
        edited = editor_invoke()
        body = editor_strip_template(edited)
        if not body:
            raise IssuesError('aborting create: empty body')
        return body
    raise IssuesError(
        'no body source: pass --body, --body-file, or run on a TTY for $EDITOR.'
    )


def cli_cmd_view(args):
    issues_dir = workspace_resolve(auto_create=False)
    task, _ = repo_read(issues_dir, args.id)
    if args.as_json:
        sys.stdout.write(output_view_json(task))
    else:
        sys.stdout.write(output_view_text(task))
    return 0


def cli_cmd_list(args):
    issues_dir = workspace_resolve(auto_create=False)
    all_tasks = [task for task, _path in repo_list(issues_dir)]
    if getattr(args, 'ready', False):
        # --ready forces open state and non-PRD type; composes with --label.
        sorted_tasks = query_ready_set(all_tasks, labels=args.label or [])
    else:
        filtered = query_filter(
            all_tasks,
            state=args.state,
            task_type=args.task_type,
            labels=args.label or [],
        )
        sorted_tasks = sorted(filtered, key=query_sort_key)
    if args.as_json:
        sys.stdout.write(output_list_json(sorted_tasks))
    else:
        sys.stdout.write(output_list_text(sorted_tasks))
    return 0


def cli_cmd_status(_args):
    issues_dir = workspace_resolve(auto_create=False)
    all_tasks = [task for task, _path in repo_list(issues_dir)]
    sys.stdout.write(output_status_text(all_tasks))
    return 0


def cli_cmd_close(args):
    issues_dir = workspace_resolve(auto_create=False)
    author = _get_git_author() if args.comment else None
    task, path = repo_close(
        issues_dir,
        args.id,
        reason=args.reason,
        comment_body=args.comment,
        comment_author=author,
    )
    sys.stdout.write(f"Closed task #{task['number']} ({args.reason}) at {path}\n")
    return 0


def cli_cmd_reopen(args):
    issues_dir = workspace_resolve(auto_create=False)
    task, path = repo_reopen(issues_dir, args.id)
    sys.stdout.write(f"Reopened task #{task['number']} at {path}\n")
    return 0


def cli_cmd_comment(args):
    issues_dir = workspace_resolve(auto_create=False)
    body = _resolve_body(args)
    author = _get_git_author()
    task, _ = repo_append_comment(issues_dir, args.id, body=body, author=author)
    sys.stdout.write(f"Added comment to task #{task['number']}\n")
    return 0


def _parse_priority_arg(raw):
    """Convert the --priority string to int or None.

    Accepts an integer string or the magic value 'none' (case-insensitive)
    which clears priority (returns None).  Raises IssuesError on invalid input.
    """
    if raw is None:
        return _UNSET
    if raw.lower() == 'none':
        return None
    try:
        return int(raw)
    except ValueError:
        raise IssuesError(
            f'invalid --priority value {raw!r}: must be an integer or "none"'
        )


def cli_cmd_edit(args):
    issues_dir = workspace_resolve(auto_create=False)

    # Body: only resolve if --body or --body-file was supplied.
    body = None
    if args.body is not None or args.body_file is not None:
        body = _resolve_body(args)

    priority = _parse_priority_arg(args.priority)

    # Cycle check: reject any --add-blocked-by edge that would close a cycle.
    if args.add_blocked_by:
        all_tasks = [t for t, _p in repo_list(issues_dir)]
        for new_blocker in args.add_blocked_by:
            if query_would_introduce_cycle(all_tasks, args.id, new_blocker):
                raise IssuesError(
                    f'adding --add-blocked-by {new_blocker} to task #{args.id} '
                    f'would introduce a cycle in the blockedBy graph'
                )

    task, path = repo_edit(
        issues_dir, args.id,
        title=args.title,
        body=body,
        add_labels=args.add_label,
        remove_labels=args.remove_label,
        add_blocked_by=args.add_blocked_by,
        remove_blocked_by=args.remove_blocked_by,
        priority=priority,
    )
    sys.stdout.write(f"Updated task #{task['number']} at {path}\n")
    return 0


def _confirm_delete(task, *, yes, stdin=None):
    """Prompt the user to confirm deletion.

    Returns True if confirmed, False if aborted.  Raises IssuesError if not
    on a TTY and --yes was not passed (non-interactive context guard).

    `stdin` is injectable for testing (defaults to sys.stdin).
    """
    if yes:
        return True
    stream = stdin if stdin is not None else sys.stdin
    if not stream.isatty():
        raise IssuesError(
            'refusing to delete without --yes in a non-TTY context. '
            'Pass --yes to confirm.'
        )
    title = task.get('title', '')
    number = task.get('number', '?')
    prompt = f'Delete task #{number} "{title}"? [y/N]: '
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ('y', 'yes')


def cli_cmd_delete(args):
    issues_dir = workspace_resolve(auto_create=False)
    # Read the task first so we can show the title in the prompt.
    task, _ = repo_read(issues_dir, args.id)

    if getattr(args, 'cascade', False):
        # --cascade always requires --yes; never prompt (blast radius too large).
        if not args.yes:
            raise IssuesError(
                'delete --cascade requires --yes; pass --yes to confirm '
                'deletion of the task and all its children.'
            )
        deleted = repo_cascade_delete(issues_dir, args.id)
        sys.stdout.write(
            f"Deleted {len(deleted)} task(s): "
            + ', '.join(f'#{n}' for n in deleted) + '\n'
        )
        return 0

    if not _confirm_delete(task, yes=args.yes):
        sys.stderr.write('Aborted.\n')
        return 1
    repo_delete(issues_dir, args.id)
    sys.stdout.write(f"Deleted task #{task['number']}\n")
    return 0


def cli_cmd_next(args):
    """Print the ID of the next ready task; exit 1 with empty stdout if none.

    "Ready" = open + not PRD + all blockers closed-or-missing.  Sorted by
    priority-then-ID.  `--label` constrains the candidate set (AND semantics).
    Exit code 0 on success, 1 when no ready task exists — designed for the
    shell idiom ``while id=$(issues next); do ...; done``.
    """
    issues_dir = workspace_resolve(auto_create=False)
    all_tasks = [task for task, _path in repo_list(issues_dir)]
    task = query_next(all_tasks, labels=args.label or [])
    if task is None:
        return 1
    sys.stdout.write(f"{task['number']}\n")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv=None):
    try:
        return cli_dispatch(argv) or 0
    except IssuesError as exc:
        sys.stderr.write(f'issues: {exc}\n')
        return 1
    except KeyboardInterrupt:
        sys.stderr.write('issues: interrupted\n')
        return 130


if __name__ == '__main__':
    sys.exit(main())
