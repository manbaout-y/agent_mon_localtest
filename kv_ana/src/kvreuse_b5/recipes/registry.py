from __future__ import annotations

import json
from importlib.resources import files
from typing import Iterable

from .contract import DeclarativeRecipeSpec, PaperRecipeSpec


class RecipeRegistry:
    def __init__(self) -> None:
        self.declarative: dict[str, DeclarativeRecipeSpec] = {}
        self.executable: dict[str, PaperRecipeSpec] = {}

    def register_declarative(self, spec: DeclarativeRecipeSpec) -> None:
        if spec.recipe_id in self.declarative:
            raise ValueError(f"duplicate declarative recipe {spec.recipe_id}")
        self.declarative[spec.recipe_id] = spec

    def register_executable(self, spec: PaperRecipeSpec) -> None:
        if spec.recipe_id in self.executable:
            raise ValueError(f"duplicate executable recipe {spec.recipe_id}")
        self.executable[spec.recipe_id] = spec

    def list_recipes(self) -> list[DeclarativeRecipeSpec]:
        return sorted(self.declarative.values(), key=lambda item: item.paper_id)

    def list_by_paper(self, paper_id: str) -> list[DeclarativeRecipeSpec]:
        return [item for item in self.list_recipes() if item.paper_id == str(paper_id).zfill(2)]

    def list_by_capability(self, capability: str) -> list[DeclarativeRecipeSpec]:
        return [item for item in self.list_recipes() if capability in item.execution_requirements]

    def list_executable(self) -> list[PaperRecipeSpec]:
        return sorted(self.executable.values(), key=lambda item: item.recipe_id)

    def list_blocked(self) -> list[DeclarativeRecipeSpec]:
        return [item for item in self.list_recipes() if item.blocking_fields or item.spec_status.startswith("SPEC_BLOCKED")]

    def list_by_fidelity(self, fidelity: str) -> list[DeclarativeRecipeSpec]:
        return [item for item in self.list_recipes() if item.max_fidelity == fidelity]

    def get(self, recipe_id: str) -> DeclarativeRecipeSpec | PaperRecipeSpec:
        if recipe_id in self.executable:
            return self.executable[recipe_id]
        return self.declarative[recipe_id]


def _load_declarative() -> Iterable[DeclarativeRecipeSpec]:
    root = files("kvreuse_b5.recipes").joinpath("specs")
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.endswith(".yaml"):
            data = json.loads(entry.read_text(encoding="utf-8"))
            for key in ("execution_requirements", "required_providers", "blocking_fields"):
                data[key] = tuple(data[key])
            yield DeclarativeRecipeSpec(**data)


def default_recipe_registry() -> RecipeRegistry:
    from .agentkvshift import agentkvshift_spec
    from .cacheblend import cacheblend_spec
    from .cacheslide import cacheslide_spec
    from .kvcomm import kvcomm_spec

    registry = RecipeRegistry()
    for spec in _load_declarative(): registry.register_declarative(spec)
    for spec in (
        kvcomm_spec("paper_spec"), kvcomm_spec("pinned_code"),
        agentkvshift_spec("full_exact"),
        cacheblend_spec("paper_spec"), cacheblend_spec("pinned_code"),
        cacheslide_spec("paper_spec"), cacheslide_spec("pinned_code"),
    ):
        registry.register_executable(spec)
    return registry
