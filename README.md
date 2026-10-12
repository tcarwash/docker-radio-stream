# Radio application streaming base image

Ubuntu 24.04 base for a generic GUI radio application, VNC, duration-selected **container-native WebSDR** sessions, and optional YouTube Live output. No host browser, Node installation, or audio helper is required.

```
WebSDR URL selected by frequency map
  -> container Playwright Chromium (frequency/mode/filter + decoded audio hook)
  -> mono 8 kHz PCM -> FFmpeg -> PulseAudio rx_audio
  -> rx_audio_input (Radio-Loopback) -> GUI capture application
Xvfb desktop + rx_audio.monitor -> FFmpeg H.264/AAC -> YouTube RTMPS or local FLV
Xvfb desktop -> authenticated x11vnc -> host loopback :5900
Xvfb desktop -> always-on-top frequency/readback indicator
```

This is decoded audio, **not RF/IQ**. An audio spectrum can use it; a receiver's RF waterfall cannot. The virtual card is a PulseAudio null sink, not a hardware/ALSA `snd-aloop` device. GUI capture apps must select `rx_audio_input` (`Radio-Loopback`). VNC transports video, not audio.

## Setup and run

1. `docker compose build`
2. Review `config/receivers.json`. Entries are first-match inclusive ranges, not automatic discovery, capacity checks, antenna-direction selection or failover. The shipped map uses Northern Utah WebSDR 1 for 160/80/60/40m and WebSDR 2 for 30/20/17/15/12/10m. Frequencies in gaps fail closed. See [provider evidence](tools/websdr/PROVIDER.md).
3. Create an authenticated VNC password file:
   `docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD/config:/config" --entrypoint x11vnc ghcr.io/tcarwash/radio-stream:base -storepasswd /config/vnc.pass`
   Replace any local test credentials before deployment. Classic VNC uses only the first eight password characters; never expose port 5900 publicly. Use host loopback or `ssh -L 5900:127.0.0.1:5900 your-host`.
4. Configure `.env` from `.env.example`. Set `APP_COMMAND` to an executable and arguments (not shell syntax). Default `xterm` is a generic placeholder. Set either `YT_STREAM_KEY` or `STREAM_OUTPUT=/recordings/test.flv`, or leave both empty for audio-only operation. Never commit `.env`, print full encoder commands or share full `docker inspect` output: stream keys appear in environment/process arguments.
5. `mkdir -p recordings` (writable by UID 1000), then `docker compose up -d`. Boot starts the configured GUI, Xvfb, VNC and virtual sound device. It does **not** connect to a receiver or start a broadcast.
6. Start the default workflow:
   `docker compose exec -T radio python3 /opt/radio/session.py --minutes 5`

Override tuning for one session without recreating the app container:

```sh
docker compose exec -T -e RX_FREQ_KHZ=7074 -e RX_MODE=usb -e RX_BANDWIDTH_HZ=3000 radio python3 /opt/radio/session.py --minutes 5
```

`RX_FREQ_KHZ` is kHz; mode is `usb`, `lsb`, or `am`; bandwidth is total Hz (USB 0..BW, LSB -BW..0, AM symmetric). The shipped Northern Utah map sends the requested mode directly: USB selects a positive passband and LSB a negative passband. The previous inversion workaround has been removed from the map. The HUD now separates the requested mode from the page's local mode/filter state; the page state is not an RF/audio acknowledgement. A page must implement `setfreqb`, `setmf` and `hook_webaud`; unrelated OpenWebRX pages are not compatible. Listener identity can be passed with `docker compose exec -e WEBSDR_NAME=YOURCALL ...`.

Example map entry:

```json
{"name":"Northern Utah 40m","type":"websdr","min_khz":7000,"max_khz":7300,"url":"http://websdr1.sdrutah.org:8901/index1a.html"}
```

The command blocks for the requested session duration; `--minutes` has a 20-minute default and no local maximum. Receiver, playback and configured video output stop together on completion/failure; the desktop/app remain running. The bridge fails on startup/tuning errors, WebSocket close, or 15 seconds without PCM; it **never reloads/reconnects**. Terminating the container-side session sends cleanup signals; merely killing a detached Docker CLI is not a reliable remote cancellation mechanism.

## Operator limits

There is no local per-session maximum or rolling daily quota. A process lock still prevents overlapping sessions inside this container. Set the desired duration explicitly and follow the selected receiver operator's limits. Northern Utah's [terms](https://www.sdrutah.org/info/about.html) prohibit permanent connections defeating timeouts without permission; the [Kiwi FAQ](https://www.sdrutah.org/info/kiwifaq.html) asks amateurs to prefer conventional WebSDRs on covered bands. These are separate services with separate operator rules; check operator and content permissions before unattended use or rebroadcasting. No scheduler is installed.

Legacy Kiwi maps (`host`, `port`, optional `type: kiwi`) remain available explicitly with `--audio-source kiwi`; kiwiclient is pinned to `4eb733e6b6147f7fbeb97ced64cdac029b202d18`. `--audio-source pulse` streams an already-supplied external Pulse feed. Neither is the default, and neither supplies RF/IQ. The old host-browser shell helper is retired; use the session command directly.

## Derived applications

Use `FROM ghcr.io/tcarwash/radio-stream:base`, switch to root only to install packages/copy the app, then back to `USER radio`. Package AppImages in the derived image; `APPIMAGE_EXTRACT_AND_RUN=1` runs Type 2 AppImages without privileged mode or `/dev/fuse`. Invoke the ELF directly, not through Bash. The base includes glibc 2.39, common OpenGL and Qt xcb helper libraries; a newer ABI requirement needs a compatible base. Consumer `APP_COMMAND` remains overridable.

The GUI inherits `DISPLAY=:99`, the shared `PULSE_SERVER`, `PULSE_SINK=rx_audio`, and `PULSE_SOURCE=rx_audio_input`. Inspect with `docker compose exec radio pactl list short sinks` and `docker compose exec radio pactl list short sources`. A listed device does not prove the GUI selected it; validate actual capture/visible response with the app.

## Hamlib

During an active WebSDR session, the browser bridge serves a Hamlib NET rigctl endpoint at `127.0.0.1:4532`. It reports the requested logical mode and page-local frequency state; it does not independently verify RF demodulation. With `mode_inverted: true`, Hamlib USB/LSB are mapped to the opposite WebSDR page mode and signed passband. No serial device or `rigctld` process is needed for this endpoint. Configure an application running in the container to use Hamlib model **NET rigctl (2)** at `127.0.0.1:4532`. Test from the container with `docker compose exec radio rigctl -m 2 -r 127.0.0.1:4532 f` (read Hz), `docker compose exec radio rigctl -m 2 -r 127.0.0.1:4532 F 14075000` (set 14.075 MHz in the default 20 m map), and `docker compose exec radio rigctl -m 2 -r 127.0.0.1:4532 M LSB 2400` (set mode/bandwidth). Control is available only while the WebSDR session is active, accepts USB/LSB/AM, and rejects frequencies outside the selected receiver's configured range. The endpoint is loopback-only and receive-only; it does not transmit.

The installed Hamlib utilities can also control a physical radio separately. Pass through its device in `compose.yaml` (for example `devices: - /dev/ttyUSB0:/dev/ttyUSB0`) and add the host device's group ID under Compose `group_add` if needed; check with `stat -c '%g' /dev/ttyUSB0`. List model IDs with `docker compose exec radio rigctl -l`; direct access is `docker compose exec -it radio rigctl -m MODEL_ID -r /dev/ttyUSB0`. A separate physical-radio server can be started with `rigctld -m MODEL_ID -r /dev/ttyUSB0 -T 127.0.0.1 -t 4533`. No physical radio is assumed or started by default; keep the container unprivileged and do not publish Hamlib ports publicly.

## YouTube

Enable Live in [YouTube Studio](https://support.google.com/youtube/answer/2907883?hl=en) and create/schedule the event. First-time activation can take 24 hours. Put the stream key in `.env` (not the channel URL); recreate the container yourself when changing its environment. The session uses RTMPS and starts desktop video/audio only when a key/output is configured. Check Studio's preview, then click **Go live** unless auto-start is enabled; end the event in Studio as appropriate. This image does not create/publish/end YouTube events. See [encoder and auto-start/stop instructions](https://support.google.com/youtube/answer/9854503?hl=en).

## Verification

```sh
/usr/bin/python3 -m unittest -v test_radio test_session test_tuning_status
docker build -t radio-stream:websdr-test .
/usr/bin/python3 tools/websdr/test_container.py radio-stream:websdr-test
```

The last command creates/removes a separate `--network none` container; it never recreates a consumer. Its loopback HTTP fixture checks Hamlib `rigctl` set/get frequency and mode mapping, generates PCM through the actual Chromium bridge, boots the generic app and PulseAudio, verifies local FLV audio/video and decoded volume, checks page-local tuning status, receiver/encoder cleanup and the surviving desktop. This proves the local pipeline and Hamlib-to-page control path, not public receiver availability, real RF sideband correctness, app-specific decoder success, physical-radio control, or YouTube ingest. `tools/websdr/test_bridge.js` is a shorter standalone browser/protocol/audio fixture test.
