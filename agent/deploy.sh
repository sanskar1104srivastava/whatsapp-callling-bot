#!/bin/bash

# ==============================================================================
# AWS ECR Docker Deployment Script (x86_64 / amd64 for AWS Lambda)
# ==============================================================================

set -e

# --- CONFIGURATION ---
REPO_NAME="vriddhi/agent"
IMAGE_TAG="latest"

# ---------------------------------------------------------------------------------
# ENVIRONMENT CONFIGURATION
# ---------------------------------------------------------------------------------
AWS_REGION="ap-south-2"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}"
AWS_ACCOUNT_PROFILE="${AWS_PROFILE:-default}"

echo "INFO: Using account ${AWS_ACCOUNT_ID} in region ${AWS_REGION}"
# ---------------------------------------------------------------------------------

# Full ECR repository URI
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
FULL_IMAGE_URI="${ECR_URI}/${REPO_NAME}:${IMAGE_TAG}"

# ----------------------------------------------------------------------
# CHECK PREREQUISITES
# ----------------------------------------------------------------------
echo "--- Checking prerequisites ---"

if ! command -v aws >/dev/null 2>&1; then
    echo "❌ ERROR: AWS CLI is not installed."
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "❌ ERROR: Docker is not installed."
    exit 1
fi

aws sts get-caller-identity --profile ${AWS_ACCOUNT_PROFILE} >/dev/null

echo "✅ Prerequisites met"
echo "----------------------------------------"
echo "Target Image: ${FULL_IMAGE_URI}"
echo "----------------------------------------"

# ----------------------------------------------------------------------
# 1. BUILD IMAGE (x86_64 / amd64)
# ----------------------------------------------------------------------
echo "--- Building x86_64 (amd64) Docker image ---"

DOCKER_BUILDKIT=0 docker build \
  --platform linux/amd64 \
  -t ${FULL_IMAGE_URI} .

echo "✅ Docker build successful"

# ----------------------------------------------------------------------
# 2. LOGIN TO ECR
# ----------------------------------------------------------------------
echo "--- Authenticating Docker with ECR ---"

aws ecr get-login-password \
  --profile ${AWS_ACCOUNT_PROFILE} \
  --region ${AWS_REGION} \
| docker login \
  --username AWS \
  --password-stdin ${ECR_URI}

echo "✅ ECR authentication successful"

# ----------------------------------------------------------------------
# 3. PUSH IMAGE
# ----------------------------------------------------------------------
echo "--- Pushing image to ECR ---"

docker push ${FULL_IMAGE_URI}

echo "----------------------------------------"
echo "✅ SUCCESS: x86_64 image pushed to ECR"
echo "✅ Image URI: ${FULL_IMAGE_URI}"
