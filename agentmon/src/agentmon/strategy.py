from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class StrategyDescriptor:
    name: str
    domain: str
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class StrategyRegistry(Generic[T]):
    """Small in-process strategy registry.

    Runtime plugins can register factories during process startup, while core
    services only need to look up a configured strategy name.
    """

    def __init__(self, domain: str) -> None:
        self.domain = domain
        self._factories: dict[str, Callable[..., T]] = {}
        self._descriptions: dict[str, str] = {}

    def register(
        self,
        name: str,
        factory: Callable[..., T],
        *,
        description: str = "",
        replace: bool = False,
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError(f"{self.domain} strategy name must not be empty")
        if normalized in self._factories and not replace:
            raise ValueError(f"{self.domain} strategy already registered: {normalized}")
        self._factories[normalized] = factory
        self._descriptions[normalized] = description

    def unregister(self, name: str) -> None:
        normalized = name.strip()
        self._factories.pop(normalized, None)
        self._descriptions.pop(normalized, None)

    def create(self, name: str, *args: object, **kwargs: object) -> T:
        normalized = name.strip()
        try:
            factory = self._factories[normalized]
        except KeyError as exc:
            available = ", ".join(self.names()) or "none"
            raise ValueError(
                f"{self.domain} strategy not registered: {normalized}; available: {available}"
            ) from exc
        return factory(*args, **kwargs)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def catalog(self) -> list[dict[str, str]]:
        return [
            StrategyDescriptor(
                name=name,
                domain=self.domain,
                description=self._descriptions.get(name, ""),
            ).to_dict()
            for name in self.names()
        ]
