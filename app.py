#!/usr/bin/env python3

import aws_cdk as cdk

from aws_infra.aws_infra_pipeline import TestAwsStack


def init_app() -> cdk.App | None:

    app = cdk.App()
    TestAwsStack(
        app,
        "AwsInfraPipelineStack",
        env="dev",
    )
    return app


if __name__ == "__main__":
    app = init_app()
    if app:
        app.synth()
