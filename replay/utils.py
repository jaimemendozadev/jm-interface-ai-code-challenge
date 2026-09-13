from __future__ import annotations
from artifact_schema import Artifact, ActionType, Extraction, ExtractionMethod, Step
from browser import BrowserSession



class ReplayError(Exception):
    """A hard failure during replay - carries enough detail (which step,
    what we expected, what we actually observed) to debug without
    re-running anything. This is the 'hard failure' branch of section
    3.3's three-way result contract (business outcome / recoverable /
    hard failure) - the other two branches are the next increment."""

    def __init__(self, step: int, expected: str, observed: str) -> None:
        self.step = step
        self.expected = expected
        self.observed = observed
        super().__init__(f"step {step}: expected {expected!r}, observed {observed!r}")


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
            session.type_text(target.role, resolved_name, credentials[key])

        elif step.action == ActionType.TYPE_TEXT:
            text = resolve(step.param_key or "", params)
            session.type_text(target.role, resolved_name, text)

        elif step.action == ActionType.CLICK:
            session.click(target.role, resolved_name)

        elif step.action == ActionType.NAVIGATE:
            session.navigate(resolve(step.param_key or "", params))

        elif step.action == ActionType.READ_TEXT:
            text = session.read_text(target.role, resolved_name)
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
    raise ReplayError(-1, f"known extraction method", extraction.method)


def find_output_key_for_step(artifact: Artifact, source_step: int) -> str:
    for step in artifact.steps:
        if step.step == source_step and step.output_key:
            return step.output_key
    raise ReplayError(source_step, "a step with that number and an output_key", "not found in artifact")
