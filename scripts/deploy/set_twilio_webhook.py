#!/usr/bin/env python3
"""Point the Twilio number's inbound-SMS webhook at this deployment.

Registers ``SmsUrl`` over the Twilio REST API rather than the console. The URL
is built from the app's own ``Settings`` and the webhooks router, so what gets
registered is byte-for-byte what ``_canonical_webhook_url`` reconstructs when it
verifies ``X-Twilio-Signature`` — a base URL that disagrees by so much as a
scheme produces requests that look fine and never validate.

Twilio permits exactly one ``SmsUrl`` per number, so pointing a number here
takes it away from anything else using it. Overwriting a non-empty webhook
requires ``--force``.

Usage: ./scripts/deploy/set_twilio_webhook.py [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Settings reads ./.env relative to the working directory, so anchor to the repo
# root no matter where this was invoked from.
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

try:
    from twilio.base.exceptions import TwilioException, TwilioRestException
    from twilio.rest import Client
except ImportError:  # pragma: no cover - depends on which interpreter was used
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists() and not os.environ.get("_HANGOUT_WEBHOOK_REEXEC"):
        os.environ["_HANGOUT_WEBHOOK_REEXEC"] = "1"
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])
    raise SystemExit(
        "The twilio SDK is not available. Run `uv sync` once to build .venv, "
        "or invoke this as `uv run scripts/deploy/set_twilio_webhook.py`"
    )

from app.config import get_settings  # noqa: E402
from app.routers.webhooks import router as webhooks_router  # noqa: E402


def webhook_path() -> str:
    """The inbound-SMS path as the app actually mounts it."""
    for route in webhooks_router.routes:
        if "POST" in getattr(route, "methods", set()):
            return route.path
    raise SystemExit("Could not find the inbound SMS route on the webhooks router.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a webhook that already points somewhere else",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without calling Twilio's update endpoint",
    )
    args = parser.parse_args()

    settings = get_settings()

    # sms_provider=mock leaves the credentials unvalidated by Settings, so check
    # them here rather than failing inside the SDK.
    missing = [
        name
        for name, value in (
            ("TWILIO_ACCOUNT_SID", settings.twilio_account_sid),
            ("TWILIO_AUTH_TOKEN", settings.twilio_auth_token),
            ("TWILIO_FROM_NUMBER", settings.twilio_from_number),
        )
        if not (value or "").strip()
    ]
    if missing:
        print(f"Missing in .env: {', '.join(missing)}", file=sys.stderr)
        return 1

    base_url = settings.public_base_url.rstrip("/")
    target_url = f"{base_url}{webhook_path()}"

    # The default is http://localhost:9000, which Twilio can reach from nowhere.
    if not base_url.startswith("https://") or "localhost" in base_url or "127.0.0.1" in base_url:
        print(
            f"PUBLIC_BASE_URL is {base_url!r}, which Twilio cannot reach.\n"
            "Set it to the public https:// hostname before registering the webhook.",
            file=sys.stderr,
        )
        return 1

    if settings.sms_provider != "twilio":
        print(
            f"Warning: SMS_PROVIDER is {settings.sms_provider!r}, so the app will not "
            "validate X-Twilio-Signature and this webhook will not be exercised.",
            file=sys.stderr,
        )

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    try:
        numbers = client.incoming_phone_numbers.list(
            phone_number=settings.twilio_from_number, limit=2
        )
    except TwilioRestException as exc:
        print(f"Twilio rejected the lookup: {exc.msg} (code {exc.code})", file=sys.stderr)
        return 1
    except TwilioException as exc:
        print(f"Could not reach Twilio: {exc}", file=sys.stderr)
        return 1

    if len(numbers) != 1:
        print(
            f"Expected exactly one number matching {settings.twilio_from_number}, "
            f"found {len(numbers)}.",
            file=sys.stderr,
        )
        return 1

    number = numbers[0]
    current_url = number.sms_url or ""
    print(f"Number:  {number.phone_number} ({number.sid})")
    print(f"Current: {current_url or '(unset)'} [{number.sms_method or 'POST'}]")
    print(f"Target:  {target_url} [POST]")
    # Keep this context ahead of anything the checks below write to stderr.
    sys.stdout.flush()

    if not number.capabilities.get("sms"):
        print(
            "This number is not SMS-capable; inbound messages will never arrive.", file=sys.stderr
        )
        return 1

    # A TwiML app binding takes precedence over SmsUrl, so setting the URL alone
    # would silently have no effect.
    if number.sms_application_sid:
        print(
            f"This number has SmsApplicationSid={number.sms_application_sid}, which "
            "overrides SmsUrl.\nClear that binding in Twilio first, or inbound SMS "
            "will not reach this app.",
            file=sys.stderr,
        )
        return 1

    if current_url == target_url and (number.sms_method or "POST") == "POST":
        print("Already pointed at this deployment; nothing to do.")
        return 0

    if current_url and not args.force:
        print(
            f"\nRefusing to overwrite an existing webhook.\n"
            f"This number currently delivers inbound SMS to {current_url}, and Twilio\n"
            "permits only one SmsUrl per number, so re-pointing it will break whatever\n"
            "depends on that URL. Re-run with --force if that is intended, or provision\n"
            "a separate number for this app.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("Dry run; no change made.")
        return 0

    try:
        number.update(sms_url=target_url, sms_method="POST")
        # Re-read rather than trusting the write response, so what is printed is
        # the state Twilio will actually use.
        confirmed = client.incoming_phone_numbers(number.sid).fetch()
    except TwilioRestException as exc:
        print(f"Twilio rejected the update: {exc.msg} (code {exc.code})", file=sys.stderr)
        return 1

    if confirmed.sms_url != target_url:
        print(
            f"Verification failed: Twilio reports {confirmed.sms_url!r}, expected {target_url!r}",
            file=sys.stderr,
        )
        return 1

    print(f"Verified: {confirmed.sms_url} [{confirmed.sms_method}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
