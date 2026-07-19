import unittest

from sigvue.plugin import Annotation, AnnotationField, AnnotationPlotBinding, AnnotationRequest


class CapabilityTests(unittest.TestCase):
    def test_plot_bound_number_field_validates_and_retains_transform(self):
        binding = AnnotationPlotBinding("waterfall", "xaxis2", "lower", scale=1e6)
        field = AnnotationField("frequency_lower_hz", "Lower frequency", "number", plot_binding=binding)
        self.assertEqual("waterfall", field.plot_binding.view)
        self.assertEqual(1e6, field.plot_binding.scale)
        self.assertEqual("axis", field.plot_binding.selection_policy)
        self.assertEqual(
            "box_preferred",
            AnnotationPlotBinding(
                "waterfall", "xaxis2", "lower", selection_policy="box_preferred"
            ).selection_policy,
        )
        with self.assertRaisesRegex(ValueError, "selection policy"):
            AnnotationPlotBinding("waterfall", "xaxis2", "lower", selection_policy="always")

    def test_annotation_frequency_bounds_must_be_paired_and_increasing(self):
        with self.assertRaises(ValueError):
            Annotation("a", 0.0, frequency_lower_hz=1.0)
        with self.assertRaises(ValueError):
            Annotation("a", 0.0, frequency_lower_hz=2.0, frequency_upper_hz=1.0)
        annotation = Annotation("a", 0.0, frequency_lower_hz=1.0, frequency_upper_hz=2.0)
        self.assertEqual((1.0, 2.0), (annotation.frequency_lower_hz, annotation.frequency_upper_hz))

    def test_annotation_can_target_one_local_view_choice(self):
        annotation = Annotation("a", 0.0, view_selections={"channel": 2})
        self.assertEqual({"channel": 2}, annotation.view_selections)
        with self.assertRaises(TypeError):
            annotation.view_selections["channel"] = 1

    def test_annotation_request_validates_and_freezes_view_selections(self):
        request = AnnotationRequest(1.0, values={"comment": "target"}, view_selections={"channel": 2})
        self.assertEqual(2, request.view_selections["channel"])
        with self.assertRaises(TypeError):
            request.view_selections["channel"] = 1
        with self.assertRaisesRegex(ValueError, "non-negative indexes"):
            AnnotationRequest(1.0, view_selections={"channel": True})


if __name__ == "__main__":
    unittest.main()
