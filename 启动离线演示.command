#!/bin/zsh
set -eu
cd "${0:A:h}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONPATH="$PWD"
print "Social Read · 离线演示"
print "服务启动后，请打开 http://127.0.0.1:8768"
print "演示内容完全虚构。按 Ctrl+C 停止。"
python3 -m social_read --mode demo serve --port 8768
