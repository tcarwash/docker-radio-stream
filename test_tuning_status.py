import unittest

import tuning_status


class TuningStatusTests(unittest.TestCase):
    def test_page_state_separates_logical_mode_from_inverted_page_mode(self):
        display = tuning_status.format_status({
            'state': 'page_state',
            'frequency_khz': 14074,
            'mode': 'usb',
            'page_mode': 'lsb',
            'mode_inverted': True,
            'passband_hz': [-3000, 0],
        })
        self.assertIn('Requested: 14.074 MHz USB', display)
        self.assertIn('WebSDR page mode: LSB (inverted mapping; page-local state)', display)
        self.assertIn('Page passband: -3000..0 Hz', display)
        self.assertNotIn('confirmed', display.lower())

    def test_requested_frequency_is_marked_unverified(self):
        display = tuning_status.format_status({
            'state': 'requested',
            'frequency_khz': 7074,
            'mode': 'lsb',
            'passband_hz': [-3000, 0],
        })
        self.assertIn('Requested: 7.074 MHz LSB', display)
        self.assertIn('WebSDR page readback: pending', display)

    def test_external_audio_does_not_claim_a_frequency(self):
        display = tuning_status.format_status({'state': 'external'})
        self.assertIn('External PulseAudio feed', display)
        self.assertIn('Frequency: not reported', display)


if __name__ == '__main__':
    unittest.main()
