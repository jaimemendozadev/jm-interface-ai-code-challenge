"""
The tool schema handed to Claude, and the dispatcher that executes
whichever tool Claude decides to call against a live BrowserSession.

Kept intentionally small for Step 1: click, type_text, navigate, read_text,
finish. This will grow (e.g. wait_for, dismiss_dialog) once you're building
the full observe-decide-act loop and hitting real runtime conditions.
"""
from __future__ import annotations

from typing import Any

from browser import BrowserSession

TOOLS: list[dict[str, Any]] = [
    {
        "name": "click",
        "description": (
            "Click an interactive element identified by its accessibility "
            "role and accessible name (as shown in the element list)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "ARIA role, e.g. 'button', 'link', 'checkbox'."},
                "name": {"type": "string", "description": "The element's accessible name/label, verbatim."},
            },
            "required": ["role", "name"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into a textbox/searchbox identified by role and accessible name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["role", "name", "text"],
        },
    },
    {
        "name": "navigate",
        "description": "Navigate the browser directly to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "read_text",
        "description": (
            "Read the visible text of an element identified by role and "
            "accessible name. Use this to extract data (e.g. a balance) "
            "once you've navigated to where it's shown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["role", "name"],
        },
    },
    {
        "name": "type_credential",
        "description": (
            "Type a STORED credential into a textbox identified by role and "
            "accessible name. Use this instead of type_text for anything "
            "credential-shaped (login username, password, PIN, etc). You "
            "specify which stored credential to use by its key - you never "
            "see or receive the actual value; it's injected directly by the "
            "system executing your actions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "credential_key": {
                    "type": "string",
                    "description": "Which stored credential to type, e.g. 'username' or 'password'.",
                },
            },
            "required": ["role", "name", "credential_key"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Call this once the goal has been achieved (or is impossible). "
            "Include whatever result/extracted data the goal asked for."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "result": {"type": "string"},
            },
            "required": ["success", "result"],
        },
    },
]


def dispatch(
    session: BrowserSession,
    tool_name: str,
    tool_input: dict[str, Any],
    credentials: dict[str, str] | None = None,
) -> str:
    """Execute one tool call against the live browser and return a plain-text
    observation to feed back to Claude on the next turn.

    `credentials` maps symbolic keys ("username", "password") to their real
    values. It is used ONLY inside this function, for type_credential calls -
    the raw value is never put into the observation string returned to
    Claude, so it never re-enters the conversation, the transcript, or
    anything logged from it."""
    try:
        if tool_name == "click":
            session.click(tool_input["role"], tool_input["name"])
            return "Clicked successfully."
        if tool_name == "type_text":
            session.type_text(tool_input["role"], tool_input["name"], tool_input["text"])
            return "Typed successfully."
        if tool_name == "type_credential":
            key = tool_input["credential_key"]
            if not credentials or key not in credentials:
                return f"Action failed: no stored credential is available for '{key}'."
            session.type_text(tool_input["role"], tool_input["name"], credentials[key])
            return f"Typed the stored '{key}' credential into the field."  # value itself never appears here
        if tool_name == "navigate":
            session.navigate(tool_input["url"])
            return f"Navigated to {tool_input['url']}."
        if tool_name == "read_text":
            text = session.read_text(tool_input["role"], tool_input["name"])
            return f"Read text: {text!r}"
        if tool_name == "finish":
            return "FINISHED"
    except Exception as exc:  # noqa: BLE001 - broad on purpose for step 1
        # In the full agent loop, this string is what Claude sees on its
        # next turn, so it needs to be honest and specific enough for the
        # model to try something different - this is the seed of your
        # error-handling story for REPORT.md.
        return f"Action failed: {exc}"
    return f"Unknown tool: {tool_name}"