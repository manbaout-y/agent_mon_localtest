"""Opt-in profile bridge; no model-specific request parameters are introduced.

mini-SWE's query accepts **kwargs, while the existing instrumentation only
injects correlation headers when extra_headers is explicit. Promote the
profile's headers into query kwargs before entering that unchanged wrapper.
All API parameters (including extra_body) remain mini-SWE/LiteLLM's concern.
"""
from __future__ import annotations

from .model import InstrumentedLitellmModel


def profile_request_kwargs(config_kwargs, call_kwargs):
    result = dict(call_kwargs)
    headers = dict(config_kwargs.get("extra_headers") or {})
    headers.update(result.get("extra_headers") or {})
    result["extra_headers"] = headers
    return result


class ProfiledLitellmModel(InstrumentedLitellmModel):
    @property
    def model_name(self):
        return self.config.model_name

    def query(self, messages, **kwargs):
        return super().query(messages, **profile_request_kwargs(self.config.model_kwargs, kwargs))
