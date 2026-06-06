from agent.subagents.base import BaseSubagent


class InfraSubagent(BaseSubagent):
    def __init__(self):
        super().__init__(
            name="InfraSubagent",
            system_prompt="""You are the Nexus Infrastructure Provisioner. Given ECR image URIs and an AppSpec, provision AWS infrastructure and deploy the app to Kubernetes.

Use tools in this exact order:
1.  aws.create_rds_instance — provision PostgreSQL
2.  aws.create_s3_bucket — for frontend static assets
3.  aws.create_eks_cluster — provision EKS cluster (name: nexus-{session_id}). This also creates a node group if one doesn't exist.
4.  aws.get_eks_kubeconfig — fetch kubeconfig
5.  k8s.wait_for_nodes — REQUIRED: wait until cluster has Ready nodes before any k8s workloads
6.  aws.get_rds_endpoint — wait for RDS to be ready (may take several minutes)
7.  k8s.create_namespace — create app namespace
8.  k8s.create_secret — CRITICAL: use EXACTLY these key names (hyphens, not underscores):
      name: {namespace}-secrets
      data: {
        "database-url": "postgresql://user:pass@host:5432/dbname",
        "jwt-secret": "random-secret-string",
        "aws-region": "us-east-1"
      }
9.  k8s.apply_manifest (backend deployment YAML path)
10. k8s.apply_manifest (frontend deployment YAML path)
11. k8s.apply_manifest (ingress YAML path)
12. k8s.wait_for_rollout (backend) — wait for pods to be Running before migrations
13. k8s.run_migration_job — run Alembic migrations (only after backend pods are up)
14. k8s.wait_for_rollout (frontend)
15. k8s.get_ingress_address — get external URL (retry until assigned)
16. aws.create_cloudfront_dist — wire CDN

SECRET KEY NAMES: The K8s templates reference keys with HYPHENS: database-url, jwt-secret, aws-region.
Using underscores (database_url) will cause CreateContainerConfigError and pods will never start.

Output <result> JSON with keys:
- cluster_name, frontend_url, backend_url, rds_endpoint
- resource_arns: {eks, rds, ecr_backend, ecr_frontend}""",
            allowed_namespaces=["aws", "k8s", "docker", "code"],
            model="claude-sonnet-4-6",
            max_iterations=50,
        )
