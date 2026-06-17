"""Thin wrapper over the `docker` CLI (subprocess; no docker SDK).

Provides a keep-alive container session for the `task` commands. The session
overrides the image entrypoint so the container stays running regardless of the
image's default (often interactive bash, which exits without a TTY), yields a
handle for `exec`/`cp_to`, and always force-removes the container on exit.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional


class ContainerError(RuntimeError):
    """Raised when a container cannot be started or inspected."""


def _run(args: list[str], timeout: int = 600) -> tuple[int, str, str]:
    """Run a docker command, returning (rc, stdout, stderr)."""
    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, proc.stdout, proc.stderr


def image_exists(image: str) -> bool:
    rc, _, _ = _run(["docker", "image", "inspect", image])
    return rc == 0


def image_digest(image: str) -> Optional[str]:
    """Return the first RepoDigest, or fall back to the image Id."""
    rc, out, _ = _run(
        ["docker", "inspect", "-f", "{{index .RepoDigests 0}}", image]
    )
    digest = out.strip()
    if rc == 0 and digest and digest != "<no value>":
        return digest
    rc, out, _ = _run(["docker", "inspect", "-f", "{{.Id}}", image])
    out = out.strip()
    return out or None


def image_entrypoint_cmd(image: str) -> tuple[Optional[list], Optional[list]]:
    """Return (Entrypoint, Cmd) as lists (or None) from docker inspect."""
    rc, out, err = _run(
        ["docker", "inspect", "-f", "{{json .Config.Entrypoint}} {{json .Config.Cmd}}", image]
    )
    if rc != 0:
        raise ContainerError(f"docker inspect failed for {image}: {err.strip()}")
    ep_raw, _, cmd_raw = out.strip().partition(" ")
    return json.loads(ep_raw), json.loads(cmd_raw)


class ContainerHandle:
    """Handle to a running container."""

    def __init__(self, name: str) -> None:
        self.name = name

    def exec(
        self, cmd: str, workdir: Optional[str] = None, check: bool = False,
        timeout: int = 600,
    ) -> tuple[int, str, str]:
        """Run `cmd` via `bash -lc` inside the container."""
        args = ["docker", "exec"]
        if workdir:
            args += ["-w", workdir]
        args += [self.name, "bash", "-lc", cmd]
        rc, out, err = _run(args, timeout=timeout)
        if check and rc != 0:
            raise ContainerError(
                f"exec failed (rc={rc}) in {self.name}: {cmd}\n{err.strip()}"
            )
        return rc, out, err

    def cp_to(self, src: str, dst: str) -> None:
        """Copy a host file into the container at `dst`."""
        rc, _, err = _run(["docker", "cp", src, f"{self.name}:{dst}"])
        if rc != 0:
            raise ContainerError(f"docker cp {src} -> {dst} failed: {err.strip()}")

    def cp_from(self, src: str, dst: str, timeout: int = 300) -> None:
        """Copy `src` (a path inside the container) out to host path `dst`.

        Used by the agentic solver to stage a disposable host copy of the masked
        repo. If `dst` does not exist, docker creates it with the contents of a
        source directory (including .git).
        """
        rc, _, err = _run(["docker", "cp", f"{self.name}:{src}", dst], timeout=timeout)
        if rc != 0:
            raise ContainerError(f"docker cp {self.name}:{src} -> {dst} failed: {err.strip()}")


# Keep-alive entrypoints to try, in order. Each must keep PID 1 alive.
_KEEPALIVE = [
    ("tail", ["-f", "/dev/null"]),
    ("sleep", ["infinity"]),
]


@contextmanager
def container_session(
    image: str, name: Optional[str] = None, network: Optional[str] = None,
    memory: Optional[str] = None, cpus: Optional[str] = None,
    pids_limit: Optional[int] = None,
) -> Iterator[ContainerHandle]:
    """Start a detached keep-alive container; yield a handle; always remove it.

    Overrides the image entrypoint so the container does not launch the image's
    default (e.g. interactive bash). Tries each keep-alive in turn, surfacing the
    entrypoint/cmd and `docker logs` if none stays running. Optional resource
    limits (memory/cpus/pids_limit) are passed to `docker run` when provided.
    """
    if not image_exists(image):
        raise ContainerError(f"image not present locally: {image}")

    name = name or f"taskbundle-{uuid.uuid4().hex[:12]}"
    ep, cmd = image_entrypoint_cmd(image)
    last_err = ""

    started = False
    for binary, tail_args in _KEEPALIVE:
        run_args = ["docker", "run", "-d", "--name", name, "--entrypoint", binary]
        if network:
            run_args += ["--network", network]
        if memory:
            run_args += ["--memory", memory]
        if cpus:
            run_args += ["--cpus", cpus]
        if pids_limit:
            run_args += ["--pids-limit", str(pids_limit)]
        run_args += [image, *tail_args]
        rc, out, err = _run(run_args)
        if rc != 0:
            last_err = err.strip()
            _run(["docker", "rm", "-f", name])
            continue
        # Confirm it's actually running.
        rc2, running, _ = _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name]
        )
        if rc2 == 0 and running.strip() == "true":
            started = True
            break
        # Started but exited: collect logs, clean up, try next.
        _, logs, _ = _run(["docker", "logs", name])
        last_err = f"container exited (entrypoint={binary}); logs: {logs.strip()}"
        _run(["docker", "rm", "-f", name])

    if not started:
        raise ContainerError(
            f"could not start keep-alive container for {image}\n"
            f"  image Entrypoint={ep} Cmd={cmd}\n  last error: {last_err}"
        )

    try:
        yield ContainerHandle(name)
    finally:
        _run(["docker", "rm", "-f", name])
