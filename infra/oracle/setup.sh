#!/usr/bin/env bash
# =============================================================================
# Cloud AI OS — Oracle Cloud Always Free A1 provisioning (ARCHITECTURE.md §14)
#
# Provisions ONE VM.Standard.A1.Flex instance under the CONSERVATIVE assumption
# of 2 OCPU / 12 GB (the Always Free ceiling is 4 OCPU / 24 GB across all A1
# instances, but we deliberately request less and plan ~8 GB used — §1).
#
# Run this from a machine with the OCI CLI installed and configured
# (`oci setup config`) — typically your laptop, NOT the VM itself.
#
# ZERO-COST RULES (§0):
#   - Shape MUST be VM.Standard.A1.Flex within Always Free limits.
#   - Never attach paid block storage beyond the free 200 GB total allowance.
#   - A capacity-retry loop is ACCEPTABLE ("Out of host capacity" is common for
#     free A1). Artificial-utilization tricks (dummy load, fake traffic) to
#     dodge idle reclamation are FORBIDDEN — the design recovers from
#     reclamation instead (see RUNBOOK.md + bootstrap.sh).
# =============================================================================
set -euo pipefail

# ----------------------------------------------------------------------------
# Required inputs — export these before running, or edit here.
# Find values in the OCI console or via the discovery commands below.
# ----------------------------------------------------------------------------
: "${COMPARTMENT_ID:?export COMPARTMENT_ID=ocid1.compartment.oc1..xxxx (or tenancy root OCID)}"
: "${SUBNET_ID:?export SUBNET_ID=ocid1.subnet.oc1..xxxx (public subnet of your VCN)}"
: "${SSH_PUBKEY_FILE:?export SSH_PUBKEY_FILE=~/.ssh/id_ed25519.pub}"

# Conservative Always Free sizing (§1 assumption: 2 OCPU / 12 GB).
OCPUS="${OCPUS:-2}"
MEMORY_GB="${MEMORY_GB:-12}"        # 8–12 GB is fine; 12 leaves the most headroom
BOOT_VOLUME_GB="${BOOT_VOLUME_GB:-50}"
DISPLAY_NAME="${DISPLAY_NAME:-cloudos-a1}"
SHAPE="VM.Standard.A1.Flex"

# Retry policy for "Out of host capacity" (free A1 capacity is scarce).
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"
RETRY_SLEEP_SECONDS="${RETRY_SLEEP_SECONDS:-300}"   # 5 min between attempts

# ----------------------------------------------------------------------------
# Discovery helpers (run these once, by hand, to fill the variables above):
#
#   oci iam availability-domain list --compartment-id "$COMPARTMENT_ID"
#
#   # Latest Ubuntu 24.04 aarch64 image for the A1 shape:
#   oci compute image list --compartment-id "$COMPARTMENT_ID" \
#     --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
#     --shape "$SHAPE" --sort-by TIMECREATED --sort-order DESC \
#     --query 'data[0].{id:id,name:"display-name"}'
#
#   # VCN + public subnet (create via console "Networking > VCN wizard" if none):
#   oci network subnet list --compartment-id "$COMPARTMENT_ID"
#
# Security list / NSG: open TCP 22 (SSH) and 8080 (agent-api; ideally restrict
# 8080 to Cloudflare egress or your own IP — ingress is meant to come through
# the Cloudflare Worker, §13). n8n's 5678 should stay closed publicly; reach it
# over an SSH tunnel: ssh -L 5678:localhost:5678 ubuntu@<vm-ip>
# ----------------------------------------------------------------------------
: "${AVAILABILITY_DOMAIN:?export AVAILABILITY_DOMAIN=... (from the discovery command above)}"
: "${IMAGE_ID:?export IMAGE_ID=ocid1.image.oc1... (Ubuntu 24.04 aarch64, discovery above)}"

echo "==> Launching ${SHAPE} (${OCPUS} OCPU / ${MEMORY_GB} GB) '${DISPLAY_NAME}'"
echo "    Always Free capacity is scarce; retrying up to ${MAX_ATTEMPTS}x every ${RETRY_SLEEP_SECONDS}s."

attempt=1
while :; do
    echo "==> Attempt ${attempt}/${MAX_ATTEMPTS}"
    if output=$(oci compute instance launch \
        --compartment-id "$COMPARTMENT_ID" \
        --availability-domain "$AVAILABILITY_DOMAIN" \
        --shape "$SHAPE" \
        --shape-config "{\"ocpus\": ${OCPUS}, \"memoryInGBs\": ${MEMORY_GB}}" \
        --image-id "$IMAGE_ID" \
        --subnet-id "$SUBNET_ID" \
        --assign-public-ip true \
        --display-name "$DISPLAY_NAME" \
        --boot-volume-size-in-gbs "$BOOT_VOLUME_GB" \
        --metadata "{\"ssh_authorized_keys\": \"$(cat "$SSH_PUBKEY_FILE")\"}" \
        2>&1); then
        echo "$output"
        INSTANCE_ID=$(echo "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
        echo "==> Launched: $INSTANCE_ID"
        break
    fi
    if echo "$output" | grep -qi "out of host capacity\|OutOfCapacity\|InternalError.*capacity"; then
        echo "    No A1 capacity in ${AVAILABILITY_DOMAIN}. This is normal for Always Free."
        echo "    Tip: also try the other availability domains in your home region."
        if (( attempt >= MAX_ATTEMPTS )); then
            echo "ERROR: gave up after ${MAX_ATTEMPTS} attempts. Re-run later; capacity fluctuates." >&2
            exit 1
        fi
        attempt=$((attempt + 1))
        sleep "$RETRY_SLEEP_SECONDS"
    else
        echo "ERROR: launch failed for a non-capacity reason:" >&2
        echo "$output" >&2
        exit 1
    fi
done

echo "==> Waiting for RUNNING state..."
oci compute instance get --instance-id "$INSTANCE_ID" \
    --query 'data."lifecycle-state"' || true

echo "==> Public IP:"
oci compute instance list-vnics --instance-id "$INSTANCE_ID" \
    --query 'data[0]."public-ip"' --raw-output

cat <<'NEXT'

Next steps:
  1. ssh ubuntu@<public-ip>
  2. Copy infra/oracle/bootstrap.sh to the VM (or clone the repo and run it):
       curl -fsSL https://raw.githubusercontent.com/<owner>/cloud-ai-os/main/infra/oracle/bootstrap.sh -o bootstrap.sh
       REPO_URL=https://github.com/<owner>/cloud-ai-os.git bash bootstrap.sh
  3. See infra/oracle/RUNBOOK.md for the full recovery/runbook flow.

Reminder (§0): do NOT add keep-busy cron jobs or synthetic load to avoid idle
reclamation. If Oracle reclaims the VM, RUNBOOK.md restores service on a new one.
NEXT
