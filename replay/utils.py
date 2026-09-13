"""
Shared helpers for the replay engine: error type, placeholder resolution,
per-step execution, extraction, and output-key lookup.
"""
from __future__ import annotations

from artifact_schema import Artifact, ActionType, Extraction, ExtractionMethod, Step
from browser import BrowserSession


class ReplayError(Exception):
    """A HARD failure during replay - something unexpected about the app
    itself (a non-parameterized target vanished, an unrecognized page
    state). Carries enough detail (which step, what we expected, what we
    actually observed) to debug without re-running anything."""

    def __init__(self, step: int, expected: str, observed: str) -> None:
        self.step = step
        self.expected = expected
        self.observed = observed
        super().__init__(f"step {step}: expected {expected!r}, observed {observed!r}")


class BusinessOutcomeError(Exception):
    """A legitimate, expected result that just happens to not be success -
    e.g. 'no account with that number'. The distinguishing factor: the
    failing target's name was built from a CALLER-SUPPLIED parameter, so
    the failure is about the data the caller gave us, not about the
    system being broken. Section 3.3 / the glossary calls conflating this
    with a hard failure "the most common design mistake here" - this
    class exists specifically so main.py can report the two differently."""

    def __init__(self, step: int, outcome_type: str, message: str) -> None:
        self.step = step
        self.outcome_type = outcome_type
        self.message = message
        super().__init__(message)


def resolve(template: str, params: dict[str, str]) -> str:
    """Substitute {param_name} placeholders in a target name or param_key.
    Left untouched (no-op) if the template has no braces."""
    try:
        return template.format(**params)
    except KeyError as exc:
        raise ReplayError(step=-1, expected=f"parameter {exc}", observed="not provided in --param") from exc


def run_step(
    session: BrowserSession,
    step: Step,
    params: dict[str, str],
    credentials: dict[str, str],
    step_outputs: dict[str, str],
) -> None:
    target = step.target
    resolved_name = resolve(target.name, params) if target else None
    strategy = target.locator_strategy.value if target else None
    attribute = target.attribute if target else None

    try:
        if step.action == ActionType.TYPE_CREDENTIAL:
            key = step.param_key or ""
            if key not in credentials:
                raise ReplayError(step.step, f"credential '{key}' available", "not provided")
            # No tools.dispatch() here on purpose: that wrapper exists to
            # keep secrets out of an LLM's context. There's no LLM in this
            # process at all, so calling browser.py directly is fine - the
            # security boundary that matters (never let Claude see this
            # value) simply doesn't apply during replay.
            session.type_text_target(target.role, resolved_name, credentials[key], strategy, attribute)

        elif step.action == ActionType.TYPE_TEXT:
            text = resolve(step.param_key or "", params)
            session.type_text_target(target.role, resolved_name, text, strategy, attribute)

        elif step.action == ActionType.CLICK:
            session.click_target(target.role, resolved_name, strategy, attribute)

        elif step.action == ActionType.NAVIGATE:
            session.navigate(resolve(step.param_key or "", params))

        elif step.action == ActionType.READ_TEXT:
            text = session.read_text_target(target.role, resolved_name, strategy, attribute)
            if step.output_key:
                step_outputs[step.output_key] = text

        elif step.action == ActionType.READ_PAGE_TEXT:
            text = session.read_page_text(step.param_key)
            if step.output_key:
                step_outputs[step.output_key] = text

    except ReplayError:
        raise
    except Exception as exc:
        target_desc = f"{target.role}='{resolved_name}'" if target else "(whole page)"
        # The rule: if this target's name was built from a parameter the
        # caller supplied (contains "{"), a failure to find it is about
        # THEIR data, not our system - a business outcome. A fixed,
        # non-parameterized target failing (e.g. the Log In button
        # vanishing) means something about the app itself is wrong - a
        # real hard failure.
        if target and "{" in target.name and step.action in (ActionType.READ_TEXT, ActionType.CLICK):
            raise BusinessOutcomeError(
                step.step,
                outcome_type="not_found",
                message=f"No {target.role} matching {target.name}='{resolved_name}' - the record may not exist.",
            ) from exc
        raise ReplayError(step.step, target_desc, str(exc)) from exc


def apply_extraction(raw: str, extraction: Extraction) -> str:
    if extraction.method == ExtractionMethod.WHOLE:
        return raw
    if extraction.method == ExtractionMethod.SPLIT:
        parts = raw.split(extraction.delimiter or "\t")
        index = extraction.index if extraction.index is not None else 0
        if index >= len(parts):
            raise ReplayError(-1, f"split result with index {index}", f"only {len(parts)} parts in {raw!r}")
        return parts[index].strip()
    raise ReplayError(-1, "known extraction method", extraction.method)


def find_output_key_for_step(artifact: Artifact, source_step: int) -> str:
    for step in artifact.steps:
        if step.step == source_step and step.output_key:
            return step.output_key
    raise ReplayError(source_step, "a step with that number and an output_key", "not found in artifact")