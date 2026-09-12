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
    selector: str | None = None  # set only for "fallback" elements - see snapshot()


class BrowserSession:
    """One live browser session. This is the thing that gets paused and
    handed to a human during escalation (step 5 of the plan) - so keep all
    Playwright state inside this one object, nothing global."""

    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.page: Page | None = None
        # name -> CSS selector, rebuilt on every snapshot(). Lets click()/
        # type_text()/read_text() reach elements that have no accessible
        # name at all (see snapshot() docstring for why that happens).
        self._fallback_selectors: dict[str, str] = {}

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

        Two passes:
          1. Accessibility-tree pass (aria_snapshot). Works whenever the app
             actually wires a label to a control - the common case, and the
             preferred targeting strategy because it's semantic and survives
             markup changes.
          2. Fallback pass over raw <input>/<textarea>/<select> elements.
             Real legacy enterprise apps routinely put visual label text in
             a <p> or plain <span> next to a field instead of a real
             <label>/aria-label - which means the browser computes NO
             accessible name for that field at all, and pass 1 can't see it.
             For those, we identify the element by its raw HTML name/id/
             placeholder attribute instead, and remember a CSS selector so
             click()/type_text() can still reach it. This is a deliberately
             lower-priority strategy: only used when pass 1 found nothing.
        """
        assert self.page is not None, "call start() first"
        elements: list[Element] = []
        self._fallback_selectors = {}

        # --- Pass 1: accessibility tree ---
        yaml_text = self.page.locator("body").aria_snapshot()
        for line in yaml_text.splitlines():
            match = _ARIA_LINE_RE.match(line)
            if not match:
                continue
            role = match.group("role")
            name = match.group("name") or ""
            if role in INTERACTABLE_ROLES and name:
                elements.append(Element(role=role, name=name))

        # --- Pass 2: raw form-control fallback ---
        for control in self.page.locator("input:visible, textarea:visible, select:visible").all():
            # Skip controls that already have a real accessible name -
            # those were already captured in pass 1, don't duplicate them.
            own_snapshot = control.aria_snapshot()
            first_line = own_snapshot.splitlines()[0] if own_snapshot else ""
            own_match = _ARIA_LINE_RE.match(first_line)
            if own_match and own_match.group("name"):
                continue

            tag = control.evaluate("el => el.tagName").lower()
            input_type = (control.get_attribute("type") or "text").lower()
            name_attr = control.get_attribute("name")
            id_attr = control.get_attribute("id")
            placeholder = control.get_attribute("placeholder")

            identifier = name_attr or id_attr or placeholder
            if not identifier:
                continue  # no accessible name AND no attribute to fall back on - truly unreachable, skip

            if tag == "select":
                role = "combobox"
            elif input_type == "checkbox":
                role = "checkbox"
            elif input_type == "radio":
                role = "radio"
            else:
                role = "textbox"

            if name_attr:
                selector, via = f'[name="{name_attr}"]', "name"
            elif id_attr:
                selector, via = f"#{id_attr}", "id"
            else:
                selector, via = f'[placeholder="{placeholder}"]', "placeholder"

            display_name = f"{identifier} (unlabeled field, targeted via {via} attribute)"
            elements.append(Element(role=role, name=display_name, selector=selector))
            self._fallback_selectors[display_name] = selector

        return elements

    def navigate(self, url: str) -> None:
        assert self.page is not None, "call start() first"
        self.page.goto(url, wait_until="domcontentloaded")
        self._settle()

    def click(self, role: str, name: str) -> None:
        assert self.page is not None, "call start() first"
        if name in self._fallback_selectors:
            self.page.locator(self._fallback_selectors[name]).first.click(timeout=5000)
        else:
            self.page.get_by_role(role, name=name, exact=False).first.click(timeout=5000)
        # A click on a submit-style button often triggers a full page
        # navigation. Without this, the very next snapshot() can race
        # ahead of that navigation and capture a stale, mid-load page -
        # which looked exactly like a login that silently failed.
        self._settle()

    def _settle(self) -> None:
        """Give a possible navigation triggered by the last action time to
        finish before the next snapshot() is taken. Best-effort: plenty of
        actions (e.g. clicking something that doesn't navigate at all)
        won't ever reach "networkidle", so a timeout here is expected and
        fine, not an error."""
        assert self.page is not None
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

    def type_text(self, role: str, name: str, text: str) -> None:
        assert self.page is not None, "call start() first"
        if name in self._fallback_selectors:
            self.page.locator(self._fallback_selectors[name]).first.fill(text, timeout=5000)
            return
        self.page.get_by_role(role, name=name, exact=False).first.fill(text, timeout=5000)

    def read_text(self, role: str, name: str) -> str:
        assert self.page is not None, "call start() first"
        if name in self._fallback_selectors:
            return self.page.locator(self._fallback_selectors[name]).first.input_value(timeout=5000)
        locator = self.page.get_by_role(role, name=name, exact=False).first
        return locator.inner_text(timeout=5000)