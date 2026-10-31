"""Container runtimes for the scenarios: Docker and Apptainer, both real.

HOME-isolation rule (docs/testing.md): the real host $HOME and the user's config directories are
never mounted or exposed. Docker mounts only the repo, read-only, plus the container's own
writable layer. Apptainer runs with --containall and a throwaway directory as its home.
Everything created here carries the ``herdr-fleet-test-`` prefix and only those objects are
removed at teardown; nothing is ever pruned.
"""

from __future__ import annotations

import abc
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

PREFIX = "herdr-fleet-test-"
CONTAINER_HOME = "/home/fleet"
CONTAINER_REPO = "/repo"
BASE_PATH = f"{CONTAINER_HOME}/venv/bin:/usr/local/bin:/usr/bin:/bin"
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

Completed = subprocess.CompletedProcess[str]


def _run(
    argv: Sequence[str], *, env: Mapping[str, str] | None = None, timeout: float = 900
) -> Completed:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=None if env is None else dict(env),
    )


def _checked(argv: Sequence[str], **kwargs: object) -> Completed:
    done = _run(argv, **kwargs)  # type: ignore[arg-type]
    if done.returncode != 0:
        raise RuntimeError(
            f"{' '.join(argv)} failed ({done.returncode}):\n{done.stdout}\n{done.stderr}"
        )
    return done


class ContainerRuntime(abc.ABC):
    """Run commands as the unprivileged ``fleet`` user inside an isolated container."""

    name: str

    def __init__(self) -> None:
        self.unique = uuid.uuid4().hex[:8]

    @classmethod
    @abc.abstractmethod
    def unavailable_reason(cls) -> str | None:
        """None when usable, otherwise why the tests cannot run on this machine."""

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def exec(
        self, argv: Sequence[str], *, stdin: str | None = None, timeout: float = 900
    ) -> Completed:
        """Run ``argv`` in the container with HOME=/home/fleet and a clean environment."""

    @abc.abstractmethod
    def stop(self) -> list[str]:
        """Remove what ``start`` created; returns a description of each removed object."""

    def sh(self, script: str, *, timeout: float = 900) -> Completed:
        return self.exec(["bash", "-c", script], timeout=timeout)

    def write_file(self, path: str, text: str) -> None:
        done = self.exec(
            ["bash", "-c", f'mkdir -p "$(dirname {path})" && cat > {path}'], stdin=text
        )
        if done.returncode != 0:
            raise RuntimeError(f"write_file {path}: {done.stderr}")


def _which(binary: str) -> str | None:
    return shutil.which(binary)


def _docker_ready() -> str | None:
    if _which("docker") is None:
        return "docker binary not found on PATH"
    probe = _run(["docker", "info"], timeout=60)
    return (
        None
        if probe.returncode == 0
        else f"docker daemon unreachable: {probe.stderr.strip()[:200]}"
    )


def _build_image(tag: str) -> None:
    """Build the test image from a context holding only the Dockerfile."""
    with tempfile.TemporaryDirectory(prefix=PREFIX) as context:
        shutil.copy(HERE / "Dockerfile", Path(context) / "Dockerfile")
        _checked(["docker", "build", "--quiet", "-t", tag, context], timeout=1200)


class DockerRuntime(ContainerRuntime):
    name = "docker"

    def __init__(self) -> None:
        super().__init__()
        self.image = f"{PREFIX}img-{self.unique}"
        self.container = f"{PREFIX}{self.unique}"

    @classmethod
    def unavailable_reason(cls) -> str | None:
        return _docker_ready()

    def start(self) -> None:
        _build_image(self.image)
        _checked(
            [
                "docker", "run", "--detach", "--init",
                "--name", self.container,
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--mount", f"type=bind,source={REPO},target={CONTAINER_REPO},readonly",
                self.image, "sleep", "infinity",
            ]
        )  # fmt: skip

    def exec(
        self, argv: Sequence[str], *, stdin: str | None = None, timeout: float = 900
    ) -> Completed:
        command = [
            "docker", "exec", "--interactive", "--user", "fleet", "--workdir", CONTAINER_HOME,
            "--env", f"HOME={CONTAINER_HOME}", "--env", f"PATH={BASE_PATH}",
            "--env", f"TMPDIR={CONTAINER_HOME}/tmp",
            self.container, *argv,
        ]  # fmt: skip
        return subprocess.run(
            command, input=stdin or "", capture_output=True, text=True, check=False, timeout=timeout
        )

    def stop(self) -> list[str]:
        removed: list[str] = []
        # Always try: `docker run` can create the named container and still fail (for example
        # when the command cannot start), leaving it behind.
        if _run(["docker", "container", "inspect", self.container]).returncode == 0:
            _run(["docker", "rm", "--force", self.container])
            removed.append(f"container {self.container}")
        if _run(["docker", "image", "inspect", self.image]).returncode == 0:
            _run(["docker", "rmi", self.image])
            removed.append(f"image {self.image}")
        return removed


class ApptainerRuntime(ContainerRuntime):
    name = "apptainer"

    def __init__(self) -> None:
        super().__init__()
        self.image = f"{PREFIX}img-{self.unique}"
        self.workdir = Path(tempfile.mkdtemp(prefix=f"{PREFIX}{self.unique}-"))
        self.home = self.workdir / "home"
        self.sif = self.workdir / f"{PREFIX}{self.unique}.sif"
        self._built_image = False

    @classmethod
    def unavailable_reason(cls) -> str | None:
        if _which("apptainer") is None:
            return "apptainer binary not found on PATH"
        docker = _docker_ready()
        return None if docker is None else f"cannot build the SIF from a docker image: {docker}"

    def _apptainer_env(self) -> dict[str, str]:
        """Keep apptainer's cache and temp files out of the real home directory."""
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG")}
        env["APPTAINER_CACHEDIR"] = str(self.workdir / "cache")
        env["APPTAINER_TMPDIR"] = str(self.workdir / "tmp")
        env["HOME"] = str(self.workdir / "hostenv-home")
        for sub in ("cache", "tmp", "hostenv-home"):
            (self.workdir / sub).mkdir(exist_ok=True)
        return env

    def start(self) -> None:
        self.home.mkdir()
        _build_image(self.image)
        self._built_image = True
        _checked(
            [
                "apptainer",
                "build",
                "--force",
                str(self.sif),
                f"docker-daemon://{self.image}:latest",
            ],
            env=self._apptainer_env(),
            timeout=1200,
        )

    def exec(
        self, argv: Sequence[str], *, stdin: str | None = None, timeout: float = 900
    ) -> Completed:
        command = [
            "apptainer", "exec", "--containall",
            "--home", f"{self.home}:{CONTAINER_HOME}",
            "--bind", f"{REPO}:{CONTAINER_REPO}:ro",
            "--pwd", CONTAINER_HOME,
            "--env", f"PATH={BASE_PATH}",
            "--env", f"TMPDIR={CONTAINER_HOME}/tmp",
            str(self.sif), *argv,
        ]  # fmt: skip
        return subprocess.run(
            command,
            input=stdin or "",
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=self._apptainer_env(),
        )

    def stop(self) -> list[str]:
        removed: list[str] = []
        if self._built_image and _run(["docker", "image", "inspect", self.image]).returncode == 0:
            _run(["docker", "rmi", self.image])
            removed.append(f"image {self.image}")
        if self.workdir.exists():
            # Read-only files or directories (npm caches, extracted trees) make rmtree fail.
            _run(["chmod", "-R", "u+rwX", str(self.workdir)])
            shutil.rmtree(self.workdir, ignore_errors=True)
            if self.workdir.exists():
                removed.append(f"dir {self.workdir} NOT removed: files remain")
            else:
                removed.append(f"dir {self.workdir} (home, cache, {self.sif.name})")
        return removed


RUNTIMES: tuple[type[ContainerRuntime], ...] = (DockerRuntime, ApptainerRuntime)
