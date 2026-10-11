# Radio application streaming base image

A Docker base image for a GUI amateur-radio application, bounded KiwiSDR receive-audio sessions, VNC control, and optional YouTube Live output.

```
KiwiSDR (frequency/mode/passband) -> kiwirecorder --nc-wav
                                   -> FFmpeg -> PulseAudio rx_audio virtual sink
                                   -> GUI app (same desktop/audio environment)
Xvfb desktop -> x11vnc (password required) -> 127.0.0.1:5900 on host
Xvfb desktop + rx_audio.monitor -> FFmpeg H.264/AAC -> YouTube RTMPS
```

The virtual sound card is a PulseAudio null sink, not an ALSA `snd-aloop` kernel device. Applications that accept PulseAudio output can use the default `rx_audio` sink. Apps requiring a hardware/ALSA capture input or raw SDR IQ need a separate adapter; this image supplies decoded audio, not IQ. FFmpeg streams desktop video and the virtual sink's audio.

## Setup

1. `docker compose build`
2. `config/receivers.json` maps HF frequencies (1–30,000 kHz) to the Northern Utah KiwiSDR 1 omnidirectional receiver at `kiwisdr1.sdrutah.org:8073`. The site's Kiwi 1 may redirect full sessions to another receiver. Frequency alone cannot select a preferred antenna direction or guarantee an open slot; for a different receive region, edit this map. Entries are checked in order, first matching range wins. This is not automatic discovery or active probing. Generic WebSDR/OpenWebRX sites are not interchangeable with the KiwiSDR protocol.
3. Create a VNC password file, if one is not present:
   `docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD/config:/config" --entrypoint x11vnc radio-stream:base -storepasswd /config/vnc.pass`
   The locally generated test credentials, if present, are in ignored `config/vnc-password.txt`; replace them before deployment. Classic VNC authentication only uses the first eight password characters. Never expose port 5900 publicly; use the host-loopback bind or an SSH tunnel.
4. Copy `.env.example` to `.env` and set `YT_STREAM_KEY` to your YouTube Live key. Alternatively set `STREAM_OUTPUT=/recordings/test.flv` for a local end-to-end smoke test. Leave both empty to disable video output. Do not commit `.env` or share `docker inspect` output (the key appears in container environment and FFmpeg process arguments).
5. In `compose.yaml`, change `RX_FREQ_KHZ` (kHz; e.g. `14074`), `RX_MODE` (`usb`, `lsb`, `am`), `RX_BANDWIDTH_HZ` (total audio passband width), and `APP_COMMAND` (an executable and arguments, not shell interpolation). For a mounted application use `APP_COMMAND: "/app/start.sh"`, make it executable, and install its GUI dependencies in a derived Dockerfile. The default `xterm` is just a base-image demonstration, not a radio application.
6. `mkdir -p state` (owned by container UID 1000), then `docker compose up -d`. This starts only the VNC desktop and virtual audio device; it does **not** connect to the receiver or YouTube.
7. Start one bounded session: `docker compose exec -T radio python3 /opt/radio/session.py --minutes 20`. The command blocks until the session ends (or fails to connect). To stop early, interrupt the exec command. The application remains available over VNC at `localhost:5900`. On a remote host tunnel with `ssh -L 5900:127.0.0.1:5900 your-host`. Stop the desktop with `docker compose down`.

Override tuning for a single session without recreating the container: `docker compose exec -T -e RX_FREQ_KHZ=7074 -e RX_MODE=usb -e RX_BANDWIDTH_HZ=3000 radio python3 /opt/radio/session.py --minutes 20`. The frequency is matched against the configured receiver ranges before any connection is made.

For unattended scheduling, invoke that same `docker compose exec -T` command from a **host** cron job in this project directory. For example, three 20-minute slots per day at 00:00, 08:00 and 16:00 host time (use `cd /home/tyler/dev/docker_stream && docker compose exec -T radio python3 /opt/radio/session.py --minutes 20`). No cron job is installed by this project. A persisted rolling quota in `state/usage.json` reserves the **entire requested session** before connecting, rejects concurrent sessions, limits each session to 25 minutes, and blocks requests beyond 90 reserved minutes over a conservative rolling 24-hour window. Do not remove or share the state directory between different operators; this local guard cannot coordinate multiple hosts or other Kiwi clients using the same public IP.

For a derived image, use `FROM radio-stream:base`, switch to `USER root` for `apt-get install`, then switch back to `USER radio` and adjust the compose image/build setting. The application inherits `DISPLAY=:99`, `PULSE_SERVER`, and `PULSE_SINK=rx_audio`. To verify the virtual device: `docker compose exec radio pactl list short sinks`.

## Stream to your YouTube channel

1. In [YouTube Studio's Live Control Room](https://support.google.com/youtube/answer/2907883?hl=en), use **Create → Go live** to create or schedule a stream. Enable live streaming on the channel if prompted; first-time activation can take up to 24 hours. Copy that stream's key. Do not put the key in chat or commit it.
2. Copy `.env.example` to `.env` if `.env` does not already exist, then edit `.env` to set `YT_STREAM_KEY` to the key. Leave `STREAM_OUTPUT` empty (it is the local-file alternative). The image uses YouTube's RTMPS ingest URL; you do not enter your channel URL. Host users with Docker access can see the key in container environment/process arguments, so restrict access.
3. Run `docker compose up -d --force-recreate` to load the new key into the idle container. This does **not** begin a stream.
4. At the intended start time, run `docker compose exec -T -e RX_FREQ_KHZ=7074 -e RX_MODE=usb radio python3 /opt/radio/session.py --minutes 20`. The session starts FFmpeg with Xvfb desktop video and `rx_audio.monitor` audio, and stops both the receiver and FFmpeg at session end. The container's default GUI is only `xterm`; install and configure your radio app through `APP_COMMAND` before broadcasting it.
5. Check the incoming preview in Live Control Room. For a scheduled event, click **Go live** there unless you enabled YouTube's **auto-start** setting. At the end, use **End Stream** in Live Control Room as appropriate; see [YouTube's encoder and auto-start/stop instructions](https://support.google.com/youtube/answer/9854503?hl=en). The container does not create, publish, or end YouTube events on your behalf.

The image pins [kiwiclient](https://github.com/jks-prv/kiwiclient) at commit `4eb733e6b6147f7fbeb97ced64cdac029b202d18`; its `kiwirecorder.py` documents `-f` in kHz, `-L/-H` in Hz, and `--nc --nc-wav` as streaming WAV to stdout. Connection and busy retries are disabled for a bounded session; the session process stops receiver and stream together. Northern Utah's [terms](https://www.sdrutah.org/info/about.html) prohibit permanent connections without explicit permission; its [Kiwi FAQ](https://www.sdrutah.org/info/kiwifaq.html) limits a Kiwi session to 30 minutes and each user to 90 minutes per 24 hours, and asks amateurs to use the conventional WebSDRs when the band is covered. This image only supports the KiwiSDR protocol; use its public Kiwis sparingly and prefer their WebSDRs for common ham bands. The local budget is an additional safeguard, not permission to bypass server limits. Frequency coverage in JSON does not verify hardware or slot availability. Broadcast/third-party content may have rebroadcast restrictions. YouTube Live must already be enabled on your account; this image does not create or start a YouTube Live event.

## Verification

`/usr/bin/python3 -m unittest -v test_radio test_session`

For an offline pipeline test, temporarily set `STREAM_OUTPUT=/recordings/test.flv`, start the container, and inject a test tone with:
`docker compose exec radio ffmpeg -hide_banner -loglevel error -f lavfi -i sine=frequency=1000:duration=4 -f pulse rx_audio`
Video only runs during a session. To avoid connecting to the public receiver for a smoke test, temporarily override `RECEIVERS_FILE` with a private local fixture and run a session. After it ends, inspect the file with `ffprobe -show_streams recordings/test.flv`. This proves desktop/audio muxing but not a KiwiSDR connection or YouTube ingest.
