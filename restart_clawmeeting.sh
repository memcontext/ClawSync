#!/bin/bash
# =========================================
# ClawMeeting 一键重启脚本（XShell 适用）
# =========================================
# 用法:
#   bash restart_clawmeeting.sh          普通重启（保留数据库）
#   bash restart_clawmeeting.sh --clean  清库重启（删除数据库重建）
#   bash restart_clawmeeting.sh --pull   拉取最新代码并重启
#   bash restart_clawmeeting.sh --all    拉取代码 + 清库 + 重启
# =========================================

BASE_DIR="/home/deploy/clawmeeting"
API_DIR="$BASE_DIR/api-server"
AGENT_DIR="$BASE_DIR/agent/agent"
API_PORT=7010
AGENT_PORT=8001

echo ""
echo "========================================="
echo "  ClawMeeting 服务管理脚本"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="

# 解析参数
DO_PULL=false
DO_CLEAN=false
for arg in "$@"; do
    case $arg in
        --pull)  DO_PULL=true ;;
        --clean) DO_CLEAN=true ;;
        --all)   DO_PULL=true; DO_CLEAN=true ;;
    esac
done

# ---- 拉取最新代码 ----
if [ "$DO_PULL" = true ]; then
    echo ""
    echo "📥 拉取最新代码..."
    cd $BASE_DIR
    git stash 2>/dev/null
    git pull origin main
    git stash pop 2>/dev/null
    echo "✅ 代码已更新"
fi

# ---- 清理数据库 ----
if [ "$DO_CLEAN" = true ]; then
    echo ""
    echo "🗑️  清理数据库..."
    rm -f $API_DIR/meeting_coordinator.db
    echo "✅ 数据库已删除，启动时将自动重建"
fi

# ---- 停止旧服务 ----
echo ""
echo "🛑 停止旧服务..."
kill -9 $(lsof -t -i :$API_PORT) 2>/dev/null && echo "  API Server 已停止" || echo "  API Server 未在运行"
kill -9 $(lsof -t -i :$AGENT_PORT) 2>/dev/null && echo "  Agent 已停止" || echo "  Agent 未在运行"
sleep 1

# ---- 启动 API Server ----
echo ""
echo "🚀 启动 API Server (端口 $API_PORT)..."
cd $API_DIR
source venv/bin/activate
nohup python -m uvicorn app.main:app --host 0.0.0.0 --port $API_PORT > api.log 2>&1 &
API_PID=$!
sleep 2

if lsof -i :$API_PORT > /dev/null 2>&1; then
    echo "  ✅ API Server 启动成功 (PID: $API_PID)"
else
    echo "  ❌ API Server 启动失败，错误日志:"
    tail -10 api.log
    echo ""
    echo "  请检查后重试"
    exit 1
fi

# ---- 启动 Agent ----
echo ""
echo "🚀 启动 Agent (端口 $AGENT_PORT)..."
cd $AGENT_DIR
# 杀掉可能残留的旧 Agent 进程
ps -ef | grep agent_runner.py | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
sleep 1
nohup $AGENT_DIR/venv/bin/python -u agent_runner.py > agent.log 2>&1 &
AGENT_PID=$!
sleep 3

if lsof -i :$AGENT_PORT > /dev/null 2>&1; then
    echo "  ✅ Agent 启动成功 (PID: $AGENT_PID)"
else
    echo "  ⚠️  Agent 启动失败（可能是 Agent 团队的代码问题）"
    echo "  错误日志:"
    tail -5 agent.log
fi

# ---- 状态汇总 ----
echo ""
echo "========================================="
echo "  服务状态汇总"
echo "-----------------------------------------"
echo "  API Server : http://39.105.143.2:$API_PORT"
echo "  Agent      : http://39.105.143.2:$AGENT_PORT"
echo "  Swagger    : http://39.105.143.2:$API_PORT/docs"
echo "-----------------------------------------"
echo "  查看 API 日志   : tail -f $API_DIR/api.log"
echo "  查看 Agent 日志 : tail -f $AGENT_DIR/agent.log"
echo "  查看状态日志    : tail -f $API_DIR/logs/state_transitions.log"
echo "  查看请求日志    : tail -f $API_DIR/logs/api_$(date +%Y%m%d).log"
echo "========================================="
echo ""
