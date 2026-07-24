#!/bin/bash

# 异常检测系统一键启动脚本（macOS / Linux）
#
# 使用方法：
#   1. macOS：在 Finder 中双击本脚本；首次运行若被系统拦截，请右键选择“打开”。
#   2. 终端：在项目目录执行 ./异常检测系统.command
#   3. 停止服务：回到本窗口按 Control+C；脚本会同时停止前端和后端。
#
# 可选配置：
#   BACKEND_PORT=9000 FRONTEND_PORT=5200 ./异常检测系统.command
#   如果指定端口已被占用，脚本会自动向后查找可用端口，不会结束占用端口的进程。

set -u

# 无论从 Finder、终端还是其他目录启动，都先切换到脚本所在的项目目录。
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR" || {
  echo "无法进入项目目录：$SCRIPT_DIR"
  exit 1
}

BACKEND_DIR="$SCRIPT_DIR/fastapi-app"
FRONTEND_DIR="$SCRIPT_DIR/vue"
LOG_DIR="$SCRIPT_DIR/logs"
BACKEND_START_PORT="${BACKEND_PORT:-9090}"
FRONTEND_START_PORT="${FRONTEND_PORT:-5173}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-90}"
BACKEND_PID=""
FRONTEND_PID=""

print_error_and_pause() {
  echo
  echo "启动失败：$1"
  if [ -t 0 ]; then
    echo "按回车键关闭窗口..."
    read -r _unused
  fi
  exit 1
}

if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"
elif [ -x "$SCRIPT_DIR/venv/bin/python3" ]; then
  PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  print_error_and_pause "未找到 Python 3。"
fi

if command -v npm >/dev/null 2>&1; then
  NPM_BIN="$(command -v npm)"
else
  print_error_and_pause "未找到 npm，请先安装 Node.js。"
fi

if [ ! -f "$BACKEND_DIR/main.py" ] || [ ! -f "$FRONTEND_DIR/package.json" ]; then
  print_error_and_pause "项目目录不完整，未找到前端或后端入口文件。"
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  print_error_and_pause "前端依赖尚未安装，请先在 vue 目录运行 npm install。"
fi

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn, tortoise" >/dev/null 2>&1; then
  print_error_and_pause "后端依赖尚未安装，请先运行：python3 -m pip install -r requirements.txt"
fi

find_free_port() {
  "$PYTHON_BIN" - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
while port <= 65535:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            port += 1
        else:
            print(port)
            raise SystemExit(0)
raise SystemExit("没有可用端口")
PY
}

BACKEND_PORT="$(find_free_port "$BACKEND_START_PORT")" ||
  print_error_and_pause "无法为后端找到可用端口。"
FRONTEND_PORT="$(find_free_port "$FRONTEND_START_PORT")" ||
  print_error_and_pause "无法为前端找到可用端口。"

mkdir -p "$LOG_DIR"
RUN_ID="$(date '+%Y%m%d-%H%M%S')-$$"
BACKEND_LOG="$LOG_DIR/backend-$RUN_ID.log"
FRONTEND_LOG="$LOG_DIR/frontend-$RUN_ID.log"

cleanup() {
  trap - EXIT INT TERM
  echo
  echo "正在停止异常检测系统..."
  if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  wait "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  echo "前端和后端已停止。"
}
trap cleanup EXIT
trap 'exit 0' INT TERM

echo "项目目录：$SCRIPT_DIR"
if [ "$BACKEND_PORT" != "$BACKEND_START_PORT" ]; then
  echo "后端端口 $BACKEND_START_PORT 已占用，自动改用 ${BACKEND_PORT}。"
fi
if [ "$FRONTEND_PORT" != "$FRONTEND_START_PORT" ]; then
  echo "前端端口 $FRONTEND_START_PORT 已占用，自动改用 ${FRONTEND_PORT}。"
fi
echo "正在启动后端：http://127.0.0.1:$BACKEND_PORT"
echo "正在启动前端：http://127.0.0.1:$FRONTEND_PORT"

(
  cd "$BACKEND_DIR" || exit 1
  CORS_ALLOWED_ORIGINS="http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT" \
    "$PYTHON_BIN" -m uvicorn main:app \
      --host 127.0.0.1 \
      --port "$BACKEND_PORT"
) >>"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

(
  cd "$FRONTEND_DIR" || exit 1
  VITE_BASE_URL="http://127.0.0.1:$BACKEND_PORT" \
    "$NPM_BIN" run dev -- \
      --host 127.0.0.1 \
      --port "$FRONTEND_PORT" \
      --strictPort
) >>"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

wait_until_ready() {
  service_name="$1"
  service_url="$2"
  service_pid="$3"
  service_log="$4"
  elapsed=0

  while [ "$elapsed" -lt "$STARTUP_TIMEOUT" ]; do
    if ! kill -0 "$service_pid" 2>/dev/null; then
      echo
      echo "${service_name}进程意外退出，最近的日志："
      tail -n 30 "$service_log" 2>/dev/null || true
      return 1
    fi
    if curl --silent --fail --max-time 2 "$service_url" >/dev/null 2>&1; then
      echo "${service_name}已就绪。"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  echo
  echo "等待${service_name}就绪超时，最近的日志："
  tail -n 30 "$service_log" 2>/dev/null || true
  return 1
}

if ! wait_until_ready "后端" "http://127.0.0.1:$BACKEND_PORT/" "$BACKEND_PID" "$BACKEND_LOG"; then
  print_error_and_pause "后端未能正常启动，完整日志：$BACKEND_LOG"
fi
if ! wait_until_ready "前端" "http://127.0.0.1:$FRONTEND_PORT/" "$FRONTEND_PID" "$FRONTEND_LOG"; then
  print_error_and_pause "前端未能正常启动，完整日志：$FRONTEND_LOG"
fi

FRONTEND_URL="http://127.0.0.1:$FRONTEND_PORT/"
echo
echo "异常检测系统已启动完成：$FRONTEND_URL"
echo "后端日志：$BACKEND_LOG"
echo "前端日志：$FRONTEND_LOG"
echo "按 Control+C 可同时停止所有服务。"

if command -v open >/dev/null 2>&1; then
  open "$FRONTEND_URL"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$FRONTEND_URL" >/dev/null 2>&1 &
else
  echo "未找到浏览器打开命令，请手动访问：$FRONTEND_URL"
fi

# 持续监控两个服务；任一服务退出时，统一清理并结束脚本。
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 2
done

echo "检测到服务进程退出，请检查日志。"
exit 1
