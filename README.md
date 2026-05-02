# local-issues

Local-first, file-based issue tracker. Single-file Python CLI with a `gh issue`-shaped surface, designed for AI coding workflows that need a parallel-friendly task queue without depending on GitHub.

The CLI is `issues`. Verb and flag names mirror `gh issue` (`list`, `view`, `create`, `close`, `reopen`, `edit`, `comment`, `delete`) so existing model intuition transfers, plus a `next` extension for picking the next ready task off a `blockedBy` DAG.

## Install

Requires Python 3.10+. Linux or macOS (Windows not supported).

```sh
pipx install git+ssh://git@github.com/benabraham/local-issues
# or
uv tool install git+ssh://git@github.com/benabraham/local-issues
```

## Quick start

```sh
cd ~/code/my-project

issues init                              # creates issues/{open,closed}/
issues init --gitignore                  # also adds issues/ to .gitignore

issues create --title "Wire up auth" --body "Use OAuth"
issues create --title "DB migration" --priority 0 --label backend --blocked-by 1

issues list                              # open tasks (excludes PRDs by default)
issues list --label backend
issues list -s closed                    # closed tasks (gh-style short flag)
issues view 2
issues view 2 --json                     # camelCase, gh-shaped
issues view 2 --json number,title,state  # filtered fields

issues comment 2 --body "Started this"
issues edit 2 --add-label working --priority 1
issues close 2 --reason completed --comment "shipped in abc1234"
issues reopen 2 -c "regression caught in prod"
issues delete 2 --yes

issues status                            # counts
```

## AFK-loop idiom

`issues next` returns the ID of the next ready task on the `blockedBy` DAG (sorted by priority, then ID). PRDs are always excluded. Exit 1 with empty stdout when none are ready, so this terminates cleanly:

```sh
while id=$(issues next --label AFK); do
  claude --print --dangerously-skip-permissions \
    "Implement task #$id. Run: issues view $id --json | jq ."
  issues close "$id" --reason completed -c "implemented by AFK loop"
done
```

`issues list --ready` shows the full ready set rather than just one ID.

## How it stores tasks

```
issues/
  open/
    001-wire-up-auth.md
    003-db-migration.md
  closed/
    002-spike-foo.md
  .next-id
  README.md
```

Each file: YAML frontmatter (camelCase fields matching `gh issue --json`) + markdown body. Comments append under a `## Comments` section as `### timestamp — author` blocks.

```yaml
---
number: 1
title: Wire up auth
type: task
parent: null
labels: [backend, auth]
blockedBy: []
priority: 0
state: open
stateReason: null
createdAt: 2026-05-02T17:54:49Z
closedAt: null
assignees: []
---
Use OAuth.

## Comments

### 2026-05-02T18:01:12Z — alice
Started this.
```

Filenames are `NNN-slug.md` zero-padded to 3 digits. IDs are never reused. Concurrent `issues create` calls are race-free (`O_EXCL` retry on the number reservation).

## Workspace discovery

Walks up from the current directory:

1. Find the nearest `.git` (file or directory).
2. Look for `issues/` at the repo root, then at the parent of the repo root (sibling-shared layout).
3. For git worktrees, also fall back to the main repo's `issues/` via the gitdir's `commondir` — so `issues/` shared from the main repo works seamlessly.
4. With no `.git` anywhere up the tree, plain walk-up.

Read commands error helpfully when no `issues/` is found; write commands auto-create one in the current directory.

## What's gh-shaped vs extensions

Mirrored from `gh issue`: verb names, `--label`, `--state`, `--json`, `--body`/`--body-file`, `--reason`, TTY-vs-`--yes` confirmation on `delete`, JSON shape (camelCase, uppercase state values), `--web` and `--repo` accepted as no-ops.

Extensions on top: `next` verb, `--ready` filter on `list`, `--type {task|prd|all}` (default excludes PRDs), `--priority N`, `--blocked-by N`, `--parent N`, `--cascade --yes` on delete, `--comments/-c` on view (renders comments inline in text mode).

## License

MIT — see [LICENSE](./LICENSE).
