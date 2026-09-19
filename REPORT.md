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

<br />

A few architecture choices made ahead of time:

- **No framework (LangChain/LangGraph).** The agent loop is a plain
  `while`/`for` calling the Anthropic API directly, appending each
  assistant `tool_use` and its `tool_result` to a growing `messages` list.
  This is fully legible — every mechanic in the loop is something I can
  point to and explain — rather than something a framework does for me
  that I'd have to reverse-engineer under interview questioning.<br />

- **Model/tooling:** Claude Sonnet via direct tool-use, `disable_parallel_tool_use=True`
  (exactly one action decided per turn — simpler to reason about and log,
  at the cost of more round trips for multi-step goals). Locator strategy —
  the single decision everything else depends on — is accessibility
  role + accessible name, not CSS selectors or screenshot coordinates; see
  Section 3 for why.<br />

- **CLI, not a web service.** The deliverable asks for exact commands to
  run, and the agent loop is inherently multi-turn and occasionally
  long-running (including pausing indefinitely for human input during
  escalation) — a poor fit for a synchronous HTTP request/response cycle.

<br>

## 2. Artifact schema

When we run `uv run python -m discovery.main` from the root folder, we end up
creating an `discovery_log.jsonl` file in `/evidence` that serves as the basis
for the finalized `artifact.json` that's stored in the `/artifacts` folder.

> <strong>IMPORTANT</strong>: It bears stating we use Claude to look at the
> `discovery_log.jsonl` file and create the final `artifact.json` file.
> Unfortunately there's no automated process for creating the file after
> running the `discovery.main` script, at least not for v1 of this challenge.

On the initial traversal/step (and subsequent steps) of the Parabank website, we
leverage the Playwright BrowserSession class to create a session object that allows
us/the script/llm to "see" all the elements available on the page at the time of
invocation. What happens next is we create a string description of all the elements
on the current session/page and identify those elements by their `role`, `name`, and
`value`.

Knowing what's available on the current session/page, we add this information as a
`User` message that gets added to a list of messages that's sent to the Claude
Model so it see what's on the current page. The model then decides what's the next
step and actions it should take to achieve the target goal.

Next steps for the model could be a tool actions like a `type_credential` or `click`
for example. Whatever the model decided to do, we record that step in a `step_logs`
list. We then repeat the process again until the model achieves the target under
the max steps it's allowed to take. If it achieves the goal, we take the `step_logs`
and create the final `discovery_log.jsonl` that serves as the basis for our reusable
capability in `/replay/main.py`.

When we run the final `artifact.json` file in `/replay/main.py` it has the following
the 4 important fields:

```
{
  "input_parameters": [],
  "steps": [],
  "outputs": [],
  "checkpoint": {}
}
```

The `input_parameters` are a list of described arguments with their specified types
that are needed to run the `artifact` correctly at the time of invocation.

The `steps` field contains a list of `step` objects that mirror the steps that were
recorded during the initial running of the `discovery` script. Each numbered step
tells you what the model did as an `action`, what the `target` of that step was.

So for example, if the first step was the action of `type_credential`, the model was
knew that based on the current elements of the curren page it was on, it needed to
find an element with a `role` of textbox and it could find it by using the
`locator_strategy` of `attribute_fallback`.

Essentially for every step, we were going to perform an `action` on a `target`
element that we had to find by using the `locator_strategy` of finding that element.

The `outputs` field is a list of what you get back. Each one points at which step
produced it (source_step) and how to pull the specific value out of that step's
raw text (extraction).

## 3. Determinism & error handling

Replay (`replay/`) executes an artifact's steps in order with zero model
calls, using target-aware driver methods (`click_target`/
`type_text_target`/`read_text_target`) that resolve a locator directly
from the artifact's recorded strategy — no dependency on ever having
"looked around" the page the way discovery's snapshot-driven methods do.
This split isn't incidental duplication: discovery is blind and
exploring; replay already knows exactly where everything is.

The result contract distinguishes three outcomes, and the rule for
telling them apart is simple and generalizes: **if a failing target's
name was built by substituting a caller-supplied parameter, the failure
is about their data, not our system — a business outcome. If a fixed,
non-parameterized target fails, something about the app itself changed
unexpectedly — a hard failure.** This was validated against two real
failures, not designed in the abstract: replaying with a stale account
number correctly produced `{"outcome": "business_outcome", "outcome_type":
"not_found", ...}`, while a simulated failure on the non-parameterized
Log In button correctly produced `hard_failure`. A checkpoint (re-reading
the same record that produced the primary output) confirms the run
actually reached the expected end state rather than assuming every prior
click worked because it didn't raise.

The accessible-name/attribute-fallback locator split doubles as the
runtime-error story the brief emphasizes over layout drift: Parabank's
login fields use a `<p>` tag as a visual label instead of a real
`<label>`/`aria-label`, so the browser computes no accessible name for
them at all. Discovery's first real run surfaced this directly — the
fields were simply invisible to a pure accessible-name pass — which is
what motivated the fallback pass (raw `name`/`id`/`placeholder`
attribute) as a deliberately lower-priority second strategy.

## 4. Heterogeneity & multi-tenant

The seam between "how we perceive/act on a surface" and "the recorded
flow" is the artifact/driver split described above. An artifact's steps
are pure data — `{role, name, locator_strategy}` — with no Playwright or
DOM concept anywhere in them. `browser.py` is one driver implementation,
backed by Playwright and the accessibility tree. A desktop driver would
expose the identical method signatures (`click`/`type_text`/`read_text`)
backed by OS accessibility APIs instead — UI Automation on Windows,
AXUIElement on macOS, AT-SPI on Linux — all of which expose the same
role+name concept a screen reader relies on. This is precisely why
role+name was chosen over a Playwright-specific selector in the first
place: it's the one identification concept that's genuinely
cross-platform, not a web-only convenience. The unlabeled-element
fallback has a direct desktop analogue too — old Win32/MFC apps routinely
expose empty or garbage accessibility names, and the same fallback shape
(automation ID, control class, position) would apply there.

For multi-tenant reuse (design only, not implemented): since many
tenants run the same underlying vendor product re-skinned, an artifact
recorded against a "base" tenant should be parameterizable enough
(base URL, and a per-tenant locator override table for cases where a
re-branded instance genuinely changed a control's name) to replay against
other tenants without re-recording from scratch. Drift detection follows
directly from the existing error contract: if a step's target can't be
found at replay time on a given tenant, that failure signal — not a
silent success or an opaque crash — is exactly the mechanism that would
flag "this tenant's version has diverged" for review.

## 5. Escalation & handoff

Two independent triggers detect "stuck": Claude returning a turn with no
tool call at all, and a cycle detector comparing the last _N_ actions
against the _N_ before them (_N_ = 1 to 4) for an exact match — the
agent repeating itself with no way to know it (see Section 6 for why it
can't tell on its own).

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
retried the same wrong password. The second attempt corrected this by
typing real credentials directly into the browser window, and the run
resumed and completed successfully. The honest boundary: the seam for
handoff is "whatever's reachable through the browser," not "whatever's
true about the process."

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
allowlist checked before every action executes. A known, real limitation:
it gates by generic action _type_ (`click`/`type_text`/`navigate`), not
by semantic intent — it cannot distinguish "click the Log In button" from
"click the Confirm Transfer button." `risky_actions` is empty because
this capability set is read-only; a real funds-transfer capability would
need risk classification at a finer grain (e.g. by target-name pattern)
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

- **Fine-grained/semantic risk classification.** The allowlist currently
  gates by action type only, not by what a click actually does — see
  Section 6.<br />
- **More robust stuck-detection.** Exact-cycle-matching missed a run that
  was genuinely stuck but varied its tactics — see Section 5. Tracking
  repeated failure _types_ or distinct pages visited would catch this.<br />

- **Multi-tenant reuse and desktop support** are designed (Sections 4)
  but not implemented — the brief explicitly scopes this as a design
  answer, not a build requirement.<br />

- **Operator UI fidelity.** The handoff mechanism and control-transfer
  model are real; the "console" is this terminal, not a dedicated
  interface, per the brief's own scope note.<br />
