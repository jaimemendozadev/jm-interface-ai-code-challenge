# Computer-use automation — interface.ai challenge

An LLM-driven agent that operates a real web app (a stand-in for a bank's
back-office UI) to accomplish a goal, then records what it did as a
reusable, replayable artifact — deterministic replay of that artifact is
the path an AI agent would invoke in production. See `/REPORT.md` for the
full design write-up.

## Status

Every core requirement has real, working code behind it, backed by
evidence in `/evidence/` from actual runs - not just described:

- ✅ Discovery agent (`discovery/main.py`) — the observe → decide → act
  loop, live against Parabank.
- ✅ Credential handling — secrets never enter the LLM's context (see
  `tools.py`'s `type_credential`).
- ✅ Locator strategy — accessibility role+name primary, raw HTML
  attribute fallback for unlabeled fields (see `browser.py`).
- ✅ Artifact schema (`artifact_schema.py`) — typed, versioned, validates
  against real captured data.
- ✅ Deterministic replay engine (`replay/`) — zero LLM calls, separates
  business outcomes from hard failures.
- ✅ Safety allowlist (`safety.py`) — domain + action-type enforcement,
  shared by both discovery and replay.
- ✅ Human escalation & handoff (`discovery/escalation.py`) — pauses the
  live session, hands it to a human, resumes on their signal.

## Setup

```bash
uv sync
uv run playwright install chromium      # see Troubleshooting below if this fails

cp .env.example .env                    # then fill in your real values
```

You'll need:

- An Anthropic API key from https://console.anthropic.com (separate from
  any claude.ai subscription — a few dollars of credit covers this whole
  project).
- A Parabank test account — register one for free at
  https://parabank.parasoft.com/parabank/register.htm (fake info is fine,
  it's a public sandbox). **Note:** this is a shared public demo
  environment and accounts have been observed to stop authenticating
  after a few hours — if login starts failing, re-registering usually
  fixes it (see `REPORT.md`'s Escalation section for how this was
  actually diagnosed live).

The target domain (`parabank.parasoft.com`) is preconfigured in
`allowlist.json` — see `safety.py` if you need to point this at a
different target.

## Run it

**Discovery** — a live, LLM-driven run against the real app:

```bash
uv run python -m discovery.main \
  --url "https://parabank.parasoft.com/parabank/index.htm" \
  --goal "Log in, then read and report the checking account balance"
```

Leave off `--headless` so you can watch it work (and so you can actually
take over the browser if it escalates to you — see below). Each run
writes a timestamped folder under `evidence/discovery_<timestamp>/`
containing a structured JSONL step log, a final screenshot, a run
summary, and - if the agent got stuck and a human intervened -
`interventions.json`.

If it detects it's stuck (e.g. repeating the same failing action several
times in a row), it pauses and hands you the live browser window
directly. Type your notes on what you did, or `abort` to stop the run.

**Replay** — deterministic, no LLM involved, using the artifact discovery
produces:

```bash
uv run python -m replay.main \
  --artifact artifacts/parabank_read_account_balance.v1.json \
  --param account_number=<a real account number from your test account>
```

Prints a structured result (`success`, `business_outcome`, or
`hard_failure`) and writes the same to
`evidence/replay_<timestamp>/replay_result.json` plus a screenshot.

`evidence/` is gitignored by default — every run creates a fresh,
timestamped folder, and nothing accumulates in git until you deliberately
`git add -f` the specific run(s) you want to keep. See the already-
committed runs under `/evidence/` for real examples of each outcome type,
including a full escalation-and-resume cycle.

## Troubleshooting `playwright install chromium`

Check in this order:

1. **Missing OS-level libraries (most common on Linux).**
   ```bash
   uv run playwright install --with-deps chromium
   ```
2. **Network/firewall/VPN blocking the download** — the browser binary
   comes from Playwright's CDN, not PyPI. Try a different network.
3. **Permission or disk-space error** writing to the cache directory
   (`~/.cache/ms-playwright` on Linux/macOS). Try:
   ```bash
   PLAYWRIGHT_BROWSERS_PATH=./.playwright-browsers uv run playwright install chromium
   ```
4. **Stale/partial previous install** — delete the cache directory above
   and retry.

## Layout

Root - shared by both discovery and replay:

- `browser.py` — Playwright driver. Elements targeted by **accessibility
  role + accessible name** primarily, with a raw-HTML-attribute fallback
  for elements with no accessible name at all (common in legacy/non-
  semantic markup - see the module docstring for why). Discovery-facing
  methods (`click`/`type_text`/`read_text`) rely on a snapshot-built
  cache; replay-facing methods (`click_target`/`type_text_target`/
  `read_text_target`) are self-sufficient from the artifact alone.
- `tools.py` — the tool schema Claude sees during discovery, and the
  dispatcher that executes whichever one it picks. `type_credential`
  keeps secrets out of the LLM's context entirely.
- `artifact_schema.py` — the typed, versioned Pydantic schema a
  successful discovery run gets distilled into.
- `safety.py` / `allowlist.json` — domain and action-type enforcement,
  checked before either discovery or replay ever touches a browser.
- `utils.py` — discovery-specific helpers (per-step log record, element
  formatting, system prompt builder).

`discovery/` — the LLM-driven agent loop:

- `main.py` — observe → decide → act, with cycle/dead-end detection.
- `escalation.py` — pauses automation and hands the live session to a
  human when the agent can't safely proceed.

`replay/` — the deterministic production path:

- `main.py` — loads an artifact, executes it with zero LLM calls.
- `utils.py` — per-step execution, placeholder resolution, output
  extraction, and the business-outcome vs. hard-failure distinction.

`artifacts/` — saved capability artifacts (the reusable output of a
successful discovery run).

`evidence/` — real run output: logs, screenshots, and results from actual
discovery and replay runs (gitignored except for curated examples).
