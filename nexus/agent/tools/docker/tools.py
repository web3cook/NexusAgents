"""Docker build, tag, push, and inspection tools."""

from __future__ import annotations

import subprocess

from agent.core.errors import NetworkError, NexusError
from agent.core.observability import instrument
from agent.core.retry import rate_limit, retry
from agent.tools.registry import registry


def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Runs docker with a hard timeout and no interactive stdin.

    Args:
        *args: Arguments forwarded to docker.
        timeout: Hard timeout in seconds before the call is killed.

    Returns:
        A CompletedProcess; on timeout, returncode is 1 and stderr
        describes the failure.
    """
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True, text=True, check=False,
            stdin=subprocess.DEVNULL, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess(["docker", *args], returncode=1)
        r.stdout = ""
        r.stderr = f"docker timed out after {timeout}s"
        return r


@registry.register(
    name="docker.build_image",
    description="Build a Docker image from a Dockerfile context directory",
    input_schema={
        "type": "object",
        "properties": {
            "context_path": {"type": "string"},
            "tag": {"type": "string"},
            "dockerfile": {"type": "string"},
        },
        "required": ["context_path", "tag"],
    },
)
@instrument(namespace="docker", tool="build_image")
@retry(max_attempts=2, base_delay_seconds=2.0, retryable_on=[NexusError])
def build_image(
    context_path: str, tag: str, dockerfile: str = "Dockerfile"
) -> dict:
    """Builds a Docker image from a context directory.

    Args:
        context_path: Path to the Docker build context.
        tag: The image tag (name:version).
        dockerfile: Path to the Dockerfile relative to context_path.

    Returns:
        A dict with tag, success flag, and truncated stdout.

    Raises:
        NexusError: If docker build exits with a non-zero code.
    """
    rate_limit("docker")
    result = _docker(
        "build", "-t", tag, "-f", dockerfile, context_path, timeout=900
    )
    if result.returncode != 0:
        raise NexusError(
            f"docker build failed: {result.stderr[:500]}", retryable=True
        )
    return {"tag": tag, "success": True, "stdout": result.stdout[-500:]}


@registry.register(
    name="docker.tag_image",
    description="Tag a Docker image for ECR push",
    input_schema={
        "type": "object",
        "properties": {
            "source_tag": {"type": "string"},
            "target_tag": {"type": "string"},
        },
        "required": ["source_tag", "target_tag"],
    },
)
@instrument(namespace="docker", tool="tag_image")
def tag_image(source_tag: str, target_tag: str) -> dict:
    """Tags a Docker image for ECR push.

    Args:
        source_tag: The existing image tag.
        target_tag: The new tag to apply.

    Returns:
        A dict with source_tag and target_tag.

    Raises:
        NexusError: If docker tag fails (retryable=False).
    """
    rate_limit("docker")
    result = _docker("tag", source_tag, target_tag, timeout=30)
    if result.returncode != 0:
        raise NexusError(
            f"docker tag failed: {result.stderr}", retryable=False
        )
    return {"source_tag": source_tag, "target_tag": target_tag}


@registry.register(
    name="docker.push_to_ecr",
    description=(
        "Push a Docker image to AWS ECR "
        "(assumes docker login already done)"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "image_tag": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["image_tag", "region"],
    },
)
@instrument(namespace="docker", tool="push_to_ecr")
@retry(
    max_attempts=3,
    base_delay_seconds=5.0,
    retryable_on=[NetworkError, NexusError],
)
def push_to_ecr(image_tag: str, region: str) -> dict:
    """Pushes a Docker image to AWS ECR.

    Assumes docker is already authenticated to ECR via docker login.

    Args:
        image_tag: The full ECR image URI including tag.
        region: Unused; provided for consistency with other AWS tools.

    Returns:
        A dict with ecr_uri and pushed flag.

    Raises:
        NetworkError: If docker push fails.
    """
    rate_limit("docker")
    result = _docker("push", image_tag, timeout=600)
    if result.returncode != 0:
        raise NetworkError(f"docker push failed: {result.stderr[:300]}")
    return {"ecr_uri": image_tag, "pushed": True}


@registry.register(
    name="docker.run_local",
    description="Run a container locally for a smoke test, returns logs",
    input_schema={
        "type": "object",
        "properties": {
            "image_tag": {"type": "string"},
            "port": {"type": "integer"},
        },
        "required": ["image_tag"],
    },
)
@instrument(namespace="docker", tool="run_local")
def run_local(image_tag: str, port: int = 8080) -> dict:
    """Runs a container locally for a smoke test.

    Args:
        image_tag: The image to run.
        port: The host port to map to the container's port 8000.

    Returns:
        A dict with container_id, port, and started flag.
    """
    rate_limit("docker")
    result = _docker(
        "run", "--rm", "-d", "-p", f"{port}:8000", image_tag, timeout=30
    )
    container_id = result.stdout.strip()
    if result.returncode != 0:
        return {
            "container_id": "",
            "port": port,
            "started": False,
            "error": result.stderr[:200],
        }
    return {"container_id": container_id, "port": port, "started": True}


@registry.register(
    name="docker.inspect_image",
    description="Inspect a Docker image to get layer count and size",
    input_schema={
        "type": "object",
        "properties": {"image_tag": {"type": "string"}},
        "required": ["image_tag"],
    },
)
@instrument(namespace="docker", tool="inspect_image")
def inspect_image(image_tag: str) -> dict:
    """Inspects a Docker image to get its size in megabytes.

    Args:
        image_tag: The image tag to inspect.

    Returns:
        A dict with image_tag and size_mb.
    """
    rate_limit("docker")
    result = _docker(
        "inspect", "--format", "{{.Size}}", image_tag, timeout=15
    )
    if result.returncode != 0:
        return {
            "image_tag": image_tag,
            "size_mb": 0,
            "error": result.stderr[:200],
        }
    size_bytes = (
        int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    )
    return {
        "image_tag": image_tag,
        "size_mb": round(size_bytes / 1_048_576, 1),
    }
