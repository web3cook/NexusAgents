from __future__ import annotations
import json
import subprocess
from datetime import date, datetime, timedelta
import boto3
from botocore.exceptions import ClientError
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
    except ClientError as e:
        raise TransientAwsError(f"create_ecr_repo failed: {e}") from e
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
@retry(max_attempts=3, base_delay_seconds=10.0, retryable_on=[TransientAwsError, NetworkError])
def create_eks_cluster(cluster_name: str, region: str, node_type: str = "t3.medium", node_count: int = 2) -> dict:
    rate_limit("aws")
    eks = _client("eks", region)
    try:
        resp = eks.describe_cluster(name=cluster_name)
        status = resp["cluster"]["status"]
        if status == "ACTIVE":
            return {"cluster_name": cluster_name, "region": region, "status": "ACTIVE", "created": False}
        if status in ("CREATING", "UPDATING"):
            # polls every 30s, up to 40 attempts (20 minutes)
            waiter = eks.get_waiter("cluster_active")
            waiter.wait(name=cluster_name, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
            return {"cluster_name": cluster_name, "region": region, "status": "ACTIVE", "created": False}
    except eks.exceptions.ResourceNotFoundException:
        pass  # cluster doesn't exist — create it

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
            timeout=1500,  # eksctl blocks until ACTIVE — allow up to 25 minutes
        )
    except subprocess.TimeoutExpired:
        raise TransientAwsError("eksctl timed out after 1500s")
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
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def get_eks_kubeconfig(cluster_name: str, region: str) -> dict:
    rate_limit("aws")
    try:
        result = subprocess.run(
            ["aws", "eks", "update-kubeconfig", "--name", cluster_name, "--region", region],
            capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise TransientAwsError("update-kubeconfig timed out after 60s")
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
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def create_rds_instance(db_identifier: str, region: str, db_name: str, master_username: str, master_password: str) -> dict:
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
        return {"db_identifier": db_identifier, "status": resp["DBInstance"]["DBInstanceStatus"], "created": True}
    except rds.exceptions.DBInstanceAlreadyExistsFault:
        resp = rds.describe_db_instances(DBInstanceIdentifier=db_identifier)
        return {"db_identifier": db_identifier, "status": resp["DBInstances"][0]["DBInstanceStatus"], "created": False}
    except ClientError as e:
        raise TransientAwsError(f"create_rds_instance failed: {e}") from e


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
@retry(max_attempts=20, base_delay_seconds=30.0, retryable_on=[TransientAwsError])
def get_rds_endpoint(db_identifier: str, region: str) -> dict:
    rate_limit("aws")
    rds = _client("rds", region)
    try:
        resp = rds.describe_db_instances(DBInstanceIdentifier=db_identifier)
    except ClientError as e:
        raise TransientAwsError(f"get_rds_endpoint failed: {e}") from e
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
@retry(max_attempts=3, base_delay_seconds=2.0, retryable_on=[TransientAwsError])
def create_s3_bucket(bucket_name: str, region: str) -> dict:
    rate_limit("aws")
    s3 = _client("s3", region)
    kwargs: dict = {"Bucket": bucket_name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
        return {"bucket_name": bucket_name, "region": region, "created": True}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            return {"bucket_name": bucket_name, "region": region, "created": False}
        raise TransientAwsError(f"create_s3_bucket failed: {e}") from e


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
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def create_cloudfront_dist(bucket_name: str, region: str) -> dict:
    rate_limit("aws")
    cf = _client("cloudfront", "us-east-1")  # CloudFront is always us-east-1
    try:
        resp = cf.create_distribution(DistributionConfig={
            "CallerReference": bucket_name,  # idempotency key — same bucket = same dist
            "Origins": {"Quantity": 1, "Items": [{"Id": bucket_name, "DomainName": f"{bucket_name}.s3.amazonaws.com", "S3OriginConfig": {"OriginAccessIdentity": ""}}]},
            "DefaultCacheBehavior": {"TargetOriginId": bucket_name, "ViewerProtocolPolicy": "redirect-to-https", "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}}, "MinTTL": 0},
            "Comment": f"Nexus CDN for {bucket_name}",
            "Enabled": True,
        })
        return {"distribution_id": resp["Distribution"]["Id"], "domain_name": resp["Distribution"]["DomainName"], "created": True}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "DistributionAlreadyExists":
            # CallerReference collision means the same dist was already created — list to find it
            dists = cf.list_distributions()
            items = dists.get("DistributionList", {}).get("Items", [])
            for d in items:
                for origin in d.get("Origins", {}).get("Items", []):
                    if origin["Id"] == bucket_name:
                        return {"distribution_id": d["Id"], "domain_name": d["DomainName"], "created": False}
        raise TransientAwsError(f"create_cloudfront_dist failed: {e}") from e


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
@retry(max_attempts=3, base_delay_seconds=5.0, retryable_on=[TransientAwsError])
def get_cloudwatch_metrics(cluster_name: str, service_name: str, region: str) -> dict:
    rate_limit("aws")
    cw = _client("cloudwatch", region)
    end = datetime.utcnow()
    start = end - timedelta(hours=1)
    try:
        resp = cw.get_metric_statistics(
            Namespace="ContainerInsights",
            MetricName="pod_cpu_utilization",
            Dimensions=[{"Name": "ClusterName", "Value": cluster_name}, {"Name": "ServiceName", "Value": service_name}],
            StartTime=start, EndTime=end, Period=300, Statistics=["Average"],
        )
    except ClientError as e:
        raise TransientAwsError(f"get_cloudwatch_metrics failed: {e}") from e
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
@retry(max_attempts=3, base_delay_seconds=3.0, retryable_on=[TransientAwsError])
def create_iam_role(role_name: str, region: str) -> dict:
    rate_limit("aws")
    iam = _client("iam", region)
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    try:
        resp = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust)
        return {"role_arn": resp["Role"]["Arn"], "role_name": role_name, "created": True}
    except iam.exceptions.EntityAlreadyExistsException:
        resp = iam.get_role(RoleName=role_name)
        return {"role_arn": resp["Role"]["Arn"], "role_name": role_name, "created": False}
    except ClientError as e:
        raise TransientAwsError(f"create_iam_role failed: {e}") from e
