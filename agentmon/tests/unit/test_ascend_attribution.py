import tempfile
import unittest
from pathlib import Path

from agentmon.reporting.ascend import resolve_ascend_target_devices, summarize_ascend_devices


GIB = 1024**3


class AscendAttributionTests(unittest.TestCase):
    def test_target_devices_do_not_mix_host_wide_aicore(self) -> None:
        ascend = make_ascend_report()

        summary = summarize_ascend_devices(
            ascend,
            target_devices=["0", "1", "2", "3"],
            target_device_source="explicit",
            mapping_quality="explicit",
        )

        self.assertEqual(summary["target_peak_hbm_used_bytes"], 60 * GIB)
        self.assertEqual(summary["target_max_aicore_util_pct"], 2.0)
        self.assertEqual(summary["host_peak_hbm_used_bytes"], 60 * GIB)
        self.assertEqual(summary["host_max_aicore_util_pct"], 65.0)
        self.assertEqual(summary["target_attribution_status"], "complete")

    def test_missing_target_devices_do_not_fall_back_to_host_wide(self) -> None:
        summary = summarize_ascend_devices(make_ascend_report())

        self.assertEqual(summary["host_peak_hbm_used_bytes"], 60 * GIB)
        self.assertEqual(summary["host_max_aicore_util_pct"], 65.0)
        self.assertIsNone(summary["target_peak_hbm_used_bytes"])
        self.assertIsNone(summary["target_max_aicore_util_pct"])
        self.assertEqual(summary["target_attribution_status"], "unavailable")

    def test_explicit_devices_take_priority_over_metadata_and_proc_environ(self) -> None:
        events = [
            capability_event({"visible_devices": ["4", "5"]}),
            p2_event_with_pid(pid=1234, starttime=99),
        ]

        resolved = resolve_ascend_target_devices(explicit_devices="0,1,2,3", p2_events=events)

        self.assertEqual(resolved["devices"], ["0", "1", "2", "3"])
        self.assertEqual(resolved["source"], "explicit")
        self.assertEqual(resolved["mapping_quality"], "explicit")

    def test_p2_capability_metadata_resolves_target_devices(self) -> None:
        resolved = resolve_ascend_target_devices(
            p2_events=[capability_event({"visible_devices": ["0", "1", "2", "3"]})]
        )

        self.assertEqual(resolved["devices"], ["0", "1", "2", "3"])
        self.assertEqual(resolved["source"], "service_metadata")

    def test_proc_environ_starttime_mismatch_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            proc_dir = proc_root / "1234"
            proc_dir.mkdir()
            proc_dir.joinpath("stat").write_text("1234 (python) S " + " ".join(["0"] * 18 + ["88"]), encoding="utf-8")
            proc_dir.joinpath("environ").write_bytes(b"ASCEND_RT_VISIBLE_DEVICES=0,1,2,3\0")

            resolved = resolve_ascend_target_devices(
                p2_events=[p2_event_with_pid(pid=1234, starttime=99)],
                proc_root=proc_root,
            )

        self.assertEqual(resolved["devices"], [])
        self.assertEqual(resolved["source"], "unavailable")

    def test_proc_environ_matching_starttime_resolves_devices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            proc_dir = proc_root / "1234"
            proc_dir.mkdir()
            proc_dir.joinpath("stat").write_text("1234 (python) S " + " ".join(["0"] * 18 + ["99"]), encoding="utf-8")
            proc_dir.joinpath("environ").write_bytes(b"ASCEND_RT_VISIBLE_DEVICES=0,1,2,3\0")

            resolved = resolve_ascend_target_devices(
                p2_events=[p2_event_with_pid(pid=1234, starttime=99)],
                proc_root=proc_root,
            )

        self.assertEqual(resolved["devices"], ["0", "1", "2", "3"])
        self.assertEqual(resolved["source"], "proc_environ")


def make_ascend_report() -> dict:
    return {
        "status": "complete",
        "parser_confidence": 0.6,
        "hbm_quality": "sampled",
        "aicore_quality": "sampled",
        "devices": {
            "0": {"peak_hbm_used_bytes": 60 * GIB, "max_aicore_util_pct": 2.0},
            "1": {"peak_hbm_used_bytes": 60 * GIB, "max_aicore_util_pct": 2.0},
            "2": {"peak_hbm_used_bytes": 60 * GIB, "max_aicore_util_pct": 2.0},
            "3": {"peak_hbm_used_bytes": 60 * GIB, "max_aicore_util_pct": 2.0},
            "4": {"peak_hbm_used_bytes": 40 * GIB, "max_aicore_util_pct": 65.0},
            "5": {"peak_hbm_used_bytes": 40 * GIB, "max_aicore_util_pct": 64.0},
        },
    }


def capability_event(payload: dict) -> dict:
    return {"source": "vllm_p2", "event_type": "vllm.adapter.capability", "payload": payload}


def p2_event_with_pid(*, pid: int, starttime: int) -> dict:
    return {
        "source": "vllm_p2",
        "event_type": "vllm.request.received",
        "payload": {"source_pid": pid, "source_starttime_ticks": starttime},
    }
