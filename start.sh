#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PYTHON="${PYTHON:-python3}"
VENV_DIR="$PROJECT_DIR/.venv"
REQUIREMENTS_FILE="$PROJECT_DIR/requirement"

fail() {
    printf '错误：%s\n' "$1" >&2
    exit 1
}

CHECK_ONLY=false
case "${1:-}" in
    "") ;;
    --check) CHECK_ONLY=true ;;
    --help|-h)
        printf '用法：%s [--check]\n' "${0##*/}"
        printf '  --check 只校验运行环境和依赖，不启动 TUI\n'
        exit 0
        ;;
    *) fail "未知参数：$1（使用 --help 查看用法）" ;;
esac

printf '[1/4] 检查运行环境\n'
command -v "$PYTHON" >/dev/null 2>&1 || fail "找不到 Python 命令：$PYTHON"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "需要 Python 3.10 或更高版本"
[[ -f "$REQUIREMENTS_FILE" ]] || fail "依赖清单不存在：$REQUIREMENTS_FILE"

printf '[2/4] 创建并安装虚拟环境依赖\n'
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV_DIR" || fail "创建虚拟环境失败"
fi
"$VENV_DIR/bin/python" -m pip install -r "$REQUIREMENTS_FILE"

printf '[3/4] 验证依赖\n'
"$VENV_DIR/bin/python" -m pip check
"$VENV_DIR/bin/python" -c 'import openai, textual' \
    || fail "依赖导入失败，请检查 requirement 并重新安装"

if [[ "$CHECK_ONLY" == true ]]; then
    printf '校验通过\n'
    exit 0
fi

printf '[4/4] 启动 reSEN Agent\n'
exec "$VENV_DIR/bin/python" "$PROJECT_DIR/tui/main.py"