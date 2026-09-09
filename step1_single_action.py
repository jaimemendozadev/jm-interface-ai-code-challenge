"""
STEP 1 - prove the plumbing works.

Takes ONE accessibility snapshot of a live page, hands it to Claude along
with a goal and the tool definitions, and executes exactly ONE tool call
Claude decides on. This is not the full agent loop yet (that's step 2) -
it's the smallest possible slice that proves:

    browser -> accessibility tree -> Claude -> one real action on the page

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python step1_single_action.py \\
        --url "https://parabank.parasoft.com/parabank/index.htm" \\
        --goal "Click the link to log in to Parabank"

Run it a few times with different --goal values against different pages.
Once you trust this works, step 2 wraps this in a while-loop that keeps
going until the goal is met or a stopping condition is hit.
"""
from __future__ import annotations

import argparse
import json
import os

import anthropic

from browser import BrowserSession
from tools import TOOLS, dispatch

MODEL = "claude-sonnet-5"


def build_prompt(goal: str, elements: list) -> str:
    element_lines = "\n".join(
        f"- role={e.role!r} name={e.name!r}" + (f" value={e.value!r}" if e.value else "")
        for e in elements
    )
    return (
        f"Goal: {goal}\n\n"
        f"Here are the currently interactable elements on the page, identified "
        f"by their accessibility role and accessible name:\n{element_lines}\n\n"
        f"Decide the single next action that makes progress toward the goal. "
        f"Call exactly one tool."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Page to load first.")
    parser.add_argument("--goal", required=True, help="Natural-language goal for this one step.")
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser window.")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in your environment first.")

    client = anthropic.Anthropic(api_key=api_key)
    session = BrowserSession(headless=args.headless)
    session.start(args.url)

    try:
        elements = session.snapshot()
        print(f"[snapshot] found {len(elements)} interactable elements")
        for e in elements:
            print(f"   - {e.role}: {e.name!r}")

        prompt = build_prompt(args.goal, elements)

        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            tools=TOOLS,
            messages=[{"role": "user", "content": prompt}],
        )

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            print("[claude] did not call a tool. Raw response content:")
            print(response.content)
            return

        block = tool_use_blocks[0]
        print(f"[claude] wants to call: {block.name}({json.dumps(block.input)})")

        observation = dispatch(session, block.name, block.input)
        print(f"[result] {observation}")

    finally:
        input("\nPress Enter to close the browser...")
        session.close()


if __name__ == "__main__":
    main()
