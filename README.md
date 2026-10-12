# Radio application streaming base image

Ubuntu 24.04 base for a generic GUI radio application, VNC, bounded **container-native WebSDR** sessions, and optional YouTube Live output. No host browser, Node installation, or audio helper is required.

```
WebSDR URL selected by frequency map
  -> container Playwright Chromium (frequency/mode/filter + decoded audio hook)
  -> mono 8 kHz PCM -> FFmpeg -> PulseAudio rx_audio
  -> rx_audio_input (Radio-Loopback) -> GUI capture application
Xvfb desktop + rx_audio.monitor -> FFmpeg H.264/AAC -> YouTube RTMPS or local FLV
Xvfb desktop -> authenticated x11vnc -> host loopback :5900
```

This is decoded audio, **not RF/IQ**. An audio spectrum can use it; a receiver's RF waterfall cannot. The virtual card is a PulseAudio null sink, not a hardware/ALSA `snd-aloop` device. GUI capture apps must select `rx_audio_input` (`Radio-Loopback`). VNC transports video, not audio.

## Setup and run

1. `docker compose build`
2. Review `config/receivers.json`. Entries are first-match inclusive ranges, not automatic discovery, capacity checks, antenna-direction selection or failover. The shipped map uses Northern Utah WebSDR 1 for 160/80/60/40m and WebSDR 2 for 30/20/17/15/12/10m. Frequencies in gaps fail closed. See [provider evidence](tools/websdr/PROVIDER.md).
3. Create an authenticated VNC password file:
   `docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD/config:/config" --entrypoint x11vnc ghcr.io/tcarwash/radio-stream:base -storepasswd /config/vnc.pass`
   Replace any local test credentials before deployment. Classic VNC uses only the first eight password characters; never expose port 5900 publicly. Use host loopback or `ssh -L 5900:127.0.0.1:5900 your-host`.
4. Configure `.env` from `.env.example`. Set `APP_COMMAND` to an executable and arguments (not shell syntax). Default `xterm` is a generic placeholder. Set either `YT_STREAM_KEY` or `STREAM_OUTPUT=/recordings/test.flv`, or leave both empty for audio-only operation. Never commit `.env`, print full encoder commands or share full `docker inspect` output: stream keys appear in environment/process arguments.
5. `mkdir -p state recordings` (writable by UID 1000), then `docker compose up -d`. Boot starts the configured GUI, Xvfb, VNC and virtual sound device. It does **not** connect to a receiver or start a broadcast.
6. Start the default workflow:
   `docker compose exec -T radio python3 /opt/radio/session.py --minutes 5`

Override tuning for one session without recreating the app container:

```sh
docker compose exec -T -e RX_FREQ_KHZ=7074 -e RX_MODE=usb -e RX_BANDWIDTH_HZ=3000 radio python3 /opt/radio/session.py --minutes 5
```

`RX_FREQ_KHZ` is kHz; mode is `usb`, `lsb`, or `am`; bandwidth is total Hz (USB 0..BW, LSB -BW..0, AM symmetric). The bridge calls the provider's **verified** `setfreqb(kHz)`, then `setmf(mode, low_kHz, high_kHz)` and reads back tuning before forwarding audio. A page must implement those APIs and `hook_webaud`; unrelated OpenWebRX pages are not compatible. Listener identity can be passed with `docker compose exec -e WEBSDR_NAME=YOURCALL ...`.

Example map entry:

```json
{"name":"Northern Utah 40m","type":"websdr","min_khz":7000,"max_khz":7300,"url":"http://websdr1.sdrutah.org:8901/index1a.html"}
```

The command blocks for the bounded session; receiver, playback and configured video output stop together on completion/failure. Desktop/app remain running. The bridge fails on startup/tuning errors, WebSocket close, or 15 seconds without PCM; it **never reloads/reconnects**. It has a separate 25-minute hard cap. Terminating the container-side session sends cleanup signals; merely killing a detached Docker CLI is not a reliable remote cancellation mechanism.

## Quota and provider policy

`state/usage.json` persists a conservative rolling budget across restarts: each session reserves its entire requested 1–25 minutes, with at most 90 reserved minutes per rolling day. A lock rejects concurrent sessions. Never clear state to bypass limits. This cannot coordinate other clients/hosts sharing a public IP. Northern Utah's [terms](https://www.sdrutah.org/info/about.html) prohibit permanent connections defeating timeouts without permission; the [Kiwi FAQ](https://www.sdrutah.org/info/kiwifaq.html) asks amateurs to prefer conventional WebSDRs on covered bands. The local limits are safeguards, **not** a claim that WebSDR and Kiwi have identical limits or permission for unattended rebroadcasting. Check operator/content permissions before scheduling. No scheduler is installed.

Legacy Kiwi maps (`host`, `port`, optional `type: kiwi`) remain available explicitly with `--audio-source kiwi`; kiwiclient is pinned to `4eb733e6b6147f7fbeb97ced64cdac029b202d18`. `--audio-source pulse` streams an already-supplied external Pulse feed. Neither is the default, and neither supplies RF/IQ. The old host-browser shell helper is retired; use the session command directly.

## Derived applications

Use `FROM ghcr.io/tcarwash/radio-stream:base`, switch to root only to install packages/copy the app, then back to `USER radio`. Package AppImages in the derived image; `APPIMAGE_EXTRACT_AND_RUN=1` runs Type 2 AppImages without privileged mode or `/dev/fuse`. Invoke the ELF directly, not through Bash. The base includes glibc 2.39, common OpenGL and Qt xcb helper libraries; a newer ABI requirement needs a compatible base. Consumer `APP_COMMAND` remains overridable.

The GUI inherits `DISPLAY=:99`, the shared `PULSE_SERVER`, `PULSE_SINK=rx_audio`, and `PULSE_SOURCE=rx_audio_input`. Inspect with `docker compose exec radio pactl list short sinks` and `docker compose exec radio pactl list short sources`. A listed device does not prove the GUI selected it; validate actual capture/visible response with the app.

## YouTube

Enable Live in [YouTube Studio](https://support.google.com/youtube/answer/2907883?hl=en) and create/schedule the event. First-time activation can take 24 hours. Put the stream key in `.env` (not the channel URL); recreate the container yourself when changing its environment. The session uses RTMPS and starts desktop video/audio only when a key/output is configured. Check Studio's preview, then click **Go live** unless auto-start is enabled; end the event in Studio as appropriate. This image does not create/publish/end YouTube events. See [encoder and auto-start/stop instructions](https://support.google.com/youtube/answer/9854503?hl=en).

## Verification

```sh
/usr/bin/python3 -m unittest -v test_radio test_session
docker build -t radio-stream:websdr-test .
/usr/bin/python3 tools/websdr/test_container.py radio-stream:websdr-test
```

The last command creates/removes a separate `--network none` container; it never recreates a consumer. Its loopback HTTP fixture checks tuning, generates PCM through the actual Chromium bridge, boots the generic app and PulseAudio, runs a real one-minute session, checks local FLV video/audio duration and decoded volume, persisted quota, receiver/encoder cleanup and surviving desktop. This proves the local pipeline, not public receiver availability, real RF reception, app-specific decoder success, or YouTube ingest. `tools/websdr/test_bridge.js` is a shorter standalone real-browser PCM/cleanup fixture test.
