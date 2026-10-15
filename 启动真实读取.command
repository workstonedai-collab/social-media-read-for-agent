#!/bin/zsh
set -eu
cd "${0:A:h}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONPATH="$PWD"
print "social-media-read-for-agent · 真实读取"
print "服务启动后，请打开 http://127.0.0.1:8767"
print "首次使用需要 OpenCLI 扩展和正常的平台登录。按 Ctrl+C 停止。"
python3 -m social_read serve
