#!/bin/bash
# DataOps Knowledge Hub — SeaweedFS bucket + seed documents (runs in the init-seaweedfs one-shot service).
# The amazon/aws-cli image doesn't guarantee curl, so readiness is checked through the S3 API itself.
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY come from the environment (.env).
set -euo pipefail

S3_ENDPOINT="${S3_ENDPOINT:-http://seaweedfs:8333}"
BUCKET="${SEAWEEDFS_BUCKET:-dataops-lake}"

s3() {
    aws --endpoint-url "$S3_ENDPOINT" s3 "$@"
}

# Wait for the SeaweedFS S3 gateway to be ready
until s3 ls > /dev/null 2>&1; do
    echo "Waiting for SeaweedFS S3 gateway..."
    sleep 2
done

# Create bucket (idempotent: skip if it already exists)
if s3 ls "s3://${BUCKET}" > /dev/null 2>&1; then
    echo "Bucket '${BUCKET}' already exists."
else
    s3 mb "s3://${BUCKET}"
fi

# Upload seed documents to the bucket root (idempotent: sync only copies new/changed files)
s3 sync /docs/ "s3://${BUCKET}/"

echo "SeaweedFS bucket '${BUCKET}' ready with seed documents:"
s3 ls "s3://${BUCKET}/"
