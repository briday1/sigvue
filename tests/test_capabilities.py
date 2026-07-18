import unittest

from sigvue.plugin import Annotation, AnnotationField, AnnotationPlotBinding


class CapabilityTests(unittest.TestCase):
    def test_plot_bound_number_field_validates_and_retains_transform(self):
        binding = AnnotationPlotBinding("waterfall", "xaxis2", "lower", scale=1e6)
        field = AnnotationField("frequency_lower_hz", "Lower frequency", "number", plot_binding=binding)
        self.assertEqual("waterfall", field.plot_binding.view)
        self.assertEqual(1e6, field.plot_binding.scale)

    def test_annotation_frequency_bounds_must_be_paired_and_increasing(self):
        with self.assertRaises(ValueError):
            Annotation("a", 0.0, frequency_lower_hz=1.0)
        with self.assertRaises(ValueError):
            Annotation("a", 0.0, frequency_lower_hz=2.0, frequency_upper_hz=1.0)
        annotation = Annotation("a", 0.0, frequency_lower_hz=1.0, frequency_upper_hz=2.0)
        self.assertEqual((1.0, 2.0), (annotation.frequency_lower_hz, annotation.frequency_upper_hz))


if __name__ == "__main__":
    unittest.main()
