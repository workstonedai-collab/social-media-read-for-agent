#!/bin/bash
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
echo '智能体国内社媒使用工具'
echo '将下载项目专用运行环境并配置已有的 Codex / Hermes。不会改系统环境或自动授权扩展。'
if /bin/bash "$ROOT/install.sh" --install-ocr; then
  echo '依赖与配置步骤结束。请回到 Agent，让它继续引导专用浏览器授权、登录与真实读取。'
else
  echo '安装未完成；已成功的步骤保留。请把错误提示交给 Agent，它可以继续处理。'
fi
read -r -p '按回车关闭此窗口。' _
