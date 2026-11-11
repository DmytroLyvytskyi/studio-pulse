# CLAUDE.md

Guidance for coding agents working in this repository. This file is self-sufficient: the full project specification is a local file that is not part of the repository.

## What this is

**Studio Pulse** reads time tracking and budget data from Productive (productive.io) for a creative agency and provides:

1. an **MCP server** (`MCPServer`, stdio, entry point `studio-pulse-mcp`) so project managers can ask Claude questions such as "Who didn't log their time yesterday?";
2. a **daily report** CLI (`studio-pulse report`, Typer) posted every working morning to Microsoft Teams (Workflows webhook) and/or Discord.

The project's own code never calls an LLM. Claude is only a client of the MCP server.

## Commands

```bash
uv sync                      # install dependencies (Python 3.12, see .python-version)
uv run pytest                # tests
uv run ruff check .          # lint
uv run ruff format .         # format (Markdown is excluded)
uv run ruff format --check . # format check, as in CI
```

Run all three checks (`pytest`, `ruff check`, `ruff format --check`) before declaring work done.

## Principles

1. **Numbers are computed by code, never by an LLM.** All sums, percentages and thresholds live in plain, tested Python in `analyze.py`. Each number is computed in exactly one place; the MCP server and the report share the same functions.
2. **Mock first.** Everything runs end to end with `DATA_SOURCE=mock`, without accounts or tokens. The Productive source is a second implementation of the same `DataSource` interface.
3. **Read only.** No tool or command ever writes to Productive.
4. **Simple over clever.** No premature abstractions, no unneeded frameworks. Clear names, short functions, type hints everywhere.
5. **Configurable without code.** Thresholds live in `config.yaml`; secrets in environment variables / `.env`.
6. **No invented data.** If the Productive API cannot provide something reliably, use a safe default, add a `TODO` explaining why, and list it under "Known limitations" in the README.

## Tech stack

| Part | Choice | Notes |
| --- | --- | --- |
| Language | Python 3.12 | |
| Packaging | uv | `pyproject.toml`, entry points `studio-pulse` and `studio-pulse-mcp` |
| HTTP | httpx | timeouts, retry on 429 |
| Models and settings | Pydantic v2, pydantic-settings[yaml] | `.env` for secrets, `config.yaml` for thresholds |
| Time zones | zoneinfo + tzdata | tzdata needed on Windows |
| MCP | official Python SDK `mcp` (`MCPServer`), v2.x | stdio transport; check the current API in its docs, do not rely on older examples |
| CLI | Typer | |
| Tests | pytest, respx | |
| Lint and format | ruff | Markdown excluded from formatting |
| Scheduling and CI | GitHub Actions | |

**No other dependencies without asking the user first.**

## Repository structure

```text
studio-pulse/
├── src/studio_pulse/
│   ├── __init__.py          # StudioPulseError
│   ├── config.py            # Settings: .env + config.yaml
│   ├── models.py            # Pydantic models; durations in minutes (int)
│   ├── dates.py             # previous_working_day, date parsing
│   ├── analyze.py           # pure analysis functions, no I/O
│   ├── sources/
│   │   ├── __init__.py      # get_source(settings)
│   │   ├── base.py          # DataSource Protocol
│   │   ├── mock.py          # reads mock/*.json
│   │   └── productive.py    # Productive JSON:API client; all response mapping lives here
│   ├── delivery/
│   │   ├── base.py          # Delivery Protocol, post_json (retry on 429)
│   │   ├── text.py          # Slovak report text shared by the adapters
│   │   ├── console.py
│   │   ├── teams.py         # build_adaptive_card(report) -> dict
│   │   └── discord.py
│   ├── report.py            # CLI
│   └── mcp_server.py        # MCP server (MCPServer)
├── mock/                    # generated JSON, fixed seed
├── scripts/
│   ├── generate_mock.py
│   ├── explore_productive.py       # answers open API questions against a real account
│   └── save_productive_fixtures.py # saves raw API responses as test fixtures
├── skills/studio-pulse-pm/SKILL.md  # planned
├── tests/
│   └── fixtures/productive/        # raw JSON:API responses, emails redacted
├── .github/workflows/       # ci.yml, daily-report.yml
├── config.yaml
├── .env.example
├── CLAUDE.md
├── pyproject.toml
└── README.md
```

Not every file exists yet; the project is built milestone by milestone.

## Key domain rules

- Durations are stored in **minutes** (`int`) and converted to hours (rounded to 0.1) only for output.
- `DataSource` Protocol: `people()`, `projects()`, `budgets()`, `time_entries(date_from, date_to)`.
- Budget status: `ok` below the warning percent, `warning` from the warning percent up to 100 %, `over` above 100 %, `no_budget` when the budget is `None`.
- Previous working day: Monday, Saturday, Sunday → previous Friday; Tuesday to Friday → previous day. Public holidays are ignored (known limitation).
- Mock data uses fictional Slovak names and companies only; never real companies or real residential projects.
- Report text is in Slovak with Slovak date and number formatting (decimal comma).
- Teams delivery uses a **Workflows** webhook with an Adaptive Card (version 1.4). Office 365 Connectors incoming webhooks are retired and must not be used.
- MCP tools: docstrings state what the tool answers with an example question; flat JSON with names instead of ids; fuzzy (case-insensitive substring) matching for person/project/client with a helpful error listing close options; dates accept `YYYY-MM-DD`, `today`, `yesterday`; errors are returned as messages, never raw exceptions.

## Productive API

- **Never invent Productive API fields or filters.** Verify them in the official docs (https://developer.productive.io). If the docs are unclear, write a minimal exploratory script and ask the user to run it against a real account.
- JSON:API format, pagination via `page[number]` / `page[size]`, rate limit 100 requests per 10 s (`429` → retry with backoff).
- Verified on a trial account (the docs say otherwise or are silent):
  - `time`, `billable_time` and `budgeted_time` are **minutes**, although the docs call the last two hours.
  - Relationship linkage is returned only for relationships listed in `include`.
  - Time entry notes are HTML (or null).
  - "Save as draft" in the UI does not mark entries as drafts in the API (`draft: false, submitted: false`); unsubmitted and draft entries both count as logged time.
  - A budget is a deal with `budget: true`; its limit is the sum of its services' `budgeted_time`.

## Testing

- `analyze.py` and `dates.py`: every rule and edge case (no entries, zero hours, no budget, exactly at the warning threshold, weekend and Monday handling).
- Productive source: respx with realistic JSON:API responses, pagination over two pages, `429` followed by success.
- Teams card: snapshot test of `build_adaptive_card()` for the mock report.
- MCP tools: call the tool functions with the mock source; test fuzzy matching and error messages.
- **No test makes a real network call.**
- Tests must not depend on the developer's environment; `tests/conftest.py` clears the relevant env vars.

## Code conventions

- Type hints on all functions; ruff clean.
- English in code, comments and docs; Slovak only in user-facing report text.
- No `print` outside `report.py`, `delivery/console.py` and `scripts/`; use `logging` (enforced by ruff rule T20).
- Expected failures (missing config, API errors) raise `StudioPulseError` with a human-readable message.
- Secrets only through environment variables (`SecretStr` in settings). `.env` is never committed. Tokens and webhook URLs are never logged.

## Working rules

- Work in small milestones. At the end of each: run tests and ruff, summarize the changes, propose a commit message, and **wait for the user's approval before committing**.
- Ask before adding dependencies or deviating from these rules.
- `PROJECT_SPEC.md` is a local-only file: never commit it, and never remove it from `.gitignore`.
