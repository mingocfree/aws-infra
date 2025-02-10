from aws_cdk import SecretValue, Stack, Stage, pipelines
from constructs import Construct


class InfraDeployStage(Stage):

    def __init__(
        self, scope: Construct, construct_id: str, config: dict, **kwargs
    ):  # noqa
        super().__init__(scope, construct_id, **kwargs)

        from aws_infra.aws_infra_stack import AwsInfraStack

        AwsInfraStack(
            self,
            f"{config.get('env', 'dev')}-InfraStack",
            config=config,
            env=kwargs.get("env"),
        )


class InfraPipelineStack(Stack):

    def __init__(
        self, scope: Construct, construct_id: str, config: dict, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env = config.get("env", "dev")
        github_repository = config.get("repository", {})

        synth_commands = [
            "npm install -g aws-cdk",
            "python -m pip install -r requirements.txt",
        ]

        if env == "dev":
            synth_commands.extend(
                [
                    "npm i -g cdk-nag",
                    "npx cdk-nag-scan --template-path cdk.out/*.template.json --verbose --fail-on-warnings",  # noqa
                ]
            )

        synth_commands.append("cdk synth")

        pipeline = pipelines.CodePipeline(
            self,
            f"{env}-InfraPipeline",
            pipeline_name=f"{env}-infra-pipeline",
            synth=pipelines.ShellStep(
                "Synth",
                input=pipelines.CodePipelineSource.git_hub(
                    owner=github_repository.get("owner", "mingocfree"),
                    repo=github_repository.get("name", "aws-infra"),
                    branch=config.get("repository_branch", "main"),
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

        # Add deployment stage
        deploy_stage = InfraDeployStage(
            self, f"{env}-Deploy", config=config, env=kwargs.get("env")
        )
        pipeline.add_stage(deploy_stage)
