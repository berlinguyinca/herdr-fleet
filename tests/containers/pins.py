"""Real, verified pins used by the container scenarios (see tests/containers/fixtures)."""

from __future__ import annotations

# Node 22.22.0: a second real release, for upgrade/downgrade drift.
# sha256 from https://nodejs.org/dist/v22.22.0/SHASUMS256.txt
ALT_NODE_VERSION = "22.22.0"
ALT_NODE_SHA256 = {
    "linux-x64": "9aa8e9d2298ab68c600bd6fb86a6c13bce11a4eca1ba9b39d79fa021755d7c37",
    "linux-arm64": "1bf1eb9ee63ffc4e5d324c0b9b62cf4a289f44332dfef9607cea1a0d9596ba6f",
    "darwin-x64": "5ea50c9d6dea3dfa3abb66b2656f7a4e1c8cef23432b558d45fb538c7b5dedce",
    "darwin-arm64": "5ed4db0fcf1eaf84d91ad12462631d73bf4576c1377e192d222e48026a902640",
}

# An older pi-cli release that exists on npm.
ALT_PI_CLI_VERSION = "1.3.0"

# Node 20.19.5: the "nvm default" decoy. sha256 from https://nodejs.org/dist/v20.19.5/SHASUMS256.txt
DECOY_NODE_VERSION = "20.19.5"
DECOY_NODE_SHA256 = {
    "linux-x64": "315046739a513a70e03a4a55a8afda8cf979f30852e576075c340084e3f8ac0f",
    "linux-arm64": "d462267863ae8ee556039ebdf559055a8ec562c633889ef1403f3adb449ba1dd",
}
