from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as codepipeline_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import pipelines
from aws_cdk.aws_codecommit import Repository
from constructs import Construct


class AwsInfraStack(Stack):

    def __init__(
        self, scope: Construct, construct_id: str, config: dict, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc(self, f"{config["env"]}-VPC")

        # Security Group for EC2 instances
        sg = ec2.SecurityGroup(
            self, f"{config["env"]}-SG", vpc=vpc, allow_all_outbound=True
        )
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "Allow HTTP")
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "Allow SSH")
        # The code that defines your stack goes here

        branch = config["env"]
        if config["env"] == "production":
            branch = "main"
        pipeline = pipelines.CodePipeline(
            self,
            "Pipeline",
            synth=pipelines.ShellStep(
                "Synth",
                input=pipelines.CodePipelineSource.git_hub("owner/repo", branch),
                commands=["npx", "cdk", "synth"],
                primary_output_directory="cdk.out",
            ),
        )

        # example resource
        # queue = sqs.Queue(
        #     self, "AwsInfraQueue",
        #     visibility_timeout=Duration.seconds(300),
        # )
