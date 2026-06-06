from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import retry, rate_limit
from agent.core.errors import NetworkError, NexusError

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"


def _kube(*args: str, timeout: int = 60, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    """Run kubectl with a hard timeout and no interactive stdin."""
    try:
        return subprocess.run(
            ["kubectl", *args],
            input=stdin_text,
            capture_output=True, text=True, check=False,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess(["kubectl", *args], returncode=1)
        r.stdout = ""
        r.stderr = f"kubectl timed out after {timeout}s"
        return r


@registry.register(
    name="k8s.apply_manifest",
    description="Apply a Kubernetes manifest file using kubectl apply",
    input_schema={
        "type": "object",
        "properties": {
            "manifest_path": {"type": "string"},
            "kubeconfig": {"type": "string"},
        },
        "required": ["manifest_path"],
    },
)
@instrument(namespace="k8s", tool="apply_manifest")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[NetworkError, NexusError])
def apply_manifest(manifest_path: str, kubeconfig: str | None = None) -> dict:
    rate_limit("k8s")
    args = ["apply", "-f", manifest_path]
    if kubeconfig:
        args += ["--kubeconfig", kubeconfig]
    result = _kube(*args, timeout=120)
    if result.returncode != 0:
        raise NexusError(f"kubectl apply failed: {result.stderr[:300]}", retryable=True)
    return {"manifest_path": manifest_path, "applied": True, "stdout": result.stdout}


@registry.register(
    name="k8s.delete_manifest",
    description="Delete Kubernetes resources defined in a manifest file",
    input_schema={
        "type": "object",
        "properties": {"manifest_path": {"type": "string"}},
        "required": ["manifest_path"],
    },
)
@instrument(namespace="k8s", tool="delete_manifest")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[NexusError])
def delete_manifest(manifest_path: str) -> dict:
    rate_limit("k8s")
    result = _kube("delete", "-f", manifest_path, "--ignore-not-found=true", timeout=60)
    if result.returncode != 0:
        raise NexusError(f"kubectl delete failed: {result.stderr[:200]}", retryable=True)
    return {"manifest_path": manifest_path, "deleted": True}


@registry.register(
    name="k8s.create_namespace",
    description="Create a Kubernetes namespace",
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
)
@instrument(namespace="k8s", tool="create_namespace")
@retry(max_attempts=3, base_delay_seconds=3.0, retryable_on=[NexusError])
def create_namespace(name: str) -> dict:
    rate_limit("k8s")
    manifest = f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {name}\n"
    result = _kube("apply", "-f", "-", stdin_text=manifest, timeout=30)
    if result.returncode != 0:
        raise NexusError(f"create_namespace failed: {result.stderr[:200]}", retryable=True)
    return {"namespace": name, "created": True}


@registry.register(
    name="k8s.create_secret",
    description="Create a Kubernetes secret from a key-value dict",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "namespace": {"type": "string"},
            "data": {"type": "object"},
        },
        "required": ["name", "namespace", "data"],
    },
)
@instrument(namespace="k8s", tool="create_secret")
@retry(max_attempts=3, base_delay_seconds=3.0, retryable_on=[NexusError])
def create_secret(name: str, namespace: str, data: dict) -> dict:
    rate_limit("k8s")
    literals = [f"--from-literal={k}={v}" for k, v in data.items()]
    dry = _kube(
        "create", "secret", "generic", name,
        "--namespace", namespace, "--dry-run=client", "-o", "yaml",
        *literals, timeout=30,
    )
    if dry.returncode != 0:
        raise NexusError(f"secret dry-run failed: {dry.stderr[:200]}", retryable=True)
    apply = _kube("apply", "-f", "-", stdin_text=dry.stdout, timeout=30)
    if apply.returncode != 0:
        raise NexusError(f"secret apply failed: {apply.stderr[:200]}", retryable=True)
    return {"name": name, "namespace": namespace, "created": True}


@registry.register(
    name="k8s.create_configmap",
    description="Create a Kubernetes ConfigMap from a key-value dict",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "namespace": {"type": "string"},
            "data": {"type": "object"},
        },
        "required": ["name", "namespace", "data"],
    },
)
@instrument(namespace="k8s", tool="create_configmap")
@retry(max_attempts=3, base_delay_seconds=3.0, retryable_on=[NexusError])
def create_configmap(name: str, namespace: str, data: dict) -> dict:
    rate_limit("k8s")
    literals = [f"--from-literal={k}={v}" for k, v in data.items()]
    dry = _kube(
        "create", "configmap", name,
        "--namespace", namespace, "--dry-run=client", "-o", "yaml",
        *literals, timeout=30,
    )
    if dry.returncode != 0:
        raise NexusError(f"configmap dry-run failed: {dry.stderr[:200]}", retryable=True)
    apply = _kube("apply", "-f", "-", stdin_text=dry.stdout, timeout=30)
    if apply.returncode != 0:
        raise NexusError(f"configmap apply failed: {apply.stderr[:200]}", retryable=True)
    return {"name": name, "namespace": namespace, "created": True}


@registry.register(
    name="k8s.deploy_helm_chart",
    description="Install or upgrade a Helm chart",
    input_schema={
        "type": "object",
        "properties": {
            "release_name": {"type": "string"},
            "chart_path": {"type": "string"},
            "namespace": {"type": "string"},
            "values": {"type": "object"},
        },
        "required": ["release_name", "chart_path", "namespace"],
    },
)
@instrument(namespace="k8s", tool="deploy_helm_chart")
@retry(max_attempts=3, base_delay_seconds=10.0, retryable_on=[NexusError])
def deploy_helm_chart(release_name: str, chart_path: str, namespace: str, values: dict | None = None) -> dict:
    rate_limit("k8s")
    cmd = ["helm", "upgrade", "--install", release_name, chart_path,
           "--namespace", namespace, "--create-namespace"]
    if values:
        for k, v in values.items():
            cmd += ["--set", f"{k}={v}"]
    try:
        # 600s: complex charts with CRDs, webhooks, and many resources can take time
        result = subprocess.run(cmd, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=600)
    except subprocess.TimeoutExpired:
        raise NexusError("helm upgrade timed out after 600s", retryable=True)
    if result.returncode != 0:
        raise NexusError(f"helm upgrade failed: {result.stderr[:300]}", retryable=True)
    return {"release_name": release_name, "deployed": True}


@registry.register(
    name="k8s.get_pod_status",
    description="Check health and readiness of pods in a namespace for a deployment",
    input_schema={
        "type": "object",
        "properties": {"namespace": {"type": "string"}, "deployment": {"type": "string"}},
        "required": ["namespace"],
    },
)
@instrument(namespace="k8s", tool="get_pod_status")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[NexusError])
def get_pod_status(namespace: str, deployment: str | None = None) -> dict:
    rate_limit("k8s")
    result = _kube(
        "get", "pods", "-n", namespace, "-o",
        r"jsonpath={range .items[*]}{.metadata.name},{.status.phase},{.status.containerStatuses[0].ready}\n{end}",
        timeout=30,
    )
    if result.returncode != 0:
        raise NexusError(f"get_pod_status failed: {result.stderr[:200]}", retryable=True)
    pods = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) == 3:
            pods.append({"name": parts[0], "phase": parts[1], "ready": parts[2] == "true"})
    # Empty pod list means not scheduled yet — not "all good"
    all_ready = len(pods) > 0 and all(p["ready"] for p in pods)
    return {"namespace": namespace, "pods": pods, "all_ready": all_ready}


@registry.register(
    name="k8s.get_pod_logs",
    description="Fetch recent logs from pods of a deployment",
    input_schema={
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "deployment": {"type": "string"},
            "tail": {"type": "integer"},
        },
        "required": ["namespace", "deployment"],
    },
)
@instrument(namespace="k8s", tool="get_pod_logs")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[NexusError])
def get_pod_logs(namespace: str, deployment: str, tail: int = 100) -> dict:
    rate_limit("k8s")
    result = _kube("logs", "-n", namespace, f"deployment/{deployment}", f"--tail={tail}", timeout=30)
    if result.returncode != 0:
        raise NexusError(f"get_pod_logs failed: {result.stderr[:200]}", retryable=True)
    return {"deployment": deployment, "logs": result.stdout, "lines": len(result.stdout.splitlines())}


@registry.register(
    name="k8s.wait_for_rollout",
    description="Block until a Kubernetes deployment rollout is complete",
    input_schema={
        "type": "object",
        "properties": {"namespace": {"type": "string"}, "deployment": {"type": "string"}},
        "required": ["namespace", "deployment"],
    },
)
@instrument(namespace="k8s", tool="wait_for_rollout")
@retry(max_attempts=3, base_delay_seconds=30.0, retryable_on=[NexusError])
def wait_for_rollout(namespace: str, deployment: str) -> dict:
    rate_limit("k8s")
    # 600s for kubectl: allows for ECR image pull on cold nodes (can take 8-12 min on first deploy)
    # subprocess timeout is 660s so Python always receives kubectl's own error message
    result = _kube(
        "rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=600s",
        timeout=660,
    )
    if result.returncode != 0:
        raise NexusError(f"rollout not ready: {result.stderr[:200]}", retryable=True)
    return {"deployment": deployment, "ready": True}


@registry.register(
    name="k8s.scale_deployment",
    description="Scale a deployment to the specified number of replicas",
    input_schema={
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "deployment": {"type": "string"},
            "replicas": {"type": "integer"},
        },
        "required": ["namespace", "deployment", "replicas"],
    },
)
@instrument(namespace="k8s", tool="scale_deployment")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[NexusError])
def scale_deployment(namespace: str, deployment: str, replicas: int) -> dict:
    rate_limit("k8s")
    result = _kube("scale", f"deployment/{deployment}", f"--replicas={replicas}", "-n", namespace, timeout=30)
    if result.returncode != 0:
        raise NexusError(f"scale_deployment failed: {result.stderr[:200]}", retryable=True)
    return {"deployment": deployment, "replicas": replicas, "scaled": True}


@registry.register(
    name="k8s.get_ingress_address",
    description="Get the external IP or hostname from the ingress resource",
    input_schema={
        "type": "object",
        "properties": {"namespace": {"type": "string"}},
        "required": ["namespace"],
    },
)
@instrument(namespace="k8s", tool="get_ingress_address")
@retry(max_attempts=8, base_delay_seconds=15.0, retryable_on=[NexusError])
def get_ingress_address(namespace: str) -> dict:
    rate_limit("k8s")
    result = _kube(
        "get", "ingress", "-n", namespace,
        "-o", "jsonpath={.items[0].status.loadBalancer.ingress[0].hostname}",
        timeout=30,
    )
    address = result.stdout.strip()
    if not address:
        raise NexusError("Ingress address not yet assigned", retryable=True)
    return {"address": address, "url": f"http://{address}"}


@registry.register(
    name="k8s.run_migration_job",
    description="Run a Kubernetes Job to execute Alembic database migrations",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "namespace": {"type": "string"},
            "image": {"type": "string"},
        },
        "required": ["workspace", "namespace", "image"],
    },
)
@instrument(namespace="k8s", tool="run_migration_job")
@retry(max_attempts=3, base_delay_seconds=10.0, retryable_on=[NexusError])
def run_migration_job(workspace: str, namespace: str, image: str) -> dict:
    rate_limit("k8s")
    JOB_NAME = "backend-migration"

    # If the job already completed successfully, skip re-running it
    check = _kube(
        "get", "job", JOB_NAME, "-n", namespace,
        "-o", r"jsonpath={.status.conditions[?(@.type=='Complete')].status}",
        timeout=15,
    )
    if check.returncode == 0 and check.stdout.strip() == "True":
        return {"completed": True, "namespace": namespace, "skipped": True}

    # Render manifest
    jinja = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR / "k8s")),
        trim_blocks=True, lstrip_blocks=True,
    )
    manifest = jinja.get_template("migration-job.yaml.j2").render(
        name="backend", namespace=namespace, image=image,
    )
    manifest_path = Path(workspace) / "k8s" / "migration-job.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest)

    # Jobs are immutable once created — delete any existing one first
    _kube("delete", "job", JOB_NAME, "-n", namespace, "--ignore-not-found=true", timeout=30)

    apply = _kube("apply", "-f", str(manifest_path), timeout=60)
    if apply.returncode != 0:
        raise NexusError(f"migration job apply failed: {apply.stderr[:200]}", retryable=True)

    # Poll for Complete or Failed (up to 5 min) — kubectl wait only supports one
    # condition at a time, so polling lets us detect failure early instead of
    # waiting the full timeout when alembic exits non-zero.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = _kube(
            "get", "job", JOB_NAME, "-n", namespace,
            "-o", r"jsonpath={.status.conditions}",
            timeout=15,
        )
        if status.returncode == 0 and status.stdout.strip():
            try:
                for cond in json.loads(status.stdout):
                    if cond.get("type") == "Complete" and cond.get("status") == "True":
                        return {"completed": True, "namespace": namespace}
                    if cond.get("type") == "Failed" and cond.get("status") == "True":
                        logs = _kube(
                            "logs", "-n", namespace, f"-l job-name={JOB_NAME}", "--tail=50",
                            timeout=20,
                        )
                        raise NexusError(
                            f"migration job failed — alembic logs: {logs.stdout[-400:] or logs.stderr[-200:]}",
                            retryable=False,
                        )
            except (json.JSONDecodeError, KeyError):
                pass
        time.sleep(5)

    raise NexusError("migration job timed out after 300s", retryable=True)


@registry.register(
    name="k8s.get_resource_usage",
    description="Query metrics-server for CPU and memory usage per pod",
    input_schema={
        "type": "object",
        "properties": {"namespace": {"type": "string"}},
        "required": ["namespace"],
    },
)
@instrument(namespace="k8s", tool="get_resource_usage")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[NexusError])
def get_resource_usage(namespace: str) -> dict:
    rate_limit("k8s")
    result = _kube("top", "pods", "-n", namespace, "--no-headers", timeout=30)
    if result.returncode != 0:
        raise NexusError(f"kubectl top failed: {result.stderr[:200]}", retryable=True)
    pods = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            pods.append({"name": parts[0], "cpu": parts[1], "memory": parts[2]})
    return {"namespace": namespace, "pods": pods}
