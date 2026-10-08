from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from enum import Enum
from typing import Any

from kvreuse_b5.recipes import default_recipe_registry


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum): return value.value
    if isinstance(value, tuple): return list(value)
    if isinstance(value, dict): return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list): return [_jsonable(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect paper recipe contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    show = sub.add_parser("show"); show.add_argument("recipe_id")
    check = sub.add_parser("check"); check.add_argument("recipe_id")
    args = parser.parse_args(argv)
    registry = default_recipe_registry()
    if args.command == "list":
        for item in registry.list_recipes():
            print(f"{item.recipe_id}\t{item.spec_status}\t{item.max_fidelity}\t{'executable-component' if item.implementable_now else 'blocked'}")
        return 0
    item = registry.get(args.recipe_id)
    payload = _jsonable(asdict(item))
    payload["recipe_spec_hash"] = item.recipe_spec_hash
    if hasattr(item, "recipe_variant_hash"): payload["recipe_variant_hash"] = item.recipe_variant_hash
    if args.command == "show":
        print(json.dumps(payload, indent=2, sort_keys=True)); return 0
    required = payload.get("required_providers", [])
    blockers = payload.get("blocking_fields", payload.get("unresolved_fields", []))
    print(json.dumps({"recipe_id": args.recipe_id, "required_providers": required, "blocking_fields": blockers, "hash": payload["recipe_spec_hash"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
