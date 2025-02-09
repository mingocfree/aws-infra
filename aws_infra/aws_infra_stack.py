from aws_cdk import Duration, RemovalPolicy, SecretValue, Stack
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as pipeline_actions
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from constructs import Construct

from aws_infra.autoscaling_group_stack import AutoScalingGroupStack


class AwsInfraStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, config: dict, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env = config.get("env", "dev")
        github_repository = config.get("repository", {})

        branch = env
        if env == "production":
            branch = "main"

        repository = ecr.Repository(
            self,
            f"{env}-Repository",
            repository_name=github_repository.get("name", "aws-java"),
            removal_policy=RemovalPolicy.DESTROY,
            image_scan_on_push=True,
        )

        build_project = codebuild.PipelineProject(
            self,
            f"{env}-BuildProject",
            environment=codebuild.BuildEnvironment(
                privileged=True,
                build_image=codebuild.LinuxBuildImage.STANDARD_5_0,
            ),
            environment_variables={
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(
                    value=self.region
                ),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(
                    value=self.account
                ),
                "ECR_REPOSITORY_NAME": codebuild.BuildEnvironmentVariable(
                    value=repository.repository_name
                ),
            },
            timeout=Duration.minutes(30),
        )

        auto_scaling_group_stack = AutoScalingGroupStack(
            self,
            f"{env}-AutoScalingGroupStack",
            config=config,
            ecr_repository_uri=repository.repository_uri,
            env={"region": self.region, "account": self.account},
        )

        source_output = codepipeline.Artifact()
        build_output = codepipeline.Artifact()

        pipeline = codepipeline.Pipeline(
            self,
            f"{env}-Pipeline",
            pipeline_name=f"{env}-aws-java-pipeline",
        )

        source_action = pipeline_actions.GitHubSourceAction(
            action_name=f"{env}-Source",
            owner=github_repository.get("owner", "mingocfree"),
            repo=github_repository.get("name", "aws-java"),
            branch=branch,
            oauth_token=SecretValue.secrets_manager("github-token"),
            output=source_output,
        )
        pipeline.add_stage(stage_name=f"{env}-Source", actions=[source_action])

        build_action = pipeline_actions.CodeBuildAction(
            action_name="BuildAndPush",
            project=build_project,
            input=source_output,
            outputs=[build_output],
        )
        pipeline.add_stage(stage_name="Build", actions=[build_action])

        ecs_application = codedeploy.EcsApplication(
            self,
            f"{env}-EcsApplication",
            application_name=f"{env}-EcsApplication",  # noqa
        )
        codedeploy_role = iam.Role(
            self,
            "CodeDeployRole",
            assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSCodeDeployRole"
                )  # noqa
            ],
        )
        deployment_group = codedeploy.EcsDeploymentGroup(
            self,
            f"{env}-DeploymentGroup",
            application=ecs_application,
            deployment_group_name=f"{env}-BlueGreenDeployment",
            service=ecs.Ec2Service,
            auto_scaling_groups=[auto_scaling_group_stack.asg],
            role=codedeploy_role,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(  # noqa
                test_listener=auto_scaling_group_stack.listener_green,
                listener=auto_scaling_group_stack.listener,
                blue_target_group=auto_scaling_group_stack.target_group_blue,
                green_target_group=auto_scaling_group_stack.target_group_green,
                termination_wait_time=Duration.minutes(5),
            ),
            deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10PERCENT_5MINUTES,  # noqa
            auto_rollback=codedeploy.AutoRollbackConfig(
                stopped_deployment=True
            ),  # noqa
        )
        repository.grant_pull(auto_scaling_group_stack.asg)
        repository.grant_pull(build_project)
        codedeploy_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AWSCodeDeployFullAccess"
            )  # noqa
        )

        deploy_action = pipeline_actions.CodeDeployEcsDeployAction(
            action_name="Deploy",
            deployment_group=deployment_group,
            input=build_output,
        )
        pipeline.add_stage(stage_name="Deploy", actions=[deploy_action])
