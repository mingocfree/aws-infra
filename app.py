#!/usr/bin/env python3
import os

import aws_cdk as cdk
from git import Repo
from ruamel.yaml import YAML

from aws_infra.aws_infra_pipeline import TestAwsStack

CONFIG_FILE_PATHS = {
    "dev": "config/dev.yaml",
    "test": "config/test.yaml",
    "production": "config/prod.yaml",
}


def load_config() -> dict:

    current_branch = Repo(search_parent_directories=True).active_branch
    env = current_branch.name
    if current_branch.name == "main":
        env = "production"

    if env not in CONFIG_FILE_PATHS:
        return {}
    config = {
        "env": env,
        "repository_branch": current_branch.name,
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


def init_app() -> cdk.App | None:

    app = cdk.App()
    config = load_config()
    if not config:
        return None
    TestAwsStack(
        app,
        "AwsInfraPipelineStack",
        env=config["env"],
        config=config,
    )
    return app


if __name__ == "__main__":
    app = init_app()
    if app:
        app.synth()
