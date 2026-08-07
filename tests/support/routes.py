"""The app's HTTP surface, read off the live FastAPI router.

Smoke tests parametrize over this instead of a hand-written list, so a route
added tomorrow gets the same treatment without anyone remembering to register
it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args, get_origin

from fastapi import params
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.main import app

# FastAPI's own docs endpoints: none of our handler code runs behind them.
DOC_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


@dataclass(frozen=True)
class FormField:
    name: str
    required: bool
    repeated: bool  # list[...]: submitted as a repeated form key


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str
    endpoint: str
    is_page: bool  # an HTML page a person can navigate to
    path_params: tuple[str, ...]
    form_fields: tuple[FormField, ...]
    body_model: type[BaseModel] | None

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def takes_form(self) -> bool:
        return bool(self.form_fields)

    @property
    def takes_json(self) -> bool:
        return self.body_model is not None

    @property
    def field_names(self) -> tuple[str, ...]:
        if self.form_fields:
            return tuple(field.name for field in self.form_fields)
        if self.body_model is not None:
            return tuple(self.body_model.model_fields)
        return ()


def _mentions_list(annotation: Any) -> bool:
    """True for list[...] and for unions containing it, e.g. list[int] | None."""
    for candidate in (annotation, *get_args(annotation)):
        if candidate is list or get_origin(candidate) is list:
            return True
    return False


def _model_in(annotation: Any) -> type[BaseModel] | None:
    for candidate in (annotation, *get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def iter_routes() -> tuple[RouteSpec, ...]:
    """Every route the app serves, one spec per method."""
    specs: list[RouteSpec] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in DOC_PATHS:
            continue

        form_fields: list[FormField] = []
        body_model: type[BaseModel] | None = None
        for param in route.dependant.body_params:
            if isinstance(param.field_info, params.Form):
                form_fields.append(
                    FormField(
                        name=param.name,
                        required=param.required,
                        repeated=_mentions_list(param.field_info.annotation),
                    )
                )
            elif body_model is None:
                body_model = _model_in(param.field_info.annotation)

        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            specs.append(
                RouteSpec(
                    method=method,
                    path=route.path,
                    endpoint=route.name,
                    is_page=method == "GET" and "web" in (route.tags or []),
                    path_params=tuple(param.name for param in route.dependant.path_params),
                    form_fields=tuple(form_fields),
                    body_model=body_model,
                )
            )
    return tuple(sorted(specs, key=lambda spec: (spec.path, spec.method)))


ALL_ROUTES = iter_routes()
PAGE_ROUTES = tuple(spec for spec in ALL_ROUTES if spec.is_page)


def fill_path(path: str, ids: dict[str, int]) -> str:
    for name, value in ids.items():
        path = path.replace("{" + name + "}", str(value))
    return path
