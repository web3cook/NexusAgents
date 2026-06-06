from __future__ import annotations
import json
import subprocess
from datetime import date, datetime, timedelta
import boto3
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import retry, rate_limit
from agent.core.errors import TransientAwsError, NetworkError


def _client(service: str, region: str):
    return boto3.client(service, region_name=region)


@registry.register(
    name="aws.create_ecr_repo",
    description="Create an ECR repository for storing Docker images",
    input_schema={
        "type": "object",
        "properties": {"repo_name": {"type": "string"}, "region": {"type": "string"}},
        "required": ["repo_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_ecr_repo")
@retry(max_attempts=3, base_delay_seconds=2.0, retryable_on=[TransientAwsError])
def create_ecr_repo(repo_name: str, region: str) -> dict:
    rate_limit("aws")
    ecr = _client("ecr", region)
    try:
        resp = ecr.create_repository(repositoryName=repo_name)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        resp = ecr.describe_repositories(repositoryNames=[repo_name])
        return {"repository_uri": resp["repositories"][0]["repositoryUri"], "created": False}
    return {"repository_uri": resp["repository"]["repositoryUri"], "created": True}


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
@retry(max_attempts=2, base_delay_seconds=10.0, retryable_on=[TransientAwsError, NetworkError])
def create_eks_cluster(cluster_name: str, region: str, node_type: str = "t3.medium", node_count: int = 2) -> dict:
    rate_limit("aws")
    # Check if the cluster already exists and is active — skip creation if so
    eks = _client("eks", region)
    try:
        resp = eks.describe_cluster(name=cluster_name)
        status = resp["cluster"]["status"]
        if status == "ACTIVE":
            return {"cluster_name": cluster_name, "region": region, "status": "ACTIVE", "created": False}
        if status in ("CREATING", "UPDATING"):
            # Wait for it to become active using the boto3 waiter (polls every 30s, up to 40 attempts)
            waiter = eks.get_waiter("cluster_active")
            waiter.wait(name=cluster_name, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
            return {"cluster_name": cluster_name, "region": region, "status": "ACTIVE", "created": False}
    except eks.exceptions.ResourceNotFoundException:
        pass  # cluster doesn't exist yet — create it

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
        timeout=1500,  # eksctl blocks until ACTIVE — allow up to 25 minutes
    )
    if result.returncode != 0:
        raise TransientAwsError(f"eksctl failed: {result.stderr[:400]}")
    return {"cluster_name": cluster_name, "region": region, "status": "ACTIVE", "created": True}


@registry.register(
    name="aws.get_eks_kubeconfig",
    description="Fetch and merge kubeconfig for an EKS cluster",
    input_schema={
        "type": "object",
        "properties": {"cluster_name": {"type": "string"}, "region": {"type": "string"}},
        "required": ["cluster_name", "region"],
    },
)
@instrument(namespace="aws", tool="get_eks_kubeconfig")
def get_eks_kubeconfig(cluster_name: str, region: str) -> dict:
    rate_limit("aws")
    result = subprocess.run(
        ["aws", "eks", "update-kubeconfig", "--name", cluster_name, "--region", region],
        capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=60,
    )
    if result.returncode != 0:
        raise TransientAwsError(f"update-kubeconfig failed: {result.stderr[:300]}")
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
        "required": ["db_identifier", "region", "db_name", "master_username", "master_password"],
    },
)
@instrument(namespace="aws", tool="create_rds_instance")
@retry(max_attempts=2, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def create_rds_instance(db_identifier: str, region: str, db_name: str, master_username: str, master_password: str) -> dict:
    rate_limit("aws")
    rds = _client("rds", region)
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
    return {"db_identifier": db_identifier, "status": resp["DBInstance"]["DBInstanceStatus"]}


@registry.register(
    name="aws.get_rds_endpoint",
    description="Get the connection endpoint for an RDS instance (waits until available)",
    input_schema={
        "type": "object",
        "properties": {"db_identifier": {"type": "string"}, "region": {"type": "string"}},
        "required": ["db_identifier", "region"],
    },
)
@instrument(namespace="aws", tool="get_rds_endpoint")
@retry(max_attempts=10, base_delay_seconds=30.0, retryable_on=[TransientAwsError])
def get_rds_endpoint(db_identifier: str, region: str) -> dict:
    rate_limit("aws")
    rds = _client("rds", region)
    resp = rds.describe_db_instances(DBInstanceIdentifier=db_identifier)
    instance = resp["DBInstances"][0]
    if instance["DBInstanceStatus"] != "available":
        raise TransientAwsError(f"RDS not ready: {instance['DBInstanceStatus']}")
    endpoint = instance["Endpoint"]["Address"]
    port = instance["Endpoint"]["Port"]
    return {"endpoint": endpoint, "port": port, "connection_string": f"postgresql://:{port}/{db_identifier}"}


@registry.register(
    name="aws.create_s3_bucket",
    description="Create an S3 bucket for static frontend assets",
    input_schema={
        "type": "object",
        "properties": {"bucket_name": {"type": "string"}, "region": {"type": "string"}},
        "required": ["bucket_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_s3_bucket")
def create_s3_bucket(bucket_name: str, region: str) -> dict:
    rate_limit("aws")
    s3 = _client("s3", region)
    kwargs: dict = {"Bucket": bucket_name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)
    return {"bucket_name": bucket_name, "region": region, "created": True}


@registry.register(
    name="aws.create_cloudfront_dist",
    description="Create a CloudFront distribution pointing to an S3 bucket",
    input_schema={
        "type": "object",
        "properties": {"bucket_name": {"type": "string"}, "region": {"type": "string"}},
        "required": ["bucket_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_cloudfront_dist")
def create_cloudfront_dist(bucket_name: str, region: str) -> dict:
    rate_limit("aws")
    cf = _client("cloudfront", region)
    resp = cf.create_distribution(DistributionConfig={
        "CallerReference": bucket_name,
        "Origins": {"Quantity": 1, "Items": [{"Id": bucket_name, "DomainName": f"{bucket_name}.s3.amazonaws.com", "S3OriginConfig": {"OriginAccessIdentity": ""}}]},
        "DefaultCacheBehavior": {"TargetOriginId": bucket_name, "ViewerProtocolPolicy": "redirect-to-https", "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}}, "MinTTL": 0},
        "Comment": f"Nexus CDN for {bucket_name}",
        "Enabled": True,
    })
    return {"distribution_id": resp["Distribution"]["Id"], "domain_name": resp["Distribution"]["DomainName"]}


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
    rate_limit("aws")
    ce = boto3.client("ce", region_name="us-east-1")
    end = date.today().isoformat()
    start = date.today().replace(day=1).isoformat()
    try:
        resp = ce.get_cost_and_usage(TimePeriod={"Start": start, "End": end}, Granularity="MONTHLY", Metrics=["BlendedCost"])
        total = float(resp["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"])
    except Exception:
        total = 0.0
    return {"total_usd": round(total, 2), "period_start": start, "period_end": end}


@registry.register(
    name="aws.get_cloudwatch_metrics",
    description="Pull CloudWatch metrics (CPU, memory, error count) for a service",
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
def get_cloudwatch_metrics(cluster_name: str, service_name: str, region: str) -> dict:
    rate_limit("aws")
    cw = _client("cloudwatch", region)
    end = datetime.utcnow()
    start = end - timedelta(hours=1)
    resp = cw.get_metric_statistics(
        Namespace="ContainerInsights",
        MetricName="pod_cpu_utilization",
        Dimensions=[{"Name": "ClusterName", "Value": cluster_name}, {"Name": "ServiceName", "Value": service_name}],
        StartTime=start, EndTime=end, Period=300, Statistics=["Average"],
    )
    datapoints = [{"timestamp": dp["Timestamp"].isoformat(), "cpu_percent": round(dp["Average"], 2)} for dp in resp.get("Datapoints", [])]
    return {"cluster_name": cluster_name, "service_name": service_name, "datapoints": datapoints}


@registry.register(
    name="aws.create_iam_role",
    description="Create an IAM role for EKS service account (IRSA)",
    input_schema={
        "type": "object",
        "properties": {"role_name": {"type": "string"}, "region": {"type": "string"}},
        "required": ["role_name", "region"],
    },
)
@instrument(namespace="aws", tool="create_iam_role")
def create_iam_role(role_name: str, region: str) -> dict:
    rate_limit("aws")
    iam = _client("iam", region)
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    resp = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust)
    return {"role_arn": resp["Role"]["Arn"], "role_name": role_name}
