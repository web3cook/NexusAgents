from __future__ import annotations
from typing import Any, Literal

class NexusError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable

class PlanningError(NexusError):
    def __init__(self, message: str):
        super().__init__(message, retryable=False)

class BuildError(NexusError):
    def __init__(self, message: str, phase: Literal["backend", "frontend"], files_created: list[str]):
        super().__init__(message, retryable=True)
        self.phase = phase
        self.files_created = files_created

class DeploymentError(NexusError):
    def __init__(self, message: str, last_successful_step: str, cluster_name: str | None = None):
        super().__init__(message, retryable=True)
        self.last_successful_step = last_successful_step
        self.cluster_name = cluster_name

class TestFailure(NexusError):
    __test__ = False  # prevent pytest from collecting this as a test class

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message, retryable=False)
        self.report = report

class AlertingError(NexusError):
    def __init__(self, message: str):
        super().__init__(message, retryable=True)

class RateLimitError(NexusError):
    def __init__(self, namespace: str):
        super().__init__(f"Rate limit exceeded for namespace: {namespace}", retryable=True)
        self.namespace = namespace

class TransientAwsError(NexusError):
    def __init__(self, message: str):
        super().__init__(message, retryable=True)

class NetworkError(NexusError):
    def __init__(self, message: str):
        super().__init__(message, retryable=True)
