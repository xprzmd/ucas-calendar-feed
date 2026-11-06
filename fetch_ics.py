#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetch the UCAS (Qingxin) timetable and write an iCalendar feed.

Credentials come from SCHOOL_ACCOUNT / SCHOOL_PASSWORD. The password is never
written to disk or to the generated feed.
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://iclass.ucas.edu.cn:8181/app/"
VERIFY = ("http://iclass.ucas.edu.cn:88/ve/webservices/mobileCheck.shtml"
          "?method=mobileLogin&username=${0}&password=${1}&lx=${2}")
UA = "student_5.0.1.2_android_12_20_100000000000000_110000"
TZID = "Asia/Shanghai"
TZ = dt.timezone(dt.timedelta(hours=8))


def enc(value):
    return urllib.parse.quote(str(value), safe="-._~")


def post(path, form, session=None):
    body = "&".join("%s=%s" % (enc(k), enc(v)) for k, v in form).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
    req.add_header("User-Agent", UA)
    req.add_header("Cache-Control", "no-store")
    if session:
        req.add_header("sessionId", session)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login(account, password):
    payload = post("user/login.action", [
        ("phone", account.strip()), ("password", password),
        ("verificationType", "1"), ("verificationUrl", VERIFY), ("userLevel", "1"),
    ])
    result = payload.get("result")
    if str(payload.get("STATUS")) != "0" or not isinstance(result, dict):
        raise RuntimeError("登录失败：%s" % (payload.get("ERRMSG") or payload.get("ERRCODE") or "请检查账号密码"))
    user_id = str(result.get("id") or "").strip()
    session = str(result.get("sessionId") or "").strip()
    if not user_id or not session:
        raise RuntimeError("登录响应缺少身份信息")
    return user_id, session


def normalize_day(value):
    return re.sub(r"[-/]", "", str(value or "").strip())


def parse_course(row, day):
    name = str(row.get("courseName") or "").strip() or "未命名课程"
    teacher = str(row.get("teacherName") or "").strip()
    location = ""
    for key in ("classRoomName", "classroom", "classRoom", "address", "place", "room"):
        value = str(row.get(key) or "").strip()
        if value:
            location = value
            break
    course = {
        "schedule_id": str(row.get("id") or "").strip(),
        "uuid": str(row.get("uuid") or "").strip(),
        "name": name,
        "teacher": teacher,
        "location": location,
        "day": day,
        "begin": str(row.get("classBeginTime") or "").strip(),
        "end": str(row.get("classEndTime") or "").strip(),
    }
    return course


def fetch_day(user_id, session, day):
    payload = post("course/get_stu_course_sched.action",
                   [("id", user_id), ("dateStr", day)], session)
    rows = payload.get("result")
    if str(payload.get("STATUS")) == "0" and isinstance(rows, list):
        return [parse_course(row, day) for row in rows]
    return []


def fetch_week(user_id, session, day):
    """Returns {day: [courses]}; may be empty if the server has no week data."""
    payload = post("course/get_stu_course_sched_week.action",
                   [("id", user_id), ("dateStr", day)], session)
    result = payload.get("result")
    if str(payload.get("STATUS")) != "0" or not isinstance(result, list):
        return {}
    days = {}
    for item in result:
        if not isinstance(item, dict):
            continue
        key = normalize_day(item.get("dateStr"))
        rows = item.get("schedData")
        if key and isinstance(rows, list):
            days[key] = [parse_course(row, key) for row in rows]
    return days


def merge(found, day, rows):
    bucket = found.setdefault(day, {})
    for course in rows:
        key = (course["schedule_id"], course["uuid"], course["begin"], course["name"])
        bucket.setdefault(key, course)


def collect(user_id, session, start, days):
    found = {}
    cursor = start
    stop = start + dt.timedelta(days=days)
    requests = 0
    while cursor <= stop and requests < 400:
        key = cursor.strftime("%Y%m%d")
        week = {}
        try:
            week = fetch_week(user_id, session, key)
            requests += 1
        except (urllib.error.URLError, ValueError, KeyError, TypeError):
            week = {}
        if week:
            for day, rows in week.items():
                merge(found, day, rows)
            last = max(week)
            try:
                last_date = dt.datetime.strptime(last, "%Y%m%d").date()
            except ValueError:
                last_date = cursor
            # Some servers ignore dateStr and return the current week; never
            # let the cursor stand still.
            cursor = max(last_date, cursor) + dt.timedelta(days=1)
            continue
        try:
            rows = fetch_day(user_id, session, key)
            requests += 1
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError("登录已失效，请检查账号密码")
            raise
        merge(found, key, rows)
        cursor += dt.timedelta(days=1)
    return found


TIME_WITH_DATE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})")
TIME_ONLY = re.compile(r"^(\d{1,2}):(\d{2})")


def event_times(course):
    day = course["day"]
    try:
        base = dt.datetime.strptime(day, "%Y%m%d")
    except ValueError:
        return None
    begin = None
    match = TIME_WITH_DATE.search(course["begin"])
    if match:
        begin = dt.datetime(*[int(part) for part in match.groups()])
    else:
        match = TIME_ONLY.search(course["begin"])
        if match:
            begin = base.replace(hour=int(match.group(1)), minute=int(match.group(2)))
    if begin is None:
        return None
    end = None
    match = TIME_WITH_DATE.search(course["end"])
    if match:
        end = dt.datetime(*[int(part) for part in match.groups()])
    else:
        match = TIME_ONLY.search(course["end"])
        if match:
            end = begin.replace(hour=int(match.group(1)), minute=int(match.group(2)))
    if end is None or end <= begin:
        end = begin + dt.timedelta(minutes=45)
    return begin, end


def escape(value):
    return (str(value).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r", "").replace("\n", "\\n"))


def fold(line):
    """RFC 5545 folding by octets, never splitting a UTF-8 character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts = []
    chunk = b""
    limit = 75
    for char in line:
        data = char.encode("utf-8")
        if len(chunk) + len(data) > limit:
            parts.append(chunk.decode("utf-8"))
            chunk = data
            limit = 74  # continuation lines start with a space
        else:
            chunk += data
    parts.append(chunk.decode("utf-8"))
    return "\r\n ".join(parts)


def vevent(course, alarm_minutes):
    times = event_times(course)
    if times is None:
        print("跳过时间无法解析的课程：%s %s" % (course["day"], course["name"]), file=sys.stderr)
        return None
    begin, end = times
    stamp = f"{begin:%Y%m%d}T{begin:%H%M%S}"
    finish = f"{end:%Y%m%d}T{end:%H%M%S}"
    identity = "|".join([course["day"], course["schedule_id"], course["uuid"], course["begin"], course["name"]])
    uid = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]
    description = "教师：%s" % course["teacher"] if course["teacher"] else ""
    lines = [
        "BEGIN:VEVENT",
        "UID:%s@ucas-guoke" % uid,
        "DTSTAMP:%s" % dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "DTSTART;TZID=%s:%s" % (TZID, stamp),
        "DTEND;TZID=%s:%s" % (TZID, finish),
        "SUMMARY:%s" % escape(course["name"]),
    ]
    if course["location"]:
        lines.append("LOCATION:%s" % escape(course["location"]))
    if description:
        lines.append("DESCRIPTION:%s" % escape(description))
    lines += ["STATUS:CONFIRMED", "TRANSP:OPAQUE"]
    if alarm_minutes > 0:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:%s" % escape(course["name"]),
            "TRIGGER:-PT%dM" % alarm_minutes,
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def calendar(courses, name, alarm_minutes, days):
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//guoke-calendar//UCAS timetable//ZH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:%s" % escape(name),
        "X-WR-TIMEZONE:%s" % TZID,
        "X-WR-CALDESC:%s" % escape("学校课表自动同步，共 %d 天。" % days),
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "BEGIN:VTIMEZONE",
        "TZID:%s" % TZID,
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    body = []
    for course in courses:
        lines = vevent(course, alarm_minutes)
        if lines:
            body.extend(lines)
    tail = ["END:VCALENDAR", ""]
    lines = header + body + tail
    return "\r\n".join(fold(line) for line in lines)


def demo_courses():
    today = dt.datetime.now(TZ).date()
    rows = []
    for offset in range(0, 14):
        day = today + dt.timedelta(days=offset)
        rows.append({"schedule_id": "1000001", "uuid": "", "name": "学术英语",
                     "teacher": "示例教师", "location": "教一-101", "day": day.strftime("%Y%m%d"),
                     "begin": "08:30", "end": "10:10"})
        rows.append({"schedule_id": "1000002", "uuid": "", "name": "科学研究方法",
                     "teacher": "示例教师", "location": "教二-203", "day": day.strftime("%Y%m%d"),
                     "begin": "13:30", "end": "15:10"})
    return rows


def main():
    out = os.environ.get("OUT", "public/calendar.ics")
    days = int(os.environ.get("FEED_DAYS", "180"))
    alarm = int(os.environ.get("ALARM_MINUTES", "10"))
    name = os.environ.get("CAL_NAME", "果壳课表")
    demo = "--demo" in sys.argv

    if demo:
        courses = demo_courses()
    else:
        account = os.environ.get("SCHOOL_ACCOUNT", "").strip()
        password = os.environ.get("SCHOOL_PASSWORD", "")
        if not account or not password:
            print("缺少 SCHOOL_ACCOUNT / SCHOOL_PASSWORD 环境变量。", file=sys.stderr)
            return 2
        try:
            user_id, session = login(account, password)
            start = dt.datetime.now(TZ).date()
            found = collect(user_id, session, start, days)
        except urllib.error.URLError as error:
            print("无法连接学校服务：%s" % error.reason, file=sys.stderr)
            return 1
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 1
        courses = []
        start_key = start.strftime("%Y%m%d")
        for day in sorted(found):
            if day < start_key:
                continue
            courses.extend(found[day].values())
        courses.sort(key=lambda course: (course["day"], course["begin"], course["name"]))
        if not courses:
            print("未获取到任何课程，保留上一次发布的订阅内容。", file=sys.stderr)
            return 3

    text = calendar(courses, name, alarm, days)
    path = os.path.abspath(out)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    print("已写入 %s（%d 门课程）" % (path, len(courses)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
