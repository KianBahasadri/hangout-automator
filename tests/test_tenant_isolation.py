"""Tenant isolation: workspace A's requests must never reach workspace B.

The matrix is generated from the live router inventory (like the route smoke
tests), so a new route or a new id-taking endpoint is covered the moment it is
registered. Workspace A is fully populated; every request is authenticated as
A's user; workspace B's ids and strings are substituted everywhere. A 2xx with
B's data in the body is the failure this file exists to catch.
"""

import pytest
from clerk_backend_api.security import AuthStatus, RequestState

from app.config import Settings
from app.models import (
    Allergy,
    Drive,
    Hangout,
    HangoutInvite,
    HangoutStatus,
    InviteStatus,
    Profile,
    Tag,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
    YesNo,
)
from tests.support.routes import ALL_ROUTES, fill_path

_IDS_IN_BODY = ("profile_ids", "tag_ids", "allergy_ids", "organizer_profile_id")

# Which row a web POST route's id path parameter refers to, for the
# foreign-row-survives check below.
_MODEL_FOR_ID_PARAM = {
    "profile_id": Profile,
    "tag_id": Tag,
    "allergy_id": Allergy,
    "hangout_id": Hangout,
}


def _clerk_settings() -> Settings:
    return Settings(
        clerk_enabled=True,
        clerk_publishable_key="pk_test_example",
        clerk_frontend_api_url="https://example.clerk.accounts.dev",
        clerk_secret_key="sk_test_example",
        public_base_url="http://localhost:9000",
        _env_file=None,
    )


@pytest.fixture
def workspace_a(db):
    ws = Workspace(name="Workspace A", slug="workspace-a")
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, clerk_user_id="user_a", role=WorkspaceRole.owner))
    db.commit()
    return ws


@pytest.fixture
def workspace_b(db):
    ws = Workspace(name="Workspace B", slug="workspace-b")
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, clerk_user_id="user_b", role=WorkspaceRole.owner))
    db.commit()
    return ws


def _full_data(db, workspace, *, name: str, phone: str) -> dict:
    """A complete dataset for one workspace, with distinguishable strings."""
    tag = Tag(name=f"{name}'s tag", workspace_id=workspace.id)
    allergy = Allergy(name=f"{name}'s allergy", workspace_id=workspace.id)
    db.add_all([tag, allergy])
    db.flush()

    profile = Profile(
        name=name,
        phone=phone,
        drinks=YesNo.yes,
        smokes=YesNo.no,
        drive=Drive.maybe,
        workspace_id=workspace.id,
    )
    profile.tags = [tag]
    profile.allergies = [allergy]
    db.add(profile)
    db.flush()

    hangouts: dict[str, int] = {}
    for state in (HangoutStatus.draft, HangoutStatus.active, HangoutStatus.closed):
        hangout = Hangout(
            status=state,
            motive=f"{name} {state.value} plans",
            organizer_profile_id=profile.id,
            organizer_phone=phone,
            workspace_id=workspace.id,
        )
        db.add(hangout)
        db.flush()
        db.add(
            HangoutInvite(
                hangout_id=hangout.id,
                profile_id=profile.id,
                status=InviteStatus.pending,
                workspace_id=workspace.id,
            )
        )
        hangouts[state.value] = hangout.id
    db.commit()
    return {
        "workspace_id": workspace.id,
        "profile_id": profile.id,
        "tag_id": tag.id,
        "allergy_id": allergy.id,
        "hangout_id": hangouts["draft"],
        "hangouts": hangouts,
        "strings": {name, phone, f"{name} {HangoutStatus.draft.value} plans"},
    }


@pytest.fixture
def workspace_a_data(db, workspace_a):
    return _full_data(db, workspace_a, name="Sam Rivera", phone="+15551110001")


@pytest.fixture
def workspace_b_data(db, workspace_b):
    return _full_data(db, workspace_b, name="Bree Torres", phone="+15559990001")


def _as_clerk_user(client, monkeypatch, sub: str):
    """Authenticate every request as `sub` (Clerk enabled, no membership)."""
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.tenancy.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.web.get_settings", lambda: settings)

    async def fake_authenticate(request, settings):
        return RequestState(
            status=AuthStatus.SIGNED_IN,
            payload={"sub": sub, "sid": f"sess_{sub}"},
            token="test-token",
        )

    monkeypatch.setattr("app.auth.authenticate_clerk_request", fake_authenticate)
    return client


def _as_user_a(client, monkeypatch):
    """Authenticate every request as workspace A's owner (Clerk enabled)."""
    return _as_clerk_user(client, monkeypatch, "user_a")


def _body_with_b_ids(spec, data_b: dict) -> dict:
    """Body where every id-ish field holds workspace B's ids."""
    body: dict = {}
    for name in spec.field_names:
        if name in _IDS_IN_BODY:
            value = data_b.get(name.removesuffix("_id"))
            if name.endswith("_ids"):
                body[name] = [value] if value is not None else []
            elif value is not None:
                body[name] = value
    return body


def _b_strings(data_b: dict) -> set[str]:
    return data_b["strings"]


@pytest.mark.parametrize(
    "spec", [spec for spec in ALL_ROUTES if spec.path_params], ids=lambda s: s.label
)
def test_foreign_ids_never_answer_200_or_leak_data(
    db, client, monkeypatch, workspace_a_data, workspace_b_data, spec
):
    """Every route that takes an id, hit with workspace B's ids as user A."""
    auth_client = _as_user_a(client, monkeypatch)
    url = fill_path(spec.path, workspace_b_data)
    kwargs: dict = {}
    if spec.takes_form:
        kwargs["data"] = _body_with_b_ids(spec, workspace_b_data)
    elif spec.takes_json:
        kwargs["json"] = _body_with_b_ids(spec, workspace_b_data)

    # Never follow: a 303 → 200 redirect to a page A may legitimately read is
    # not a 200 to B's id, and the redirect itself must not be 200.
    response = auth_client.request(spec.method, url, follow_redirects=False, **kwargs)

    assert response.status_code != 200, (
        f"{spec.method} {url} answered 200 to workspace B's id — expected 404/403/400/303"
    )
    if spec.path.startswith("/api/"):
        assert response.status_code in (400, 403, 404), (
            f"{spec.method} {url} answered {response.status_code} (expected 404) to a foreign id"
        )
    # The landing page of a redirect must not leak B's content either.
    check_body = response.text
    if response.status_code in (301, 302, 303, 307, 308):
        check_body = auth_client.request(spec.method, url, follow_redirects=True, **kwargs).text
    # Substring match, not whitespace tokens: a multi-word string like
    # "Bree Torres" or a phone number wrapped in tags would tokenise into
    # fragments that never equal the stored strings. The response must not
    # contain B's strings anywhere.
    leaked = [s for s in _b_strings(workspace_b_data) if s in check_body]
    assert not leaked, f"{spec.method} {url} leaked workspace B content: {leaked}"

    # A destructive web POST answers 303 no matter what, so the status and
    # body checks above cannot see whether B's row was touched. With correct
    # scoping the request is a no-op, so B's row must still exist afterwards.
    # (The response body never shows the leak: the redirect lands on A's
    # pages.) Expire the session first so a deleted row is not served back
    # from the identity map.
    if spec.method == "POST" and not spec.path.startswith("/api/"):
        db.expire_all()
        for param, model in _MODEL_FOR_ID_PARAM.items():
            if param in spec.path_params:
                row_id = workspace_b_data[param]
                assert db.get(model, row_id) is not None, (
                    f"{spec.method} {url} destroyed workspace B's "
                    f"{model.__name__} (id {row_id}) — a foreign-id request "
                    f"must be a no-op"
                )


@pytest.mark.parametrize(
    "spec",
    [spec for spec in ALL_ROUTES if not spec.path_params and spec.field_names],
    ids=lambda s: s.label,
)
def test_creates_never_adopt_foreign_ids_or_leak_data(
    client, monkeypatch, workspace_a_data, workspace_b_data, spec
):
    """Routes that create rows: B's ids in the body must be inert, and any
    created row must land in workspace A."""
    auth_client = _as_user_a(client, monkeypatch)
    kwargs: dict = {}
    if spec.takes_form:
        kwargs["data"] = _body_with_b_ids(spec, workspace_b_data)
    elif spec.takes_json:
        kwargs["json"] = _body_with_b_ids(spec, workspace_b_data)

    response = auth_client.request(spec.method, spec.path, follow_redirects=True, **kwargs)

    leaked = [s for s in _b_strings(workspace_b_data) if s in response.text]
    assert not leaked, f"{spec.method} {spec.path} leaked workspace B content: {leaked}"


def test_route_inventory_covers_the_tenant_matrix():
    """An empty matrix would pass every isolation test while checking nothing."""
    with_ids = [spec for spec in ALL_ROUTES if spec.path_params]
    assert len(with_ids) >= 10, f"only {len(with_ids)} id-taking routes found"
    labels = {spec.label for spec in with_ids}
    assert {
        "GET /hangouts/{hangout_id}",
        "GET /api/hangouts/{hangout_id}",
        "PATCH /api/profiles/{profile_id}",
        "DELETE /api/tags/{tag_id}",
    } <= labels


# --- Workspace bootstrap (P3.10) ---


def test_brand_new_user_gets_an_empty_workspace(client, monkeypatch, db, sample_data):
    """First authenticated request provisions a workspace; the default
    workspace's data stays invisible."""
    default_hangout_id = sample_data["hangouts"]["active"]
    auth_client = _as_clerk_user(client, monkeypatch, "user_new")

    tags = auth_client.get("/api/tags")
    assert tags.status_code == 200
    assert tags.json() == []
    assert auth_client.get("/api/profiles").json() == []
    assert auth_client.get("/api/hangouts").json() == []

    # The provisioned workspace exists with an owner membership.
    from app.models import Workspace, WorkspaceMember

    member = db.query(WorkspaceMember).filter(WorkspaceMember.clerk_user_id == "user_new").one()
    workspace = db.get(Workspace, member.workspace_id)
    assert workspace is not None
    assert workspace.slug == "user-user_new"
    assert member.role.value == "owner"

    # The default workspace's rows are unreachable through the new workspace.
    foreign = auth_client.get(f"/api/hangouts/{default_hangout_id}")
    assert foreign.status_code == 404
    assert "Sam Rivera" not in foreign.text


def test_repeated_first_requests_do_not_create_duplicate_workspaces(client, monkeypatch, db):
    """Two requests from a user with no membership still yield one workspace:
    the deterministic slug's unique constraint makes the loser re-read."""
    from app.models import Workspace, WorkspaceMember

    auth_client = _as_clerk_user(client, monkeypatch, "user_repeat")

    assert auth_client.get("/api/tags").status_code == 200
    assert auth_client.get("/api/tags").status_code == 200

    workspaces = (
        db.query(Workspace)
        .join(WorkspaceMember)
        .filter(WorkspaceMember.clerk_user_id == "user_repeat")
        .all()
    )
    assert len(workspaces) == 1
