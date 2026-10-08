import math
import unittest

from agentmon.analysis.prometheus import parse_prometheus_text


class PrometheusTests(unittest.TestCase):
    def test_parser_handles_labels_escapes_histogram_and_nan(self) -> None:
        text = r'''
# HELP vllm:request_latency_seconds request latency
# TYPE vllm:request_latency_seconds histogram
vllm:request_latency_seconds_bucket{model="qwen\"x",le="+Inf"} 3
vllm:request_latency_seconds_sum{model="qwen\nx"} 1.5
vllm:gpu_cache_usage_perc NaN
'''.strip()

        samples = parse_prometheus_text(text)

        self.assertEqual(samples[0].metric_type, "histogram")
        self.assertEqual(samples[0].labels["model"], 'qwen"x')
        self.assertEqual(samples[0].labels["le"], "+Inf")
        self.assertEqual(samples[1].labels["model"], "qwen\nx")
        self.assertTrue(math.isnan(samples[2].value))
