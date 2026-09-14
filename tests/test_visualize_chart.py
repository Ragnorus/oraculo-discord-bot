from __future__ import annotations

import unittest

from oraculo_bot.visualize_chart import generate_chart_html


class VisualizeChartSecurityTest(unittest.TestCase):
    def test_generate_chart_html_escapes_closing_script_tags_in_json(self) -> None:
        payload = {
            "title": "Race",
            "queue": "All queues",
            "period": "Weekly",
            "frames": [
                {
                    "date": "2026-08-01",
                    "values": [
                        {"name": "A</script><script>alert(1)</script>", "value": 42.0},
                    ],
                }
            ],
        }

        html = generate_chart_html(payload)

        self.assertIn('<script id="race-data" type="application/json">', html)
        self.assertIn("A<\\/script><script>alert(1)<\\/script>", html)
        self.assertNotIn("A</script><script>alert(1)</script>", html)
