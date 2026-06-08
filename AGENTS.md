
# OTT Sync for MPV

This is a plugin that syncs a local mp4 instance to a known room on opentogethertube. Some delay (~10s) is fine. This plugin only follows the room, and does not do control.


## Tooling

Managed with `uv`. Common commands:

- `uv run pytest` — run the test suite
- `uv run ruff check .` — lint (add `--fix` to auto-fix)
- `uv run ruff format .` — format (add `--check` to verify only)
- `uv run ott-mpv-sync <room-url>` — run the CLI

# General 

This section is the same for multiple projects. 

## Principles

- 📏 Big functions are fine. Extract when there's reuse or the established abstractions call for it.
- ⏳ No premature performance optimization.
- 📋 Plans define what and done when, not how. Challenge a plan when it fights reality; don't silently deviate.
- 🔊 Fail loudly. Throw errors, don't swallow them. Log failures clearly. If something is wrong, the developer should know immediately, not discover it later through subtle misbehavior.

## Emoji

Use consistently in code, commits, and logging.

### Commits

Human-made commits usually contain no emoji, while agent-made commits do.

`<emoji> <type>: <description>`

- ✨ feat: new feature
- 🐛 fix: bug fix
- 🔧 config: configuration changes
- 📦 deps: dependency changes
- 🧪 test: tests
- 📝 docs: documentation
- 🧹 refactor: cleanup (no behavior change)


### Logging

- ✅ success operations
- ❌ errors and failures
- ⚠️ warnings
