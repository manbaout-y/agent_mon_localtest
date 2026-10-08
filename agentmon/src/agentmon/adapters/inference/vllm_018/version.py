from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import asdict, dataclass
import re
from typing import Any


SUPPORTED_VLLM_VERSION = "0.18.0"


class UnsupportedVllmVersion(RuntimeError):
    pass


@dataclass(frozen=True)
class VersionResolution:
    selected_version: str | None
    selected_release: str | None
    selected_source: str | None
    module_version: str | None
    module_release: str | None
    metadata_version: str | None
    metadata_release: str | None
    module_status: str
    metadata_status: str
    version_source_consistency: str
    source_consistency: str
    version_consistency: str
    version_identity_status: str
    compatibility_status: str
    version_status: str
    module_file: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def base_version(version: Any) -> str | None:
    release = release_version(version)
    if release is None:
        return None
    return ".".join(str(part) for part in release)


def release_version(version: Any) -> tuple[int, ...] | None:
    if version in {None, ""}:
        return None
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(version).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_supported_vllm_version(version: Any) -> bool:
    return base_version(version) == SUPPORTED_VLLM_VERSION


def resolve_vllm_version(imported_module: Any = None) -> VersionResolution:
    module = imported_module
    module_status = "ok"
    error_type: str | None = None
    error_message: str | None = None
    if module is None:
        try:
            module = importlib.import_module("vllm")
        except Exception as exc:  # noqa: BLE001 - version discovery must report import failures as data.
            module = None
            module_status = "import_error"
            error_type = type(exc).__name__
            error_message = str(exc)

    module_version = normalized_version(getattr(module, "__version__", None)) if module is not None else None
    module_file = str(getattr(module, "__file__", "")) or None if module is not None else None
    module_release = base_version(module_version)

    try:
        metadata_version = normalized_version(importlib.metadata.version("vllm"))
        metadata_status = "ok"
    except importlib.metadata.PackageNotFoundError:
        metadata_version = None
        metadata_status = "not_found"
    except Exception as exc:  # noqa: BLE001 - preserve unexpected metadata failures as evidence.
        metadata_version = None
        metadata_status = "error"
        if error_type is None:
            error_type = type(exc).__name__
            error_message = str(exc)
    metadata_release = base_version(metadata_version)

    if module_version is not None:
        selected_version = module_version
        selected_source = "module.__version__"
        selected_release = module_release
    elif metadata_version is not None:
        selected_version = metadata_version
        selected_source = "importlib.metadata"
        selected_release = metadata_release
    else:
        selected_version = None
        selected_source = None
        selected_release = None

    consistency = version_source_consistency(module_version, metadata_version)
    identity_status = version_identity_status(module_version, metadata_version)

    if module_status == "import_error":
        status = "unavailable"
    elif selected_version is None:
        status = "unavailable"
    elif is_supported_vllm_version(selected_version):
        status = "compatible"
    else:
        status = "mismatch"

    return VersionResolution(
        selected_version=selected_version,
        selected_release=selected_release,
        selected_source=selected_source,
        module_version=module_version,
        module_release=module_release,
        metadata_version=metadata_version,
        metadata_release=metadata_release,
        module_status=module_status,
        metadata_status=metadata_status,
        version_source_consistency=consistency,
        source_consistency=consistency,
        version_consistency=consistency,
        version_identity_status=identity_status,
        compatibility_status=status,
        version_status=status,
        module_file=module_file,
        error_type=error_type,
        error_message=error_message,
    )


def detected_vllm_version() -> str | None:
    return resolve_vllm_version().selected_version


def version_source_consistency(module_version: Any, metadata_version: Any) -> str:
    if module_version in {None, ""} or metadata_version in {None, ""}:
        return "unknown"
    module_release = release_version(module_version)
    metadata_release = release_version(metadata_version)
    if module_release is None or metadata_release is None:
        return "unknown"
    if module_release == metadata_release:
        return "consistent"
    return "conflict"


def version_identity_status(module_version: Any, metadata_version: Any) -> str:
    module = normalized_version(module_version)
    metadata = normalized_version(metadata_version)
    if module is None or metadata is None:
        return "unknown"
    if module == metadata:
        return "exact_match"
    if version_source_consistency(module, metadata) == "consistent":
        return "release_match"
    return "conflict"


def require_supported_vllm_version(version: Any = None) -> str:
    if version is None:
        resolution = resolve_vllm_version()
        detected = resolution.selected_version
        supported = resolution.version_status == "compatible"
        detail = f" ({resolution.version_status})"
    else:
        detected = normalized_version(version)
        supported = is_supported_vllm_version(detected)
        detail = ""
    if not supported:
        raise UnsupportedVllmVersion(
            f"AgentMon P2 supports vLLM {SUPPORTED_VLLM_VERSION}; detected {detected or 'unknown'}{detail}"
        )
    return detected


def normalized_version(version: Any) -> str | None:
    if version in {None, ""}:
        return None
    text = str(version).strip()
    return text or None
