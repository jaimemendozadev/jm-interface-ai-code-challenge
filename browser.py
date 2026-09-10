"""
Thin wrapper around Playwright that exposes the browser as a small set of
role-based actions.

Design decision (this matters for REPORT.md): elements are identified by
their ACCESSIBILITY ROLE + ACCESSIBLE NAME (e.g. role="button", name="Log In"),
not by raw CSS selectors or pixel coordinates. Reasoning:
  - Legacy/server-rendered banking UIs rarely have test IDs or stable
    class names, but form controls and buttons almost always have an
    accessible role and name (that's what makes them usable at all,
    including for screen readers).
  - The same strategy works on modern web apps, legacy web apps, AND
    native desktop apps (via OS accessibility APIs) - so this choice is
    also most of the answer to the "heterogeneous surfaces" design question.

Observation is done via Playwright's aria_snapshot() (YAML-based accessibility
tree). Actions are executed via get_by_role(role, name=...), which is a
separate, stable public API independent of how the snapshot was captured.

This module is deliberately dumb: it does one action per call and returns
plain data. All "deciding what to do" logic lives outside it (in the LLM
loop for discovery, or the artifact for replay).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

# Matches lines like:  - button "Log In"   or   - heading "Sign in" [level=1]
# from the YAML aria_snapshot() output. We only need role + name; the
# indentation/nesting and bracketed attributes don't matter for flattening.
_ARIA_LINE_RE = re.compile(r'^\s*-\s+(?P<role>[a-zA-Z][a-zA-Z0-9]*)(?:\s+"(?P<name>[^"]*)")?')

# Roles worth showing to the LLM / worth targeting. Purely structural nodes
# (generic, none, text runs without interactivity) are filtered out so the
# snapshot stays small and relevant.
INTERACTABLE_ROLES = {
    "button",
    "link",
    "textbox",
    "searchbox",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "tab",
    "switch",
    "option",
}


@dataclass
class Element:
    role: str
    name: str
    value: str | None = None


class BrowserSession:
    """One live browser session. This is the thing that gets paused and
    handed to a human during escalation (step 5 of the plan) - so keep all
    Playwright state inside this one object, nothing global."""

    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.page: Page | None = None

    def start(self, url: str) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self.page = self._browser.new_page()
        self.page.goto(url, wait_until="domcontentloaded")

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def snapshot(self) -> list[Element]:
        """Flatten the accessibility tree into a list of interactable
        elements. This - not raw HTML - is what gets shown to Claude.

        Uses aria_snapshot(), which returns the tree as YAML text (Playwright
        removed the older dict-based page.accessibility.snapshot() in 1.57
        after a 3-year deprecation). We don't need the nesting/hierarchy for
        this purpose, so a line-by-line regex parse is enough - actions still
        target elements by role+name via get_by_role(), unchanged."""
        assert self.page is not None, "call start() first"
        yaml_text = self.page.locator("body").aria_snapshot()
        elements: list[Element] = []
        for line in yaml_text.splitlines():
            match = _ARIA_LINE_RE.match(line)
            if not match:
                continue
            role = match.group("role")
            name = match.group("name") or ""
            if role in INTERACTABLE_ROLES and name:
                elements.append(Element(role=role, name=name))
        return elements

    def navigate(self, url: str) -> None:
        assert self.page is not None, "call start() first"
        self.page.goto(url, wait_until="domcontentloaded")

    def click(self, role: str, name: str) -> None:
        assert self.page is not None, "call start() first"
        self.page.get_by_role(role, name=name, exact=False).first.click(timeout=5000)

    def type_text(self, role: str, name: str, text: str) -> None:
        assert self.page is not None, "call start() first"
        self.page.get_by_role(role, name=name, exact=False).first.fill(text, timeout=5000)

    def read_text(self, role: str, name: str) -> str:
        assert self.page is not None, "call start() first"
        locator = self.page.get_by_role(role, name=name, exact=False).first
        return locator.inner_text(timeout=5000)