"""Receiver audio -> virtual sound card -> desktop application -> video stream."""
import json
import os
import re
import resource
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from tuning_status import DEFAULT_STATUS_PATH, write_status


@dataclass
class Config:
    frequency: float
    mode: str
    passband: tuple[int, int]
    receiver: dict
    app_command: list[str]
    stream_output: str | None
    width: int
    height: int
    fps: int

    def websdr_command(self):
        if self.receiver.get('type') != 'websdr':
            raise ValueError('Default sessions require a WebSDR URL map; use --audio-source kiwi for legacy maps')
        return ['node', '/opt/radio/websdr/websdr_bridge.js', self.receiver['url'],
                f'{self.frequency:g}', self.mode, *map(str, self.passband)]

    def receiver_command(self):
        low, high = self.passband
        return ['/opt/kiwiclient/kiwirecorder.py', '-s', self.receiver['host'],
                '-p', str(self.receiver['port']), '-u', 'radio-stream',
                '-f', f'{self.frequency:g}', '-m', self.mode, '-L', str(low),
                '-H', str(high), '--nc', '--nc-wav']

    def stream_command(self):
        if not self.stream_output:
            return None
        return ['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'warning',
                '-f', 'x11grab', '-framerate', str(self.fps), '-video_size',
                f'{self.width}x{self.height}', '-i', ':99.0',
                '-f', 'pulse', '-i', 'rx_audio.monitor',
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264',
                '-preset', 'veryfast', '-tune', 'zerolatency', '-pix_fmt', 'yuv420p',
                '-b:v', '2500k', '-maxrate', '2500k', '-bufsize', '5000k',
                '-g', str(self.fps * 2), '-c:a', 'aac', '-b:a', '128k',
                '-ar', '48000', '-ac', '1', '-f', 'flv', self.stream_output]


def _positive_int(value, name, maximum):
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} must be an integer') from None
    if not 0 < result <= maximum:
        raise ValueError(f'{name} must be between 1 and {maximum}')
    return result


def configure(env):
    try:
        frequency = float(env['RX_FREQ_KHZ'])
    except (KeyError, ValueError):
        raise ValueError('RX_FREQ_KHZ must be a number in kHz') from None
    if not 0 < frequency <= 30000:
        raise ValueError('RX_FREQ_KHZ must be within 0-30000 kHz')
    mode = env.get('RX_MODE', 'usb').lower()
    if mode not in ('usb', 'lsb', 'am'):
        raise ValueError('RX_MODE must be usb, lsb, or am')
    bw = _positive_int(env.get('RX_BANDWIDTH_HZ', '3000'), 'RX_BANDWIDTH_HZ', 12000)
    passband = ((0, bw) if mode == 'usb' else (-bw, 0) if mode == 'lsb'
                else (-bw // 2, bw - bw // 2))
    try:
        receivers = json.loads(Path(env.get('RECEIVERS_FILE', '/config/receivers.json')).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Invalid receiver configuration: {exc}') from exc
    if not isinstance(receivers, list) or not receivers:
        raise ValueError('Receiver map must be a nonempty JSON array')
    for receiver in receivers:
        try:
            kind = receiver.get('type', 'kiwi')
            if kind == 'websdr':
                url = urlsplit(receiver['url'])
                if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                    raise ValueError('invalid WebSDR URL')
                if url.port is not None:
                    _positive_int(url.port, 'port', 65535)
                if 'mode_inverted' in receiver and not isinstance(receiver['mode_inverted'], bool):
                    raise ValueError('mode_inverted must be a boolean')
            elif kind == 'kiwi':
                host = receiver['host']
                if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.\-]{0,252}', host):
                    raise ValueError('invalid host')
                _positive_int(receiver['port'], 'port', 65535)
            else:
                raise ValueError('unknown receiver type')
            lower, upper = float(receiver['min_khz']), float(receiver['max_khz'])
            if not 0 <= lower < upper <= 30000:
                raise ValueError('invalid frequency range')
            if not isinstance(receiver['name'], str):
                raise ValueError('invalid name')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'Invalid receiver entry: {exc}') from exc
    selected = next((r for r in receivers if r['min_khz'] <= frequency <= r['max_khz']), None)
    if selected is None:
        raise ValueError(f'No configured receiver covers {frequency:g} kHz')
    app_command = shlex.split(env.get('APP_COMMAND', 'xterm -geometry 100x30'))
    if not app_command:
        raise ValueError('APP_COMMAND cannot be empty')
    key = env.get('YT_STREAM_KEY', '')
    output = env.get('STREAM_OUTPUT', '')
    if key and output:
        raise ValueError('Set either YT_STREAM_KEY or STREAM_OUTPUT, not both')
    if key and ('/' in key or any(c.isspace() for c in key)):
        raise ValueError('Invalid YT_STREAM_KEY')
    if env.get('VNC_PASSWORD_FILE') and not Path(env['VNC_PASSWORD_FILE']).is_file():
        raise ValueError(f"VNC password file missing: {env['VNC_PASSWORD_FILE']}")
    width = _positive_int(env.get('WIDTH', '1280'), 'WIDTH', 3840)
    height = _positive_int(env.get('HEIGHT', '720'), 'HEIGHT', 2160)
    fps = _positive_int(env.get('FPS', '15'), 'FPS', 60)
    return Config(frequency, mode, passband, selected, app_command,
                  f'rtmps://a.rtmps.youtube.com/live2/{key}' if key else output or None,
                  width, height, fps)


def spawn(command, **kwargs):
    return subprocess.Popen(command, start_new_session=True, **kwargs)


def terminate(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def wait_ready(command, process, name):
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError(f'{name} exited before becoming ready')
        if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
        time.sleep(0.2)
    raise RuntimeError(f'{name} did not become ready')


def setup_audio():
    subprocess.run(['pactl', 'load-module', 'module-null-sink', 'sink_name=rx_audio',
                    'rate=48000', 'channels=1'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['pactl', 'load-module', 'module-remap-source',
                    'source_name=rx_audio_input', 'master=rx_audio.monitor',
                    'channels=1', 'source_properties=device.description=Radio-Loopback'],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['pactl', 'set-default-sink', 'rx_audio'], check=True)
    subprocess.run(['pactl', 'set-default-source', 'rx_audio_input'], check=True)
    os.environ['PULSE_SINK'] = 'rx_audio'
    os.environ['PULSE_SOURCE'] = 'rx_audio_input'


def main():
    # x11vnc's select loop can spin indefinitely with Docker's enormous
    # RLIMIT_NOFILE (observed: 2147483584), delaying VNC handshakes.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(soft, 8192), hard))
    cfg = configure(os.environ)
    write_status({
        'state': 'configured',
        'frequency_khz': cfg.frequency,
        'mode': cfg.mode,
        'passband_hz': cfg.passband,
        'receiver': cfg.receiver['name'],
    }, os.environ.get('TUNING_STATUS_FILE', DEFAULT_STATUS_PATH))
    print(f"Selected {cfg.receiver['name']} at {cfg.frequency:g} kHz ({cfg.mode}, {cfg.passband} Hz)", flush=True)
    os.environ['DISPLAY'] = ':99'
    os.environ['PULSE_SINK'] = 'rx_audio'
    os.environ['PULSE_SERVER'] = 'unix:/run/radio/pulse/native'
    processes = {}
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        processes['x'] = spawn(['Xvfb', ':99', '-screen', '0', f'{cfg.width}x{cfg.height}x24', '-nolisten', 'tcp'])
        wait_ready(['xdpyinfo', '-display', ':99'], processes['x'], 'Xvfb')
        processes['pulse'] = spawn(['pulseaudio', '--daemonize=no', '--exit-idle-time=-1', '--log-target=stderr'])
        wait_ready(['pactl', 'info'], processes['pulse'], 'PulseAudio')
        setup_audio()
        processes['wm'] = spawn(['openbox'])
        processes['status'] = spawn(['python3', '/opt/radio/tuning_status.py'])
        password_file = os.environ.get('VNC_PASSWORD_FILE', '/config/vnc.pass')
        if not Path(password_file).is_file():
            raise ValueError(f'VNC password file missing: {password_file} (create with x11vnc -storepasswd)')
        processes['vnc'] = spawn(['x11vnc', '-display', ':99', '-forever', '-shared',
                                  '-rfbauth', password_file, '-rfbport', '5900', '-noxdamage'])
        print('VNC available on :5900; publish to host loopback only', flush=True)

        processes['app'] = spawn(cfg.app_command)
        print('Desktop ready. Start a duration-selected session with docker compose exec.', flush=True)
        while not stopping:
            time.sleep(2)
            for name in ('x', 'pulse', 'wm', 'status', 'vnc'):
                if processes[name].poll() is not None:
                    raise RuntimeError(f'{name} exited')
            if processes['app'].poll() is not None:
                print('app exited; restarting in 5 seconds', flush=True)
                time.sleep(5)
                if not stopping:
                    processes['app'] = spawn(cfg.app_command)
    finally:
        for process in reversed(list(processes.values())):
            terminate(process)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
