import unittest

from agentmon.sampling.ascend_smi import parse_npu_smi_text


class AscendSmiTests(unittest.TestCase):
    def test_key_value_parser_normalizes_usage_fields(self) -> None:
        text = """
Memory Capacity(MB) : 65536
Memory Usage(MB) : 12345
Aicore Usage Rate(%) : 76
Temperature(C) : 42
Power(W) : 310
""".strip()

        parsed = parse_npu_smi_text(text)

        self.assertEqual(parsed.parser, "usage_key_value")
        device = parsed.devices[0]
        self.assertEqual(device["hbm_total_bytes"], 65536 * 1024 * 1024)
        self.assertEqual(device["hbm_used_bytes"], 12345 * 1024 * 1024)
        self.assertEqual(device["aicore_util_pct"], 76)
        self.assertEqual(device["temperature_c"], 42)

    def test_table_parser_uses_hbm_ratio_not_hugepages_zero_ratio(self) -> None:
        text = """
+--------------------------------------------------------------------------------+
| NPU     Name       | Health | Power(W) Temp(C) Hugepages-Usage(page)           |
| Chip    Device     | Bus-Id | AICore(%) Memory-Usage(MB) HBM-Usage(MB)        |
+--------------------------------------------------------------------------------+
| 0       910B       | OK     | 310      42      0 / 0                          |
| 0                 | 0000:00| 76       0 / 0            12345 / 65536         |
+--------------------------------------------------------------------------------+
""".strip()

        parsed = parse_npu_smi_text(text)

        self.assertEqual(parsed.parser, "table_910")
        device = parsed.devices[0]
        self.assertEqual(device["device_id"], "0")
        self.assertEqual(device["chip_id"], "0")
        self.assertEqual(device["hbm_total_bytes"], 65536 * 1024 * 1024)
        self.assertEqual(device["hbm_used_bytes"], 12345 * 1024 * 1024)
        self.assertEqual(device["aicore_util_pct"], 76)

    def test_usage_rate_percent_is_not_reported_as_hbm_bytes(self) -> None:
        text = """
Memory Capacity(MB) : 65536
Memory Usage Rate(%) : 0
Aicore Usage Rate(%) : 0
""".strip()

        parsed = parse_npu_smi_text(text)

        device = parsed.devices[0]
        self.assertEqual(device["hbm_total_bytes"], 65536 * 1024 * 1024)
        self.assertNotIn("hbm_used_bytes", device)
        self.assertEqual(device["aicore_util_pct"], 0)
