"""AWS provisioning tools for EKS, ECR, RDS, S3, and CloudFront."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta

import boto3
from botocore.exceptions import ClientError

from agent.core.errors import NetworkError, TransientAwsError
from agent.core.observability import instrument
from agent.core.retry import rate_limit, retry
from agent.tools.registry import registry


def _client(service: str, region: str):
    """Creates a boto3 client for the given service and region.

    Args:
        service: The AWS service name (e.g., "eks", "ecr").
        region: The AWS region identifier.

    Returns:
        A boto3 client for the requested service and region.
    """
    return boto3.client(service, region_name=region)


@registry.register(
    name="aws.create_ecr_repo",
    description="Create an ECR repository for storing Docker images",
    input_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["repo_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_ecr_repo")
@retry(max_attempts=3, base_delay_seconds=2.0, retryable_on=[TransientAwsError])
def create_ecr_repo(repo_name: str, region: str) -> dict:
    """Creates an ECR repository, returning the existing one if present.

    Args:
        repo_name: The repository name to create.
        region: The AWS region for the repository.

    Returns:
        A dict with repository_uri and a created flag.

    Raises:
        TransientAwsError: On unexpected AWS errors.
    """
    rate_limit("aws")
    ecr = _client("ecr", region)
    try:
        resp = ecr.create_repository(repositoryName=repo_name)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        resp = ecr.describe_repositories(repositoryNames=[repo_name])
        return {
            "repository_uri": resp["repositories"][0]["repositoryUri"],
            "created": False,
        }
    except ClientError as e:
        raise TransientAwsError(f"create_ecr_repo failed: {e}") from e
    return {
        "repository_uri": resp["repository"]["repositoryUri"],
        "created": True,
    }


@registry.register(
    name="aws.create_eks_cluster",
    description="Provision an EKS cluster using the AWS CLI",
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "region": {"type": "string"},
            "node_type": {"type": "string"},
            "node_count": {"type": "integer"},
        },
        "required": ["cluster_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_eks_cluster")
@retry(
    max_attempts=3,
    base_delay_seconds=10.0,
    retryable_on=[TransientAwsError, NetworkError],
)
def create_eks_cluster(
    cluster_name: str,
    region: str,
    node_type: str = "t3.medium",
    node_count: int = 2,
) -> dict:
    """Provisions an EKS cluster with a managed node group.

    Creates a new cluster via eksctl if it does not exist. If the cluster
    already exists but has no node groups, creates one automatically.

    Args:
        cluster_name: The EKS cluster name.
        region: The AWS region to provision in.
        node_type: EC2 instance type for worker nodes.
        node_count: Desired number of nodes.

    Returns:
        A dict with cluster_name, region, status, created, and nodegroups.

    Raises:
        TransientAwsError: On cluster creation or node group failures.
    """
    rate_limit("aws")
    eks = _client("eks", region)
    cluster_existed = False
    try:
        resp = eks.describe_cluster(name=cluster_name)
        status = resp["cluster"]["status"]
        cluster_existed = True
        if status in ("CREATING", "UPDATING"):
            # polls every 30s, up to 40 attempts (20 minutes)
            waiter = eks.get_waiter("cluster_active")
            waiter.wait(
                name=cluster_name,
                WaiterConfig={"Delay": 30, "MaxAttempts": 40},
            )
    except eks.exceptions.ResourceNotFoundException:
        cluster_existed = False

    if not cluster_existed:
        # Fresh cluster + managed node group in one eksctl call.
        try:
            result = subprocess.run(
                [
                    "eksctl", "create", "cluster",
                    "--name", cluster_name,
                    "--region", region,
                    "--node-type", node_type,
                    "--nodes", str(node_count),
                    "--managed",
                ],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
                # eksctl blocks until ACTIVE — allow up to 45 minutes;
                # node group CF stacks routinely take 30+ minutes.
                timeout=2700,
            )
        except subprocess.TimeoutExpired:
            raise TransientAwsError(
                "eksctl create cluster timed out after 2700s"
            )
        if result.returncode != 0:
            raise TransientAwsError(
                f"eksctl create cluster failed: {result.stderr[:400]}"
            )
        # Verify eksctl actually attached a nodegroup (exits 0 even on
        # "0 nodegroups created" for compatibility-check runs).
        post_ngs = eks.list_nodegroups(
            clusterName=cluster_name
        ).get("nodegroups", [])
        if not post_ngs:
            raise TransientAwsError(
                "eksctl create cluster exited 0 but has 0 nodegroups "
                f"(stdout: {result.stdout[-300:]})"
            )
        return {
            "cluster_name": cluster_name,
            "region": region,
            "status": "ACTIVE",
            "created": True,
            "nodegroup_created": True,
        }

    # Cluster already exists — verify it has at least one node group.
    # On resume, the cluster may be ACTIVE but the node group was never
    # created (eksctl returned early on a previous run).
    ngs = eks.list_nodegroups(clusterName=cluster_name).get("nodegroups", [])
    if not ngs:
        try:
            ng_result = subprocess.run(
                [
                    "eksctl", "create", "nodegroup",
                    "--cluster", cluster_name,
                    "--region", region,
                    "--name", "nexus-nodes",
                    "--node-type", node_type,
                    "--nodes", str(node_count),
                    "--nodes-min", "1",
                    "--nodes-max", str(node_count + 2),
                    "--managed",
                ],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
                # Node group CF stack can take 30+ min; allow 35 min.
                timeout=2100,
            )
        except subprocess.TimeoutExpired:
            raise TransientAwsError(
                "eksctl create nodegroup timed out after 2100s"
            )
        if ng_result.returncode != 0:
            raise TransientAwsError(
                f"eksctl create nodegroup failed: {ng_result.stderr[:400]}"
            )
        # eksctl exits 0 even when it creates 0 nodegroups (e.g. "fix
        # cluster compatibility, no tasks"). Verify the nodegroup exists.
        ngs = eks.list_nodegroups(
            clusterName=cluster_name
        ).get("nodegroups", [])
        if not ngs:
            raise TransientAwsError(
                "eksctl create nodegroup exited 0 but created 0 nodegroups "
                f"(stdout: {ng_result.stdout[-300:]})"
            )

    return {
        "cluster_name": cluster_name,
        "region": region,
        "status": "ACTIVE",
        "created": False,
        "nodegroups": ngs,
    }


@registry.register(
    name="aws.get_eks_kubeconfig",
    description="Fetch and merge kubeconfig for an EKS cluster",
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["cluster_name", "region"],
    },
)
@instrument(namespace="aws", tool="get_eks_kubeconfig")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def get_eks_kubeconfig(cluster_name: str, region: str) -> dict:
    """Fetches and merges the kubeconfig for an EKS cluster.

    Args:
        cluster_name: The EKS cluster name.
        region: The AWS region.

    Returns:
        A dict with cluster_name and kubeconfig_updated flag.

    Raises:
        TransientAwsError: If the update-kubeconfig call fails.
    """
    rate_limit("aws")
    try:
        result = subprocess.run(
            [
                "aws", "eks", "update-kubeconfig",
                "--name", cluster_name,
                "--region", region,
            ],
            capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise TransientAwsError("update-kubeconfig timed out after 60s")
    if result.returncode != 0:
        raise TransientAwsError(
            f"update-kubeconfig failed: {result.stderr[:300]}"
        )
    return {"cluster_name": cluster_name, "kubeconfig_updated": True}


@registry.register(
    name="aws.create_rds_instance",
    description="Provision a PostgreSQL RDS instance",
    input_schema={
        "type": "object",
        "properties": {
            "db_identifier": {"type": "string"},
            "region": {"type": "string"},
            "db_name": {"type": "string"},
            "master_username": {"type": "string"},
            "master_password": {"type": "string"},
        },
        "required": [
            "db_identifier", "region", "db_name",
            "master_username", "master_password",
        ],
    },
)
@instrument(namespace="aws", tool="create_rds_instance")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def create_rds_instance(
    db_identifier: str,
    region: str,
    db_name: str,
    master_username: str,
    master_password: str,
) -> dict:
    """Provisions a PostgreSQL RDS instance.

    Returns the existing instance status if the identifier already exists.

    Args:
        db_identifier: The RDS instance identifier.
        region: The AWS region.
        db_name: The database name to create.
        master_username: The master username.
        master_password: The master password.

    Returns:
        A dict with db_identifier, status, and created flag.

    Raises:
        TransientAwsError: On unexpected AWS errors.
    """
    rate_limit("aws")
    rds = _client("rds", region)
    try:
        resp = rds.create_db_instance(
            DBInstanceIdentifier=db_identifier,
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            MasterUsername=master_username,
            MasterUserPassword=master_password,
            DBName=db_name,
            AllocatedStorage=20,
            PubliclyAccessible=False,
        )
        return {
            "db_identifier": db_identifier,
            "status": resp["DBInstance"]["DBInstanceStatus"],
            "created": True,
        }
    except rds.exceptions.DBInstanceAlreadyExistsFault:
        resp = rds.describe_db_instances(DBInstanceIdentifier=db_identifier)
        return {
            "db_identifier": db_identifier,
            "status": resp["DBInstances"][0]["DBInstanceStatus"],
            "created": False,
        }
    except ClientError as e:
        raise TransientAwsError(f"create_rds_instance failed: {e}") from e


@registry.register(
    name="aws.get_rds_endpoint",
    description=(
        "Get the connection endpoint for an RDS instance "
        "(waits until available)"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "db_identifier": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["db_identifier", "region"],
    },
)
@instrument(namespace="aws", tool="get_rds_endpoint")
@retry(
    max_attempts=20,
    base_delay_seconds=30.0,
    retryable_on=[TransientAwsError],
)
def get_rds_endpoint(db_identifier: str, region: str) -> dict:
    """Gets the connection endpoint for an RDS instance.

    Polls until the instance is available; retries up to 20 times with
    30-second delays (up to 10 minutes total).

    Args:
        db_identifier: The RDS instance identifier.
        region: The AWS region.

    Returns:
        A dict with endpoint, port, and connection_string.

    Raises:
        TransientAwsError: If the instance is not yet available.
    """
    rate_limit("aws")
    rds = _client("rds", region)
    try:
        resp = rds.describe_db_instances(DBInstanceIdentifier=db_identifier)
    except ClientError as e:
        raise TransientAwsError(f"get_rds_endpoint failed: {e}") from e
    instance = resp["DBInstances"][0]
    if instance["DBInstanceStatus"] != "available":
        raise TransientAwsError(
            f"RDS not ready: {instance['DBInstanceStatus']}"
        )
    endpoint = instance["Endpoint"]["Address"]
    port = instance["Endpoint"]["Port"]
    return {
        "endpoint": endpoint,
        "port": port,
        "connection_string": f"postgresql://:{port}/{db_identifier}",
    }


@registry.register(
    name="aws.create_s3_bucket",
    description="Create an S3 bucket for static frontend assets",
    input_schema={
        "type": "object",
        "properties": {
            "bucket_name": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["bucket_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_s3_bucket")
@retry(max_attempts=3, base_delay_seconds=2.0, retryable_on=[TransientAwsError])
def create_s3_bucket(bucket_name: str, region: str) -> dict:
    """Creates an S3 bucket for static frontend assets.

    Returns the existing bucket if it already exists.

    Args:
        bucket_name: The S3 bucket name.
        region: The AWS region.

    Returns:
        A dict with bucket_name, region, and created flag.

    Raises:
        TransientAwsError: On unexpected AWS errors.
    """
    rate_limit("aws")
    s3 = _client("s3", region)
    kwargs: dict = {"Bucket": bucket_name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {
            "LocationConstraint": region
        }
    try:
        s3.create_bucket(**kwargs)
        return {
            "bucket_name": bucket_name,
            "region": region,
            "created": True,
        }
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            return {
                "bucket_name": bucket_name,
                "region": region,
                "created": False,
            }
        raise TransientAwsError(f"create_s3_bucket failed: {e}") from e


@registry.register(
    name="aws.create_cloudfront_dist",
    description="Create a CloudFront distribution pointing to an S3 bucket",
    input_schema={
        "type": "object",
        "properties": {
            "bucket_name": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["bucket_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_cloudfront_dist")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def create_cloudfront_dist(bucket_name: str, region: str) -> dict:
    """Creates a CloudFront distribution pointing to an S3 bucket.

    Returns the existing distribution if one was previously created for
    this bucket (matched by CallerReference).

    Args:
        bucket_name: The S3 bucket to use as the origin.
        region: Unused; CloudFront is always provisioned in us-east-1.

    Returns:
        A dict with distribution_id, domain_name, and created flag.

    Raises:
        TransientAwsError: On unexpected AWS errors.
    """
    rate_limit("aws")
    cf = _client("cloudfront", "us-east-1")  # CloudFront is always us-east-1
    try:
        resp = cf.create_distribution(
            DistributionConfig={
                # idempotency key — same bucket = same dist
                "CallerReference": bucket_name,
                "Origins": {
                    "Quantity": 1,
                    "Items": [{
                        "Id": bucket_name,
                        "DomainName": (
                            f"{bucket_name}.s3.amazonaws.com"
                        ),
                        "S3OriginConfig": {"OriginAccessIdentity": ""},
                    }],
                },
                "DefaultCacheBehavior": {
                    "TargetOriginId": bucket_name,
                    "ViewerProtocolPolicy": "redirect-to-https",
                    "ForwardedValues": {
                        "QueryString": False,
                        "Cookies": {"Forward": "none"},
                    },
                    "MinTTL": 0,
                },
                "Comment": f"Nexus CDN for {bucket_name}",
                "Enabled": True,
            }
        )
        return {
            "distribution_id": resp["Distribution"]["Id"],
            "domain_name": resp["Distribution"]["DomainName"],
            "created": True,
        }
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "DistributionAlreadyExists":
            # CallerReference collision — list to find the existing dist.
            dists = cf.list_distributions()
            items = dists.get("DistributionList", {}).get("Items", [])
            for d in items:
                for origin in d.get("Origins", {}).get("Items", []):
                    if origin["Id"] == bucket_name:
                        return {
                            "distribution_id": d["Id"],
                            "domain_name": d["DomainName"],
                            "created": False,
                        }
        raise TransientAwsError(
            f"create_cloudfront_dist failed: {e}"
        ) from e


@registry.register(
    name="aws.get_cost_estimate",
    description="Query AWS Cost Explorer for current month cost breakdown",
    input_schema={
        "type": "object",
        "properties": {"region": {"type": "string"}},
        "required": ["region"],
    },
)
@instrument(namespace="aws", tool="get_cost_estimate")
def get_cost_estimate(region: str) -> dict:
    """Queries AWS Cost Explorer for the current month's cost.

    Args:
        region: Unused; Cost Explorer is always queried in us-east-1.

    Returns:
        A dict with total_usd, period_start, and period_end.
    """
    rate_limit("aws")
    ce = boto3.client("ce", region_name="us-east-1")
    end = date.today().isoformat()
    start = date.today().replace(day=1).isoformat()
    try:
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Metrics=["BlendedCost"],
        )
        total = float(
            resp["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"]
        )
    except Exception:
        total = 0.0
    return {
        "total_usd": round(total, 2),
        "period_start": start,
        "period_end": end,
    }


@registry.register(
    name="aws.get_cloudwatch_metrics",
    description=(
        "Pull CloudWatch metrics (CPU, memory, error count) for a service"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "service_name": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["cluster_name", "service_name", "region"],
    },
)
@instrument(namespace="aws", tool="get_cloudwatch_metrics")
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def get_cloudwatch_metrics(
    cluster_name: str, service_name: str, region: str
) -> dict:
    """Pulls CPU utilization metrics from CloudWatch ContainerInsights.

    Args:
        cluster_name: The EKS cluster name.
        service_name: The Kubernetes service name.
        region: The AWS region.

    Returns:
        A dict with cluster_name, service_name, and datapoints list.

    Raises:
        TransientAwsError: On CloudWatch API errors.
    """
    rate_limit("aws")
    cw = _client("cloudwatch", region)
    end = datetime.utcnow()
    start = end - timedelta(hours=1)
    try:
        resp = cw.get_metric_statistics(
            Namespace="ContainerInsights",
            MetricName="pod_cpu_utilization",
            Dimensions=[
                {"Name": "ClusterName", "Value": cluster_name},
                {"Name": "ServiceName", "Value": service_name},
            ],
            StartTime=start,
            EndTime=end,
            Period=300,
            Statistics=["Average"],
        )
    except ClientError as e:
        raise TransientAwsError(
            f"get_cloudwatch_metrics failed: {e}"
        ) from e
    datapoints = [
        {
            "timestamp": dp["Timestamp"].isoformat(),
            "cpu_percent": round(dp["Average"], 2),
        }
        for dp in resp.get("Datapoints", [])
    ]
    return {
        "cluster_name": cluster_name,
        "service_name": service_name,
        "datapoints": datapoints,
    }


@registry.register(
    name="aws.create_iam_role",
    description="Create an IAM role for EKS service account (IRSA)",
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["role_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_iam_role")
@retry(max_attempts=3, base_delay_seconds=3.0, retryable_on=[TransientAwsError])
def create_iam_role(role_name: str, region: str) -> dict:
    """Creates an IAM role for EKS service accounts (IRSA).

    Returns the existing role ARN if the role already exists.

    Args:
        role_name: The IAM role name.
        region: Unused; IAM is a global service.

    Returns:
        A dict with role_arn, role_name, and created flag.

    Raises:
        TransientAwsError: On unexpected AWS errors.
    """
    rate_limit("aws")
    iam = _client("iam", region)
    trust = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "eks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    })
    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust,
        )
        return {
            "role_arn": resp["Role"]["Arn"],
            "role_name": role_name,
            "created": True,
        }
    except iam.exceptions.EntityAlreadyExistsException:
        resp = iam.get_role(RoleName=role_name)
        return {
            "role_arn": resp["Role"]["Arn"],
            "role_name": role_name,
            "created": False,
        }
    except ClientError as e:
        raise TransientAwsError(f"create_iam_role failed: {e}") from e
