# Step 1 — Playwright + Claude, talking to each other

This is the smallest possible slice of the full system: load a real page,
take an accessibility-tree snapshot, hand it to Claude with a set of tools,
and execute exactly one action Claude decides on. Nothing more yet — no
loop, no artifact, no replay. Just proving the plumbing works before we
build the real observe-decide-act loop on top of it.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management
— add `pyproject.toml` below (uv will create `uv.lock` the first time you
sync). `requirements.txt` from before is no longer needed once you're on uv;
safe to delete it.

```bash
uv sync                                    # creates .venv, installs deps from pyproject.toml
uv run playwright install chromium         # downloads the actual browser binary

cp .env.example .env                       # then paste your real key into .env
export ANTHROPIC_API_KEY=sk-ant-...        # or `source .env` if you prefer
```

You'll need an Anthropic API key from https://console.anthropic.com — this
is separate from any claude.ai chat subscription. A few dollars of credit
is more than enough for this whole project.

## Run it

```bash
uv run step1_single_action.py \
  --url "https://parabank.parasoft.com/parabank/index.htm" \
  --goal "Click the link to log in to Parabank"
```

Leave off `--headless` the first several times so you can _watch_ the
browser window and see Claude's action actually happen. Try a few
different `--goal` values against different pages once the first one
works — e.g. after logging in, point `--url` at the accounts overview
page and ask it to click into an account.

## What "working" looks like

```
[snapshot] found 14 interactable elements
   - textbox: 'Username'
   - textbox: 'Password'
   - button: 'Log In'
   - link: 'Forgot login info?'
   ...
[claude] wants to call: click({"role": "link", "name": "Forgot login info?"})
[result] Clicked successfully.
```

If Claude picks a reasonable-but-wrong element, that's fine for Step 1 —
it means the plumbing works, and it's useful signal for how you'll want
to phrase the goal/prompt once you build the real loop.

## Troubleshooting `playwright install chromium`

If `uv run playwright install chromium` is failing, it's almost always one
of these — check in this order:

1. **Missing OS-level libraries (most common on Linux).** The Chromium
   binary itself downloads fine, but it can't _launch_ without system
   libraries Playwright doesn't bundle. Fix:
   ```bash
   uv run playwright install --with-deps chromium
   ```
   This installs both the browser and the required system packages (needs
   sudo on most distros — it'll prompt if so).
2. **Network/firewall/VPN blocking the download.** The browser binary comes
   from Playwright's CDN, not PyPI — a corporate network, VPN, or strict
   firewall can silently block just this download even though `uv sync`
   worked fine. Try turning off VPN, or on a different network, and re-run.
3. **Permission or disk-space error writing to the cache directory**
   (`~/.cache/ms-playwright` on Linux/macOS, `%USERPROFILE%\AppData\Local\ms-playwright`
   on Windows). Check free disk space, or redirect the cache elsewhere:
   ```bash
   PLAYWRIGHT_BROWSERS_PATH=./.playwright-browsers uv run playwright install chromium
   ```
4. **Stale/partial previous install.** Delete the cache directory above and
   re-run — a half-finished download from an earlier attempt can leave
   things in a broken state.

If none of those match, paste me the exact error output and I'll pinpoint it.

## Known rough edges (fine for now, fix in Step 2+)

- Only one step runs, then the script waits for you to press Enter and
  exits. Step 2 wraps this in a loop with a max-step limit.
- `read_text` and error handling exist in `tools.py` but nothing yet
  distinguishes recoverable conditions from hard failures — that's the
  Step 4 replay-engine work.
- No logging to `/evidence/` yet — add that once you're doing a real
  multi-step run worth saving.

## Files

- `browser.py` — Playwright wrapper. Elements are targeted by
  **accessibility role + accessible name**, not CSS selectors or pixel
  coordinates — this is the locator-robustness decision, and it's the
  same mechanism that extends to legacy web apps and (via OS accessibility
  APIs) desktop apps later.
- `tools.py` — the tool schema Claude sees, plus the dispatcher that
  executes whichever tool it picks.
- `step1_single_action.py` — the single-step proof-of-life script above.
