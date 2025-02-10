#!/bin/bash

# Update all packages
yum update -y

# Install Docker
yum install -y docker

# Start and enable Docker service
systemctl start docker
systemctl enable docker

# Install Docker Compose
curl -L "https://github.com/docker/compose/releases/download/$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep -Po '"tag_name": "\K.*?(?=")')/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Login to AWS ECR
aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${REPO_URI}

mkdir -p /etc/docker
cat <<EOT >/etc/docker/docker-compose.yml
${DOCKER_COMPOSE_CONTENT}
EOT

docker compose -f /etc/docker/docker-compose.yml up -d
