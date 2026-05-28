import unittest

from andie_adapters.telemetry_evaluation import assess_telemetry_curve


class TelemetryEvaluationTests(unittest.TestCase):
    def test_improving_curve(self):
        assessment = assess_telemetry_curve((0.55, 0.62, 0.71, 0.79))
        self.assertEqual("improving", assessment.trend)
        self.assertLess(assessment.threshold_adjustment, 0.0)

    def test_degrading_curve(self):
        assessment = assess_telemetry_curve((0.92, 0.86, 0.8, 0.74))
        self.assertEqual("degrading", assessment.trend)
        self.assertGreater(assessment.threshold_adjustment, 0.0)

    def test_oscillating_curve_extends_window(self):
        assessment = assess_telemetry_curve((0.8, 0.6, 0.82, 0.58, 0.84))
        self.assertEqual("oscillating", assessment.trend)
        self.assertGreater(assessment.timeout_extension_seconds, 0)


if __name__ == "__main__":
    unittest.main()
