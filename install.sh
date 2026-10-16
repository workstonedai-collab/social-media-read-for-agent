#!/bin/bash
# Download pinned, checksum-verified project runtimes. No sudo or shell edits.
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
STATE="$ROOT/.state"
RUNTIME="$STATE/runtime"
RUNTIME_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --runtime-only) RUNTIME_ONLY=1 ;;
    --help|-h)
      cat <<'HELP'
智能体国内社媒使用工具 · 独立安装
用法: /bin/bash install.sh [bootstrap 选项]
示例: /bin/bash install.sh --clients codex --install-ocr
      /bin/bash install.sh --clients hermes --install-ocr
      /bin/bash install.sh --runtime-only
不需要预装 Python、Node、npm 或 uv；不会请求 sudo、改 shell 配置或自动授权扩展。
需要系统自带的 bash/curl/tar/awk 与 sha256sum 或 shasum。首次需要下载。
HELP
      exit 0 ;;
    *) ;;
  esac
done
case "$(uname -s)" in Darwin) OS=darwin ;; Linux) OS=linux ;; *) echo '暂不支持此系统；请查看安装说明。' >&2; exit 2 ;; esac
case "$(uname -m)" in arm64|aarch64) ARCH=arm64 ;; x86_64|amd64) ARCH=x64 ;; *) echo '暂不支持此处理器。' >&2; exit 2 ;; esac
for utility in curl tar awk mktemp; do
  command -v "$utility" >/dev/null || { echo "缺少系统工具: $utility" >&2; exit 2; }
done
if command -v sha256sum >/dev/null; then HASH=(sha256sum); elif command -v shasum >/dev/null; then HASH=(shasum -a 256); else echo '缺少 SHA256 校验工具。' >&2; exit 2; fi
[ ! -L "$STATE" ] && [ ! -L "$RUNTIME" ] || { echo '状态目录不能是符号链接。' >&2; exit 2; }
mkdir -p "$RUNTIME"
chmod 700 "$STATE" "$RUNTIME"
LOCK="$STATE/install.lock"
mkdir "$LOCK" 2>/dev/null || { echo '另一安装正在运行，或上次中断留下 install.lock。请由 Agent 核对后继续。' >&2; exit 3; }
STAGING=''
cleanup() { [ -z "$STAGING" ] || rm -rf -- "$STAGING"; rmdir "$LOCK" 2>/dev/null || true; }
trap 'installer_exit=$?; cleanup; exit "$installer_exit"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
download_runtime() {
  local kind="$1" url="$2" archive="$3" code=0 route="${SOCIAL_READ_DOWNLOAD_ROUTE:-auto}" proxy='' network attempt active_route
  # Bash 3.2 treats expansion of an empty array as unbound under set -u.
  local proxy_args=(--retry-delay 1)
  case "$route" in auto|direct|proxy) ;; *) echo '无效下载路径选项。' >&2; exit 2 ;; esac
  # curl does not read macOS network settings. Reuse only an existing static
  # HTTPS proxy; never guess local ports or modify system/network settings.
  if [ "$route" != direct ] && [ -z "${https_proxy:-${HTTPS_PROXY:-${ALL_PROXY:-${all_proxy:-}}}}" ] && [ "$OS" = darwin ] && [ -x /usr/sbin/scutil ]; then
    network="$(/usr/sbin/scutil --proxy)"
    if [ "$(printf '%s' "$network" | awk '$1=="HTTPSEnable" {print $3}')" = 1 ]; then
      proxy="$(printf '%s' "$network" | awk '$1=="HTTPSProxy" {print $3}')"
      local port="$(printf '%s' "$network" | awk '$1=="HTTPSPort" {print $3}')"
      if [[ "$proxy" =~ ^[a-zA-Z0-9.-]+$ ]] && [[ "$port" =~ ^[0-9]+$ ]]; then proxy_args=(--proxy "http://$proxy:$port"); fi
    fi
  fi
  [ "$route" != direct ] || proxy_args=(--noproxy '*')
  for attempt in 1 2 3; do
    code=0
    active_route="$route"
    local active_args=("${proxy_args[@]}")
    if [ "$route" = auto ] && [ "$attempt" = 2 ]; then active_args=(--noproxy '*'); active_route=direct; fi
    printf '{"stage":"%s_download","status":"running","attempt":%s}\n' "$kind" "$attempt" > "$STATE/setup-progress.json"
    # A partial archive survives both this process and route changes. curl
    # resumes by Range; execution still requires the full committed checksum.
    curl --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 300 --speed-limit 4096 --speed-time 30 --continue-at - --silent --show-error "${active_args[@]}" "$url" -o "$archive" || code=$?
    printf '{"component":"%s","route":"%s","attempt":%s,"curl_exit":%s,"tls_verification_enabled":true}\n' "$kind" "$active_route" "$attempt" "$code" >> "$STATE/runtime-downloads.jsonl"
    [ "$code" != 0 ] || break
    # HTTP denial and certificate errors stop. A server declining Range may
    # restart this one public download, never any browser/platform operation.
    if [ "$code" = 33 ]; then rm -f -- "$archive";
    elif ! [[ "$code" =~ ^(5|6|7|18|28|35|52|55|56)$ ]]; then break; fi
    if [ "$attempt" != 3 ]; then echo "${kind} 公开下载中断，将续传已取得部分（${attempt}/3）；用户无需重新设置。" >&2; fi
  done
  if [ "$code" != 0 ]; then
    printf '{"stage":"%s_download","status":"failed","reason":"runtime_download_failed"}\n' "$kind" > "$STATE/setup-progress.json"
    printf '{"status":"setup_incomplete","stage":"%s_download","reason":"runtime_download_failed","curl_exit":%s,"resume":"rerun_install_preserves_completed_steps"}\n' "$kind" "$code"
    exit "$code"
  fi
}
install_runtime() {
  local kind="$1" key record version url checksum installed actual archive
  key="$kind-$OS-$ARCH"
  record="$(awk -F '\t' -v key="$key" '$1==key {print $2"\t"$3"\t"$4}' "$ROOT/runtime-lock.tsv")"
  IFS=$'\t' read -r version url checksum <<< "$record"
  [[ "$checksum" =~ ^[0-9a-f]{64}$ ]] || { echo "无效运行时清单: $key" >&2; exit 2; }
  case "$url" in https://github.com/astral-sh/python-build-standalone/releases/download/*|https://nodejs.org/dist/*) ;; *) echo '运行时来源未登记。' >&2; exit 2 ;; esac
  installed="$RUNTIME/$kind"
  [ ! -L "$installed" ] || { echo '运行时目录不能是符号链接。' >&2; exit 2; }
  if [ -f "$installed/.social-read-runtime" ] && [ "$(cat "$installed/.social-read-runtime")" = "$key $version $checksum" ]; then
    if [ "$kind" = python ] && [ -x "$installed/bin/python3" ] && "$installed/bin/python3" -I -c 'import ssl,venv,json' >/dev/null 2>&1; then echo '复用项目专用 Python。' >&2; return; fi
    if [ "$kind" = node ] && [ -x "$installed/bin/node" ] && [ "$("$installed/bin/node" --version)" = "v$version" ]; then echo '复用项目专用 Node。' >&2; return; fi
  fi
  [ ! -e "$installed" ] || { echo "现有 ${kind} 运行时与固定版本不符；请由 Agent 核对，安装器不会覆盖它。" >&2; exit 3; }
  echo "正在下载并校验项目专用 ${kind} ${version}（${OS}/${ARCH}）……" >&2
  local cache="$STATE/download-cache/runtimes"
  [ ! -L "$STATE/download-cache" ] && [ ! -L "$cache" ] || { echo '下载缓存不能是符号链接。' >&2; exit 2; }
  mkdir -p "$cache"
  archive="$cache/$checksum.tar.gz"
  [ ! -L "$archive" ] && [ ! -L "$archive.part" ] || { echo '下载缓存不能是符号链接。' >&2; exit 2; }
  if [ ! -f "$archive" ]; then
    download_runtime "$kind" "$url" "$archive.part"
    mv "$archive.part" "$archive"
  fi
  actual="$("${HASH[@]}" "$archive" | awk '{print $1}')"
  [ "$actual" = "$checksum" ] || { rm -f -- "$archive"; printf '{"stage":"%s_checksum","status":"failed"}\n' "$kind" > "$STATE/setup-progress.json"; echo '下载校验失败，已停止；没有执行该文件。' >&2; exit 4; }
  STAGING="$(mktemp -d "$RUNTIME/.download-$kind.XXXXXX")"
  mkdir "$STAGING/unpacked"
  tar -xzf "$archive" --strip-components=1 -C "$STAGING/unpacked"
  if [ "$kind" = python ]; then
    "$STAGING/unpacked/bin/python3" -I -c 'import ssl,venv,ensurepip,json'
  else
    [ "$("$STAGING/unpacked/bin/node" --version)" = "v$version" ]
  fi
  printf '%s\n' "$key $version $checksum" > "$STAGING/unpacked/.social-read-runtime"
  mv "$STAGING/unpacked" "$installed"
  printf '{"stage":"%s_runtime","status":"ready"}\n' "$kind" > "$STATE/setup-progress.json"
  rm -rf -- "$STAGING"; STAGING=''
}
install_runtime python
install_runtime node
export PATH="$RUNTIME/node/bin:$RUNTIME/python/bin:${PATH:-/usr/bin:/bin}"
PYTHON="$RUNTIME/python/bin/python3"
if [ "$RUNTIME_ONLY" = 1 ]; then
  "$PYTHON" -I -c 'import json,sys;print(json.dumps({"status":"runtimes_ready","python":sys.executable,"system_installation_modified":False}))'
else
  echo '正在配置 Agent；用户只在扩展授权和平台正常登录时参与。' >&2
  "$PYTHON" "$ROOT/bootstrap.py" --apply --bridge project "$@"
fi
