"""Every route, hit with the input a careless client actually sends.

The failure this exists to catch is a handler that works on a filled-in form
and raises on a blank one, which reaches the user as a 500. Nothing here is a
hand-written list of endpoints: the matrix is generated from the live route
table, so a new route or a new field is covered the moment it is registered.

A 5xx is the only failure. Which non-5xx answer a route gives (303 back to the
form, 400, 404, 422) is that route's business, asserted in the feature tests.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from tests.support.routes import ALL_ROUTES, PAGE_ROUTES, fill_path

# Values that have to survive landing in any field of any form.
HOSTILE_VALUES = (
    "",
    "   ",
    "abc",
    "-1",
    "0",
    "9" * 25,  # far past the largest id SQLite can bind
    "1.5",
    "yes",  # a valid value, in the wrong field
    "<script>alert(1)</script>",
    "'; DROP TABLE profiles;--",
    "ünïcode ✨",
    "x" * 1000,  # longer than any column
)

HOSTILE_JSON = (
    None,
    "",
    "   ",
    0,
    -1,
    10**25,
    1.5,
    True,
    [],
    {},
    ["x"],
    [10**25],
    {"nested": 1},
    "yes",
)

# Ids that name no row, or that are not ids at all.
HOSTILE_IDS = ("999999", "0", "-1", "abc", "9" * 25, "1.5", "%20")

# The state a fresh install is in: nothing has been created yet.
NO_ROWS = dict.fromkeys({name for spec in ALL_ROUTES for name in spec.path_params}, 999999)

_WITH_FIELDS = [spec for spec in ALL_ROUTES if spec.field_names]
_WITH_IDS = [spec for spec in ALL_ROUTES if spec.path_params]


def _label(spec) -> str:
    return spec.label


def _send(client, method, attempts) -> list[str]:
    """Make each (description, url, kwargs) attempt; report the ones that 5xx.

    Redirects are followed so the page a handler bounces to has to render too —
    that is where a template meeting an unexpected None tends to blow up.
    """
    failures = []
    for description, url, kwargs in attempts:
        response = client.request(method, url, follow_redirects=True, **kwargs)
        if response.status_code >= 500:
            failures.append(f"{method} {url} — {description} → {response.status_code}")
    return failures


def _blank_attempts(spec, url) -> list[tuple[str, str, dict]]:
    attempts = [("no body at all", url, {})]
    if spec.takes_form:
        attempts += [
            ("empty form", url, {"data": {}}),
            ("every field blank", url, {"data": {f.name: "" for f in spec.form_fields}}),
            ("every field whitespace", url, {"data": {f.name: "   " for f in spec.form_fields}}),
        ]
    if spec.takes_json:
        attempts += [
            ("empty JSON object", url, {"json": {}}),
            ("null body", url, {"json": None}),
            ("every field blank", url, {"json": dict.fromkeys(spec.field_names, "")}),
        ]
    return attempts


# --- the inventory itself ---------------------------------------------------


def test_route_inventory_reflects_the_real_app():
    """A silently empty matrix would pass everything below without testing anything."""
    labels = {spec.label for spec in ALL_ROUTES}

    assert {
        "GET /",
        "POST /profiles",
        "POST /hangouts/new",
        "POST /hangouts/{hangout_id}/setup",
        "POST /api/hangouts/{hangout_id}/setup",
        "POST /webhooks/sms",
    } <= labels
    assert len(ALL_ROUTES) >= 20
    assert len(PAGE_ROUTES) >= 4

    hangout_form = next(spec for spec in ALL_ROUTES if spec.label == "POST /hangouts/new")
    assert {"action", "profile_ids", "motive"} <= set(hangout_form.field_names)


def test_every_path_parameter_has_a_sample_row(sample_data):
    """A route taking a new kind of id needs a row in `sample_data` to be tested against."""
    used = {name for spec in ALL_ROUTES for name in spec.path_params}

    assert used <= set(sample_data), f"no sample row for {sorted(used - set(sample_data))}"


# --- blank and hostile input ------------------------------------------------


@pytest.mark.parametrize("spec", ALL_ROUTES, ids=_label)
def test_blank_submission_is_answered_not_crashed(client_no_raise, sample_data, spec):
    url = fill_path(spec.path, sample_data)

    failures = _send(client_no_raise, spec.method, _blank_attempts(spec, url))

    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("spec", ALL_ROUTES, ids=_label)
def test_blank_submission_with_no_data_in_the_app_is_answered_not_crashed(client_no_raise, spec):
    url = fill_path(spec.path, NO_ROWS)

    failures = _send(client_no_raise, spec.method, _blank_attempts(spec, url))

    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("spec", _WITH_FIELDS, ids=_label)
def test_hostile_field_values_are_answered_not_crashed(client_no_raise, sample_data, spec):
    url = fill_path(spec.path, sample_data)
    attempts: list[tuple[str, str, dict]] = []
    if spec.takes_form:
        blank = {f.name: "" for f in spec.form_fields}
        for form_field in spec.form_fields:
            for value in HOSTILE_VALUES:
                attempts.append(
                    (
                        f"{form_field.name}={value[:40]!r}",
                        url,
                        {"data": {**blank, form_field.name: value}},
                    )
                )
    else:
        for name in spec.field_names:
            for value in HOSTILE_JSON:
                attempts.append((f"{name}={value!r}", url, {"json": {name: value}}))
        attempts += [
            ("unknown field", url, {"json": {"surprise": "value"}}),
            ("array body", url, {"json": []}),
            ("string body", url, {"json": "nope"}),
        ]

    failures = _send(client_no_raise, spec.method, attempts)

    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("spec", _WITH_IDS, ids=_label)
def test_unknown_and_malformed_row_ids_are_answered_not_crashed(client_no_raise, sample_data, spec):
    attempts = [
        (f"id={bad_id[:40]!r}", fill_path(spec.path, dict.fromkeys(spec.path_params, bad_id)), {})
        for bad_id in HOSTILE_IDS
    ]

    failures = _send(client_no_raise, spec.method, attempts)

    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("spec", PAGE_ROUTES, ids=_label)
def test_pages_survive_hostile_query_strings(client_no_raise, sample_data, spec):
    """Pages read query parameters (`?error=…`) that anyone can type into the bar."""
    path = fill_path(spec.path, sample_data)
    attempts = [
        (f"?error={value[:40]!r}", f"{path}?error={quote(value)}&unexpected={quote(value)}", {})
        for value in HOSTILE_VALUES
    ]

    failures = _send(client_no_raise, "GET", attempts)

    assert not failures, "\n".join(failures)
