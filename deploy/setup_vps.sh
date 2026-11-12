#!/usr/bin/env bash
set -euo pipefail

echo "======================================================"
echo "  Kronos Bybit 24/7 VPS Automated Setup Script"
echo "======================================================"

INSTALL_DIR="/opt/kronos-bybit"

# 1. System packages
echo "[1/5] Updating apt packages and installing Python 3..."
sudo apt-get update -y
sudo apt-get install -y git python3 python3-pip python3-venv curl build-essential

# 2. Application Directory
echo "[2/5] Setting up directory at $INSTALL_DIR..."
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Directory $INSTALL_DIR does not exist. Please clone or copy the repository to $INSTALL_DIR first."
    exit 1
fi

cd "$INSTALL_DIR"

# 3. Virtual environment & dependencies
echo "[3/5] Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Check .env configuration
echo "[4/5] Checking .env configuration..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example. Please edit .env with your Bybit API keys!"
    else
        echo "Warning: .env not found. Please create one before starting the service."
    fi
fi

# 5. Setup systemd service
echo "[5/5] Configuring systemd service..."
sudo cp deploy/kronos-bybit.service /etc/systemd/system/kronos-bybit.service
sudo systemctl daemon-reload
sudo systemctl enable kronos-bybit.service

echo ""
echo "======================================================"
echo "  Setup Complete!"
echo "  To start the 24/7 trading daemon, run:"
echo "    sudo systemctl start kronos-bybit"
echo ""
echo "  To view live logs in real time, run:"
echo "    sudo journalctl -u kronos-bybit -f"
echo ""
echo "  To stop the daemon, run:"
echo "    sudo systemctl stop kronos-bybit"
echo "======================================================"

