#!/bin/bash

# Update system packages
yum update -y

# Install Docker
sudo amazon-linux-extras install docker

sudo service docker start

sudo usermod -a -G docker ec2-user

# Authenticate Docker with AWS ECR
aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ECR_REPOSITORY_URI}

sudo curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m) -o /usr/local/bin/docker-compose

sudo chmod +x /usr/local/bin/docker-compose

sudo ln -s /usr/local/bin/docker-compose /usr/bin/docker-compose

docker-compose version

# Create directory for Docker CLI plugins
mkdir -p ~/.docker/cli-plugins

# Download docker-rollout script to Docker CLI plugins directory
curl -sSL https://raw.githubusercontent.com/wowu/docker-rollout/main/docker-rollout -o ~/.docker/cli-plugins/docker-rollout

# Make the script executable
chmod +x ~/.docker/cli-plugins/docker-rollout

mkdir -p /etc/docker
cat <<EOT >/etc/docker/docker-compose.yml
${DOCKER_COMPOSE_CONTENT}
EOT

docker-compose -f /etc/docker/docker-compose.yml up -d
