#!/usr/bin/env bash
# Paste this file into the NVIDIA Brev VM Mode setup script field.
set -euo pipefail

# Set this to a release tag when saving a versioned Launchable.
export URDF2VLA_REF="${URDF2VLA_REF:-main}"

setup_script="$(mktemp /tmp/urdf2vla-brev-setup.XXXXXX)"
trap 'rm -f -- "$setup_script"' EXIT
curl --fail --silent --show-error --location --retry 3 \
  "https://raw.githubusercontent.com/momoiorg-repository/urdf2vla/${URDF2VLA_REF}/docker/brev_setup.sh" \
  --output "$setup_script"
bash "$setup_script"
