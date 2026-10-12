# Container WebSDR bridge

The default `session.py` path now runs Node/Playwright/Chromium **inside the image**. Use:

```sh
docker compose exec -T -e RX_FREQ_KHZ=7074 -e RX_MODE=usb -e RX_BANDWIDTH_HZ=3000 radio python3 /opt/radio/session.py --minutes 5
```

Set `RECEIVERS_FILE` to the JSON range map (`name`, `type: websdr`, `min_khz`, `max_khz`, `url`, optional boolean `mode_inverted`). No host browser/runtime is required. The existing `websdr-audio.sh` host helper is retired and must not be used with the new bridge CLI. `FIREFOX`, `STALL_S` and blind-view host options no longer apply; reconnect is not supported. `WEBSDR_NAME` is optional, defaults to `N0CALL`, and can be set via Compose exec environment.

Bridge CLI (normally called by the duration-selected session):

```
node /opt/radio/websdr/websdr_bridge.js URL frequency_kHz mode low_Hz high_Hz
```

The pinned `playwright@1.56.1` package installs its matched Chromium in `/opt/playwright` during image build. Autoplay is enabled, browser playback is muted to avoid duplicate audio, and the hook feeds mono 8 kHz signed 16-bit PCM on stdout. Diagnostics stay on stderr. A 15-second no-audio failure check and WebSocket-close handler fail closed without retries; the parent session controls duration and termination closes the browser and encoder together. The bridge status reports the requested logical mode and the page's local mode/filter state; it is not an independent server/RF demodulator acknowledgement. When a receiver's sideband audio is reversed, set `mode_inverted: true` in that receiver's JSON map entry; Hamlib's USB/LSB API and matching passband are then inverted at the WebSDR boundary. WebSDR pages must implement the APIs in [PROVIDER.md](PROVIDER.md).

While this bridge is running, it also exposes a loopback-only Hamlib NET rigctl service on port 4532. Applications in the same container should select Hamlib model 2 (`NET rigctl`) and connect to `127.0.0.1:4532`. Frequency and mode changes call the WebSDR page APIs and refresh the visible tuning status; frequency is constrained to the selected receiver's configured map range. `get_mode` returns the requested logical Hamlib mode even if the page-side mode is inverted. Only USB, LSB and AM are supported, and transmit requests are rejected. The port is unavailable outside a WebSDR session.

Offline verification: `test_bridge.js` serves its own HTTP page, verifies tuning and non-silent PCM and graceful closure. `test_container.py` tests the complete image with external networking disabled; see root README. Public RF reception and YouTube ingest are intentionally not exercised by automated tests.

The original audio hook/resampler derives from [baitisj/websdr-bench](https://github.com/baitisj/websdr-bench) commit `3c34b08bfb57f4ca57687e5a233f3ca64d686dcd`; see `LICENSE` (MIT). This is decoded audio, not IQ or the remote RF waterfall.
