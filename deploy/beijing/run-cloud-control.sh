#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROFILE_NAME="instant-ai-beijing-control"
readonly REGION_ID="cn-beijing"

fail() {
  printf 'BEIJING_CLOUD_CONTROL_FAILED: %s\n' "$*" >&2
  exit 1
}

[[ "$#" -eq 2 ]] || fail "usage: run-cloud-control.sh <inspect|publish> <beijing-instance-id>"
action="$1"
instance_id="$2"
[[ "${instance_id}" =~ ^i-[A-Za-z0-9]+$ ]] || fail "invalid Beijing ECS instance ID"

if command -v aliyun >/dev/null 2>&1; then
  aliyun_bin="$(command -v aliyun)"
elif [[ -x "/home/compassdev/.local/bin/aliyun" ]]; then
  aliyun_bin="/home/compassdev/.local/bin/aliyun"
else
  fail "Alibaba Cloud CLI 3.3.0 or later is required"
fi

case "${action}" in
  inspect)
    remote_command="sudo -n /usr/local/sbin/beijing-suite-inspect"
    ;;
  publish)
    remote_command="sudo -n /usr/local/sbin/beijing-collector-publish-trigger"
    ;;
  *)
    fail "only inspect or publish is allowed"
    ;;
esac

run_payload="$("${aliyun_bin}" ecs RunCommand \
  --profile "${PROFILE_NAME}" \
  --RegionId "${REGION_ID}" \
  --Name "instant-ai-${action}" \
  --Type RunShellScript \
  --ContentEncoding PlainText \
  --CommandContent "${remote_command}" \
  --KeepCommand false \
  --Timeout 60 \
  --Username beijingcodex \
  --InstanceId.1 "${instance_id}")" || fail "RunCommand request failed"
invoke_id="$(printf '%s' "${run_payload}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("InvokeId", ""))')"
[[ "${invoke_id}" =~ ^t-[A-Za-z0-9]+$ ]] || fail "RunCommand returned no valid InvokeId"

for attempt in $(seq 1 40); do
  result_payload="$("${aliyun_bin}" ecs DescribeInvocationResults \
    --profile "${PROFILE_NAME}" \
    --RegionId "${REGION_ID}" \
    --InvokeId "${invoke_id}" \
    --InstanceId "${instance_id}" \
    --ContentEncoding PlainText \
    --MaxResults 1)" || fail "DescribeInvocationResults request failed"
  mapfile -t result_fields < <(printf '%s' "${result_payload}" | python3 -c '
import base64
import json
import sys

payload = json.load(sys.stdin)
invocation = payload.get("Invocation", payload)
results = invocation.get("InvocationResults", {}).get("InvocationResult", [])
if not results:
    raise SystemExit(0)
item = results[0]
print(str(item.get("InvocationStatus", "")))
print(str(item.get("ExitCode", "")))
output = str(item.get("Output", ""))
print(base64.b64encode(output.encode("utf-8")).decode("ascii"))
')
  status="${result_fields[0]:-}"
  exit_code="${result_fields[1]:-}"
  output_b64="${result_fields[2]:-}"
  case "${status}" in
    Success)
      [[ "${exit_code}" == "0" ]] || fail "remote command reported success with a nonzero exit code"
      printf '%s' "${output_b64}" | python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'
      printf 'BEIJING_CLOUD_CONTROL_OK action=%s invoke_id=%s\n' "${action}" "${invoke_id}"
      exit 0
      ;;
    Failed|Error|Timeout|Cancelled|Terminated|Aborted|Invalid)
      if [[ -n "${output_b64}" ]]; then
        printf '%s' "${output_b64}" | python3 -c 'import base64,sys; sys.stderr.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'
      fi
      fail "remote command ended with status=${status} exit_code=${exit_code}"
      ;;
  esac
  sleep 2
done

fail "remote command result was not final within 80 seconds"
