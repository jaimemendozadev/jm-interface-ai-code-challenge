from dataclasses import dataclass

@dataclass
class StepLog:
    step: int
    tool_name: str | None
    tool_input: dict | None  # safe to log as-is: type_credential never carries a raw value
    observation: str
    elapsed_seconds: float


def describe_elements(elements: list) -> str:
    lines = "\n".join(
        f"- role={e.role!r} name={e.name!r}" + (f" value={e.value!r}" if e.value else "")
        for e in elements
    )
    return f"Currently interactable elements:\n{lines}" if lines else "No interactable elements found."


def build_system_prompt(goal: str, credential_keys: list[str]) -> str:
    credential_note = ""
    if credential_keys:
        keys = ", ".join(credential_keys)
        credential_note = (
            f"\nStored credentials are available for: {keys}. Use the "
            f"type_credential tool for these - you will not see their "
            f"actual values, only reference them by key.\n"
        )
    return (
        f"You are operating a real web application to accomplish a goal.\n\n"
        f"Goal: {goal}\n"
        f"{credential_note}\n"
        f"On each turn you'll be shown the currently interactable elements "
        f"on the page. Decide the single next action that makes progress "
        f"toward the goal, and call exactly one tool. When the goal is "
        f"fully achieved (or you determine it's impossible), call finish "
        f"with the result. If an action fails or produces an unexpected "
        f"result, read the observation carefully and adjust - don't repeat "
        f"the exact same failing action."
    )