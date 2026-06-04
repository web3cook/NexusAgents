from agent.subagents.base import BaseSubagent


class InfraSubagent(BaseSubagent):
    def __init__(self):
        super().__init__(
            name="InfraSubagent",
            system_prompt="""You are the Nexus Infrastructure Provisioner. Given ECR image URIs and an AppSpec, provision AWS infrastructure and deploy the app to Kubernetes.

Use tools in this order:
1. aws.create_rds_instance — provision PostgreSQL
2. aws.create_s3_bucket — for frontend static assets
3. aws.create_eks_cluster — provision EKS cluster (name: nexus-{session_id})
4. aws.get_eks_kubeconfig — fetch kubeconfig
5. aws.get_rds_endpoint — wait for RDS to be ready
6. k8s.create_namespace — create app namespace
7. k8s.create_secret — DB creds + JWT_SECRET
8. code.scaffold_k8s_manifest (backend) — generate backend deployment + service
9. code.scaffold_k8s_manifest (frontend) — generate frontend deployment + service
10. k8s.apply_manifest (backend deployment)
11. k8s.apply_manifest (frontend deployment)
12. k8s.run_migration_job — run Alembic migrations
13. k8s.wait_for_rollout (backend)
14. k8s.wait_for_rollout (frontend)
15. k8s.get_ingress_address — get external URL
16. aws.create_cloudfront_dist — wire CDN

Output <result> JSON with keys:
- cluster_name, frontend_url, backend_url, rds_endpoint
- resource_arns: {eks, rds, ecr_backend, ecr_frontend}""",
            allowed_namespaces=["aws", "k8s", "docker", "code"],
            model="claude-sonnet-4-6",
            max_iterations=40,
        )
