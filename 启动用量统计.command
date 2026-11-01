#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ -d "dist/macos-arm64/Token Ledger.app" ]]; then
  open "dist/macos-arm64/Token Ledger.app"
  exit 0
fi
for interpreter in .venv-desktop/bin/python /usr/local/bin/python3.14 /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14 python3; do
  if "$interpreter" -c 'import tkinter' >/dev/null 2>&1; then
    exec "$interpreter" -m tokenlog
  fi
done
echo '未找到带 Tk 的 Python。请使用打包后的 Token Ledger.app，或安装 python.org 的 Python。'
read -r '?按回车关闭…'
