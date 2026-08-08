"""Press every button on every page with the form left untouched.

This is the reported bug in general form: "I clicked Set up hangout without
filling anything in and got a 500". The forms are read out of the rendered
HTML, so a new field, a new select, or a new button is submitted exactly as a
browser would send it without this file being updated.
"""

from __future__ import annotations

import pytest

from tests.support.html_forms import forms_in
from tests.support.routes import PAGE_ROUTES, fill_path

PAGE_LABELS = (
    "sign in",
    "hangout list",
    "profiles",
    "new profiles",
    "settings",
    "sms simulator",
    "deleted hangouts",
    "new hangout",
    "draft hangout",
    "edit draft hangout",
    "active hangout",
    "closed hangout",
)

# Forms that must be found, so a broken parser fails loudly instead of quietly
# reducing this file to a page-load test.
EXPECTED_ACTIONS = frozenset({"/profiles", "/tags", "/allergies", "/hangouts/new"})


def _pages(sample_data) -> dict[str, str]:
    """Every page a person can open, in every state that renders differently."""
    hangouts = sample_data["hangouts"]
    return {
        "sign in": "/sign-in",
        "hangout list": "/",
        "profiles": "/profiles",
        "new profiles": "/profiles/new",
        "settings": "/settings",
        "sms simulator": "/settings/sms-simulator",
        "deleted hangouts": "/settings/deleted-hangouts",
        "new hangout": "/hangouts/new",
        "draft hangout": f"/hangouts/{hangouts['draft']}",
        "edit draft hangout": f"/hangouts/{hangouts['draft']}/edit",
        "active hangout": f"/hangouts/{hangouts['active']}",
        "closed hangout": f"/hangouts/{hangouts['closed']}",
    }


def test_every_page_route_is_visited(sample_data):
    """A new page has to be listed here, or this fails."""
    pages = _pages(sample_data)
    expected = {fill_path(spec.path, sample_data) for spec in PAGE_ROUTES}

    assert set(pages) == set(PAGE_LABELS)
    assert expected <= set(pages.values()), (
        f"pages never opened by these tests: {sorted(expected - set(pages.values()))}"
    )


@pytest.mark.parametrize("label", PAGE_LABELS)
def test_page_renders(client_no_raise, sample_data, label):
    response = client_no_raise.get(_pages(sample_data)[label])

    assert response.status_code == 200


def test_the_expected_forms_are_found(client, sample_data):
    actions = {
        form.action
        for url in _pages(sample_data).values()
        for form in forms_in(client.get(url).text)
    }

    assert EXPECTED_ACTIONS <= actions, f"forms found on no page: {sorted(EXPECTED_ACTIONS - actions)}"


@pytest.mark.parametrize("label", PAGE_LABELS)
def test_submitting_every_untouched_form_is_answered_not_crashed(
    client_no_raise, sample_data, label
):
    page = _pages(sample_data)[label]
    failures = []

    for form in forms_in(client_no_raise.get(page).text):
        if form.method != "post":
            continue
        for button, data in form.submissions():
            response = client_no_raise.post(form.action, data=data, follow_redirects=True)
            if response.status_code >= 500:
                failures.append(
                    f"{page}: {button} → POST {form.action} {data} → {response.status_code}"
                )

    assert not failures, "\n".join(failures)


def test_set_up_hangout_with_nothing_filled_in_returns_to_the_form(client, sample_data):
    """The reported bug, spelled out: open the new-hangout page, press the button."""
    page = client.get("/hangouts/new")
    form = next(form for form in forms_in(page.text) if form.action == "/hangouts/new")
    button, data = next(
        submission for submission in form.submissions() if submission[0].value == "setup"
    )

    response = client.post(form.action, data=data, follow_redirects=False)

    assert "Set up hangout" in button.label
    assert response.status_code == 303
    assert response.headers["location"].endswith("?error=need_profiles")
