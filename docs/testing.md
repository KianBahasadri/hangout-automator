# Testing

`pytest` under `tests/`. Run it with `uv run --group dev pytest` (see
[local-development.md](./local-development.md)).

Config lives in `pyproject.toml`: `testpaths = ["tests"]`, `--strict-markers
--strict-config`, and `filterwarnings = ["error"]` — a warning fails the run,
because a deprecated call that quietly changes meaning (an HTTP client not
sending a form as a form, say) turns a passing test into a test of nothing.

## Layout

| File | Covers |
|------|--------|
| `conftest.py` | Temp SQLite per session, table wipe after each test, clients, `sample_data` |
| `support/routes.py` | Route inventory read off the live FastAPI router |
| `support/html_forms.py` | HTML form parser: what a browser would submit untouched |
| `test_route_surface.py` | Every route × blank and hostile input |
| `test_ui_forms.py` | Every page, every button, form left untouched |
| `test_api.py`, `test_profiles.py`, `test_web_ui.py`, `test_hangout_setup.py` | Endpoint behaviour |
| `test_rsvp_flow.py`, `test_followups.py`, `test_sms_guard.py`, `test_webhook_security.py` | SMS in, invites/follow-ups out, webhook signatures |
| `test_config.py`, `test_logging.py`, `test_migrations.py` | Settings, audit log, SQLite migrations |

Fixtures: `client` (raises server exceptions), `client_no_raise` (returns the
500 so it can be asserted on), `db`, and `sample_data` — one row of everything,
keyed by *path parameter name* (`profile_id`, `hangout_id`, …) plus a
`hangouts` map of draft/active/closed.

## The generated smoke matrix

Both smoke files derive their cases from the app rather than from a list
somebody has to remember to update.

- `support/routes.py` walks `app.routes` and reports each route's method, path
  parameters, form fields (from the FastAPI dependant), and JSON body model. A
  new endpoint is covered as soon as it is registered.
- `support/html_forms.py` parses the rendered HTML and rebuilds each form the
  way a browser would submit it with nothing touched: text defaults, the
  selected (or first) option of each select, unchecked boxes left out, and one
  submission per submit button, carrying that button's own name/value. A new
  field or button on a page is submitted without editing the test.

`test_route_surface.py` sends, per route: no body, an empty body, every field
blank, every field whitespace, and each field in turn holding a hostile value
(wrong type, out-of-range id, 1000 characters, `<script>`, unicode, SQL). It
does this against seeded data *and* against an empty database, follows
redirects so the page a handler bounces to has to render too, and fails only on
a 5xx. Which non-5xx answer a route gives is the feature tests' business.

`test_ui_forms.py` opens each page (list, profiles, add profiles, settings, new
hangout, and a draft/active/closed hangout) and presses every button on it. That is the
literal reported bug — "Set up hangout with nothing filled in" — and it catches
what the route matrix cannot: the matrix does not know that `action=setup` is
the interesting value for that field, the rendered button does.

Both files carry guard tests (`test_route_inventory_reflects_the_real_app`,
`test_the_expected_forms_are_found`, `test_every_page_route_is_visited`,
`test_every_path_parameter_has_a_sample_row`). Without them, a broken parser or
a renamed attribute would empty the matrix and every generated test would pass
while checking nothing.

## Adding to it

- New route: nothing to do, unless it takes a new kind of id — then add a row
  to `sample_data` (a guard test names the missing key).
- New page: add it to `PAGE_LABELS`/`_pages` in `test_ui_forms.py`; a guard test
  fails until you do.
- New behaviour: assert it in the topic-specific file. The smoke matrix only
  ever says "this did not crash", never "this did the right thing".

## Nothing runs this for you

This repository has **no GitHub Actions and no CI** — that is deliberate, not an
oversight. Run the suite yourself before you push:

```bash
uv run --group dev pytest
```

There was a workflow here until it was removed. It is worth knowing why, because
it is the reason a 500 shipped: it installed `requirements.txt` (which has no
pytest) and then called `pytest`, so every run since it was added failed with
`pytest: command not found` and no test ever gated a push. A test suite nobody
runs is worse than none, because it looks like coverage. If CI is ever added
back, check that a deliberately broken test actually turns the run red.

Terraform has its own checks (`terraform fmt -check`, `terraform validate`);
they are an operator step, described in [deploy.md](./deploy.md).
