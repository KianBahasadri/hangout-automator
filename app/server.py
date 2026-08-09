"""Uvicorn launchers for the web process.

Two console scripts point here (see [project.scripts] in pyproject.toml):
`dev` reloads on edit, `main` does not. The worker process is separate —
see app/worker.py.
"""

from __future__ import annotations

import uvicorn

from app.config import get_settings


def run(*, reload: bool = False) -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=reload,
    )


def dev() -> None:
    """Entry point for `uv run dev` — same launcher, with reload."""
    run(reload=True)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
