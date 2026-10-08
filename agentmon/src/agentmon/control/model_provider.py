from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from agentmon.config import AgentMonConfig
from agentmon.optimizer.types import OptimizerDecision
from agentmon.runtime.audit import RuntimeAuditLog

from .model_gate import ModelGateLease, ModelRequestGate


@dataclass(frozen=True)
class ModelProviderResult:
    accepted: bool
    lease: ModelGateLease
    status_code: int | None = None
    body: Any = None
    error: str | None = None
    quality: str = "openai_compatible_http"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.control.model_provider_result",
            **asdict(self),
            "lease": self.lease.to_dict(),
        }


class GatedOpenAICompatibleProvider:
    """OpenAI-compatible model client guarded by ModelRequestGate."""

    def __init__(self, cfg: AgentMonConfig, gate: ModelRequestGate | None = None) -> None:
        self.cfg = cfg
        self.gate = gate or ModelRequestGate(cfg)
        self.audit = RuntimeAuditLog(cfg)

    def chat_completions(
        self,
        payload: dict[str, Any],
        *,
        decision: OptimizerDecision | None = None,
        request_id: str | None = None,
        current_inflight_requests: int | None = None,
    ) -> ModelProviderResult:
        lease = self.gate.try_acquire(
            decision,
            request_id=request_id,
            current_inflight_requests=current_inflight_requests,
        )
        if not lease.admitted:
            result = ModelProviderResult(
                accepted=False,
                lease=lease,
                error=lease.reason,
                quality="local_gate_rejected",
            )
            self.audit.emit(
                "control.model_provider.rejected",
                source="control",
                payload=result.to_dict(),
            )
            return result
        try:
            status_code, body = self._post_json("chat/completions", payload, lease.request_id)
            result = ModelProviderResult(
                accepted=True,
                lease=lease,
                status_code=status_code,
                body=body,
            )
            self.audit.emit(
                "control.model_provider.completed",
                source="control",
                payload={
                    "accepted": result.accepted,
                    "status_code": result.status_code,
                    "lease": result.lease.to_dict(),
                    "error": result.error,
                },
            )
            return result
        except HTTPError as exc:
            body = _decode_response_body(exc.read())
            result = ModelProviderResult(
                accepted=True,
                lease=lease,
                status_code=exc.code,
                body=body,
                error=str(exc),
            )
            self.audit.emit(
                "control.model_provider.error",
                source="control",
                payload={
                    "accepted": result.accepted,
                    "status_code": result.status_code,
                    "lease": result.lease.to_dict(),
                    "error": result.error,
                },
            )
            return result
        except URLError as exc:
            result = ModelProviderResult(accepted=True, lease=lease, error=str(exc.reason))
            self.audit.emit(
                "control.model_provider.error",
                source="control",
                payload={"accepted": result.accepted, "lease": result.lease.to_dict(), "error": result.error},
            )
            return result
        finally:
            if current_inflight_requests is None:
                self.gate.release(lease.request_id)

    def _post_json(self, endpoint: str, payload: dict[str, Any], request_id: str) -> tuple[int, Any]:
        base = self.cfg.vllm.api_base.rstrip("/") + "/"
        url = urljoin(base, endpoint)
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "agentmon/0.1",
                "X-AgentMon-Request-Id": request_id,
            },
        )
        with urlopen(request, timeout=self.cfg.vllm.request_timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200))
            return status_code, _decode_response_body(response.read())


def _decode_response_body(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
