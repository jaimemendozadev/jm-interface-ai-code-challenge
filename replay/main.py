"""
The replay engine - the production execution path (section 3.3).

Given a saved artifact and a set of input parameters, executes it with NO
LLM involvement at all: load the JSON, walk the steps in order, call the
exact same browser.py driver methods discovery used, verify the
checkpoint, and return typed outputs. This is what an AI agent would
invoke in production instead of running a fresh discovery loop every time
- same driver, same Target/role/name concept, zero model calls.

Usage (run from the project root):
    uv run python -m replay.main \\
        --artifact parabank_read_account_balance.v1.json \\
        --param account_number=31437
"""
from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from artifact_schema import Artifact
from browser import BrowserSession
from .utils import run_step, resolve, ReplayError, find_output_key_for_step, apply_extraction

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, help="Path to the artifact JSON file.")
    parser.add_argument("--param", action="append", default=[], help="key=value input parameter, repeatable.")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    params: dict[str, str] = {}
    for kv in args.param:
        key, _, value = kv.partition("=")
        params[key] = value

    credentials = {
        "username": os.environ.get("PARABANK_USERNAME"),
        "password": os.environ.get("PARABANK_PASSWORD"),
    }
    credentials = {k: v for k, v in credentials.items() if v}

    with open(args.artifact) as f:
        artifact = Artifact.model_validate(json.load(f))

    print(f"Replaying {artifact.capability_id} v{artifact.version} ({len(artifact.steps)} steps)")

    session = BrowserSession(headless=args.headless)
    session.start(artifact.base_url)
    step_outputs: dict[str, str] = {}
    result: dict = {"capability_id": artifact.capability_id, "version": artifact.version}

    try:
        for step in artifact.steps:
            print(f"[replay step {step.step}] {step.action.value}")
            run_step(session, step, params, credentials, step_outputs)

        
        # Checkpoint: confirm we actually reached the state we expect,
        # rather than assuming every prior click/type worked just because
        # it didn't raise. This is what stops replay from reporting
        # success on the wrong page.
        cp = artifact.checkpoint
        cp_name = resolve(cp.target.name, params)

        try:
            session.read_text(cp.target.role, cp_name)
        except Exception as exc:
            raise ReplayError(-1, cp.description, str(exc)) from exc

        outputs: dict[str, str] = {}
        for out in artifact.outputs:
            output_key = find_output_key_for_step(artifact, out.source_step)
            raw = step_outputs.get(output_key, "")
            outputs[out.name] = apply_extraction(raw, out.extraction)

        result["outcome"] = "success"
        result["outputs"] = outputs

    except ReplayError as exc:
        # Everything caught here is currently classified as a hard failure.
        # Distinguishing "expected business outcome" (e.g. invalid
        # credentials) from this is the next increment - it needs replay
        # to recognize specific known error states, not just catch
        # exceptions.
        result["outcome"] = "hard_failure"
        result["failed_step"] = exc.step
        result["expected"] = exc.expected
        result["observed"] = exc.observed

    finally:
        print(json.dumps(result, indent=2))
        input("\nPress Enter to close the browser...")
        session.close()


if __name__ == "__main__":
    main()