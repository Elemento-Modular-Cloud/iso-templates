#!/bin/bash

usage() {
  echo "Usage:"
  echo "  $0 --file <json_file>"
  exit 1
}

# Parse headers and output JSON object
parse_headers() {
  local name="$1"
  local url="$2"
  local os_version="$3"
  local headers
  headers=$(curl -s -I "$url")

  local status_code=$(echo "$headers" | grep -m 1 "HTTP/" | awk '{print $2}')
  local content_length=$(echo "$headers" | grep -i "Content-Length" | awk '{print $2}' | tr -d '\r')
  local content_type=$(echo "$headers" | grep -i "Content-Type" | awk '{print $2}' | tr -d '\r')
  local content_disp=$(echo "$headers" | grep -i "Content-Disposition" | cut -d':' -f2- | xargs | tr -d '\000-\037' | sed 's/"/\\"/g')

  echo "  {"
  echo "    \"name\": \"${name}\","
  echo "    \"url\": \"${url}\","
  if [ -n "$os_version" ]; then
    echo "    \"os_version\": \"${os_version}\","
  fi
  echo "    \"status\": ${status_code:-0},"
  echo "    \"valid\": $( [ "$status_code" == "200" ] && echo true || echo false ),"
  echo "    \"content_length\": \"${content_length:-unknown}\","
  echo "    \"content_type\": \"${content_type:-unknown}\","
  echo "    \"content_disposition\": \"${content_disp:-none}\""
  echo "  }"
}

if [ $# -ne 2 ]; then
  usage
fi

case "$1" in
  --file)
    JSON_FILE="$2"
    if [ ! -f "$JSON_FILE" ]; then
      echo "Error: File not found: $JSON_FILE"
      exit 1
    fi

    echo "["
    first=1
    jq -c '.[]?' "$JSON_FILE" | while read -r entry; do
      name=$(echo "$entry" | jq -r '.name')
      url=$(echo "$entry" | jq -r '.iso_url')
      os_version=$(echo "$entry" | jq -r '.os_version // empty')

      [ -z "$url" ] && continue

      if [ $first -eq 0 ]; then
        echo ","
      fi
      parse_headers "$name" "$url" "$os_version"
      first=0
    done
    echo ""
    echo "]"
    ;;
  *)
    usage
    ;;
esac