"""Run one duration-selected receiver/stream session from docker compose exec."""
import argparse
import fcntl
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from radio import configure, spawn, terminate
from tuning_status import DEFAULT_STATUS_PATH, write_status


@contextmanager
def session_lock(path='/run/radio/session.lock'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A session is already running') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run(minutes, audio_source='websdr'):
    if minutes < 1:
        raise ValueError('minutes must be at least 1')
    if audio_source not in ('websdr', 'kiwi', 'pulse'):
        raise ValueError('audio_source must be websdr, kiwi or pulse')
    cfg = configure(os.environ)
    command = cfg.websdr_command() if audio_source == 'websdr' else None
    lock_path = os.environ.get('SESSION_LOCK_FILE', '/run/radio/session.lock')
    with session_lock(lock_path):
        return _run_locked(minutes, audio_source, cfg, command)


def _run_locked(minutes, audio_source, cfg, command):
    status_path = os.environ.get('TUNING_STATUS_FILE', DEFAULT_STATUS_PATH)
    if audio_source == 'pulse':
        write_status({'state': 'external'}, status_path)
        print(f"Streaming external PulseAudio input for up to {minutes} minutes", flush=True)
    else:
        write_status({
            'state': 'requested' if audio_source == 'websdr' else 'commanded',
            'frequency_khz': cfg.frequency,
            'mode': cfg.mode,
            'passband_hz': cfg.passband,
            'receiver': cfg.receiver['name'],
        }, status_path)
        print(f"Listening on {cfg.receiver['name']} {cfg.frequency:g} kHz for up to {minutes} minutes", flush=True)
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
            receiver_env = os.environ.copy()
            receiver_env['TUNING_RECEIVER'] = cfg.receiver['name']
            receiver_env['TUNING_MIN_KHZ'] = str(cfg.receiver.get('min_khz', 0))
            receiver_env['TUNING_MAX_KHZ'] = str(cfg.receiver.get('max_khz', 30000))
            receiver_env['TUNING_MODE_INVERTED'] = str(int(
                audio_source == 'websdr' and cfg.receiver.get('mode_inverted', False)))
            rx = spawn(command, stdout=subprocess.PIPE, env=receiver_env)
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
    parser = argparse.ArgumentParser(description='One duration-selected audio receive-and-stream session')
    parser.add_argument('--minutes', type=int, default=20)
    parser.add_argument('--audio-source', choices=('websdr', 'kiwi', 'pulse'), default='websdr',
                        help='container WebSDR (default), legacy KiwiSDR, or external PulseAudio')
    args = parser.parse_args()
    try:
        if not run(args.minutes, audio_source=args.audio_source):
            sys.exit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
