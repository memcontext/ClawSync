#!/usr/bin/env python3
"""
ClawMeeting Test Client -- for testers to connect to the server and test plugin functionality

Usage:
    python test_client.py                        # Interactive menu
    python test_client.py --server http://39.105.143.2:7010  # Specify server address

Features:
    1. Register/bind user
    2. Create meeting
    3. View pending tasks
    4. Submit available time
    5. Reject meeting
    6. View meeting details
    7. View my meeting list
    8. View Agent status
    9. View server database overview
"""

import requests
import json
import sys
import argparse
from datetime import datetime, timedelta

# ========== Configuration ==========

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
            return 0, {"code": 0, "message": f"Cannot connect to server {self.server_url}"}
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
            return 0, {"code": 0, "message": f"Cannot connect to server {self.server_url}"}
        except Exception as e:
            return 0, {"code": 0, "message": str(e)}

    def _print_json(self, data):
        print(json.dumps(data, indent=2, ensure_ascii=False))

    # ========== Feature Methods ==========

    def health_check(self):
        """Check server connectivity"""
        code, data = self._get("/health", use_token=False)
        if code == 200:
            print(f"  Server OK -- {data.get('timestamp', '')}")
        else:
            print(f"  Server not responding -- {data.get('message', '')}")
        return code == 200

    def bind_user(self):
        """Register/bind user"""
        email = input("  Enter email: ").strip()
        if not email:
            print("  Email cannot be empty")
            return

        code, data = self._post("/api/auth/bind", {"email": email}, use_token=False)
        if code == 200 and data.get("code") == 200:
            self.current_token = data["data"]["token"]
            self.current_email = email
            self.current_user_id = data["data"]["user_id"]
            print(f"  Binding successful")
            print(f"     Email: {email}")
            print(f"     Token: {self.current_token}")
            print(f"     User ID: {self.current_user_id}")
        else:
            print(f"  Binding failed: {data.get('message', '')}")

    def create_meeting(self):
        """Create meeting"""
        if not self.current_token:
            print("  Please bind a user first (option 1)")
            return

        title = input("  Meeting title: ").strip() or "Test Meeting"
        duration = input("  Meeting duration (minutes, default 30): ").strip() or "30"
        invitees_str = input("  Invitee emails (comma-separated): ").strip()
        if not invitees_str:
            print("  At least one invitee is required")
            return

        invitees = [e.strip() for e in invitees_str.split(",") if e.strip()]

        # Generate default time slots (morning and afternoon for the next 3 days)
        print("  Enter available time slots (one per line, format: 2026-03-21 14:00-17:00)")
        print("  Press Enter to use default times (next 3 days 10:00-12:00 and 14:00-17:00):")
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
            print(f"    Using default times: {', '.join(slots[:3])}...")

        preference = input("  Preference note (optional): ").strip() or None

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
            print(f"  Meeting created successfully")
            print(f"     Meeting ID: {data['data'].get('meeting_id') or data['data'].get('id')}")
            print(f"     Status: {data['data']['status']}")
            print(f"     Invitees: {', '.join(invitees)}")
        else:
            print(f"  Creation failed: {data.get('message', '')}")

    def check_pending_tasks(self):
        """View pending tasks"""
        if not self.current_token:
            print("  Please bind a user first (option 1)")
            return

        code, data = self._get("/api/tasks/pending")
        if code == 200 and data.get("code") == 200:
            tasks = data["data"]["pending_tasks"]
            if not tasks:
                print("  No pending tasks")
                return

            print(f"  {len(tasks)} pending task(s):\n")
            for i, task in enumerate(tasks, 1):
                print(f"  [{i}] {task['title']}")
                print(f"      Meeting ID: {task['meeting_id']}")
                print(f"      Initiator: {task['initiator']}")
                print(f"      Type: {task['task_type']}")
                print(f"      Duration: {task['duration_minutes']} minutes")
                print(f"      Round: {task['round_count']}")
                if task.get('initiator_slots'):
                    print(f"      Initiator time: {', '.join(task['initiator_slots'][:3])}")
                if task.get('suggested_slots'):
                    print(f"      Suggested time: {', '.join(task['suggested_slots'][:3])}")
                print(f"      Message: {task['message']}")
                print()
        else:
            print(f"  Query failed: {data.get('message', '')}")

    def submit_availability(self):
        """Submit available time"""
        if not self.current_token:
            print("  Please bind a user first (option 1)")
            return

        meeting_id = input("  Meeting ID: ").strip()
        if not meeting_id:
            print("  Meeting ID cannot be empty")
            return

        print("  Response type:")
        print("    1. INITIAL (first submission)")
        print("    2. COUNTER (resubmit)")
        print("    3. ACCEPT_PROPOSAL (accept proposal)")
        print("    4. REJECT (reject)")
        choice = input("  Choose (1-4, default 1): ").strip() or "1"

        type_map = {"1": "INITIAL", "2": "COUNTER", "3": "ACCEPT_PROPOSAL", "4": "REJECT"}
        response_type = type_map.get(choice, "INITIAL")

        submit_data = {"response_type": response_type}

        if response_type in ("INITIAL", "COUNTER"):
            print("  Enter available time slots (one per line, press Enter to finish):")
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
                print(f"    Using default times")

            submit_data["available_slots"] = slots
            preference = input("  Preference note (optional): ").strip()
            if preference:
                submit_data["preference_note"] = preference

        elif response_type == "REJECT":
            reason = input("  Rejection reason (optional): ").strip()
            if reason:
                submit_data["preference_note"] = reason

        code, data = self._post(f"/api/meetings/{meeting_id}/submit", submit_data)
        if code == 200 and data.get("code") == 200:
            print(f"  Submission successful")
            print(f"     Status: {data['data']['status']}")
            print(f"     All submitted: {data['data'].get('all_submitted', 'N/A')}")
        else:
            print(f"  Submission failed: {data.get('message', '')}")

    def view_meeting(self):
        """View meeting details"""
        if not self.current_token:
            print("  Please bind a user first (option 1)")
            return

        meeting_id = input("  Meeting ID: ").strip()
        if not meeting_id:
            print("  Meeting ID cannot be empty")
            return

        code, data = self._get(f"/api/meetings/{meeting_id}")
        if code == 200 and data.get("code") == 200:
            d = data["data"]
            print(f"\n  Meeting Details")
            print(f"  {'='*50}")
            print(f"  Title: {d['title']}")
            print(f"  Meeting ID: {d['meeting_id']}")
            print(f"  Status: {d['status']}")
            print(f"  Duration: {d.get('duration_minutes', 'N/A')} minutes")
            print(f"  Round: {d['round_count']}")
            print(f"  Final time: {d.get('final_time') or 'Not determined'}")
            print(f"  Coordination analysis: {d.get('coordinator_reasoning') or 'None'}")
            print(f"\n  Participants:")
            for p in d.get("participants", []):
                status = "Submitted" if p.get("has_submitted") else "Pending"
                print(f"    {p['email']} ({p['role']}) -- {status}")
                if p.get("latest_slots"):
                    print(f"      Time: {', '.join(p['latest_slots'][:3])}")
                if p.get("preference_note"):
                    print(f"      Preference: {p['preference_note'][:50]}")
        else:
            print(f"  Query failed: {data.get('message', '')}")

    def list_meetings(self):
        """View my meeting list"""
        if not self.current_token:
            print("  Please bind a user first (option 1)")
            return

        code, data = self._get("/api/meetings")
        if code == 200 and data.get("code") == 200:
            meetings = data["data"]["meetings"]
            if not meetings:
                print("  No meetings found")
                return

            print(f"\n  {data['data']['total']} meeting(s):\n")
            for m in meetings:
                status_icon = {"COLLECTING": ">>>", "ANALYZING": "[~]", "CONFIRMED": "[OK]", "FAILED": "[X]", "NEGOTIATING": "[<>]"}.get(m["status"], "[?]")
                print(f"  {status_icon} {m['title']}")
                print(f"     ID: {m['meeting_id']}")
                print(f"     Status: {m['status']}  Role: {m['my_role']}  Progress: {m['progress']}")
                print(f"     Initiator: {m['initiator_email']}  Duration: {m['duration_minutes']}min")
                if m.get("final_time"):
                    print(f"     Final time: {m['final_time']}")
                print()
        else:
            print(f"  Query failed: {data.get('message', '')}")

    def check_agent_status(self):
        """View Agent status"""
        # Agent pending tasks
        code, data = self._get("/api/agent/tasks/pending", use_token=False)
        if code == 200:
            tasks = data["data"]["pending_tasks"]
            print(f"  Agent pending: {len(tasks)} meeting(s)")
            for t in tasks:
                print(f"     {t['meeting_id']} -- {t['title']} (round {t['round_count']}/{t.get('max_rounds', 3)})")
        else:
            print(f"  Query failed: {data.get('message', '')}")

        # Agent health check
        try:
            agent_url = self.server_url.rsplit(":", 1)[0] + ":8001"
            r = requests.get(f"{agent_url}/health", timeout=3)
            if r.status_code == 200:
                print(f"  Agent service OK ({agent_url})")
            else:
                print(f"  Agent response abnormal: {r.status_code}")
        except:
            print(f"  Agent service not responding")

    def db_overview(self):
        """View database overview (via API)"""
        # User count
        code, data = self._post("/api/auth/bind", {"email": "__probe__@test.com"}, use_token=False)

        # Agent tasks
        code, data = self._get("/api/agent/tasks/pending", use_token=False)
        if code == 200:
            analyzing = len(data["data"]["pending_tasks"])
            print(f"  Meetings in ANALYZING status: {analyzing}")

        # Current user meetings
        if self.current_token:
            code, data = self._get("/api/meetings")
            if code == 200:
                meetings = data["data"]["meetings"]
                by_status = {}
                for m in meetings:
                    by_status[m["status"]] = by_status.get(m["status"], 0) + 1
                print(f"  My total meetings: {len(meetings)}")
                for status, count in sorted(by_status.items()):
                    print(f"    {status}: {count}")

    def run(self):
        """Main loop"""
        print(f"\n{'='*60}")
        print(f"  ClawMeeting Test Client")
        print(f"  Server: {self.server_url}")
        print(f"{'='*60}\n")

        if not self.health_check():
            print("\n  Cannot connect to server. Please check if the address is correct.")
            return

        while True:
            print(f"\n{'---'*20}")
            if self.current_email:
                print(f"  Current user: {self.current_email} (ID: {self.current_user_id})")
            else:
                print(f"  Current user: Not bound")
            print(f"{'---'*20}")
            print("  1. Register/bind user")
            print("  2. Create meeting")
            print("  3. View pending tasks")
            print("  4. Submit time/reject/accept")
            print("  5. View meeting details")
            print("  6. View my meeting list")
            print("  7. View Agent status")
            print("  8. Database overview")
            print("  0. Exit")
            print()

            choice = input("  Choose (0-8): ").strip()

            if choice == "0":
                print("\n  Goodbye!")
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
                print("  Invalid option")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ClawMeeting Test Client")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"Server address (default: {DEFAULT_SERVER})")
    args = parser.parse_args()

    client = TestClient(args.server)
    client.run()
