from __future__ import annotations
import subprocess
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import retry, rate_limit
from agent.core.errors import NetworkError, NexusError


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
def build_image(context_path: str, tag: str, dockerfile: str = "Dockerfile") -> dict:
    rate_limit("docker")
    result = subprocess.run(
        ["docker", "build", "-t", tag, "-f", dockerfile, context_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise NexusError(f"docker build failed: {result.stderr[:500]}", retryable=True)
    return {"tag": tag, "success": True, "stdout": result.stdout[-500:]}


@registry.register(
    name="docker.tag_image",
    description="Tag a Docker image for ECR push",
    input_schema={
        "type": "object",
        "properties": {"source_tag": {"type": "string"}, "target_tag": {"type": "string"}},
        "required": ["source_tag", "target_tag"],
    },
)
@instrument(namespace="docker", tool="tag_image")
def tag_image(source_tag: str, target_tag: str) -> dict:
    rate_limit("docker")
    result = subprocess.run(["docker", "tag", source_tag, target_tag], capture_output=True, text=True)
    if result.returncode != 0:
        raise NexusError(f"docker tag failed: {result.stderr}", retryable=False)
    return {"source_tag": source_tag, "target_tag": target_tag}


@registry.register(
    name="docker.push_to_ecr",
    description="Push a Docker image to AWS ECR (assumes docker login already done)",
    input_schema={
        "type": "object",
        "properties": {"image_tag": {"type": "string"}, "region": {"type": "string"}},
        "required": ["image_tag", "region"],
    },
)
@instrument(namespace="docker", tool="push_to_ecr")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[NetworkError, NexusError])
def push_to_ecr(image_tag: str, region: str) -> dict:
    rate_limit("docker")
    result = subprocess.run(["docker", "push", image_tag], capture_output=True, text=True)
    if result.returncode != 0:
        raise NetworkError(f"docker push failed: {result.stderr[:300]}")
    return {"ecr_uri": image_tag, "pushed": True}


@registry.register(
    name="docker.run_local",
    description="Run a container locally for a smoke test, returns logs",
    input_schema={
        "type": "object",
        "properties": {"image_tag": {"type": "string"}, "port": {"type": "integer"}},
        "required": ["image_tag"],
    },
)
@instrument(namespace="docker", tool="run_local")
def run_local(image_tag: str, port: int = 8080) -> dict:
    rate_limit("docker")
    result = subprocess.run(
        ["docker", "run", "--rm", "-d", "-p", f"{port}:8000", image_tag],
        capture_output=True, text=True,
    )
    container_id = result.stdout.strip()
    return {"container_id": container_id, "port": port, "started": result.returncode == 0}


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
    rate_limit("docker")
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.Size}}", image_tag],
        capture_output=True, text=True,
    )
    size_bytes = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    return {"image_tag": image_tag, "size_mb": round(size_bytes / 1_048_576, 1)}
