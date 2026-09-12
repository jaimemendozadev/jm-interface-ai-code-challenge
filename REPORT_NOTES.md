# REPORT.md notes (working draft — polish into final REPORT.md at the end)

Add 2-3 bullets here right when a decision gets made. Don't wait until the
end to reconstruct this from memory — you'll lose the good specifics.

## 1. Architecture

- CLI script, not a web service. The deliverable itself asks for "exact
  commands to run" — a service adds nothing here and the agent loop is
  inherently multi-turn/stateful, which doesn't fit a synchronous HTTP
  request/response cycle anyway.
- No LangChain/LangGraph. A plain loop calling the Anthropic API directly
  is fully legible and defensible; a framework would hide exactly the
  mechanics I need to be able to explain.
- Playwright (Python) as the browser driver.

## 2. Artifact schema

- (not built yet — Step 3)
- Design decision to make deliberately: the discovery transcript can (and
  did, in practice) include exploratory detours and dead ends - e.g. a
  run that clicked into an account details page looking for a balance,
  didn't find a usable value there, backed out, and found it on the
  overview page instead. The artifact should record the DISTILLED minimal
  correct path, not a verbatim replay of every step the discovery run
  actually took - otherwise replay would waste time (or fail) reproducing
  a dead end that isn't actually necessary to reach the goal.

## 3. Determinism & error handling

- Elements are targeted by accessibility role + accessible name first
  (get_by_role) — semantic, survives markup changes, and is the same
  mechanism available on desktop apps via OS accessibility APIs.
- Real discovery run surfaced a concrete legacy-app problem: Parabank's
  username/password fields use a <p> tag as the visual label instead of a
  real <label>/aria-label, so the browser computes NO accessible name for
  them at all — pass 1 (accessibility tree) can't see them.
- Fallback strategy added: a second pass scans raw <input>/<textarea>/
  <select> elements not already covered by pass 1, and identifies them by
  raw HTML name/id/placeholder attribute instead, with a remembered CSS
  selector. This is a deliberately lower-priority, less-robust strategy —
  used only when the semantic one finds nothing.

## 4. Heterogeneity & multi-tenant

- Surface abstraction: the artifact records steps as {action, target:
  {role, name}, params} - pure data, with no Playwright/DOM concept
  anywhere in it. It carries a `surface_type` field ("web",
  "windows_desktop", "macos_desktop") telling the replay engine which
  driver to use.
- The driver is a small interface (click/type_text/read_text/snapshot)
  that each surface implements independently. browser.py is the web
  implementation, backed by Playwright + the accessibility tree
  (aria_snapshot/get_by_role). A desktop implementation would expose the
  identical four methods, backed by OS accessibility APIs instead: UI
  Automation on Windows, AXUIElement on macOS, AT-SPI on Linux - all of
  which expose the same role+name concept a screen reader relies on.
  Same artifact JSON, different driver underneath.
- This is exactly why "role+name" was chosen as the identification
  strategy over a Playwright-specific selector back in Step 1/3 - it's
  the one concept that's genuinely cross-platform, not a web-only idea
  borrowed for convenience.
- The "unlabeled element" fallback (target via name/id/placeholder
  attribute) has a direct desktop analogue: old Win32/MFC apps routinely
  expose empty/garbage accessibility names too. Same fallback shape
  (automation ID, control class, position) would apply there.
- Multi-tenant reuse (not implemented, design only): since many tenants
  run the same underlying vendor product just re-skinned, an artifact
  recorded against a "base" tenant should be parameterizable enough
  (base URL, maybe a per-tenant locator override table) to replay against
  a re-branded/reconfigured instance of the same app without re-recording
  from scratch. Drift detection would mean: if a step's role+name target
  can't be found at replay time, that's the signal a tenant's version has
  diverged, not a silent hard failure.

## 5. Escalation & handoff

- (not built yet — Step 5)

## 6. Safety

- Credentials never enter the LLM's context. Claude gets a distinct
  `type_credential` tool that takes a symbolic key ("username", "password"),
  not a value — the tool schema has no field for a raw value at all, so
  it's structurally impossible for Claude to receive or echo one.
  Resolution to the real value happens only inside dispatch(), and never
  appears in the observation string sent back to Claude, so it can't leak
  into the transcript/evidence log either.
- Credentials stored in environment variables via .env (gitignored) for
  this project. Production would use a secrets manager — see Cuts.
- Trade-off discovered in practice: because the model never sees the raw
  credential value, it also can't tell whether it's retrying the exact
  same (wrong) value or something different - every type_credential call
  looks identical to it regardless of whether the underlying value
  changed. Hit this directly: a bad test-account password caused the
  agent to blindly retry login several times before hitting max-steps,
  because it had no way to recognize "I already tried this." Security
  and self-awareness are in tension here; a loop-detection guard (N
  identical consecutive tool calls -> stop/escalate rather than retry)
  would be the fix, planned as part of Step 5's escalation logic rather
  than patched in here.

## 7. Cuts

- Secrets manager (using .env instead) — fine for a take-home, would flag
  as the first thing to change for production.
- (add more as they come up — multi-tenant, desktop support, operator UI
  fidelity, etc.)
