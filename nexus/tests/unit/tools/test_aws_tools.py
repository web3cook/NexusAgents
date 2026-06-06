import pytest
import boto3
from moto import mock_aws
from unittest.mock import patch, MagicMock
from agent.tools.aws.tools import (
    create_ecr_repo, create_s3_bucket, get_cost_estimate, create_iam_role
)


@mock_aws
def test_create_ecr_repo():
    result = create_ecr_repo(repo_name="nexus-backend", region="us-east-1")
    assert "repository_uri" in result
    assert "nexus-backend" in result["repository_uri"]


@mock_aws
def test_create_s3_bucket():
    result = create_s3_bucket(bucket_name="nexus-test-frontend-123", region="us-east-1")
    assert result["bucket_name"] == "nexus-test-frontend-123"
    assert result["created"] is True


def test_get_cost_estimate_returns_breakdown():
    with patch("agent.tools.aws.tools.boto3.client") as mock_client:
        mock_ce = MagicMock()
        mock_ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Groups": [], "Total": {"BlendedCost": {"Amount": "47.20"}}}]
        }
        mock_client.return_value = mock_ce
        result = get_cost_estimate(region="us-east-1")
    assert "total_usd" in result


@mock_aws
def test_create_iam_role():
    result = create_iam_role(role_name="nexus-eks-role", region="us-east-1")
    assert "role_arn" in result
