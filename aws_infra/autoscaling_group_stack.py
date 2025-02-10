import os

from aws_cdk import Duration, Stack
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from constructs import Construct


def user_data_generation(ecr_repository_uri: str, region: str) -> str:
    """Generates user data for EC2 instances."""
    path = os.path.dirname(__file__)

    with open(
        os.path.join(path, "scripts/docker/docker-compose.yaml"),
        "r",
        encoding="utf-8",  # noqa
    ) as f:
        docker_compose_text = f.read().replace(
            "${ECR_REPOSITORY_URI}", ecr_repository_uri
        )

    with open(
        os.path.join(path, "scripts/user-data/backend-service.sh"),
        "r",
        encoding="utf-8",
    ) as f:
        user_data_text = f.read()

    return (
        user_data_text.replace("${REPO_URI}", ecr_repository_uri)
        .replace("${DOCKER_COMPOSE_CONTENT}", docker_compose_text)
        .replace("${REGION}", region)
    )


class AutoScalingGroupStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: dict,
        ecr_repository_uri: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env = config.get("env", "dev")
        ec2_properties = config.get("ec2_properties", {})
        scaling_policy = config.get("scaling_policy", {})

        vpc = ec2.Vpc(self, f"{env}-VPC", max_azs=2)

        lb_security_group = ec2.SecurityGroup(
            self, f"{env}-LB-SG", vpc=vpc, allow_all_outbound=True
        )
        lb_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "Allow HTTP traffic"  # noqa
        )

        self.lb = elbv2.ApplicationLoadBalancer(
            self,
            f"{env}-ALB",
            vpc=vpc,
            internet_facing=True,
            security_group=lb_security_group,
        )

        self.target_group_blue = elbv2.ApplicationTargetGroup(
            self,
            f"{env}-TargetGroupBlue",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(
                path="/health", healthy_http_codes="200"
            ),  # noqa
        )

        self.target_group_green = elbv2.ApplicationTargetGroup(
            self,
            f"{env}-TargetGroupGreen",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(
                path="/health", healthy_http_codes="200"
            ),  # noqa
        )

        self.listener = self.lb.add_listener(
            "ListenerBlue",
            port=80,
            default_target_groups=[self.target_group_blue],  # noqa
        )
        self.listener_green = self.lb.add_listener(
            "ListenerGreen",
            port=80,
            default_target_groups=[self.target_group_green],  # noqa
        )

        role = iam.Role(
            self,
            "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                )
            ],
        )

        launch_template = ec2.LaunchTemplate(
            self,
            "LaunchTemplate",
            instance_type=ec2.InstanceType(
                ec2_properties.get("instance_type", "t2.micro")
            ),
            machine_image=ec2.AmazonLinuxImage(),
            user_data=ec2.UserData.custom(
                user_data_generation(ecr_repository_uri, self.region)
            ),
            role=role,
        )

        self.asg = autoscaling.AutoScalingGroup(
            self,
            "AutoScalingGroup",
            vpc=vpc,
            launch_template=launch_template,
            min_capacity=scaling_policy.get("min_capacity", 2),
            max_capacity=scaling_policy.get("max_capacity", 4),
            health_check=autoscaling.HealthCheck.elb(
                grace=Duration.seconds(60)
            ),  # noqa
        )

        self.target_group_blue.add_target(self.asg)
        self.target_group_green.add_target(self.asg)
