#!/usr/bin/env bash
#
# setup-vm.sh
#
# One-time setup for a fresh Ubuntu VM (e.g. an Oracle Cloud Always Free
# Ampere A1 instance) that will host this project. Run this once, right
# after first SSH login, before `docker compose up`.
#
#   scp -r energi-mcp-server ubuntu@<vm-ip>:~/
#   ssh ubuntu@<vm-ip>
#   cd energi-mcp-server
#   chmod +x scripts/setup-vm.sh
#   sudo ./scripts/setup-vm.sh
#
# What this does:
#   1. Installs Docker + the Compose plugin.
#   2. Opens ports 80/443 on the local firewall (ufw) -- you still need
#      to open them in your cloud provider's Security List/Group too.
#   3. Enables unattended-upgrades so OS security patches apply
#      automatically without you having to SSH in regularly.
#
# Safe to re-run; every step is idempotent.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run as root (sudo ./scripts/setup-vm.sh)" >&2
    exit 1
fi

echo "==> Updating apt package index..."
apt-get update -y

echo "==> Installing Docker Engine + Compose plugin..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
else
    echo "    Docker already installed, skipping."
fi

# Let the invoking (non-root) user run docker without sudo.
TARGET_USER="${SUDO_USER:-$USER}"
if [[ "$TARGET_USER" != "root" ]]; then
    usermod -aG docker "$TARGET_USER"
    echo "    Added $TARGET_USER to the docker group (log out/in for it to take effect)."
fi

echo "==> Configuring firewall (ufw): allow SSH, 80, 443..."
apt-get install -y ufw
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
echo "    Remember: also open TCP 80 and 443 in your cloud provider's"
echo "    Security List/Security Group -- ufw alone is not enough."

echo "==> Enabling automatic OS security updates (unattended-upgrades)..."
apt-get install -y unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo ""
echo "==> Done. Next steps:"
echo "    1. cp .env.example .env   (then fill in real values)"
echo "    2. docker compose up -d"
