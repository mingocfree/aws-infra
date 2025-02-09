#!/usr/bin/env python3
import os

import aws_cdk as cdk
from ruamel.yaml import YAML

from aws_infra.aws_infra_stack import AwsInfraStack

CONFIG_FILE_PATHS = {
    "dev": "config/dev.yaml",
    "test": "config/test.yaml",
    "production": "config/prod.yaml",
}


def load_config() -> dict:

    env = os.getenv("ENV", "dev").lower()
    config = {
        "env": env,
        "backend_repository_url": os.getenv(
            "BACKEND_REPOSITORY", "mingocfree/aws-java"
        ),
    }
    loaded_config = {}
    with open(CONFIG_FILE_PATHS[env]) as config_file:
        loaded_config = YAML().load(config_file.read())

    config.update(loaded_config)
    if not config.get("account_number", ""):
        config["account_number"] = os.getenv("AWS_ACCOUNT_NUMBER", "")
    return config


def init_app() -> cdk.App:

    app = cdk.App()
    config = load_config()
    AwsInfraStack(
        app,
        "AwsInfraStack",
        config=config,
        env={"account": config["account_number"], "region": config["region"]},
    )
    return app


if __name__ == "__main__":
    app = init_app()
    app.synth()
