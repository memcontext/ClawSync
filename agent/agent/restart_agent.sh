#!/bin/bash
# 杀掉旧进程
ps -ef | grep agent_runner.py | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
sleep 1

# 启动新进程
cd /home/deploy/clawmeeting/agent/agent
nohup /home/deploy/clawmeeting/agent/agent/venv/bin/python -u agent_runner.py > agent.log 2>&1 &

echo "  Agent PID: $!"
sleep 2
tail -3 agent.log
