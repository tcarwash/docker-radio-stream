import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import session


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'usage.json'

    def test_reservation_counts_requested_minutes(self):
        with session.reserve(self.state, 20, now=100000):
            self.assertEqual(json.loads(self.state.read_text()), [[100000, 20]])

    def test_rejects_more_than_session_limit(self):
        with self.assertRaisesRegex(ValueError, '25'):
            with session.reserve(self.state, 26, now=100000):
                pass

    def test_rejects_rolling_daily_budget_and_keeps_state(self):
        for now in (100000, 104000, 108000):
            with session.reserve(self.state, 25, now=now):
                pass
        before = self.state.read_text()
        with self.assertRaisesRegex(ValueError, 'daily'):
            with session.reserve(self.state, 20, now=110000):
                pass
        self.assertEqual(self.state.read_text(), before)

    def test_budget_returns_after_previous_session_outside_rolling_day(self):
        with session.reserve(self.state, 25, now=100000):
            pass
        with session.reserve(self.state, 25, now=100000 + 25 * 60 + 86401):
            self.assertEqual(len(json.loads(self.state.read_text())), 1)

    def test_duplicate_session_is_rejected(self):
        with session.reserve(self.state, 10, now=100000):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                with session.reserve(self.state, 10, now=100000):
                    pass

    def test_disconnected_receiver_reports_failure(self):
        cfg = Mock()
        cfg.receiver = {'name': 'offline-test'}
        cfg.frequency = 14074
        cfg.receiver_command.return_value = ['kiwirecorder']
        cfg.stream_command.return_value = None
        rx = Mock(stdout=Mock())
        rx.poll.return_value = 1
        playback = Mock()
        with patch('session.configure', return_value=cfg), patch('session.spawn', side_effect=[rx, playback]), patch('session.terminate'):
            self.assertIs(session.run(1, self.state), False)


if __name__ == '__main__':
    unittest.main()
