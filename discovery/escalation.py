"""
Human escalation & handoff (section 3.6).

When the agent can't safely proceed, this pauses automation and hands the
SAME live browser session - not a fresh one - to a human. They operate
the actual visible window directly, then control comes back to the
automated loop.

Scope note from the brief: a full real-time co-browsing console is
explicitly out of scope. What has to be real is the handoff mechanism and
control-transfer model, not UI polish. So the "operator console" here is
deliberately minimal: this terminal process. It's real in the sense that
matters - the human is genuinely operating the exact browser instance the
automation was using, with the same cookies, same login session, same
page state - not a mock of one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from browser import BrowserSession


@dataclass
class HandoffResult:
    resumed: bool
    human_notes: str
    screenshot_before: str
    screenshot_after: str


def request_intervention(
    session: BrowserSession,
    evidence_dir: Path,
    goal_or_capability: str,
    step: int,
    reason: str,
) -> HandoffResult:
    """Pause automation and hand the live session to a human.

    Blocks on real terminal input() - deliberately, since there's no
    separate operator process to signal in this minimal version. The
    person running the script IS the operator, and control genuinely
    transfers to them: while this call is blocked, nothing in the
    automation touches the browser at all - the human has it, fully,
    until they type something back.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    before_path = evidence_dir / f"intervention_{timestamp}_before.png"
    if session.page is not None:
        session.page.screenshot(path=str(before_path))

    print("\n" + "=" * 60)
    print("HUMAN INTERVENTION REQUESTED")
    print(f"  Goal/capability : {goal_or_capability}")
    print(f"  Stopped at step : {step}")
    print(f"  Reason          : {reason}")
    print(f"  Screenshot      : {before_path}")
    print("=" * 60)
    print(
        "Control is now yours - the browser window in front of you is "
        "the SAME live session the automation was using, not a fresh "
        "one. Do whatever's needed manually, then come back here."
    )

    response = input("Notes on what you did (or type 'abort' to stop the run): ").strip()

    if response.lower() == "abort":
        return HandoffResult(
            resumed=False,
            human_notes="(operator chose to abort)",
            screenshot_before=str(before_path),
            screenshot_after="",
        )

    after_path = evidence_dir / f"intervention_{timestamp}_after.png"
    if session.page is not None:
        session.page.screenshot(path=str(after_path))

    print("Control returned to automation - resuming from the current page state.\n")
    return HandoffResult(
        resumed=True,
        human_notes=response or "(no notes provided)",
        screenshot_before=str(before_path),
        screenshot_after=str(after_path),
    )