# Test image for herdr-fleet container scenarios: a bare Debian with a non-root user and no sudo.
# python3 here is 3.11; herdr-fleet (ansible-core 2.21) needs 3.12, so uv provides it at test time.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv curl ca-certificates xz-utils git bash tar \
 && rm -rf /var/lib/apt/lists/*

# Only the sudo scenario (test_baseline_scenario.py) builds with WITH_SUDO=yes; it installs sudo
# but grants nothing: the test writes the sudoers rule it wants into the running container.
ARG WITH_SUDO=no
RUN if [ "$WITH_SUDO" = yes ]; then \
      apt-get update && apt-get install -y --no-install-recommends sudo \
      && rm -rf /var/lib/apt/lists/*; \
    fi

RUN python3 -m venv /opt/bootstrap \
 && /opt/bootstrap/bin/pip install --no-cache-dir uv==0.11.19 \
 && ln -s /opt/bootstrap/bin/uv /usr/local/bin/uv

RUN useradd --create-home --home-dir /home/fleet --shell /bin/bash --uid 1000 fleet
USER fleet
WORKDIR /home/fleet
