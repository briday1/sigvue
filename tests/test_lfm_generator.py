import unittest
from math import log10
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np

from scripts.generate_lfm_collection import (
    OTA_PRF_HZ,
    OTA_PULSE_WIDTH_SECONDS,
    R_OHMS,
    THERMAL_NOISE_DBM_HZ,
    VOLTS_PER_COUNT,
    noise_component_std,
    write_member,
)
from examples.lfm_pipeline import _averaged_psd, _single_psd


class LfmGeneratorTests(unittest.TestCase):
    def test_noise_scale_encodes_requested_noise_figure(self):
        sample_rate = 10_000_000
        noise_figure = 7.0
        component_std = noise_component_std(sample_rate, noise_figure)
        complex_noise_power = (2 * component_std**2) / (2 * R_OHMS)
        psd_dbm_hz = 10 * log10((complex_noise_power / sample_rate) / 1e-3)
        self.assertAlmostEqual(THERMAL_NOISE_DBM_HZ + noise_figure, psd_dbm_hz)

    def test_terminated_noise_member_is_white_and_recovers_noise_figure(self):
        sample_rate = 10_000_000
        noise_figure = 7.0
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_member(
                root,
                "terminated-noise",
                channel=1,
                duration=0.01,
                sample_rate=sample_rate,
                calibration_dbm=-20.0,
                noise_figure_db=noise_figure,
                seed=1234,
            )
            iq = np.fromfile(root / "terminated-noise-ch1.sigmf-data", dtype="<i2").reshape(-1, 2)
            noise = (iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)) * VOLTS_PER_COUNT

        measured_power = np.mean(np.abs(noise) ** 2) / (2 * R_OHMS)
        measured_psd_dbm_hz = 10 * np.log10((measured_power / sample_rate) / 1e-3)
        lag_one_correlation = abs(np.vdot(noise[:-1], noise[1:])) / np.vdot(noise, noise).real
        _, averaged_psd = _averaged_psd(noise, sample_rate)
        self.assertAlmostEqual(noise_figure, measured_psd_dbm_hz - THERMAL_NOISE_DBM_HZ, delta=0.15)
        self.assertLess(lag_one_correlation, 0.015)
        self.assertLess(float(np.std(averaged_psd)), 0.7)

    def test_contiguous_calibration_samples_retain_the_one_mhz_tone(self):
        sample_rate = 10_000_000
        time = np.arange(4096) / sample_rate
        tone = np.exp(1j * 2 * np.pi * 1_000_000 * time)
        frequency, psd = _single_psd(tone, sample_rate)
        self.assertLessEqual(abs(float(frequency[np.argmax(psd)]) - 1_000_000), sample_rate / 1024)

    def test_generated_calibration_is_a_pure_known_power_tone(self):
        sample_rate = 10_000_000
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_member(root, "calibration", 1, 0.001, sample_rate, -20.0, 7.0, 1234)
            iq = np.fromfile(root / "calibration-ch1.sigmf-data", dtype="<i2").reshape(-1, 2)
        tone = (iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)) * VOLTS_PER_COUNT
        time = np.arange(tone.size) / sample_rate
        carrier = np.exp(1j * 2 * np.pi * 1_000_000 * time)
        coefficient = np.vdot(carrier, tone) / np.vdot(carrier, carrier)
        residual = tone - coefficient * carrier
        measured_dbm = 10 * np.log10((np.mean(np.abs(tone) ** 2) / (2 * R_OHMS)) / 1e-3)
        self.assertAlmostEqual(-20.0, measured_dbm, delta=0.01)
        self.assertLess(np.mean(np.abs(residual) ** 2) / np.mean(np.abs(tone) ** 2), 1e-8)

    def test_generated_ota_is_a_one_khz_pulsed_lfm(self):
        sample_rate = 10_000_000
        duration = 0.005
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_member(root, "ota", 1, duration, sample_rate, -20.0, 7.0, 1234)
            iq = np.fromfile(root / "ota-ch1.sigmf-data", dtype="<i2").reshape(-1, 2)
        ota = iq[:, 0].astype(float) + 1j * iq[:, 1].astype(float)
        pri_samples = round(sample_rate / OTA_PRF_HZ)
        pulse_samples = round(sample_rate * OTA_PULSE_WIDTH_SECONDS)
        rows = ota.reshape(round(duration * OTA_PRF_HZ), pri_samples)
        on_power = np.mean(np.abs(rows[:, :pulse_samples]) ** 2)
        off_power = np.mean(np.abs(rows[:, pulse_samples:]) ** 2)
        self.assertEqual(5, rows.shape[0])
        self.assertGreater(10 * np.log10(on_power / off_power), 70.0)


if __name__ == "__main__":
    unittest.main()
