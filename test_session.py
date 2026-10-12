import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import session


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_retired_host_helper_fails_before_starting_containers(self):
        import os
        import subprocess
        result = subprocess.run(['bash', str(Path(__file__).parent / 'tools/websdr/websdr-audio.sh')],
                                env={'PATH': os.environ['PATH']}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('session.py --minutes 5', result.stderr)
        self.assertIn('retired', result.stderr)

    def test_session_duration_can_exceed_25_minutes(self):
        cfg = Mock()
        cfg.stream_command.return_value = None
        with patch('session.configure', return_value=cfg), \
             patch('session.spawn'), patch('session.terminate'), \
             patch('session.time.monotonic', side_effect=[0, 0, 1801]), \
             patch('session.time.sleep'):
            self.assertTrue(session.run(30, audio_source='pulse'))

    def test_concurrent_sessions_are_still_rejected(self):
        path = Path(self.temp.name) / 'session.lock'
        with session.session_lock(path):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                with session.session_lock(path):
                    pass

    def test_disconnected_receiver_reports_failure(self):
        cfg = Mock()
        cfg.receiver = {'name': 'offline-test'}
        cfg.frequency = 14074
        cfg.mode = 'usb'
        cfg.passband = (0, 3000)
        cfg.receiver_command.return_value = ['kiwirecorder']
        cfg.stream_command.return_value = None
        rx = Mock(stdout=Mock())
        rx.poll.return_value = 1
        playback = Mock()
        with patch('session.configure', return_value=cfg), patch('session.spawn', side_effect=[rx, playback]), patch('session.terminate'):
            self.assertFalse(session.run(1, audio_source='kiwi'))

    def test_default_websdr_session_pipes_pcm_and_cleans_up(self):
        cfg = Mock(receiver={'name': 'fixture', 'mode_inverted': True}, frequency=7074, mode='usb', passband=(0, 3000))
        cfg.websdr_command.return_value = ['node', 'bridge', 'http://fixture', '7074', 'usb', '0', '3000']
        cfg.stream_command.return_value = ['ffmpeg', 'video']
        rx, playback, stream = Mock(stdout=Mock()), Mock(), Mock()
        rx.poll.return_value = 1
        with patch('session.configure', return_value=cfg), \
             patch('session.spawn', side_effect=[rx, playback, stream]) as spawn, \
             patch('session.terminate') as terminate:
            self.assertFalse(session.run(1))
        self.assertEqual(spawn.call_args_list[0].args[0], cfg.websdr_command())
        self.assertEqual(spawn.call_args_list[0].kwargs['env']['TUNING_MODE_INVERTED'], '1')
        self.assertIn('s16le', spawn.call_args_list[1].args[0])
        self.assertIn('8000', spawn.call_args_list[1].args[0])
        self.assertEqual([c.args[0] for c in terminate.call_args_list], [stream, playback, rx])

    def test_pulse_source_starts_stream_without_kiwirecorder(self):
        cfg = Mock()
        cfg.stream_command.return_value = ['ffmpeg', 'stream']
        stream = Mock()
        stream.poll.return_value = None
        with patch('session.configure', return_value=cfg), \
             patch('session.spawn', return_value=stream) as spawn, \
             patch('session.terminate') as terminate, \
             patch('session.time.monotonic', side_effect=[0, 0, 61]), \
             patch('session.time.sleep'):
            self.assertTrue(session.run(1, audio_source='pulse'))
        cfg.receiver_command.assert_not_called()
        spawn.assert_called_once_with(['ffmpeg', 'stream'])
        terminate.assert_any_call(stream)


if __name__ == '__main__':
    unittest.main()
