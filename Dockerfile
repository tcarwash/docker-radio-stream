FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    XDG_RUNTIME_DIR=/run/radio \
    DISPLAY=:99 \
    PULSE_SERVER=unix:/run/radio/pulse/native

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg git openbox pulseaudio pulseaudio-utils \
    python3 python3-numpy python3-png x11-utils x11vnc xterm xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/sh radio \
    && mkdir -p /run/radio /opt/radio /config \
    && chown radio:radio /run/radio /config \
    && chmod 700 /run/radio \
    && git clone https://github.com/jks-prv/kiwiclient.git /opt/kiwiclient \
    && cd /opt/kiwiclient && git checkout 4eb733e6b6147f7fbeb97ced64cdac029b202d18 \
    && chmod +x /opt/kiwiclient/kiwirecorder.py \
    && rm -rf /opt/kiwiclient/.git

RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

COPY radio.py /opt/radio/radio.py
COPY session.py /opt/radio/session.py
USER radio
WORKDIR /opt/radio
EXPOSE 5900
CMD ["python3", "-u", "/opt/radio/radio.py"]
