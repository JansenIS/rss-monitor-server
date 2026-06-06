#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo "Запусти от root: sudo bash scripts/linux/open_firewall_8080.sh" >&2
  exit 1
fi
ufw allow OpenSSH || true
ufw allow 8080/tcp
ufw --force enable
ufw status verbose
