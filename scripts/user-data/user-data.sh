#!/bin/bash

# Update system packages
sudo dnf update -y

# Install Docker
sudo dnf install -y docker
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ec2-user

# Authenticate Docker with AWS ECR
aws ecr get-login-password --region ${REGION} | sudo docker login --username AWS --password-stdin ${ECR_REPOSITORY_URI}

# Install Docker Compose v2
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -sL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-"$(uname -m)" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chown root:root /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Install Docker Rollout plugin system-wide
sudo curl -sSL https://raw.githubusercontent.com/wowu/docker-rollout/main/docker-rollout \
  -o /usr/local/lib/docker/cli-plugins/docker-rollout
sudo chown root:root /usr/local/lib/docker/cli-plugins/docker-rollout
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-rollout

# Create docker-compose configuration
sudo mkdir -p /etc/docker
cat <<EOT | sudo tee /etc/docker/docker-compose.yml >/dev/null
${DOCKER_COMPOSE_CONTENT}
EOT

# Start containers
sudo docker compose -f /etc/docker/docker-compose.yml up -d
