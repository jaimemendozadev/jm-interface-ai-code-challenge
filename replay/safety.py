"""
Safety allowlist enforcement (section 3.4).

Two things get checked, at the one seam both discovery and replay share
before they ever touch the browser: which DOMAINS the agent is allowed to
navigate to, and which ACTION TYPES it's allowed to perform at all.
Loaded from a config file rather than hardcoded, so it's the kind of
thing a human reviewer or ops team could edit without touching code.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel


class AllowlistViolation(Exception):
    """Raised the instant a navigation or action falls outside policy.
    Deliberately a HARD stop, not a warning - an agent that's about to
    act outside its permitted scope shouldn't get to try anyway and see
    what happens."""


class AllowlistConfig(BaseModel):
    allowed_domains: list[str]
    allowed_actions: list[str]
    risky_actions: list[str] = []  # requires extra confirmation - see check_action_allowed


def load_allowlist(path: str = "allowlist.json") -> AllowlistConfig:
    with open(path) as f:
        return AllowlistConfig.model_validate(json.load(f))


def check_domain_allowed(url: str, config: AllowlistConfig) -> None:
    domain = urlparse(url).netloc
    if not any(domain == d or domain.endswith(f".{d}") for d in config.allowed_domains):
        raise AllowlistViolation(f"Domain '{domain}' is not in the allowlist {config.allowed_domains}.")


def check_action_allowed(action_name: str, config: AllowlistConfig) -> None:
    if action_name not in config.allowed_actions:
        raise AllowlistViolation(f"Action '{action_name}' is not in the allowlist {config.allowed_actions}.")
    if action_name in config.risky_actions:
        # This capability set currently has no irreversible actions to
        # gate (read-only: click/type/read/navigate). If a future
        # capability added something irreversible (e.g. a funds transfer
        # or account closure), this is the point that would block it
        # pending human confirmation rather than letting it proceed
        # silently - see REPORT_NOTES.md's Safety section.
        raise AllowlistViolation(
            f"Action '{action_name}' is marked risky and requires human confirmation - not implemented yet."
        )
