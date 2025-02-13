import hashlib
import os

from aws_cdk import SecretValue, Stack, Tags
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as codepipeline_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct

path = os.path.dirname(__file__)


def user_data_generation(ecr_repository_uri: str, region: str) -> str:
    """Generates user data for EC2 instances."""
    with open(
        os.path.join(path, "../scripts/docker/docker-compose.yaml"),
        "r",
        encoding="utf-8",
    ) as f:
        docker_compose_text = f.read().replace(
            "${ECR_REPOSITORY_URI}", ecr_repository_uri
        )

    with open(
        os.path.join(path, "../scripts/user-data/user-data.sh"),
        "r",
        encoding="utf-8",
    ) as f:
        user_data_text = f.read()

    return (
        user_data_text.replace("${ECR_REPOSITORY_URI}", ecr_repository_uri)
        .replace("${DOCKER_COMPOSE_CONTENT}", docker_compose_text)
        .replace("${REGION}", region)
    )


class NginxInfraStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, ecr_repo_uri: str, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc(self, "NginxVPC", restrict_default_security_group=False)

        user_data, hash_value = self.create_user_data(ecr_repo_uri)

        security_group = ec2.SecurityGroup(
            self,
            "NginxSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
        )
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow inbound HTTP traffic",
        )
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow inbound HTTPS traffic",
        )

        self.instance = ec2.Instance(
            self,
            "NginxInstance",
            instance_type=ec2.InstanceType("t3.nano"),
            machine_image=ec2.MachineImage.latest_amazon_linux(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023
            ),
            vpc=vpc,
            user_data=user_data,
            ssm_session_permissions=True,
            allow_all_outbound=True,
            user_data_causes_replacement=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            role=iam.Role(
                self,
                "NginxInstanceRole",
                assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                inline_policies={
                    "ECRPolicy": iam.PolicyDocument(
                        statements=[
                            iam.PolicyStatement(
                                actions=[
                                    "ecr:GetAuthorizationToken",
                                    "ecr:BatchCheckLayerAvailability",
                                    "ecr:GetDownloadUrlForLayer",
                                    "ecr:BatchGetImage",
                                ],
                                resources=["*"],
                                effect=iam.Effect.ALLOW,
                            )
                        ]
                    )
                },
            ),
            security_group=security_group,
        )
        Tags.of(self.instance).add("id", str(hash_value))
        ssm.StringParameter(
            self,
            "NginxInstanceParameter",
            parameter_name="/nginx/instance-id",
            string_value=self.instance.instance_id,
        )

        ec2.CfnEIP(self, "dev-EIP", instance_id=self.instance.instance_id)

    def create_user_data(self, ecr_repo_uri: str):
        user_data = ec2.UserData.custom(
            user_data_generation(ecr_repo_uri, self.region)
        )  # noqa

        return user_data, hash(user_data)


class TestAwsStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.ecr_repo = ecr.Repository(self, "NginxRepo")

        infra_stack = NginxInfraStack(
            self, f"{env}-NginxInfraStack", self.ecr_repo.repository_uri
        )
        pipeline_stack = NginxPipelineStack(
            self,
            f"{env}-NginxPipelineStack",
            env=env,
            github_repo="test-nginx",
            github_owner="mingocfree",
            github_branch="dev",
            ecr_repo=self.ecr_repo,
        )
        pipeline_stack.add_dependency(infra_stack)


class NginxPipelineStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        env,
        github_repo: str,
        github_owner: str,
        github_branch: str,
        ecr_repo: ecr.Repository,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)

        pipeline = codepipeline.Pipeline(
            self, "NginxPipeline", pipeline_name=f"{env}-nginx-pipeline"  # noqa
        )

        source_output = codepipeline.Artifact()
        source_action = codepipeline_actions.GitHubSourceAction(
            action_name="GitHub_Source",
            owner=github_owner,
            repo=github_repo,
            branch=github_branch,
            oauth_token=SecretValue.secrets_manager("github-token-infra"),
            output=source_output,
        )

        build_project = codebuild.PipelineProject(
            self,
            "FastApiBuildProject",
            environment=codebuild.BuildEnvironment(
                privileged=True,
                build_image=codebuild.LinuxBuildImage.STANDARD_5_0,  # noqa
                environment_variables={
                    "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(
                        value=self.region
                    ),
                    "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(
                        value=ecr_repo.repository_uri
                    ),
                },
            ),
            build_spec=codebuild.BuildSpec.from_asset(
                os.path.join(
                    path, "../scripts/buildspec/nginx-pipeline/deploy.yaml"
                )  # noqa
            ),
            role=iam.Role(
                self,
                "CodeBuildRole",
                assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "AmazonEC2ContainerRegistryPowerUser"
                    ),
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "AmazonSSMFullAccess"
                    ),
                ],
            ),
        )

        build_output = codepipeline.Artifact()
        build_action = codepipeline_actions.CodeBuildAction(
            action_name="Build",
            project=build_project,
            input=source_output,
            outputs=[build_output],
        )

        # Deploy Stage
        deploy_project = codebuild.PipelineProject(
            self,
            "DeployProject",
            build_spec=codebuild.BuildSpec.from_asset(
                os.path.join(path, "../scripts/buildspec/buildspec.yaml")
            ),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_6_0,
                compute_type=codebuild.ComputeType.SMALL,
                environment_variables={
                    "AWS_REGION": codebuild.BuildEnvironmentVariable(
                        value=self.region
                    ),  # noqa
                    "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(
                        value=ecr_repo.repository_uri
                    ),
                },
            ),
            role=iam.Role(
                self,
                "DeployRole",
                assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "AmazonSSMFullAccess"
                    ),
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "AmazonEC2ReadOnlyAccess"
                    ),
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "AutoScalingReadOnlyAccess"
                    ),
                ],
            ),
        )

        deploy_action = codepipeline_actions.CodeBuildAction(
            action_name="Deploy",
            project=deploy_project,
            input=build_output,
        )

        pipeline.add_stage(stage_name="Source", actions=[source_action])
        pipeline.add_stage(stage_name="Build", actions=[build_action])
        pipeline.add_stage(stage_name="Deploy", actions=[deploy_action])

        Tags.of(pipeline).add(
            "id",
            hashlib.md5(
                open(
                    os.path.join(
                        path, "../scripts/buildspec/nginx-pipeline/deploy.yaml"
                    ),  # noqa
                    "rb",  # noqa
                ).read(),
                usedforsecurity=False,
            ).hexdigest(),
        )
