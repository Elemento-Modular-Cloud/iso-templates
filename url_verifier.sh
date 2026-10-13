#!/bin/bash
set -uo pipefail

usage() {
  echo "Usage:"
  echo "  $0 --file <json_file>"
  exit 1
}

CURL_OPTS=(-s -L --max-time 60 --retry 2 --retry-delay 5)

# Headers of the final response after redirects
final_headers() {
  tr -d '\r' | awk '/^HTTP\// { block = "" } { block = block $0 "\n" } END { printf "%s", block }'
}

# Check a URL and print its report as a JSON object
check_url() {
  local name="$1"
  local url="$2"
  local os_version="$3"
  local headers status

  headers=$(curl "${CURL_OPTS[@]}" -I "$url" | final_headers)
  status=$(echo "$headers" | awk 'NR == 1 { print $2 }')

  # Some servers reject HEAD: retry with a one-byte GET
  if [[ "$status" =~ ^(403|405|501)$ ]]; then
    headers=$(curl "${CURL_OPTS[@]}" -r 0-0 -o /dev/null -D - "$url" | final_headers)
    status=$(echo "$headers" | awk 'NR == 1 { print $2 }')
    [ "$status" == "206" ] && status=200
  fi

  header() { echo "$headers" | grep -i -m 1 "^$1:" | cut -d':' -f2- | tr -d '\000-\037' | sed 's/^ *//;s/ *$//'; }

  jq -n \
    --arg name "$name" \
    --arg url "$url" \
    --arg os_version "$os_version" \
    --argjson status "${status:-0}" \
    --arg content_length "$(header Content-Length)" \
    --arg content_type "$(header Content-Type)" \
    --arg content_disposition "$(header Content-Disposition)" \
    '{
      name: $name,
      url: $url,
      status: $status,
      valid: ($status == 200),
      content_length: (if $content_length == "" then "unknown" else $content_length end),
      content_type: (if $content_type == "" then "unknown" else $content_type end),
      content_disposition: (if $content_disposition == "" then "none" else $content_disposition end)
    } + (if $os_version == "" then {} else {os_version: $os_version} end)'
}

if [ $# -ne 2 ] || [ "$1" != "--file" ]; then
  usage
fi

JSON_FILE="$2"
if [ ! -f "$JSON_FILE" ]; then
  echo "Error: File not found: $JSON_FILE" >&2
  exit 1
fi

# Entries without a URL (e.g. Windows) have nothing to check
jq -c '.[] | select(.iso_url != null and .iso_url != "")' "$JSON_FILE" |
  while read -r entry; do
    name=$(jq -r '.name' <<< "$entry")
    url=$(jq -r '.iso_url' <<< "$entry")
    os_version=$(jq -r '.os_version // empty' <<< "$entry")
    check_url "$name" "$url" "$os_version"
  done | jq -s '.'
