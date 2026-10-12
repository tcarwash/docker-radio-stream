FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    XDG_RUNTIME_DIR=/run/radio \
    DISPLAY=:99 \
    PULSE_SERVER=unix:/run/radio/pulse/native \
    APPIMAGE_EXTRACT_AND_RUN=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg git openbox pulseaudio pulseaudio-utils \
    libegl1 libopengl0 python3 python3-numpy python3-png \
    x11-utils x11vnc xterm xvfb \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu already assigns UID 1000 to "ubuntu"; rename it to preserve the
# unprivileged radio account and host-volume ownership convention.
RUN usermod --login radio --home /home/radio --move-home --shell /bin/sh ubuntu \
    && groupmod --new-name radio ubuntu \
    && mkdir -p /run/radio /opt/radio /config \
    && chown radio:radio /run/radio /config \
    && chmod 700 /run/radio \
    && git clone https://github.com/jks-prv/kiwiclient.git /opt/kiwiclient \
    && cd /opt/kiwiclient && git checkout 4eb733e6b6147f7fbeb97ced64cdac029b202d18 \
    && chmod +x /opt/kiwiclient/kiwirecorder.py \
    && rm -rf /opt/kiwiclient/.git

# Qt's xcb platform plugin expects this host-side helper library; AppImages
# intentionally leave display-server integration libraries to the host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxcb-cursor0 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

# Pinned Playwright supplies a real browser, not Ubuntu's Firefox snap stub.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY tools/websdr/package.json /opt/radio/websdr/package.json
RUN cd /opt/radio/websdr && npm install --ignore-scripts --omit=dev \
    && npx playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright && rm -rf /var/lib/apt/lists/*
COPY tools/websdr/websdr_bridge.js tools/websdr/LICENSE /opt/radio/websdr/

COPY radio.py /opt/radio/radio.py
COPY session.py /opt/radio/session.py
USER radio
WORKDIR /opt/radio
EXPOSE 5900
CMD ["python3", "-u", "/opt/radio/radio.py"]
