#!/usr/bin/env python3
"""
ClawMeeting 测试客户端 — 供测试人员在本地连接服务器进行插件功能测试

使用方法:
    python test_client.py                        # 交互式菜单
    python test_client.py --server http://39.105.143.2:7010  # 指定服务器地址

功能:
    1. 注册/绑定用户
    2. 发起会议
    3. 查看待办任务
    4. 提交空闲时间
    5. 拒绝会议
    6. 查看会议详情
    7. 查看我的会议列表
    8. 查看 Agent 状态
    9. 查看服务器数据库概览
"""

import requests
import json
import sys
import argparse
from datetime import datetime, timedelta

# ========== 配置 ==========

DEFAULT_SERVER = "http://39.105.143.2:7010"


class TestClient:
    def __init__(self, server_url):
        self.server_url = server_url.rstrip("/")
        self.current_token = None
        self.current_email = None
        self.current_user_id = None

    def _post(self, path, data=None, use_token=True):
        headers = {"Content-Type": "application/json"}
        if use_token and self.current_token:
            headers["Authorization"] = f"Bearer {self.current_token}"
        try:
            r = requests.post(f"{self.server_url}{path}", json=data, headers=headers, timeout=10)
            return r.status_code, r.json()
        except requests.exceptions.ConnectionError:
            return 0, {"code": 0, "message": f"无法连接服务器 {self.server_url}"}
        except Exception as e:
            return 0, {"code": 0, "message": str(e)}

    def _get(self, path, use_token=True):
        headers = {}
        if use_token and self.current_token:
            headers["Authorization"] = f"Bearer {self.current_token}"
        try:
            r = requests.get(f"{self.server_url}{path}", headers=headers, timeout=10)
            return r.status_code, r.json()
        except requests.exceptions.ConnectionError:
            return 0, {"code": 0, "message": f"无法连接服务器 {self.server_url}"}
        except Exception as e:
            return 0, {"code": 0, "message": str(e)}

    def _print_json(self, data):
        print(json.dumps(data, indent=2, ensure_ascii=False))

    # ========== 功能方法 ==========

    def health_check(self):
        """检查服务器连通性"""
        code, data = self._get("/health", use_token=False)
        if code == 200:
            print(f"  ✅ 服务器正常 — {data.get('timestamp', '')}")
        else:
            print(f"  ❌ 服务器无响应 — {data.get('message', '')}")
        return code == 200

    def bind_user(self):
        """注册/绑定用户"""
        email = input("  输入邮箱: ").strip()
        if not email:
            print("  ❌ 邮箱不能为空")
            return

        code, data = self._post("/api/auth/bind", {"email": email}, use_token=False)
        if code == 200 and data.get("code") == 200:
            self.current_token = data["data"]["token"]
            self.current_email = email
            self.current_user_id = data["data"]["user_id"]
            print(f"  ✅ 绑定成功")
            print(f"     邮箱: {email}")
            print(f"     Token: {self.current_token}")
            print(f"     User ID: {self.current_user_id}")
        else:
            print(f"  ❌ 绑定失败: {data.get('message', '')}")

    def create_meeting(self):
        """发起会议"""
        if not self.current_token:
            print("  ❌ 请先绑定用户（选项 1）")
            return

        title = input("  会议标题: ").strip() or "测试会议"
        duration = input("  会议时长（分钟，默认30）: ").strip() or "30"
        invitees_str = input("  受邀人邮箱（多个用逗号分隔）: ").strip()
        if not invitees_str:
            print("  ❌ 至少需要一个受邀人")
            return

        invitees = [e.strip() for e in invitees_str.split(",") if e.strip()]

        # 生成默认时间槽（未来3天的上午和下午）
        print("  输入可用时间段（每行一个，格式: 2026-03-21 14:00-17:00）")
        print("  直接回车使用默认时间（未来3天 10:00-12:00 和 14:00-17:00）:")
        slots = []
        while True:
            slot = input("    > ").strip()
            if not slot:
                break
            slots.append(slot)

        if not slots:
            today = datetime.now()
            for i in range(3):
                d = today + timedelta(days=i + 1)
                date_str = d.strftime("%Y-%m-%d")
                slots.append(f"{date_str} 10:00-12:00")
                slots.append(f"{date_str} 14:00-17:00")
            print(f"    使用默认时间: {', '.join(slots[:3])}...")

        preference = input("  偏好说明（可选）: ").strip() or None

        code, data = self._post("/api/meetings", {
            "title": title,
            "duration_minutes": int(duration),
            "invitees": invitees,
            "initiator_data": {
                "available_slots": slots,
                "preference_note": preference
            }
        })

        if code == 200 and data.get("code") == 200:
            print(f"  ✅ 会议创建成功")
            print(f"     Meeting ID: {data['data'].get('meeting_id') or data['data'].get('id')}")
            print(f"     状态: {data['data']['status']}")
            print(f"     受邀人: {', '.join(invitees)}")
        else:
            print(f"  ❌ 创建失败: {data.get('message', '')}")

    def check_pending_tasks(self):
        """查看待办任务"""
        if not self.current_token:
            print("  ❌ 请先绑定用户（选项 1）")
            return

        code, data = self._get("/api/tasks/pending")
        if code == 200 and data.get("code") == 200:
            tasks = data["data"]["pending_tasks"]
            if not tasks:
                print("  📭 没有待办任务")
                return

            print(f"  📋 共 {len(tasks)} 个待办任务:\n")
            for i, task in enumerate(tasks, 1):
                print(f"  [{i}] {task['title']}")
                print(f"      Meeting ID: {task['meeting_id']}")
                print(f"      发起人: {task['initiator']}")
                print(f"      类型: {task['task_type']}")
                print(f"      时长: {task['duration_minutes']} 分钟")
                print(f"      轮次: {task['round_count']}")
                if task.get('initiator_slots'):
                    print(f"      发起人时间: {', '.join(task['initiator_slots'][:3])}")
                if task.get('suggested_slots'):
                    print(f"      建议时间: {', '.join(task['suggested_slots'][:3])}")
                print(f"      消息: {task['message']}")
                print()
        else:
            print(f"  ❌ 查询失败: {data.get('message', '')}")

    def submit_availability(self):
        """提交空闲时间"""
        if not self.current_token:
            print("  ❌ 请先绑定用户（选项 1）")
            return

        meeting_id = input("  Meeting ID: ").strip()
        if not meeting_id:
            print("  ❌ Meeting ID 不能为空")
            return

        print("  响应类型:")
        print("    1. INITIAL（首次提交）")
        print("    2. COUNTER（重新提交）")
        print("    3. ACCEPT_PROPOSAL（接受方案）")
        print("    4. REJECT（拒绝）")
        choice = input("  选择 (1-4, 默认1): ").strip() or "1"

        type_map = {"1": "INITIAL", "2": "COUNTER", "3": "ACCEPT_PROPOSAL", "4": "REJECT"}
        response_type = type_map.get(choice, "INITIAL")

        submit_data = {"response_type": response_type}

        if response_type in ("INITIAL", "COUNTER"):
            print("  输入可用时间段（每行一个，回车结束）:")
            slots = []
            while True:
                slot = input("    > ").strip()
                if not slot:
                    break
                slots.append(slot)

            if not slots:
                today = datetime.now()
                for i in range(3):
                    d = today + timedelta(days=i + 1)
                    date_str = d.strftime("%Y-%m-%d")
                    slots.append(f"{date_str} 10:00-12:00")
                    slots.append(f"{date_str} 14:00-17:00")
                print(f"    使用默认时间")

            submit_data["available_slots"] = slots
            preference = input("  偏好说明（可选）: ").strip()
            if preference:
                submit_data["preference_note"] = preference

        elif response_type == "REJECT":
            reason = input("  拒绝原因（可选）: ").strip()
            if reason:
                submit_data["preference_note"] = reason

        code, data = self._post(f"/api/meetings/{meeting_id}/submit", submit_data)
        if code == 200 and data.get("code") == 200:
            print(f"  ✅ 提交成功")
            print(f"     状态: {data['data']['status']}")
            print(f"     全员提交: {data['data'].get('all_submitted', 'N/A')}")
        else:
            print(f"  ❌ 提交失败: {data.get('message', '')}")

    def view_meeting(self):
        """查看会议详情"""
        if not self.current_token:
            print("  ❌ 请先绑定用户（选项 1）")
            return

        meeting_id = input("  Meeting ID: ").strip()
        if not meeting_id:
            print("  ❌ Meeting ID 不能为空")
            return

        code, data = self._get(f"/api/meetings/{meeting_id}")
        if code == 200 and data.get("code") == 200:
            d = data["data"]
            print(f"\n  📋 会议详情")
            print(f"  {'='*50}")
            print(f"  标题: {d['title']}")
            print(f"  Meeting ID: {d['meeting_id']}")
            print(f"  状态: {d['status']}")
            print(f"  时长: {d.get('duration_minutes', 'N/A')} 分钟")
            print(f"  轮次: {d['round_count']}")
            print(f"  最终时间: {d.get('final_time') or '未确定'}")
            print(f"  协调分析: {d.get('coordinator_reasoning') or '无'}")
            print(f"\n  参与者:")
            for p in d.get("participants", []):
                status = "✅ 已提交" if p.get("has_submitted") else "⏳ 待提交"
                print(f"    {p['email']} ({p['role']}) — {status}")
                if p.get("latest_slots"):
                    print(f"      时间: {', '.join(p['latest_slots'][:3])}")
                if p.get("preference_note"):
                    print(f"      偏好: {p['preference_note'][:50]}")
        else:
            print(f"  ❌ 查询失败: {data.get('message', '')}")

    def list_meetings(self):
        """查看我的会议列表"""
        if not self.current_token:
            print("  ❌ 请先绑定用户（选项 1）")
            return

        code, data = self._get("/api/meetings")
        if code == 200 and data.get("code") == 200:
            meetings = data["data"]["meetings"]
            if not meetings:
                print("  📭 没有参与的会议")
                return

            print(f"\n  📋 共 {data['data']['total']} 个会议:\n")
            for m in meetings:
                status_icon = {"COLLECTING": "📥", "ANALYZING": "🔍", "CONFIRMED": "✅", "FAILED": "❌", "NEGOTIATING": "🔄"}.get(m["status"], "❓")
                print(f"  {status_icon} {m['title']}")
                print(f"     ID: {m['meeting_id']}")
                print(f"     状态: {m['status']}  角色: {m['my_role']}  进度: {m['progress']}")
                print(f"     发起人: {m['initiator_email']}  时长: {m['duration_minutes']}分钟")
                if m.get("final_time"):
                    print(f"     最终时间: {m['final_time']}")
                print()
        else:
            print(f"  ❌ 查询失败: {data.get('message', '')}")

    def check_agent_status(self):
        """查看 Agent 状态"""
        # Agent 待处理任务
        code, data = self._get("/api/agent/tasks/pending", use_token=False)
        if code == 200:
            tasks = data["data"]["pending_tasks"]
            print(f"  🤖 Agent 待处理: {len(tasks)} 个会议")
            for t in tasks:
                print(f"     {t['meeting_id']} — {t['title']} (轮次 {t['round_count']}/{t.get('max_rounds', 3)})")
        else:
            print(f"  ❌ 查询失败: {data.get('message', '')}")

        # Agent 健康检查
        try:
            agent_url = self.server_url.rsplit(":", 1)[0] + ":8001"
            r = requests.get(f"{agent_url}/health", timeout=3)
            if r.status_code == 200:
                print(f"  ✅ Agent 服务正常 ({agent_url})")
            else:
                print(f"  ⚠️ Agent 响应异常: {r.status_code}")
        except:
            print(f"  ❌ Agent 服务无响应")

    def db_overview(self):
        """查看数据库概览（通过 API）"""
        # 用户数
        code, data = self._post("/api/auth/bind", {"email": "__probe__@test.com"}, use_token=False)

        # Agent 任务
        code, data = self._get("/api/agent/tasks/pending", use_token=False)
        if code == 200:
            analyzing = len(data["data"]["pending_tasks"])
            print(f"  ANALYZING 状态会议: {analyzing}")

        # 当前用户会议
        if self.current_token:
            code, data = self._get("/api/meetings")
            if code == 200:
                meetings = data["data"]["meetings"]
                by_status = {}
                for m in meetings:
                    by_status[m["status"]] = by_status.get(m["status"], 0) + 1
                print(f"  我的会议总数: {len(meetings)}")
                for status, count in sorted(by_status.items()):
                    print(f"    {status}: {count}")

    def run(self):
        """主循环"""
        print(f"\n{'='*60}")
        print(f"  ClawMeeting 测试客户端")
        print(f"  服务器: {self.server_url}")
        print(f"{'='*60}\n")

        if not self.health_check():
            print("\n  服务器无法连接，请检查地址是否正确。")
            return

        while True:
            print(f"\n{'─'*60}")
            if self.current_email:
                print(f"  当前用户: {self.current_email} (ID: {self.current_user_id})")
            else:
                print(f"  当前用户: 未绑定")
            print(f"{'─'*60}")
            print("  1. 注册/绑定用户")
            print("  2. 发起会议")
            print("  3. 查看待办任务")
            print("  4. 提交空闲时间/拒绝/接受")
            print("  5. 查看会议详情")
            print("  6. 查看我的会议列表")
            print("  7. 查看 Agent 状态")
            print("  8. 数据库概览")
            print("  0. 退出")
            print()

            choice = input("  请选择 (0-8): ").strip()

            if choice == "0":
                print("\n  再见！")
                break
            elif choice == "1":
                self.bind_user()
            elif choice == "2":
                self.create_meeting()
            elif choice == "3":
                self.check_pending_tasks()
            elif choice == "4":
                self.submit_availability()
            elif choice == "5":
                self.view_meeting()
            elif choice == "6":
                self.list_meetings()
            elif choice == "7":
                self.check_agent_status()
            elif choice == "8":
                self.db_overview()
            else:
                print("  ❌ 无效选项")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ClawMeeting 测试客户端")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"服务器地址 (默认: {DEFAULT_SERVER})")
    args = parser.parse_args()

    client = TestClient(args.server)
    client.run()
