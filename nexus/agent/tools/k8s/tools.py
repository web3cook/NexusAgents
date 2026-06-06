from __future__ import annotations
import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import retry, rate_limit
from agent.core.errors import NetworkError, NexusError


def _kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=False)


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
@retry(max_attempts=3, base_delay_seconds=3.0, retryable_on=[NetworkError, NexusError])
def apply_manifest(manifest_path: str, kubeconfig: str | None = None) -> dict:
    rate_limit("k8s")
    cmd = ["kubectl", "apply", "-f", manifest_path]
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    result = subprocess.run(cmd, capture_output=True, text=True)
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
def delete_manifest(manifest_path: str) -> dict:
    rate_limit("k8s")
    result = _kubectl("delete", "-f", manifest_path, "--ignore-not-found=true")
    return {"manifest_path": manifest_path, "deleted": result.returncode == 0}


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
def create_namespace(name: str) -> dict:
    rate_limit("k8s")
    manifest = f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {name}\n"
    subprocess.run(["kubectl", "apply", "-f", "-"], input=manifest, capture_output=True, text=True)
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
def create_secret(name: str, namespace: str, data: dict) -> dict:
    rate_limit("k8s")
    literals = [f"--from-literal={k}={v}" for k, v in data.items()]
    dry_run = subprocess.run(
        ["kubectl", "create", "secret", "generic", name, "--namespace", namespace,
         "--dry-run=client", "-o", "yaml", *literals],
        capture_output=True, text=True,
    )
    apply = subprocess.run(["kubectl", "apply", "-f", "-"],
        input=dry_run.stdout, capture_output=True, text=True)
    return {"name": name, "namespace": namespace, "created": apply.returncode == 0}


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
def create_configmap(name: str, namespace: str, data: dict) -> dict:
    rate_limit("k8s")
    literals = [f"--from-literal={k}={v}" for k, v in data.items()]
    dry_run = subprocess.run(
        ["kubectl", "create", "configmap", name, "--namespace", namespace,
         "--dry-run=client", "-o", "yaml", *literals],
        capture_output=True, text=True,
    )
    subprocess.run(["kubectl", "apply", "-f", "-"], input=dry_run.stdout, capture_output=True, text=True)
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
@retry(max_attempts=2, base_delay_seconds=5.0, retryable_on=[NexusError])
def deploy_helm_chart(release_name: str, chart_path: str, namespace: str, values: dict | None = None) -> dict:
    rate_limit("k8s")
    cmd = ["helm", "upgrade", "--install", release_name, chart_path, "--namespace", namespace, "--create-namespace"]
    if values:
        for k, v in values.items():
            cmd += ["--set", f"{k}={v}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
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
def get_pod_status(namespace: str, deployment: str | None = None) -> dict:
    rate_limit("k8s")
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-o",
         "jsonpath={range .items[*]}{.metadata.name},{.status.phase},{.status.containerStatuses[0].ready}\\n{end}"],
        capture_output=True, text=True,
    )
    pods = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) == 3:
            pods.append({"name": parts[0], "phase": parts[1], "ready": parts[2] == "true"})
    return {"namespace": namespace, "pods": pods, "all_ready": all(p["ready"] for p in pods)}


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
def get_pod_logs(namespace: str, deployment: str, tail: int = 100) -> dict:
    rate_limit("k8s")
    result = subprocess.run(
        ["kubectl", "logs", "-n", namespace, f"deployment/{deployment}", f"--tail={tail}"],
        capture_output=True, text=True,
    )
    return {"deployment": deployment, "logs": result.stdout, "lines": len(result.stdout.splitlines())}


@registry.register(
    name="k8s.wait_for_rollout",
    description="Block until a Kubernetes deployment rollout is complete (timeout 300s)",
    input_schema={
        "type": "object",
        "properties": {"namespace": {"type": "string"}, "deployment": {"type": "string"}},
        "required": ["namespace", "deployment"],
    },
)
@instrument(namespace="k8s", tool="wait_for_rollout")
@retry(max_attempts=3, base_delay_seconds=15.0, retryable_on=[NexusError])
def wait_for_rollout(namespace: str, deployment: str) -> dict:
    rate_limit("k8s")
    result = subprocess.run(
        ["kubectl", "rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=300s"],
        capture_output=True, text=True,
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
def scale_deployment(namespace: str, deployment: str, replicas: int) -> dict:
    rate_limit("k8s")
    result = _kubectl("scale", f"deployment/{deployment}", f"--replicas={replicas}", "-n", namespace)
    return {"deployment": deployment, "replicas": replicas, "scaled": result.returncode == 0}


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
    result = subprocess.run(
        ["kubectl", "get", "ingress", "-n", namespace,
         "-o", "jsonpath={.items[0].status.loadBalancer.ingress[0].hostname}"],
        capture_output=True, text=True,
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
@retry(max_attempts=2, base_delay_seconds=5.0, retryable_on=[NexusError])
def run_migration_job(workspace: str, namespace: str, image: str) -> dict:
    rate_limit("k8s")
    templates_dir = Path(workspace).parent / "templates"
    jinja = Environment(loader=FileSystemLoader(str(templates_dir)), trim_blocks=True, lstrip_blocks=True)
    manifest = jinja.get_template("k8s/migration-job.yaml.j2").render(name="backend", namespace=namespace, image=image)
    manifest_path = Path(workspace) / "k8s" / "migration-job.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest)
    result = subprocess.run(["kubectl", "apply", "-f", str(manifest_path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise NexusError(f"migration job failed: {result.stderr[:200]}", retryable=True)
    wait = subprocess.run(["kubectl", "wait", "--for=condition=complete", "job/backend-migration",
                           "-n", namespace, "--timeout=120s"], capture_output=True, text=True)
    return {"completed": wait.returncode == 0, "namespace": namespace}


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
def get_resource_usage(namespace: str) -> dict:
    rate_limit("k8s")
    result = subprocess.run(
        ["kubectl", "top", "pods", "-n", namespace, "--no-headers"],
        capture_output=True, text=True,
    )
    pods = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            pods.append({"name": parts[0], "cpu": parts[1], "memory": parts[2]})
    return {"namespace": namespace, "pods": pods}
