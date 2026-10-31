"""Расписание Герцена: подгруппа I, общие занятия и аттестации → ICS."""
import hashlib
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://guide.herzen.spb.ru/schedule/24666/by-dates"
START_DATE = date(2026, 10, 30)
END_DATE = date(2027, 8, 31)
OUTPUT = "herzen_schedule.ics"
TZ = ZoneInfo("Europe/Moscow")


def txt(node):
    return " ".join(node.stripped_strings) if node else ""


def cls(node, name):
    return name in (node.get("class") or [])


def ics(value):
    return (str(value).replace("\\", "\\\\").replace("\r\n", "\n")
            .replace("\r", "\n").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;"))


def dt_utc(value):
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def direct_cards(column):
    """Возвращает (запись, категория) без вложенных повторов."""
    card = column.find("div", class_=lambda c: c and "rounded-lg" in c.split())
    if card:
        label = card.find("div", class_="mb-3", recursive=False)
        label_text = txt(label).lower()
        if "подгруппа" in label_text:
            # На сайте используются римские символы Ⅰ и Ⅱ.
            if not any(x in label_text for x in ("ⅰ", " i", " 1")):
                return []
            category = "Подгруппа I"
        else:
            category = "Общее занятие"
        entries = card.find_all("div", class_=lambda c: c and "p-3" in c.split(), recursive=False)
        return [(entry, category) for entry in entries]

    # Общие занятия / зачёты / консультации / экзамены.
    entries = column.find_all("div", class_=lambda c: c and "py-2" in c.split(), recursive=False)
    return [(entry, "Общее событие") for entry in entries]


def parse_entry(entry, category, day, start, end):
    subject_box = entry.find("div", class_=lambda c: c and "text-base" in c.split())
    if not subject_box:
        return None
    subject_node = subject_box.find(["a", "span"], class_=lambda c: c and "font-bold" in c.split())
    subject = txt(subject_node) or txt(subject_box)
    if not subject:
        return None

    details_box = entry.find("div", class_=lambda c: c and "flex-col" in c.split())
    detail_items = []
    if details_box:
        detail_items = [txt(x) for x in details_box.find_all(
            "div", class_=lambda c: c and "inline-flex" in c.split(), recursive=False)]
    lesson_type = detail_items[0] if len(detail_items) > 0 else ""
    teacher = detail_items[1] if len(detail_items) > 1 else ""
    room = detail_items[2] if len(detail_items) > 2 else ""
    if not lesson_type and category == "Общее событие":
        lesson_type = "занятие"

    note = ""
    for child in entry.find_all("div", recursive=False):
        if "Примечание" in txt(child):
            note = re.sub(r"^Примечание\s*:\s*", "", txt(child))
            break

    description = "\n".join(filter(None, [
        "Тип: " + lesson_type if lesson_type else "",
        "Преподаватель: " + teacher if teacher else "",
        "Аудитория: " + room if room else "",
        category,
        "Примечание: " + note if note else "",
        "Источник: " + URL,
    ]))
    prefix = (lesson_type.capitalize() + ": ") if lesson_type.lower() in {
        "зачёт", "зачет", "экзамен", "консультация"} else ""
    return {"day": day, "start": start, "end": end,
            "subject": prefix + subject, "description": description,
            "kind": lesson_type, "teacher": teacher}


def extract_events(html):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    skipped = 0
    day_count = 0
    for day_tag in soup.find_all("time"):
        day_text = txt(day_tag)
        match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*,|$)", day_text)
        if not match:
            continue
        d, m, y = map(int, match.groups())
        try:
            day = date(y, m, d)
        except ValueError:
            continue
        if not START_DATE <= day <= END_DATE:
            continue
        container = day_tag.parent
        ol = container.find("ol", recursive=False)
        if not ol:
            continue
        day_count += 1
        for li in ol.find_all("li", recursive=False):
            row = li.find("div", recursive=False)
            if not row:
                continue
            columns = row.find_all("div", recursive=False)
            if len(columns) < 2:
                continue
            tm = re.search(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})", txt(columns[0]))
            if not tm:
                skipped += 1
                continue
            h1, m1, h2, m2 = map(int, tm.groups())
            start = datetime(y, m, d, h1, m1, tzinfo=TZ)
            end = datetime(y, m, d, h2, m2, tzinfo=TZ)
            for column in columns[1:]:
                for entry, category in direct_cards(column):
                    event = parse_entry(entry, category, day, start, end)
                    if event:
                        events.append(event)
                    else:
                        skipped += 1
    unique = {}
    for event in events:
        key = (event["start"], event["end"], event["subject"], event["teacher"])
        unique[key] = event
    return sorted(unique.values(), key=lambda x: (x["start"], x["subject"])), skipped, day_count


def build_ics(events):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Herzen Schedule//RU",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:" + ics("Расписание Герцена")]
    earliest = {}
    for event in events:
        day = event["day"]
        if day not in earliest or event["start"] < earliest[day]["start"]:
            earliest[day] = event

    for event in events:
        uid_src = "|".join((event["start"].isoformat(), event["subject"], event["teacher"]))
        uid = hashlib.sha256(uid_src.encode("utf-8")).hexdigest() + "@herzen-calendar"
        lines.extend(["BEGIN:VEVENT", "UID:" + uid, "DTSTAMP:" + stamp,
                      "DTSTART:" + dt_utc(event["start"]),
                      "DTEND:" + dt_utc(event["end"]),
                      "SUMMARY:" + ics(event["subject"]),
                      "DESCRIPTION:" + ics(event["description"])])
        # Одно напоминание за день — только для первого события.
        if earliest[event["day"]] is event:
            lines.extend(["BEGIN:VALARM", "TRIGGER:-PT1H30M", "ACTION:DISPLAY",
                          "DESCRIPTION:" + ics("Скоро: " + event["subject"]), "END:VALARM"])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    # Длинные строки ICS переносим по байтовому лимиту RFC 5545.
    folded = []
    for line in lines:
        part = ""
        for ch in line:
            if len((part + ch).encode("utf-8")) > 73:
                folded.append(part)
                part = " " + ch
            else:
                part += ch
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"


def main():
    saved = Path("herzen_by_dates.html")
    if "--offline" in sys.argv:
        if not saved.exists():
            raise SystemExit("Нет файла herzen_by_dates.html для проверки")
        html = saved.read_bytes()
        print("Использую сохранённую страницу расписания")
    else:
        print("Загружаю расписание по датам...")
        response = requests.get(URL, timeout=(20, 90))
        response.raise_for_status()
        html = response.content
    events, skipped, days = extract_events(html)
    print("Дней в расписании:", days)
    print("Найдено событий:", len(events))
    print("Не удалось разобрать записей:", skipped)
    if not events:
        raise SystemExit("Событий нет. Файл календаря не перезаписан.")
    if days < 10:
        raise SystemExit("Подозрительно мало дней. Файл календаря не перезаписан.")
    output = Path(OUTPUT)
    temp = output.with_suffix(".ics.tmp")
    temp.write_bytes(build_ics(events).encode("utf-8"))
    temp.replace(output)
    print("Файл создан:", output)
    print("Первое событие:", events[0]["start"].strftime("%d.%m.%Y %H:%M"), events[0]["subject"])
    print("Последнее событие:", events[-1]["start"].strftime("%d.%m.%Y %H:%M"), events[-1]["subject"])


if __name__ == "__main__":
    main()
