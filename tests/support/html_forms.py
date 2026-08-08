"""A browser's reading of the forms on a rendered page.

Rebuilds what a browser would POST when someone submits without touching
anything: defaults for text fields and selects, unchecked boxes left out
entirely, and each submit button's own name/value. That turns "I clicked the
button without filling anything in" into a request the tests can actually make,
derived from the templates rather than from a guess at what they contain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

# A browser never submits these as part of the field set.
NON_FIELD_INPUT_TYPES = frozenset({"submit", "button", "reset", "image", "file"})
TOGGLE_INPUT_TYPES = frozenset({"checkbox", "radio"})


@dataclass(frozen=True)
class SubmitControl:
    """A button that submits the form."""

    name: str | None
    value: str
    label: str

    def __str__(self) -> str:
        label = " ".join(self.label.split()) or "(unlabelled)"
        return f"{label!r}" if self.name is None else f"{label!r} [{self.name}={self.value}]"


def as_form_data(pairs: list[tuple[str, str]]) -> dict[str, str | list[str]]:
    """Name/value pairs as an HTTP client's form mapping, keeping repeated names."""
    grouped: dict[str, list[str]] = {}
    for name, value in pairs:
        grouped.setdefault(name, []).append(value)
    return {name: values[0] if len(values) == 1 else values for name, values in grouped.items()}


@dataclass
class HtmlForm:
    action: str
    method: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    submits: list[SubmitControl] = field(default_factory=list)

    def submissions(self) -> list[tuple[SubmitControl, dict[str, str | list[str]]]]:
        """One (button, form data) pair per way of submitting this form untouched."""
        buttons = self.submits or [SubmitControl(None, "", "submit")]
        return [
            (
                button,
                as_form_data(self.fields + ([(button.name, button.value)] if button.name else [])),
            )
            for button in buttons
        ]


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[HtmlForm] = []
        self._form: HtmlForm | None = None
        self._select: dict | None = None
        self._option: dict | None = None
        self._textarea: dict | None = None
        self._button: dict | None = None

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {name.lower(): (value if value is not None else "") for name, value in attrs}

    def _add(self, name: str, value: str) -> None:
        if self._form is not None and name:
            self._form.fields.append((name, value))

    # -- tags ------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = self._attrs(attrs)
        if tag == "form":
            self._form = HtmlForm(action=a.get("action", ""), method=a.get("method", "get").lower())
            self.forms.append(self._form)
            return
        if self._form is None or "disabled" in a:
            return

        if tag == "input":
            kind = a.get("type", "text").lower()
            name = a.get("name", "")
            if kind == "submit":
                self._form.submits.append(
                    SubmitControl(name or None, a.get("value", ""), a.get("value", ""))
                )
            elif kind in TOGGLE_INPUT_TYPES:
                if "checked" in a:
                    self._add(name, a.get("value", "on"))
            elif kind not in NON_FIELD_INPUT_TYPES:
                self._add(name, a.get("value", ""))
        elif tag == "button":
            # A <button> with no type attribute submits the form.
            if a.get("type", "submit").lower() == "submit":
                self._button = {
                    "name": a.get("name") or None,
                    "value": a.get("value", ""),
                    "text": [],
                }
        elif tag == "select":
            self._select = {"name": a.get("name", ""), "selected": None, "first": None}
        elif tag == "option" and self._select is not None:
            self._option = {"attr_value": a.get("value"), "selected": "selected" in a, "text": []}
        elif tag == "textarea":
            self._textarea = {"name": a.get("name", ""), "text": []}

    def handle_data(self, data: str) -> None:
        for sink in (self._option, self._textarea, self._button):
            if sink is not None:
                sink["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form = None
        elif tag == "button" and self._button is not None:
            label = "".join(self._button["text"]).strip()
            if self._form is not None:
                self._form.submits.append(
                    SubmitControl(self._button["name"], self._button["value"], label)
                )
            self._button = None
        elif tag == "option" and self._option is not None and self._select is not None:
            # An option with no value attribute submits its own text.
            text = "".join(self._option["text"]).strip()
            value = self._option["attr_value"]
            value = text if value is None else value
            if self._select["first"] is None:
                self._select["first"] = value
            if self._option["selected"] and self._select["selected"] is None:
                self._select["selected"] = value
            self._option = None
        elif tag == "select" and self._select is not None:
            # No explicit selection: a browser submits the first option.
            chosen = self._select["selected"]
            chosen = self._select["first"] if chosen is None else chosen
            self._add(self._select["name"], chosen or "")
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            self._add(self._textarea["name"], "".join(self._textarea["text"]))
            self._textarea = None


def forms_in(html: str) -> list[HtmlForm]:
    parser = _FormParser()
    parser.feed(html)
    parser.close()
    return parser.forms
