# Container WebSDR bridge

The default `session.py` path now runs Node/Playwright/Chromium **inside the image**. Use:

```sh
docker compose exec -T -e RX_FREQ_KHZ=7074 -e RX_MODE=usb -e RX_BANDWIDTH_HZ=3000 radio python3 /opt/radio/session.py --minutes 5
```

Set `RECEIVERS_FILE` to the JSON range map (`name`, `type: websdr`, `min_khz`, `max_khz`, `url`). No host browser/runtime is required. The existing `websdr-audio.sh` host helper is retired and must not be used with the new bridge CLI. `FIREFOX`, `STALL_S` and blind-view host options no longer apply; reconnect is not supported. The shared lock and rolling quota remain in `session.py`. `WEBSDR_NAME` is optional, defaults to `N0CALL`, and can be set via Compose exec environment.

Bridge CLI (normally called only by the quota-enforcing session):

```
node /opt/radio/websdr/websdr_bridge.js URL frequency_kHz mode low_Hz high_Hz
```

The pinned `playwright@1.56.1` package installs its matched Chromium in `/opt/playwright` during image build. Autoplay is enabled, browser playback is muted to avoid duplicate audio, and the hook feeds mono 8 kHz signed 16-bit PCM on stdout. Diagnostics stay on stderr. A 15-second PCM watchdog, WebSocket-close handler and 25-minute hard cap fail closed without retries; session termination closes the browser and encoder together. WebSDR pages must implement the verified APIs in [PROVIDER.md](PROVIDER.md).

Offline verification: `test_bridge.js` serves its own HTTP page, verifies tuning and non-silent PCM and graceful closure. `test_container.py` tests the complete image with external networking disabled; see root README. Public RF reception and YouTube ingest are intentionally not exercised by automated tests.

The original audio hook/resampler derives from [baitisj/websdr-bench](https://github.com/baitisj/websdr-bench) commit `3c34b08bfb57f4ca57687e5a233f3ca64d686dcd`; see `LICENSE` (MIT). This is decoded audio, not IQ or the remote RF waterfall.
