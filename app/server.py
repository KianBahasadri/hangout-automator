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


def main() -> None:
    run()


if __name__ == "__main__":
    main()
