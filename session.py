"""Run one bounded receiver/stream session from docker compose exec."""
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from radio import configure, spawn, terminate


@contextmanager
def reserve(path, minutes, now=None):
    if not 1 <= minutes <= 25:
        raise ValueError('Session must be 1-25 minutes (local session limit)')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A session is already running') from None
        now = time.time() if now is None else now
        try:
            records = json.loads(path.read_text()) if path.exists() else []
            if not isinstance(records, list):
                raise ValueError('Usage state must be a list')
            records = [[float(start), int(duration)] for start, duration in records]
            if any(start > now or duration < 1 or duration > 25 for start, duration in records):
                raise ValueError('Usage state is invalid; refusing to reset quota')
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(f'Invalid usage state: {exc}') from exc
        # Conservatively count a whole session until 24h after it ENDED.
        records = [[start, duration] for start, duration in records
                   if start + duration * 60 > now - 86400]
        if sum(duration for _, duration in records) + minutes > 90:
            raise ValueError('The 90-minute rolling daily receive budget would be exceeded')
        records.append([now, minutes])
        staging = path.with_suffix('.new')
        staging.write_text(json.dumps(records))
        os.replace(staging, path)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run(minutes, state, audio_source='websdr'):
    if audio_source not in ('websdr', 'kiwi', 'pulse'):
        raise ValueError('audio_source must be websdr, kiwi or pulse')
    cfg = configure(os.environ)
    command = cfg.websdr_command() if audio_source == 'websdr' else None
    with reserve(state, minutes):
        if audio_source != 'pulse':
            print(f"Listening on {cfg.receiver['name']} {cfg.frequency:g} kHz for up to {minutes} minutes", flush=True)
        else:
            print(f"Streaming external PulseAudio input for up to {minutes} minutes", flush=True)
        rx = playback = stream = None
        stop = False

        def on_signal(_signal, _frame):
            nonlocal stop
            stop = True

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        deadline = time.monotonic() + minutes * 60
        failed = False
        try:
            if audio_source != 'pulse':
                if audio_source == 'kiwi':
                    command = cfg.receiver_command() + ['--connect-retries=1', '--busy-retries=1']
                rx = spawn(command, stdout=subprocess.PIPE)
                pcm = ['-f', 's16le', '-ar', '8000', '-ac', '1'] if audio_source == 'websdr' else []
                playback = spawn(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'warning',
                                  *pcm, '-i', 'pipe:0', '-ar', '48000', '-ac', '1', '-f', 'pulse',
                                  'rx_audio'], stdin=rx.stdout)
                rx.stdout.close()
            if cfg.stream_command():
                stream = spawn(cfg.stream_command())
            while not stop and time.monotonic() < deadline:
                if audio_source != 'pulse' and (rx.poll() is not None or playback.poll() is not None):
                    print('Receiver ended; not reconnecting', flush=True)
                    failed = True
                    break
                if stream and stream.poll() is not None:
                    print('Stream ended; stopping receiver', flush=True)
                    failed = True
                    break
                time.sleep(0.2)
            return not failed and not stop
        finally:
            for process in (stream, playback, rx):
                terminate(process)
            print('Session ended', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='One bounded audio receive-and-stream session')
    parser.add_argument('--minutes', type=int, default=20)
    parser.add_argument('--state', default='/state/usage.json')
    parser.add_argument('--audio-source', choices=('websdr', 'kiwi', 'pulse'), default='websdr',
                        help='container WebSDR (default), legacy KiwiSDR, or external PulseAudio')
    args = parser.parse_args()
    try:
        if not run(args.minutes, args.state, audio_source=args.audio_source):
            sys.exit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
