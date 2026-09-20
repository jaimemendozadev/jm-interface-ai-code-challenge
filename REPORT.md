# REPORT.md — Computer-Use Automation System

## 1. Architecture

The system is two independent CLI entrypoints sharing a common core, not
a service. `discovery/main.py` runs the LLM-driven observe→decide→act
loop against a live browser; `replay/main.py` executes a saved artifact
deterministically, with no LLM involved at all.

Both share the following dependencies, each file is one implementation of concern and not two drifting copies:

- `browser.py` (the Playwright driver);
- `tools.py`/`artifact_schema.py` (the action vocabulary); and
- `safety.py` (allowlist enforcement).

A few architecture choices made ahead of time:

- **No framework (LangChain/LangGraph).** The agent loop is a plain
  `while`/`for` calling the Anthropic API directly, appending each
  assistant `tool_use` and its `tool_result` to a growing `messages` list.
  This is fully legible — every mechanic in the loop is something I can
  point to and explain — rather than something a framework does for me
  that I'd have to reverse-engineer under interview questioning.

- **Model/tooling:** Claude Sonnet via direct tool-use, `disable_parallel_tool_use=True`
  (exactly one action decided per turn — simpler to reason about and log,
  at the cost of more round trips for multi-step goals). Locator strategy —
  the single decision everything else depends on — is accessibility
  role + accessible name, not CSS selectors or screenshot coordinates; see
  Section 3 for why.

- **CLI, not a web service.** The deliverable asks for exact commands to
  run, and the agent loop is inherently multi-turn and occasionally
  long-running (including pausing indefinitely for human input during
  escalation) — a poor fit for a synchronous HTTP request/response cycle.

## 2. Artifact schema

When we run `uv run python -m discovery.main` from the root folder, we end up
creating a `discovery_log.jsonl` file in `/evidence` that serves as the basis
for the finalized `artifact.json` that's stored in the `/artifacts` folder.

> <strong>IMPORTANT</strong>: It bears stating we use Claude to look at the
> `discovery_log.jsonl` file and create the final `artifact.json` file.
> Unfortunately there's no automated process for creating the file after
> running the `discovery.main` script, at least not for v1 of this challenge.

Without getting into the nitty gritty details, let's just say that when we
start the script, we start at a given `url` website. We enter a loop that
terminates at the `max-steps` number if we can't achieve the goal. When we
enter the loop, every `step` in that loop is essentially the script/llm
navigating through the website `url` we specified.

How does the script/llm navigate the website at each `step`? The script
leverages Playwright to "see" all the elements available on the page at
the time of invocation. Once the script uses Playwright to see all the
elements available on the current page, it then creates a string
description of all the elements on the page and identifies those elements
by their `role`, `name`, and `value`.

That information gets sent to the model at each step of our loop so it
can see what's on the current page and then decide what's the next action
it should take to achieve the target goal.

Whatever next step the model decides to do, we record that step in a
`step_logs` list. We then repeat the process again until the model
achieves the target `goal` or we hit a stopping condition (max steps,
timeout, or an escalation the operator chooses to abort).

Regardless of whether the goal is achieved, `step_logs` gets written to
`discovery_log.jsonl` in a `finally` block — evidence is preserved even on
failure or escalation, not only on success. When a run _does_ succeed,
that log is what gets reviewed (with Claude's help, as noted above) to
hand-author the artifact that `/replay/main.py` later consumes.

The final `artifact.json` file that `/replay/main.py` consumes has these
4 important fields:

```
{
  "input_parameters": [],
  "steps": [],
  "outputs": [],
  "checkpoint": {}
}
```

The `input_parameters` are a list of described arguments with their
specified types that are needed to run the `artifact` correctly at
the time of invocation.

The `steps` field contains a list of `step` objects that mirror the
steps that were recorded during the website navigating loop in the
`discovery` script. Each numbered step tells you what the model did
as an `action` and what the `target` of that step was.

So for example, if the first step was the action of `type_credential`,
the model knew that based on the current elements of the current page
it was on, it needed to find an element with a `role` of textbox and
it could find it by using the `locator_strategy` of `attribute_fallback`.

There are two locator strategies, and which one gets used matters:
`accessible_name` (role + the browser's own accessible name for an
element) is the default, preferred strategy — it's semantic, survives
markup changes, and is the same concept available on desktop apps
through OS accessibility APIs. `attribute_fallback` is a deliberately
lower-confidence backup, used only when an element has no accessible
name at all (see Section 3 for the real legacy-markup bug that made
this fallback necessary in the first place).

Essentially, every step performs an `action` on a `target` element,
located using whichever `locator_strategy` that step specifies.

The `outputs` field is a list of the results that you should get back.
Each output object points at which step produced it (`source_step`) and
how to pull the specific value out of that step's raw text (`extraction`).

Finally, the `checkpoint` field is one more read performed after all the
steps finish, confirming the run actually landed where it was supposed to
— not just that no step happened to throw an exception along the way. A
click can technically "succeed" while still leaving you on the wrong
page; the checkpoint is what catches that.

## 3. Determinism & error handling

Replay (`/replay`) executes an artifact's steps in order with zero model
calls, using target-aware driver methods (`click_target`/
`type_text_target`/`read_text_target`) that resolve a locator directly
from the artifact's recorded strategy. Running
`uv run python -m replay.main` from the root folder means we're no longer
blindly exploring a page to reach a goal — replay already knows exactly
where everything is.

When something goes wrong, the system sorts it into one of two outcomes,
using one simple rule: if the failing target's name came from a
parameter the caller supplied (like an account number), the failure is
about their data, not our system — **a "business outcome."** If a fixed
target that's always supposed to be there fails, something about the
app itself broke — **a "hard failure."**

- **Business outcome, seen for real:** replaying with a stale account
  number correctly produced
  `{"outcome": "business_outcome", "outcome_type": "not_found", ...}`
  (see `evidence/replay_20260913T210526Z/`).
- **Hard failure, verified by simulation:** a simulated failure on the
  Log In button — a fixed target, not a parameter — correctly produced
  `hard_failure` instead.

The checkpoint applies this exact same rule one more time: it's one more
read performed after all the steps finish, and if a parameterized
checkpoint target can't be found, that's classified as a business
outcome too — not a crash. Either way, its real job is confirming the
run actually reached the expected end state, rather than assuming every
prior click worked just because it didn't throw an error.

Separately from that outcome taxonomy, discovery's first real run also
surfaced a genuine runtime surprise — not layout drift, since nothing
changed over time, but something only discoverable by actually running
against the live page: Parabank's login fields use a `<p>` tag as a
visual label instead of a real `<label>`/`aria-label`, so the browser
computes no accessible name for them at all. That's what motivated the
attribute-fallback locator strategy (Section 2) as a deliberately
lower-priority second option.

## 4. Heterogeneity & multi-tenant

The seam between "how we perceive/act on a surface" and "the recorded
flow" is the artifact/driver split described above.

An artifact's steps are pure data — `{role, name, locator_strategy}` —
with no Playwright or DOM concept anywhere in them.

`browser.py` is one driver implementation, backed by Playwright and the
accessibility tree. A desktop driver would expose the identical method
signatures (`click`/`type_text`/`read_text`) backed by OS accessibility
APIs instead — UI Automation on Windows, AXUIElement on macOS, AT-SPI
on Linux — all of which expose the same role+name concept a screen reader
relies on.

This is precisely why `role+name` was chosen over a Playwright-specific
selector in the first place: it's the one identification concept that's
genuinely cross-platform, not a web-only convenience.

The unlabeled-element fallback has a direct desktop analogue too — old
Win32/MFC apps routinely expose empty or garbage accessibility names, and
the same fallback shape (automation ID, control class, position) would
apply there.

For multi-tenant reuse (design only, not implemented): since many
tenants run the same underlying vendor product re-skinned, an artifact
recorded against a "base" tenant should be parameterizable enough
(base URL, and a per-tenant locator override table for cases where a
re-branded instance genuinely changed a control's name) to replay against
other tenants without re-recording from scratch.

Drift detection follows directly from the existing error contract: if
a step's target can't be found at replay time on a given tenant, that
failure signal — not a silent success or an opaque crash — is exactly
the mechanism that would flag "this tenant's version has diverged"
for review.

## 5. Escalation & handoff

Two independent triggers detect "stuck":

- Claude returning a turn with no tool call at all, and
- a cycle detector comparing the last _N_ actions against the _N_ before
  them (_N_ = 1 to 4) for an exact match — the agent repeating itself
  with no way to know it (see Section 6 for why it can't tell on its own).

The operator "console" is deliberately minimal, per the brief's own scope
note that a full co-browsing UI is out of scope: it's this terminal
process. The live Playwright browser window never closes or restarts;
`input()` blocks automation entirely while a human has sole, real control
of that exact window — not a fresh session. Typed notes plus a
before/after screenshot pair get written to their own
`interventions.json` per run, separate from the ordinary step log, since
"what a human did" is categorically different information from "what the
agent did."

Two real limitations came from actually running this, not from
theorizing about it:

A human intervening can only fix things reachable through the browser UI
itself. The first live test tried editing `.env` mid-pause, expecting the
correction to take effect — it didn't, because credentials are read from
the environment exactly once at process start, and the resumed run
retried the same wrong password.

The second attempt corrected this by typing real credentials directly
into the browser window, and the run resumed and completed successfully.
The honest boundary: the seam for handoff is "whatever's reachable through
the browser," not "whatever's true about the process."

Separately, the cycle detector only catches an _exact_ repeat of a
fixed-length window. A run that was still fundamentally stuck (still
failing to log in) but varied its exact actions between attempts — an
extra `navigate`, different `read_text` guesses — evaded detection
entirely and ground to `max_steps` instead of escalating a second time.
Named as a limitation rather than patched under time pressure; see Cuts.

## 6. Safety

One shared `allowlist.json`, enforced by `safety.py` before either
discovery or replay ever opens a browser: an explicit domain allowlist
checked at startup and before every `navigate`, and an action-type
allowlist checked before every action executes.

A known, real limitation: it gates by generic action _type_
(`click`/`type_text`/`navigate`), not by semantic intent — it cannot
distinguish "click the Log In button" from "click the Confirm Transfer
button." `risky_actions` is empty because this capability set is
read-only; a real funds-transfer capability would need risk
classification at a finer grain (e.g. by target-name pattern)
before that list would gate anything meaningful.

Credentials never enter the LLM's context at all. `type_credential` takes
a symbolic key (`"username"`/`"password"`), not a value — the tool
schema has no field for a raw value, so it's structurally impossible for
Claude to receive or echo one; resolution happens only inside the
dispatcher and never appears in anything sent back to the model or
written to a log. Stored in environment variables for this project (a
secrets manager is the production equivalent — see Cuts).

A trade-off discovered in practice, not predicted in advance: because the
model never sees the raw credential value, it also can't tell whether
it's retrying the exact same wrong value or something different — every
`type_credential` call looks identical to it regardless of what actually
changed underneath. This is exactly what caused the blind login retries
that motivated the cycle detector in the first place. Security and
self-awareness are in real tension here.

## 7. Cuts

- **Automatic artifact-recording from a successful discovery transcript
  isn't built.** The artifact is currently hand-authored by reviewing
  `discovery_log.jsonl` — a generalizable version would need code that
  detects the minimal successful path and serializes it, rather than a
  human making that judgment call each time.
- **Fine-grained/semantic risk classification.** The allowlist currently
  gates by action type only, not by what a click actually does — see
  Section 6.
- **More robust stuck-detection.** Exact-cycle-matching missed a run that
  was genuinely stuck but varied its tactics — see Section 5. Tracking
  repeated failure _types_ or distinct pages visited would catch this.
- **Multi-tenant reuse and desktop support** are designed (Section 4)
  but not implemented — the brief explicitly scopes this as a design
  answer, not a build requirement.
- **Operator UI fidelity.** The handoff mechanism and control-transfer
  model are real; the "console" is this terminal, not a dedicated
  interface, per the brief's own scope note.
