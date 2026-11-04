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

# herdr 0.9.2: a second real release, for "binary replaced by a different release" drift.
# sha256 from the asset "digest" of https://api.github.com/repos/herdrdev/herdr/releases (v0.9.2).
ALT_HERDR_VERSION = "0.9.2"
ALT_HERDR_ASSET = {"linux-x64": "herdr-linux-x86_64", "linux-arm64": "herdr-linux-aarch64"}
ALT_HERDR_SHA256 = {
    "linux-x64": "74de34746f96236f76d599f8f5d2439f884351eabe25b7495e0edab75414064a",
    "linux-arm64": "50ba6fe8dde5ff35af6654082d7e1a4afcb58c9ff3d22bfcf9d41445c971fcc7",
}

# Other real commits of the public repositories (newer pi-engineering, older herdr-engineering),
# for "the pinned commit changed" drift.
ALT_PI_ENGINEERING = "278c7468054ae7e39b55052fc1cd1c5ad6f754cc"
ALT_HERDR_ENGINEERING = "0502a62e32d18d7f24b855cb16165be339228257"
