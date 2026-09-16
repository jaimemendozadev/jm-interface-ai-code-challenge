"""
The discovery agent: observe -> decide -> act, in a loop.

Runs until the goal is met (Claude calls `finish`), a stopping condition is
hit (max steps, timeout), or Claude gets stuck (returns no tool call -
nothing safe left to try, which is the seed of the human-escalation hook).
Every step gets logged as structured JSON, plus a screenshot of wherever
the run ends up - this is the /evidence/ deliverable for the discovery
run, and the raw material the artifact-recording logic will consume.

Usage (run from the project root):
    uv run python -m discovery.main \\
        --url "https://parabank.parasoft.com/parabank/index.htm" \\
        --goal "Log in, then read and report the checking account balance" \\
        --max-steps 12
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from browser import BrowserSession
from tools import TOOLS, dispatch
from utils import StepLog, describe_elements, build_system_prompt
from safety import load_allowlist, check_domain_allowed, check_action_allowed, AllowlistViolation
from .escalation import request_intervention

load_dotenv()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_STEPS = int(os.getenv("MAX_STEPS", 12))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--evidence-dir", default=None, help="Defaults to evidence/discovery_<timestamp>/"
    )
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in your environment first.")

    credentials = {
        "username": os.environ.get("PARABANK_USERNAME"),
        "password": os.environ.get("PARABANK_PASSWORD"),
    }
    credentials = {k: v for k, v in credentials.items() if v}

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = Path(args.evidence_dir or f"evidence/discovery_{timestamp}")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Safety: check the target domain before ever opening a browser to it.
    # A failure here is a hard, immediate stop - no partial attempt.
    allowlist = load_allowlist()
    check_domain_allowed(args.url, allowlist)

    client = anthropic.Anthropic(api_key=api_key)
    session = BrowserSession(headless=args.headless)
    session.start(args.url)

    system_prompt = build_system_prompt(args.goal, list(credentials.keys()))

    messages: list[dict] = []
    step_logs: list[StepLog] = []
    start_time = time.monotonic()
    outcome = "max_steps_reached"

    try:
        elements = session.snapshot()
        elements_text = describe_elements(elements)
        messages.append({"role": "user", "content": elements_text})
        action_history: list[tuple[str, str]] = []  # (tool_name, json-serialized input), for cycle detection

        for step in range(1, args.max_steps + 1):
            elapsed = time.monotonic() - start_time
            if elapsed > args.timeout_seconds:
                outcome = "timeout"
                break

            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1024,
                system=system_prompt,
                tools=TOOLS,
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                # Claude responded with text only - nothing safe to act on.
                # This used to just stop here; now it's a real escalation
                # instead of giving up.
                text = "".join(b.text for b in response.content if b.type == "text")
                print(f"[step {step}] Claude did not call a tool - escalating to a human.")
                handoff = request_intervention(
                    session, evidence_dir, args.goal, step, f"Claude returned no tool call. Said: {text!r}"
                )
                step_logs.append(
                    StepLog(
                        step, "human_intervention", None,
                        f"NO_TOOL_CALL -> escalated. resumed={handoff.resumed}. notes={handoff.human_notes}",
                        elapsed, elements_text,
                    )
                )
                if not handoff.resumed:
                    outcome = "escalated_and_aborted"
                    break
                # Control is back with automation - re-observe the page a
                # human may have just changed, and keep going rather than
                # blindly continuing from stale state.
                elements = session.snapshot()
                elements_text = describe_elements(elements)
                messages.append(
                    {"role": "user", "content": f"A human operator intervened and reported: {handoff.human_notes}\n\n{elements_text}"}
                )
                continue

            block = tool_use_blocks[0]
            print(f"[step {step}] {block.name}({json.dumps(block.input)})")

            try:
                check_action_allowed(block.name, allowlist)
                if block.name == "navigate":
                    check_domain_allowed(block.input.get("url", ""), allowlist)
            except AllowlistViolation as exc:
                print(f"[step {step}] BLOCKED by allowlist: {exc}")
                step_logs.append(StepLog(step, block.name, block.input, f"ALLOWLIST_VIOLATION: {exc}", elapsed, elements_text))
                outcome = "blocked_by_allowlist"
                break

            observation = dispatch(session, block.name, block.input, credentials=credentials)
            print(f"[step {step}] -> {observation}")
            step_logs.append(StepLog(step, block.name, block.input, observation, elapsed, elements_text))

            if block.name == "finish":
                outcome = "success" if block.input.get("success") else "goal_not_achievable"
                break

            # Cycle/dead-end detection: if the last N actions exactly repeat
            # the N before them (same tool + same input, e.g. login retried
            # identically 3 times), the agent is stuck in a loop it has no
            # way to recognize itself. This now triggers a real escalation
            # instead of just stopping.
            action_history.append((block.name, json.dumps(block.input, sort_keys=True)))
            cycle_len_detected = None
            for cycle_len in (1, 2, 3, 4):
                if (
                    len(action_history) >= 2 * cycle_len
                    and action_history[-cycle_len:] == action_history[-2 * cycle_len : -cycle_len]
                ):
                    cycle_len_detected = cycle_len
                    break

            if cycle_len_detected:
                print(f"[step {step}] Detected a repeating {cycle_len_detected}-action cycle - escalating to a human.")
                handoff = request_intervention(
                    session, evidence_dir, args.goal, step,
                    f"Repeating {cycle_len_detected}-action cycle detected - the agent has no way to tell "
                    f"it's retrying the same thing (see REPORT_NOTES.md's Safety section for why).",
                )
                step_logs.append(
                    StepLog(
                        step, "human_intervention", None,
                        f"STUCK_CYCLE -> escalated. resumed={handoff.resumed}. notes={handoff.human_notes}",
                        elapsed, elements_text,
                    )
                )
                if not handoff.resumed:
                    outcome = "escalated_and_aborted"
                    break
                elements = session.snapshot()
                elements_text = describe_elements(elements)
                messages.append(
                    {"role": "user", "content": f"A human operator intervened and reported: {handoff.human_notes}\n\n{elements_text}"}
                )
                action_history.clear()  # the human likely changed the state - stale cycle history isn't meaningful anymore
                continue

            elements = session.snapshot()
            elements_text = describe_elements(elements)
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": block.id, "content": observation},
                        {"type": "text", "text": elements_text},
                    ],
                }
            )
        else:
            outcome = "max_steps_reached"

    finally:
        # Evidence: structured per-step log (credentials never appear here -
        # see tools.dispatch), plus a screenshot of wherever we ended up,
        # which matters most when outcome != "success".
        log_path = evidence_dir / "discovery_log.jsonl"
        with log_path.open("w") as f:
            for entry in step_logs:
                f.write(json.dumps(asdict(entry)) + "\n")

        screenshot_path = evidence_dir / "final_state.png"
        if session.page is not None:
            session.page.screenshot(path=str(screenshot_path))

        summary_path = evidence_dir / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "goal": args.goal,
                    "url": args.url,
                    "outcome": outcome,
                    "steps_taken": len(step_logs),
                    "timestamp_utc": timestamp,
                },
                indent=2,
            )
        )

        print(f"\nOutcome: {outcome}")
        print(f"Evidence written to: {evidence_dir}/")
        input("Press Enter to close the browser...")
        session.close()


if __name__ == "__main__":
    main()