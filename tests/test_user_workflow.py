"""User-clone orchestration tests without HTTP mocks."""

from collections.abc import Callable

import pytest

from jellyfin_utils.user import workflow
from jellyfin_utils.user.models import CloneCounts, CloneUserRequest, UserCloneSnapshot
from jellyfin_utils.user.snapshot import verify_user_snapshot


def _request(password_resolver: Callable[[str | None, bool], str | None] | None = None) -> CloneUserRequest:
    return CloneUserRequest(
        source_username="source",
        username="copy",
        base_url="https://jellyfin.example",
        token="jellyfin-secret",  # noqa: S106
        jellyseerr_server="https://jellyseerr.example",
        jellyseerr_token="jellyseerr-secret",  # noqa: S106
        email="copy@example.com",
        password="password-secret",  # noqa: S106
        no_password=False,
        password_resolver=password_resolver or (lambda password, _no_password: password),
    )


def test_clone_request_repr_hides_credentials() -> None:
    representation = repr(_request())

    assert "jellyfin-secret" not in representation
    assert "jellyseerr-secret" not in representation
    assert "password-secret" not in representation
    assert "password_resolver" not in representation


def test_clone_validates_jellyseerr_identity_before_snapshot_or_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def check_access(_server: str, _token: str) -> None:
        events.append("access")

    def jellyseerr_users(_server: str, _token: str, query: str) -> list[dict]:
        events.append(f"identity:{query}")
        return [{"id": 4, "email": "COPY@example.com"}]

    monkeypatch.setattr(workflow, "get_users", lambda _url, _headers: [{"Id": "source-id", "Name": "source"}])
    monkeypatch.setattr(workflow, "check_jellyseerr_user_management_access", check_access)
    monkeypatch.setattr(workflow, "get_jellyseerr_users", jellyseerr_users)

    with pytest.raises(ValueError, match="already exists"):
        workflow.clone_user(_request())

    assert events == ["access", "identity:copy@example.com"]


def test_clone_returns_verification_failure_before_jellyseerr_profile_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    source_snapshot = UserCloneSnapshot(
        item_data=(("movie", {"PlayCount": 2}),),
        playlists=(),
    )
    destination_snapshot = UserCloneSnapshot(
        item_data=(("movie", {"PlayCount": 1}),),
        playlists=(),
    )
    snapshots = iter((source_snapshot, destination_snapshot, destination_snapshot))
    linked_user = {
        "id": 9,
        "email": "copy@example.com",
        "jellyfinUsername": "copy",
        "jellyfinUserId": "copy-id",
    }

    def capture(_url: str, _headers: dict[str, str], _user_id: str) -> UserCloneSnapshot:
        events.append("capture")
        return next(snapshots)

    def resolve(
        _url: str,
        _headers: dict[str, str],
        _user_id: str,
        _expected: UserCloneSnapshot,
        actual: UserCloneSnapshot,
    ) -> UserCloneSnapshot:
        events.append("resolve")
        return actual

    def apply(
        _url: str,
        _headers: dict[str, str],
        _user_id: str,
        _source: UserCloneSnapshot,
        _existing: UserCloneSnapshot | None,
    ) -> CloneCounts:
        events.append("apply")
        return CloneCounts(1, 0, 0, 0, 1, 0, 0, 0)

    def verify(expected: UserCloneSnapshot, actual: UserCloneSnapshot):  # noqa: ANN202
        events.append("verify")
        return verify_user_snapshot(expected, actual)

    def unexpected_profile_change(*_args: object, **_kwargs: object) -> None:
        events.append("profile-change")

    monkeypatch.setattr(
        workflow,
        "get_users",
        lambda _url, _headers: [
            {"Id": "source-id", "Name": "Source"},
            {"Id": "copy-id", "Name": "copy"},
        ],
    )
    monkeypatch.setattr(workflow, "check_jellyseerr_user_management_access", lambda *_args: None)
    monkeypatch.setattr(workflow, "find_linked_jellyseerr_user", lambda *_args: linked_user)
    monkeypatch.setattr(workflow, "get_jellyseerr_users", lambda *_args: [linked_user])
    monkeypatch.setattr(workflow, "capture_user_snapshot", capture)
    monkeypatch.setattr(workflow, "resolve_destination_snapshot", resolve)
    monkeypatch.setattr(workflow, "apply_user_snapshot", apply)
    monkeypatch.setattr(workflow, "verify_user_snapshot", verify)
    monkeypatch.setattr(workflow, "import_jellyfin_user", unexpected_profile_change)
    monkeypatch.setattr(workflow, "update_jellyseerr_profile", unexpected_profile_change)

    outcome = workflow.clone_user(_request())

    assert outcome.completed is False
    assert outcome.verification.verified is False
    assert outcome.jellyseerr_user_id is None
    assert events == ["capture", "capture", "resolve", "apply", "capture", "resolve", "verify"]
