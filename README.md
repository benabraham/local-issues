# local-issues

Local-first issue tracker. File-based markdown storage, single-binary CLI, designed for AI coding workflows that need a parallel-friendly task queue without depending on GitHub.

The CLI is `issues`. Surface mirrors `gh issue` (`list`, `view`, `create`, `close`, `reopen`, `edit`, `comment`, `delete`) so existing model intuition transfers, plus a `next` extension for picking the next ready task off a DAG.

**Status: design phase.** Not yet implemented.

## Why

Workflows like Matt Pocock's AI-coding flow rely on a Kanban of small tickets that AFK agents work through in parallel. Most teams use GitHub Issues. For solo developers without collaboration needs, a local file-based store is simpler — but no widely-used tool exposes a CLI shaped like `gh issue` against local storage. This is that tool.

## Design

The PRD lands as the first GitHub issue on this repo.

## License

MIT — see [LICENSE](./LICENSE).
