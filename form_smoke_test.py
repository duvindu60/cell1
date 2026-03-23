import os
import re
import sys
import time
from urllib.parse import quote, unquote

import requests


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5001").rstrip("/")
LOGIN_MOBILE = os.getenv("LOGIN_MOBILE", "").strip()
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "").strip()

RUN_FLAG_MEMBER = os.getenv("RUN_FLAG_MEMBER", "1") == "1"
RUN_ATTENDANCE = os.getenv("RUN_ATTENDANCE", "1") == "1"
RUN_DELETE_REQUEST = os.getenv("RUN_DELETE_REQUEST", "0") == "1"  # optional

session = requests.Session()
session.headers.update({"User-Agent": "form-smoke-tester/1.0"})


FORM_TAG_RE = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
MEMBER_LINK_PHONE_RE = re.compile(
    r'href="(/member/([^"]+))"[^>]*>.*?<div class="member-info">([^<]*)</div>',
    re.IGNORECASE | re.DOTALL,
)
ATTENDANCE_LINK_RE = re.compile(r'href="(/attendance/([^"]+))"', re.IGNORECASE)
MEMBER_ID_IN_ATTENDANCE_RE = re.compile(r'data-member-id="([^"]+)"', re.IGNORECASE)


def build_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return BASE_URL + path


def assert_or_fail(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def get(path: str, **kwargs) -> requests.Response:
    return session.get(build_url(path), timeout=30, **kwargs)


def post(path: str, **kwargs) -> requests.Response:
    return session.post(build_url(path), timeout=30, **kwargs)


def login() -> None:
    print("1) Logging in...")
    resp = post(
        "/login",
        data={"mobile": LOGIN_MOBILE, "password": LOGIN_PASSWORD},
        allow_redirects=True,
    )
    assert_or_fail(resp.status_code == 200, f"Login failed: HTTP {resp.status_code}")

    who = get("/api/user")
    assert_or_fail(who.status_code == 200, f"/api/user failed: HTTP {who.status_code}")
    data = who.json()
    assert_or_fail("user" in data, "No user in /api/user response")
    print(f"   Logged in as: {data['user'].get('name', 'unknown')}")


def discover_forms() -> None:
    print("2) Discovering forms...")
    pages = ["/", "/members", "/member/form", "/attendance-list"]
    discovered = []

    for path in pages:
        resp = get(path, allow_redirects=True)
        if resp.status_code != 200:
            print(f"   - {path}: HTTP {resp.status_code} (skipped)")
            continue

        tags = FORM_TAG_RE.findall(resp.text)
        for tag in tags:
            attrs = dict((k.lower(), v) for k, v in ATTR_RE.findall(tag))
            discovered.append(
                {
                    "page": path,
                    "id": attrs.get("id", ""),
                    "method": attrs.get("method", "GET").upper(),
                    "action": attrs.get("action", ""),
                }
            )

    print(f"   Found {len(discovered)} forms")
    for i, f in enumerate(discovered, start=1):
        print(
            f"   [{i}] page={f['page']} id={f['id'] or '-'} "
            f"method={f['method']} action={f['action'] or '-'}"
        )


def create_member() -> tuple[str, str]:
    print("3) Creating member (DB write)...")
    member_name = "Smoke Test Member"
    phone = str(int(time.time()))[-10:]  # 10 digits

    payload = {
        "name": member_name,
        "age": "28",
        "gender": "Male",
        "phone_number": phone,
        "zone_id": "",
        "sector_number": "",
        "district": "Smoke District",
        "province": "Smoke Province",
        "country": "",
        "branch_id": "",
        "cell_category": "youth",
        "church": "true",
        "potential_leader": "false",
    }

    resp = post("/add_member", data=payload, allow_redirects=True)
    assert_or_fail(resp.status_code == 200, f"Add member failed: HTTP {resp.status_code}")

    members_page = get("/members")
    assert_or_fail(members_page.status_code == 200, f"/members failed: HTTP {members_page.status_code}")

    member_id = None
    for _path, extracted_member_id, member_phone in MEMBER_LINK_PHONE_RE.findall(members_page.text):
        if (member_phone or "").strip() == phone:
            member_id = extracted_member_id
            break

    assert_or_fail(member_id is not None, "Created member not found on /members")
    print(f"   Member created: id={member_id}, phone={phone}")
    return member_id, phone


def update_member(member_id: str, phone: str) -> None:
    print("4) Updating member (DB write)...")
    payload = {
        "name": "Smoke Test Member Updated",
        "age": "29",
        "gender": "Male",
        "phone_number": phone,
        "zone_id": "",
        "sector_number": "",
        "district": "Updated District",
        "province": "Updated Province",
        "country": "",
        "branch_id": "",
        "cell_category": "young adult",
        "church": "true",
        "potential_leader": "true",
    }
    resp = post(f"/update_member/{member_id}", data=payload, allow_redirects=True)
    assert_or_fail(resp.status_code == 200, f"Update member failed: HTTP {resp.status_code}")

    details = get(f"/member/{member_id}")
    assert_or_fail(details.status_code == 200, f"/member/{member_id} failed: HTTP {details.status_code}")
    assert_or_fail(
        ("Smoke Test Member Updated" in details.text) or ("Updated District" in details.text),
        "Update verification text not found on member details page",
    )
    print("   Member update verified")


def flag_member(member_id: str) -> None:
    print("5) Flagging member (DB write)...")
    payload = {
        "issue_type": "attendance",
        "description": "Automated smoke test flag for connectivity verification.",
    }
    resp = post(f"/flag_member/{member_id}", data=payload, allow_redirects=True)
    assert_or_fail(resp.status_code == 200, f"Flag member failed: HTTP {resp.status_code}")
    print("   Flag submitted")


def submit_attendance(preferred_member_id: str) -> None:
    print("6) Submitting attendance (DB write)...")
    listing = get("/attendance-list")
    assert_or_fail(listing.status_code == 200, f"/attendance-list failed: HTTP {listing.status_code}")

    links = ATTENDANCE_LINK_RE.findall(listing.text)
    assert_or_fail(len(links) > 0, "No /attendance/<meeting_date> links found")

    _, meeting_segment = links[0]
    meeting_display = unquote(meeting_segment)

    detail = get(f"/attendance/{meeting_segment}")
    assert_or_fail(detail.status_code == 200, f"/attendance/{meeting_segment} failed: HTTP {detail.status_code}")

    member_ids = list(dict.fromkeys(MEMBER_ID_IN_ATTENDANCE_RE.findall(detail.text)))
    assert_or_fail(len(member_ids) > 0, "No member IDs found on attendance detail page")

    attendance_payload = []
    for member_id in member_ids:
        status = "present" if member_id == preferred_member_id else "absent"
        attendance_payload.append({"member_id": member_id, "status": status})

    encoded_meeting = quote(meeting_display, safe="")
    resp = post(f"/bulk_update_attendance/{encoded_meeting}", json={"attendance": attendance_payload})
    assert_or_fail(
        resp.status_code in (200, 400, 403),
        f"Unexpected attendance response: HTTP {resp.status_code}",
    )

    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code == 200:
        assert_or_fail(data.get("success") is True, f"Attendance API returned failure: {data}")
        print(f"   Attendance success: {data.get('message', 'ok')}")
    else:
        print(f"   Attendance skipped by business rules: HTTP {resp.status_code} -> {data}")


def request_delete_member(member_id: str) -> None:
    print("7) Requesting member delete (DB write)...")
    resp = post(f"/request_delete_member/{member_id}", allow_redirects=True)
    assert_or_fail(resp.status_code == 200, f"Delete request failed: HTTP {resp.status_code}")
    print("   Delete request submitted")


def main() -> None:
    if not LOGIN_MOBILE or not LOGIN_PASSWORD:
        raise RuntimeError("Set LOGIN_MOBILE and LOGIN_PASSWORD environment variables")

    print(f"BASE_URL={BASE_URL}")
    login()
    discover_forms()
    member_id, phone = create_member()
    update_member(member_id, phone)

    if RUN_FLAG_MEMBER:
        flag_member(member_id)

    if RUN_ATTENDANCE:
        submit_attendance(member_id)

    if RUN_DELETE_REQUEST:
        request_delete_member(member_id)

    print("\nSmoke test completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
