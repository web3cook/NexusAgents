from __future__ import annotations

from typing import Any, Literal


class NexusError(Exception):
    """Base exception for all Nexus failures.

    Attributes:
        retryable: Whether the failed operation may be retried.
    """

    def __init__(self, message: str, retryable: bool = False):
        """Initializes the error.

        Args:
            message: Human-readable error description.
            retryable: Whether the operation may be retried.
        """
        super().__init__(message)
        self.retryable = retryable


class PlanningError(NexusError):
    """Raised when the planning phase cannot produce a valid build plan."""

    def __init__(self, message: str):
        """Initializes the error.

        Args:
            message: Human-readable error description.
        """
        super().__init__(message, retryable=False)


class BuildError(NexusError):
    """Raised when scaffolding the backend or frontend fails.

    Attributes:
        phase: Which build phase failed.
        files_created: Paths written before the failure.
    """

    def __init__(
        self,
        message: str,
        phase: Literal["backend", "frontend"],
        files_created: list[str],
    ):
        """Initializes the error.

        Args:
            message: Human-readable error description.
            phase: Which build phase failed.
            files_created: Paths written before the failure.
        """
        super().__init__(message, retryable=True)
        self.phase = phase
        self.files_created = files_created


class DeploymentError(NexusError):
    """Raised when provisioning or deploying infrastructure fails.

    Attributes:
        last_successful_step: The last deployment step that succeeded.
        cluster_name: The target cluster, if one was created.
    """

    def __init__(
        self,
        message: str,
        last_successful_step: str,
        cluster_name: str | None = None,
    ):
        """Initializes the error.

        Args:
            message: Human-readable error description.
            last_successful_step: The last deployment step that succeeded.
            cluster_name: The target cluster, if one was created.
        """
        super().__init__(message, retryable=True)
        self.last_successful_step = last_successful_step
        self.cluster_name = cluster_name


class TestFailure(NexusError):
    """Raised when integration or end-to-end tests fail.

    Attributes:
        report: Structured test results.
    """

    __test__ = False  # Prevents pytest from collecting this as a test class.

    def __init__(self, message: str, report: dict[str, Any]):
        """Initializes the error.

        Args:
            message: Human-readable error description.
            report: Structured test results.
        """
        super().__init__(message, retryable=False)
        self.report = report


class AlertingError(NexusError):
    """Raised when the alerting subsystem cannot deliver an alert."""

    def __init__(self, message: str):
        """Initializes the error.

        Args:
            message: Human-readable error description.
        """
        super().__init__(message, retryable=True)


class RateLimitError(NexusError):
    """Raised when a namespace exceeds its configured call rate.

    Attributes:
        namespace: The rate-limited namespace.
    """

    def __init__(self, namespace: str):
        """Initializes the error.

        Args:
            namespace: The rate-limited namespace.
        """
        super().__init__(
            f"Rate limit exceeded for namespace: {namespace}",
            retryable=True,
        )
        self.namespace = namespace


class TransientAwsError(NexusError):
    """Raised for retryable AWS API failures."""

    def __init__(self, message: str):
        """Initializes the error.

        Args:
            message: Human-readable error description.
        """
        super().__init__(message, retryable=True)


class NetworkError(NexusError):
    """Raised for retryable network failures."""

    def __init__(self, message: str):
        """Initializes the error.

        Args:
            message: Human-readable error description.
        """
        super().__init__(message, retryable=True)
