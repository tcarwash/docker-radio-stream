import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import radio


class RadioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mapping = Path(self.tmp.name) / 'receivers.json'
        self.mapping.write_text(json.dumps([
            {'name': 'low', 'min_khz': 100, 'max_khz': 8000, 'host': 'low.example', 'port': 8073},
            {'name': 'high', 'min_khz': 8000, 'max_khz': 30000, 'host': 'high.example', 'port': 80},
        ]))
        self.env = {'RX_FREQ_KHZ': '14074', 'RX_MODE': 'usb', 'RX_BANDWIDTH_HZ': '3000',
                    'RECEIVERS_FILE': str(self.mapping)}

    def test_selects_receiver_and_passband(self):
        cfg = radio.configure(self.env)
        self.assertEqual(cfg.receiver['host'], 'high.example')
        self.assertEqual(cfg.receiver_command()[-10:], ['-f', '14074', '-m', 'usb', '-L', '0', '-H', '3000', '--nc', '--nc-wav'])

    def test_public_utah_frequency_map_selects_omni_kiwi(self):
        mapping = Path(__file__).parent / 'config' / 'receivers.json'
        for frequency in ('1800', '7040', '14074', '28074'):
            with self.subTest(frequency=frequency):
                cfg = radio.configure(dict(self.env, RECEIVERS_FILE=str(mapping), RX_FREQ_KHZ=frequency))
                self.assertEqual(cfg.receiver['host'], 'kiwisdr1.sdrutah.org')
                self.assertEqual(cfg.receiver['port'], 8073)

    def test_frequency_boundary_chooses_first_match(self):
        self.env['RX_FREQ_KHZ'] = '8000'
        self.assertEqual(radio.configure(self.env).receiver['name'], 'low')

    def test_am_bandwidth_is_total_width(self):
        self.env['RX_MODE'] = 'am'
        cfg = radio.configure(self.env)
        self.assertEqual(cfg.passband, (-1500, 1500))

    def test_lsb_passband(self):
        self.env['RX_MODE'] = 'lsb'
        self.assertEqual(radio.configure(self.env).passband, (-3000, 0))

    def test_invalid_frequency_mode_bandwidth_and_missing_station_fail_closed(self):
        for key, value in [('RX_FREQ_KHZ', 'oops'), ('RX_FREQ_KHZ', '99999'),
                           ('RX_MODE', 'iq'), ('RX_BANDWIDTH_HZ', '0'),
                           ('RX_BANDWIDTH_HZ', '30000')]:
            with self.subTest(key=key, value=value):
                env = dict(self.env, **{key: value})
                with self.assertRaises(ValueError):
                    radio.configure(env)

    def test_malformed_receiver_mapping_rejected(self):
        self.mapping.write_text(json.dumps([{'name': 'oops', 'min_khz': 0, 'max_khz': 30000,
                                             'host': '-bad', 'port': 8073}]))
        with self.assertRaises(ValueError):
            radio.configure(self.env)

    def test_stream_disabled_without_key_or_output(self):
        self.assertIsNone(radio.configure(self.env).stream_command())

    def test_local_stream_command_has_video_and_audio(self):
        self.env['STREAM_OUTPUT'] = str(Path(self.tmp.name) / 'stream.flv')
        command = radio.configure(self.env).stream_command()
        self.assertIn('x11grab', command)
        self.assertIn('rx_audio.monitor', command)
        self.assertEqual(command[-1], self.env['STREAM_OUTPUT'])

    def test_app_command_is_argv_not_shell(self):
        self.env['APP_COMMAND'] = 'xterm -e "echo hello; touch /bad"'
        self.assertEqual(radio.configure(self.env).app_command,
                         ['xterm', '-e', 'echo hello; touch /bad'])

    def test_rejects_conflicting_stream_destinations(self):
        self.env['STREAM_OUTPUT'] = 'test.flv'
        self.env['YT_STREAM_KEY'] = 'secret'
        with self.assertRaises(ValueError):
            radio.configure(self.env)

    def test_vnc_requires_password_file(self):
        self.env['VNC_PASSWORD_FILE'] = str(Path(self.tmp.name) / 'missing')
        with self.assertRaisesRegex(ValueError, 'VNC password file missing'):
            radio.configure(self.env)

    def test_receiver_command_not_shell_interpolated(self):
        self.mapping.write_text(json.dumps([{'name': 'bad', 'min_khz': 0, 'max_khz': 30000,
                                             'host': 'a;touch /bad', 'port': 8073}]))
        with self.assertRaises(ValueError):
            radio.configure(self.env)


if __name__ == '__main__':
    unittest.main()
