# CLAUDE.md

Loaded into context for every Claude Code session in this repo.

## What this is

`issues` — a local-first, file-based issue tracker with a `gh issue`-shaped CLI, designed for AI coding workflows.

**Status:** design phase. The PRD lives at GitHub Issue #1: https://github.com/benabraham/local-issues/issues/1 — read it before non-trivial work.

## Workflow

Following Matt Pocock's flow: PRD → vertical-slice Kanban tickets → AFK implementation. PRDs and slices are filed as GitHub Issues until the tool itself can host them locally.

Vertical slices, not horizontal: each slice cuts through all the deep modules to a working CLI verb. Don't propose tickets shaped like "build the Workspace module" then "build the Task module" — propose tickets shaped like "`issues init` + `issues create` + `issues view` end-to-end."

## Hard constraints

- Single-file Python script, stdlib-only, Python 3.10+
- Linux / macOS only — Windows is out of scope for v1
- Atomic filesystem ops only (`O_EXCL`, `rename(2)`, `unlink(2)`) — no locking
- CLI verb and flag names mirror `gh issue` exactly where they overlap; deltas are documented in the PRD

## Module map (planned)

Deep (testable in isolation, will get real test coverage):

- `Workspace` — CWD walk-up, `.git` resolution including worktree gitdir, `issues/` discovery
- `Task` — frontmatter parse/serialise, slug, comments-section, snake↔camel JSON
- `Repository` — atomic FS ops, state transitions, cascade delete, `.next-id` maintenance
- `Query` — filter, sort, ready-detection on the `blockedBy` DAG, cycle rejection

Glue (smoke tests only):

- `Cli` — argparse + verb dispatch
- `Output` — text-table and JSON formatters
- `Editor` — `$EDITOR` invocation

## gh CLI gotcha

The active `GITHUB_TOKEN` env (fine-grained PAT) lacks write scopes. For issue/PR creation and other write ops, prefix to fall through to the keyring OAuth token:

```
GITHUB_TOKEN= gh issue create ...
GITHUB_TOKEN= gh repo edit ...
```
