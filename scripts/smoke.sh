#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
TARGET_URL="${2:-https://example.com}"

printf "Running web scan-and-process against %s\n" "$TARGET_URL"
curl -sS -X POST "$BASE_URL/scan-and-process" \
  -H 'content-type: application/json' \
  -d "{\"url\":\"$TARGET_URL\"}" | jq '{provider, reportType, findings: .summary.totalFindings}'

printf "Creating PDF job from a URL fixture\n"
JOB_ID=$(curl -sS -X POST "$BASE_URL/pdf/jobs?pdf_url=https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf" | jq -r '.jobId')
printf "Job ID: %s\n" "$JOB_ID"

for _ in {1..20}; do
  STATUS=$(curl -sS "$BASE_URL/pdf/jobs/$JOB_ID" | jq -r '.status')
  printf "pdf job status: %s\n" "$STATUS"
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    break
  fi
  sleep 2
done

curl -sS "$BASE_URL/pdf/jobs/$JOB_ID/report?format=json" | jq '{provider, reportType, findings: .summary.totalFindings}'
