from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.event_logging import audit_event

settings = get_settings()

engine = create_engine(settings.database_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_PENDING_AUDIT_CHANGES = "hangout_pending_audit_changes"


def _audit_scalar(value):  # type: ignore[no-untyped-def]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return _audit_scalar(enum_value)
    return str(value)


def _audit_model_change(obj, operation: str):  # type: ignore[no-untyped-def]
    state = inspect(obj)
    fields: dict[str, object] = {}
    for attribute in state.mapper.column_attrs:
        history = state.attrs[attribute.key].history
        if operation == "updated" and not history.has_changes():
            continue
        if operation == "updated":
            fields[attribute.key] = {
                "old": [_audit_scalar(value) for value in history.deleted],
                "new": [_audit_scalar(value) for value in history.added],
            }
        else:
            fields[attribute.key] = _audit_scalar(getattr(obj, attribute.key, None))
    if operation == "updated" and not fields:
        return None
    return {
        "model": type(obj).__name__,
        "operation": operation,
        "id": _audit_scalar(getattr(obj, "id", None)),
        "fields": fields,
    }


def _collect_audit_changes(session: Session) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for obj in session.new:
        change = _audit_model_change(obj, "created")
        if change:
            changes.append(change)
    for obj in session.dirty:
        if obj in session.new or obj in session.deleted:
            continue
        change = _audit_model_change(obj, "updated")
        if change:
            changes.append(change)
    for obj in session.deleted:
        change = _audit_model_change(obj, "deleted")
        if change:
            changes.append(change)
    return changes


@event.listens_for(Session, "before_commit")
def _audit_transaction_started(session: Session) -> None:
    changes = _collect_audit_changes(session)
    session.info[_PENDING_AUDIT_CHANGES] = changes
    audit_event(
        "database.transaction.commit_started",
        change_count=len(changes),
        changes=changes,
    )


@event.listens_for(Session, "after_commit")
def _audit_transaction_committed(session: Session) -> None:
    changes = session.info.pop(_PENDING_AUDIT_CHANGES, [])
    audit_event(
        "database.transaction.committed",
        change_count=len(changes),
        changes=changes,
    )


@event.listens_for(Session, "after_rollback")
def _audit_transaction_rolled_back(session: Session) -> None:
    changes = session.info.pop(_PENDING_AUDIT_CHANGES, [])
    audit_event(
        "database.transaction.rolled_back",
        attempted_change_count=len(changes),
        attempted_changes=changes,
    )


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Default dietary-restriction catalog entries, seeded by the baseline Alembic
# migration (one-shot by construction). Tests re-insert them after wiping tables.
DEFAULT_DIETARY_RESTRICTIONS = ("meat", "pork")
