"""
The capability artifact schema.

This is the thing a successful discovery run gets distilled into (see
REPORT_NOTES.md section 2 for why "distilled," not "verbatim transcript").
It's the contract between three audiences at once:
  - a human reviewer, deciding whether to trust/approve this capability
  - the replay engine, which executes it with zero LLM involvement
  - a calling AI agent, which needs to know what to pass in and what it
    gets back, without reading any of our Python

Every field exists because one of those three audiences needs it.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class LocatorStrategy(str, Enum):
    """How a target element is identified. This is the single most
    important robustness decision in the whole schema - see the module
    docstrings in browser.py for the full reasoning.

    ACCESSIBLE_NAME: role + accessible name, resolved via the browser's own
        accessibility tree (get_by_role). Preferred - semantic, survives
        markup/CSS changes, and the same concept exists on desktop apps via
        OS accessibility APIs (see REPORT_NOTES.md section 4).
    ATTRIBUTE_FALLBACK: role + a raw HTML attribute (name/id/placeholder),
        used only when the element has no accessible name at all - the
        "label is a <p> tag, not a real <label>" case. Explicitly a lower-
        confidence strategy: it survives markup changes worse, because it
        depends on an attribute the app author didn't intend as an API.
    """

    ACCESSIBLE_NAME = "accessible_name"
    ATTRIBUTE_FALLBACK = "attribute_fallback"


class Target(BaseModel):
    """Identifies one element on the page."""

    role: str = Field(description="ARIA role, e.g. 'button', 'textbox', 'row'.")
    name: str = Field(
        description=(
            "Accessible name to match. May contain a {parameter_name} "
            "placeholder - e.g. name='{account_number}' for a row that "
            "needs substituting with the real account number at replay time."
        )
    )
    locator_strategy: LocatorStrategy
    attribute: str | None = Field(
        default=None,
        description="Only set when locator_strategy=attribute_fallback: which HTML attribute ('name', 'id', 'placeholder') identifies it.",
    )


class ActionType(str, Enum):
    CLICK = "click"
    TYPE_TEXT = "type_text"
    TYPE_CREDENTIAL = "type_credential"
    NAVIGATE = "navigate"
    READ_TEXT = "read_text"
    READ_PAGE_TEXT = "read_page_text"


class Step(BaseModel):
    """One action in the ordered sequence. Mirrors the tool schema in
    tools.py deliberately - a step is "the same shape as a tool call
    Claude made," which is what makes converting a discovery transcript
    into an artifact mostly mechanical rather than a rewrite."""

    step: int
    action: ActionType
    target: Target | None = Field(default=None, description="Omitted for navigate, which acts on the whole page.")
    param_key: str | None = Field(
        default=None,
        description=(
            "For type_text: literal text to type, OR a {parameter_name} "
            "placeholder. For type_credential: which credential key "
            "('username'/'password'). For navigate: the URL (may contain "
            "a {parameter_name} placeholder)."
        ),
    )
    output_key: str | None = Field(
        default=None, description="If this step's result should be captured as a named output, the key to store it under."
    )


class ParameterType(str, Enum):
    STRING = "string"
    CREDENTIAL = "credential"  # never appears in the artifact JSON itself - resolved by the caller at invocation


class InputParameter(BaseModel):
    name: str
    type: ParameterType
    description: str
    required: bool = True


class ExtractionMethod(str, Enum):
    """How to pull a specific output value out of a step's raw captured
    text. Kept as a small, closed set of machine-executable operations -
    not free text - because the replay engine has to actually run this,
    not just display it to a human."""

    WHOLE = "whole"  # the output IS the step's raw text, unmodified
    SPLIT = "split"  # split on a delimiter, take one index


class Extraction(BaseModel):
    method: ExtractionMethod = ExtractionMethod.WHOLE
    delimiter: str | None = Field(default=None, description="Required when method=split, e.g. '\\t'.")
    index: int | None = Field(default=None, description="Required when method=split - which part to keep.")


class OutputField(BaseModel):
    name: str
    type: str = Field(description="e.g. 'string', 'currency'")
    description: str = Field(description="Human-readable explanation - for a reviewer, not for replay to execute.")
    source_step: int = Field(description="Which step's output_key this comes from.")
    extraction: Extraction = Field(default_factory=Extraction)


class Checkpoint(BaseModel):
    """What must be true for the run to count as successful - the thing
    that stops replay from silently reporting success on a page that
    doesn't actually mean what we think it means."""

    description: str
    target: Target


class Artifact(BaseModel):
    capability_id: str
    version: int
    description: str
    surface_type: str = Field(description="'web', 'windows_desktop', 'macos_desktop' - see REPORT_NOTES.md section 4.")
    base_url: str
    input_parameters: list[InputParameter]
    steps: list[Step]
    outputs: list[OutputField]
    checkpoint: Checkpoint