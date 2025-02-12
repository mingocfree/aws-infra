import os

from aws_cdk import CfnOutput, Duration, Fn, SecretValue, Stack, Stage, Tags
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as codepipeline_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from aws_cdk import pipelines
from constructs import Construct


def user_data_generation(ecr_repository_uri: str, region: str) -> str:
    """Generates user data for EC2 instances."""
    path = os.path.dirname(__file__)

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

        vpc = ec2.Vpc(self, "NginxVPC")

        user_data, hash_value = self.create_user_data(ecr_repo_uri)

        self.asg = autoscaling.AutoScalingGroup(
            self,
            "NginxASG",
            vpc=vpc,
            instance_type=ec2.InstanceType("t3.nano"),
            machine_image=ec2.AmazonLinuxImage(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2
            ),
            user_data=user_data,
            min_capacity=1,
            max_capacity=4,
            ssm_session_permissions=True,
            update_policy=autoscaling.UpdatePolicy.replacing_update(),
            role=iam.Role(
                self,
                "NginxASGRole",
                assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "AmazonEC2ContainerRegistryFullAccess"
                    )
                ],
            ),
        )
        Tags.of(self.asg).add("id", str(hash_value))

        ssm.StringParameter(
            self,
            "NginxASGNameParam",
            parameter_name="/nginx/asg-name",
            string_value=self.asg.auto_scaling_group_name,
        )

        lb = elbv2.ApplicationLoadBalancer(
            self, "NginxLB", vpc=vpc, internet_facing=True
        )
        listener = lb.add_listener("Listener", port=80, open=True)
        listener.add_targets(
            "NginxTarget",
            port=80,
            targets=[self.asg],
            health_check=elbv2.HealthCheck(
                path="/", interval=Duration.seconds(60)
            ),  # noqa
        )

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
        config: dict,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.ecr_repo = ecr.Repository(self, "FastApiEcrRepo")

        CfnOutput(
            self,
            "EcrRepoUriOutput",
            value=self.ecr_repo.repository_uri,
            export_name="FastApiEcrRepoUri",
        )

        NginxPipelineStack(
            self,
            f"{env}-FastApiPipelineStack",
            env=env,
            github_repo="test-nginx",
            github_owner="mingocfree",
            github_branch="dev",
            ecr_repo=self.ecr_repo,
        )
        synth_commands = [
            "npm install -g aws-cdk",
            "python -m pip install -r requirements.txt",
            "cdk synth",
        ]

        pipeline = pipelines.CodePipeline(
            self,
            f"{env}-InfraPipeline",
            pipeline_name=f"{env}-infra-pipeline",
            synth=pipelines.ShellStep(
                "Synth",
                input=pipelines.CodePipelineSource.git_hub(
                    repo_string=f"{config.get('owner', 'mingocfree')}/{config.get('name', 'test-aws')}",  # noqa
                    branch=config.get("branch", "dev"),
                    authentication=SecretValue.secrets_manager(
                        "github-token-infra"
                    ),  # noqa
                ),
                commands=synth_commands,
                primary_output_directory="cdk.out",
            ),
            docker_enabled_for_synth=True,
            publish_assets_in_parallel=False,
        )

        stage = NginxInfraStage(self, f"{env}-NginxInfra")
        pipeline.add_stage(stage)


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
            ),
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "env": {
                        "variables": {
                            "AWS_REGION": self.region,
                            "ECR_REPO_URI": ecr_repo.repository_uri,
                        }
                    },
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "COMMIT_HASH=$(echo $CODEBUILD_RESOLVED_SOURCE_VERSION | cut -c1-8)",  # noqa
                                "export IMAGE_TAG=$COMMIT_HASH",
                                "aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REPO_URI",  # noqa
                            ]
                        },
                        "build": {
                            "commands": [
                                "docker build -t $ECR_REPO_URI:latest .",
                                "docker tag $ECR_REPO_URI:latest $ECR_REPO_URI:$IMAGE_TAG",  # noqa
                            ]
                        },
                        "post_build": {
                            "commands": [
                                "aws ssm put-parameter --name /nginx/image-tag --value $IMAGE_TAG --type String --overwrite",  # noqa
                                "docker push $ECR_REPO_URI:$IMAGE_TAG",
                                "docker push $ECR_REPO_URI:latest",
                                "echo $IMAGE_TAG > image-tag.txt",
                            ]
                        },
                    },
                    "artifacts": {"files": ["image-tag.txt"]},
                }
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
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "env": {
                        "variables": {
                            "AWS_REGION": self.region,
                            "ECR_REPO_URI": ecr_repo.repository_uri,
                        }
                    },
                    "phases": {
                        "build": {
                            "commands": [
                                "ASG_NAME=$(aws ssm get-parameter --name /nginx/asg-name --query 'Parameter.Value' --output text)",  # noqa
                                "INSTANCE_IDS=$(aws autoscaling describe-auto-scaling-groups "
                                + "--auto-scaling-group-names $ASG_NAME "
                                + "--query 'AutoScalingGroups[0].Instances[?HealthStatus==`Healthy`].InstanceId' "  # noqa
                                + "--output text)",
                                "IMAGE_TAG=$(aws ssm get-parameter --name /nginx/image-tag --region $AWS_REGION --query 'Parameter.Value' --output text)",  # noqa
                                "for INSTANCE_ID in $INSTANCE_IDS; do "
                                '  echo "Deploying to instance: $INSTANCE_ID" && '
                                "  aws ssm send-command "
                                "    --instance-ids $INSTANCE_ID "
                                '    --document-name "AWS-RunShellScript" '
                                '    --parameters commands="[ '
                                '      \\"aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REPO_URI\\", '
                                '      \\"sudo sed -i \\\\\\"s|image: $ECR_REPO_URI:[^ ]*|image: $ECR_REPO_URI:$IMAGE_TAG|g\\\\\\" /etc/docker/docker-compose.yml\\", '
                                '      \\"docker rollout -f /etc/docker/docker-compose.yml web\\" '
                                '    ]" '
                                "    --region $AWS_REGION; "
                                "done",
                            ]
                        }
                    },
                }
            ),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_5_0,
                compute_type=codebuild.ComputeType.SMALL,
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


class NginxInfraStage(Stage):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        ecr_repo_uri = Fn.import_value("FastApiEcrRepoUri")
        NginxInfraStack(self, "NginxInfraStack", ecr_repo_uri)
