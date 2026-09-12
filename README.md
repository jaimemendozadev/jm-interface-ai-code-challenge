# Computer-use automation — interface.ai challenge

An LLM-driven agent that operates a real web app (a stand-in for a bank's
back-office UI) to accomplish a goal, then — eventually — records what it
did as a reusable, replayable artifact. See `/REPORT.md` for the design
write-up once it exists; `REPORT_NOTES.md` is the running scratchpad it
gets assembled from.

## Status

- ✅ Discovery agent (`main.py`) — the observe → decide → act loop, live
  against Parabank, with structured logging to `/evidence/`.
- ✅ Credential handling — secrets never enter the LLM's context (see
  `tools.py`'s `type_credential`).
- ✅ Locator strategy — accessibility role+name primary, raw HTML
  attribute fallback for unlabeled fields (see `browser.py`).
- ⬜ Artifact schema (recording a successful run as reusable data)
- ⬜ Deterministic replay engine
- ⬜ Human escalation / handoff
- ⬜ Safety allowlist

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
  it's a public sandbox).

## Run it

```bash
uv run main.py \
  --url "https://parabank.parasoft.com/parabank/index.htm" \
  --goal "Log in, then read and report the checking account balance"
```

Leave off `--headless` so you can watch it work. Each run writes a
timestamped folder under `evidence/discovery_<timestamp>/` containing a
structured JSONL step log, a final screenshot, and a run summary. Every
run creates a new folder — `evidence/` is gitignored by default, so
nothing accumulates in git until you deliberately `git add -f` the run(s)
you want to keep as evidence.

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

## Files

- `browser.py` — Playwright wrapper. Elements are targeted by
  **accessibility role + accessible name**, with a fallback pass for
  form controls that have no accessible name at all (common in
  legacy/non-semantic markup) — see the module docstring for why.
- `tools.py` — the tool schema Claude sees (click, type_text,
  type_credential, navigate, read_text, finish) and the dispatcher that
  executes whichever one it picks.
- `utils.py` — shared helpers: the per-step log record, element-list
  formatting for prompts, and the system prompt builder.
- `main.py` — the discovery loop that ties it all together.
