#!/bin/bash
set -euo pipefail

API_URL="${1:?Usage: $0 <api-url>}"
TIMEOUT=300
POLL_INTERVAL=10

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC}: $1"; }
fail() { echo -e "${RED}FAIL${NC}: $1"; exit 1; }

echo "=== Cuseinfra Integration Test ==="
echo "API: $API_URL"
echo ""

# 1. Submit a job
echo "--- Submitting job ---"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$API_URL/jobs" \
    -H "Content-Type: application/json" \
    -d '{"taskDescription": "selenium test search", "tenantId": "test-tenant", "priority": "high"}')

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "202" ]; then
    pass "POST /jobs returned 202"
else
    fail "POST /jobs returned $HTTP_CODE: $BODY"
fi

JOB_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['jobId'])")
echo "Job ID: $JOB_ID"

# 2. Poll for completion
echo ""
echo "--- Polling for completion (timeout: ${TIMEOUT}s) ---"
ELAPSED=0
STATUS="PENDING"

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    RESPONSE=$(curl -s "$API_URL/jobs/$JOB_ID")
    STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")

    echo "  [$ELAPSED s] Status: $STATUS"

    if [ "$STATUS" = "COMPLETED" ] || [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "TIMED_OUT" ]; then
        break
    fi

    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

if [ "$STATUS" = "COMPLETED" ]; then
    pass "Job completed successfully"
elif [ "$STATUS" = "FAILED" ]; then
    fail "Job failed: $RESPONSE"
elif [ "$STATUS" = "TIMED_OUT" ]; then
    fail "Job timed out"
else
    fail "Job did not complete within ${TIMEOUT}s (status: $STATUS)"
fi

# 3. Check recording endpoint
echo ""
echo "--- Checking recording ---"
RECORDING=$(curl -s "$API_URL/jobs/$JOB_ID/recording")
HAS_RECORDING=$(echo "$RECORDING" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if d.get('recording') else 'no')" 2>/dev/null || echo "no")
SCREENSHOT_COUNT=$(echo "$RECORDING" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('screenshots',[])))" 2>/dev/null || echo "0")

if [ "$HAS_RECORDING" = "yes" ]; then
    pass "Recording URL available"
else
    echo "  WARN: No recording URL (may not have finished uploading)"
fi

if [ "$SCREENSHOT_COUNT" -gt 0 ]; then
    pass "Screenshots available: $SCREENSHOT_COUNT"
else
    echo "  WARN: No screenshots found"
fi

# 4. Verify job output
echo ""
echo "--- Checking job output ---"
JOB_RESPONSE=$(curl -s "$API_URL/jobs/$JOB_ID")
HAS_OUTPUT=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; print('yes' if json.load(sys.stdin).get('output') else 'no')" 2>/dev/null || echo "no")

if [ "$HAS_OUTPUT" = "yes" ]; then
    pass "Job output present in response"
else
    fail "No output in job response"
fi

echo ""
echo "=== Integration Test Complete ==="
