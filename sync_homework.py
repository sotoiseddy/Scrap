from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, datetime, timezone
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client


DEFAULT_URL = "https://mcsuna.in/ParentApp/HomeWork"
PORTAL_HOME_URL = "https://mcsuna.in/ParentApp/Home"
DETAIL_PATH_RE = re.compile(
    r"/ParentApp/HomeWork/Details/([^/?#]+)", re.IGNORECASE
)
REQUEST_DELAY_SECONDS = 0.5
DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)
FIELD_LABELS = {
    "title": {"title", "topic", "homework title"},
    "subject": {"subject", "subject name"},
    "teacher": {"teacher", "staff", "assigned by", "teacher name"},
    "assigned_date": {"assigned date", "homework date", "given date", "date"},
    "due_date": {"due date", "submission date", "last date"},
    "description": {"description", "homework", "homework details", "details"},
}
RECORD_FIELDS = (
    "homework_id",
    "title",
    "subject",
    "teacher",
    "assigned_date",
    "due_date",
    "description",
    "details_text",
    "attachment_urls",
    "source_url",
    "last_synced_at",
)


def _add_cookies(session: requests.Session, cookie_header: str) -> bool:
    cookies = SimpleCookie()
    try:
        cookies.load(cookie_header)
    except CookieError:
        return False
    if not cookies:
        return False
    for name, morsel in cookies.items():
        session.cookies.set(
            name, morsel.value, domain="mcsuna.in", path="/", secure=True
        )
    return True


def _looks_like_login(response: requests.Response, soup: BeautifulSoup) -> bool:
    path = urlparse(response.url).path.lower()
    password_input = soup.find(
        "input", attrs={"type": lambda value: value and value.lower() == "password"}
    )
    return "/login" in path or password_input is not None


def _fetch_html(
    session: requests.Session, url: str
) -> tuple[requests.Response, BeautifulSoup]:
    response = session.get(url, timeout=20)
    response.raise_for_status()
    final_url = urlparse(response.url)
    if final_url.scheme != "https" or final_url.hostname != "mcsuna.in":
        raise ValueError("Portal redirected outside mcsuna.in.")
    soup = BeautifulSoup(response.text, "html.parser")
    if _looks_like_login(response, soup):
        raise PermissionError("Portal returned a login page; the session may have expired.")
    if "html" not in response.headers.get("Content-Type", "").lower():
        raise ValueError("Portal response was not an HTML page.")
    return response, soup


def _clean_label(text: str) -> str:
    return re.sub(r"[\s:：]+", " ", text).strip().lower()


def _labelled_fields(soup: BeautifulSoup) -> dict[str, str]:
    values: dict[str, str] = {}

    def add(label_text: str, value: str) -> None:
        label = _clean_label(label_text)
        value = value.strip()
        if value:
            for field, names in FIELD_LABELS.items():
                if label in names:
                    values.setdefault(field, value)

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            add(cells[0].get_text(" ", strip=True), cells[1].get_text("\n", strip=True))

    for term in soup.find_all("dt"):
        definition = term.find_next_sibling("dd")
        if definition:
            add(term.get_text(" ", strip=True), definition.get_text("\n", strip=True))

    for element in soup.find_all(["strong", "b", "label"]):
        label_text = element.get_text(" ", strip=True)
        following = element.next_sibling
        if hasattr(following, "get_text"):
            value = following.get_text("\n", strip=True)
        else:
            value = str(following or "").strip(" \t\r\n:：")
        if not value and element.parent:
            value = re.sub(
                rf"^{re.escape(label_text)}\s*[:：]?\s*",
                "",
                element.parent.get_text(" ", strip=True),
                flags=re.IGNORECASE,
            ).strip()
        add(label_text, value)
    return values


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        pass
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return value


def _extract_record(
    homework_id: str,
    source_url: str,
    detail_soup: BeautifulSoup,
    list_card: Any = None,
) -> dict[str, Any]:
    detail_text = detail_soup.get_text(separator="\n", strip=True)
    if not detail_text:
        raise ValueError("Detail page contained no readable text.")

    fields = _labelled_fields(detail_soup)
    detail_root = detail_soup.select_one(".row.white-radius-sec")
    if detail_root:
        detail_box = detail_root.select_one(".details-box-wrp")
        if detail_box:
            label_to_field = {
                "title": "title",
                "date": "assigned_date",
                "due date": "due_date",
            }
            for row in detail_box.find_all("div", class_="row", recursive=False):
                label_column = row.select_one(".col-lg-3.col-md-6.col-4")
                value_column = (
                    label_column.find_next_sibling("div")
                    if label_column
                    else None
                )
                if not label_column or not value_column:
                    continue
                label = _clean_label(label_column.get_text(" ", strip=True))
                field = label_to_field.get(label)
                value = value_column.get_text(" ", strip=True)
                if field and value:
                    fields[field] = value

        description = detail_root.select_one(".message-box-wrp pre")
        if description:
            fields["description"] = description.get_text("\n", strip=True)

        subject = detail_root.select_one(".icn-cvr-1")
        teacher = detail_root.select_one(".icn-cvr-2")
        if subject:
            fields["subject"] = subject.get_text(" ", strip=True)
        if teacher:
            fields["teacher"] = teacher.get_text(" ", strip=True)

    if list_card:
        card_metadata: dict[str, str] = {}
        created = list_card.select_one(".created-wrp")
        if created:
            paragraphs = created.find_all("p", recursive=False)
            if paragraphs:
                card_metadata["title"] = paragraphs[0].get_text(" ", strip=True)
            for paragraph in paragraphs[1:]:
                text = paragraph.get_text(" ", strip=True)
                if text.lower().startswith("faculty:"):
                    card_metadata["teacher"] = text.split(":", 1)[1].strip()

        for paragraph in list_card.find_all("p", recursive=False):
            text = paragraph.get_text(" ", strip=True)
            match = re.match(r"^\s*(created date|due date)\s*:\s*(.+)$", text, re.I)
            if match:
                field = (
                    "assigned_date"
                    if match.group(1).lower() == "created date"
                    else "due_date"
                )
                card_metadata[field] = match.group(2).strip()

        for field, value in card_metadata.items():
            if value:
                fields.setdefault(field, value)

    attachments: list[str] = []
    for anchor in detail_soup.find_all("a", href=True):
        url = urljoin(source_url, str(anchor["href"]))
        path = urlparse(url).path.lower()
        link_text = anchor.get_text(" ", strip=True).lower()
        if (
            any(path.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".xls", ".xlsx"))
            or "attach" in link_text
            or "download" in link_text
        ):
            if url not in attachments:
                attachments.append(url)

    return {
        "homework_id": homework_id,
        "title": fields.get("title"),
        "subject": fields.get("subject"),
        "teacher": fields.get("teacher"),
        "assigned_date": _normalize_date(fields.get("assigned_date")),
        "due_date": _normalize_date(fields.get("due_date")),
        "description": fields.get("description"),
        "details_text": detail_text,
        "attachment_urls": attachments,
        "source_url": source_url,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }


def _get_supabase_client():
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_key:
        raise ValueError(
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the local .env file."
        )
    if urlparse(supabase_url).scheme != "https":
        raise ValueError("SUPABASE_URL must use HTTPS.")
    return create_client(supabase_url, service_key)


def _existing_homework_records(
    client: Any, homework_ids: list[str]
) -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    for start in range(0, len(homework_ids), 500):
        response = (
            client.table("homework")
            .select(", ".join(RECORD_FIELDS))
            .in_("homework_id", homework_ids[start : start + 500])
            .execute()
        )
        existing.update(
            (row["homework_id"], row) for row in (response.data or [])
        )
    return existing


def _merge_existing_record(
    record: dict[str, Any], existing: dict[str, Any] | None
) -> dict[str, Any]:
    merged = dict(record)
    if existing:
        for field in (
            "title",
            "subject",
            "teacher",
            "assigned_date",
            "due_date",
            "description",
            "details_text",
            "source_url",
        ):
            if merged.get(field) in (None, "") and existing.get(field):
                merged[field] = existing[field]
    return merged


def _save_record(client: Any, record: dict[str, Any]) -> None:
    payload = {field: record.get(field) for field in RECORD_FIELDS}
    client.table("homework").upsert(
        payload,
        on_conflict="homework_id",
    ).execute()


def main() -> int:
    load_dotenv()
    cookie = os.getenv("PORTAL_COOKIE", "").strip()
    if not cookie:
        print("Set PORTAL_COOKIE in your local .env file first.", file=sys.stderr)
        return 2

    homework_url = os.getenv("HOMEWORK_URL", DEFAULT_URL).strip() or DEFAULT_URL
    parsed_url = urlparse(homework_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "mcsuna.in":
        print("HOMEWORK_URL must be an HTTPS URL on mcsuna.in.", file=sys.stderr)
        return 2

    with requests.Session() as session:
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        if not _add_cookies(session, cookie):
            print(
                "PORTAL_COOKIE could not be parsed. Check that it is one line of "
                "name=value pairs separated by semicolons.",
                file=sys.stderr,
            )
            return 2

        try:
            client = _get_supabase_client()
        except Exception as exc:
            print(f"Could not configure Supabase: {exc}", file=sys.stderr)
            return 2

        try:
            _fetch_html(session, PORTAL_HOME_URL)
            _, list_soup = _fetch_html(session, homework_url)
        except PermissionError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except (requests.RequestException, ValueError) as exc:
            print(f"Could not fetch the portal page: {exc}", file=sys.stderr)
            return 1

        homework_links = list_soup.select("a.home-wrk-box-wrp")
        if not homework_links:
            print(
                "The page loaded, but it contains no links matching "
                "'a.home-wrk-box-wrp'. No file was saved.",
                file=sys.stderr,
            )
            return 1

        list_text = list_soup.get_text(separator="\n", strip=True)
        sections = [
            "HOMEWORK LIST",
            f"Source: {homework_url}",
            "",
            list_text,
        ]
        detail_urls: dict[str, str] = {}
        list_cards: dict[str, Any] = {}
        for link in homework_links:
            href = link.get("href")
            if not href:
                continue
            detail_url = urljoin(homework_url, str(href))
            parsed_detail = urlparse(detail_url)
            match = DETAIL_PATH_RE.fullmatch(parsed_detail.path)
            if (
                parsed_detail.scheme == "https"
                and parsed_detail.hostname == "mcsuna.in"
                and match
            ):
                homework_id = match.group(1)
                detail_urls.setdefault(homework_id, detail_url)
                list_cards.setdefault(homework_id, link)

        if not detail_urls:
            print(
                "Found homework cards, but none had a recognized detail URL. "
                "No file was saved.",
                file=sys.stderr,
            )
            return 1

        try:
            existing_records = _existing_homework_records(
                client, list(detail_urls)
            )
        except Exception as exc:
            print(f"Could not check existing homework in Supabase: {exc}", file=sys.stderr)
            return 1

        upsert_successes = 0
        inserted = 0
        updated = 0
        database_failures: list[str] = []
        failed_ids: list[str] = []
        for index, (homework_id, detail_url) in enumerate(detail_urls.items()):
            if index:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                detail_response, detail_soup = _fetch_html(session, detail_url)
                actual_id = DETAIL_PATH_RE.fullmatch(
                    urlparse(detail_response.url).path
                )
                if not actual_id or actual_id.group(1) != homework_id:
                    raise ValueError("Detail request redirected to a different assignment.")
                record = _extract_record(
                    homework_id,
                    detail_response.url,
                    detail_soup,
                    list_cards.get(homework_id),
                )
                if not record.get("title") or _clean_label(record["title"]) in {
                    "homework details",
                    "home work details",
                }:
                    raise ValueError(
                        "Could not extract an assignment-specific title from its detail page."
                    )
                record = _merge_existing_record(
                    record, existing_records.get(homework_id)
                )
                sections.extend(
                    [
                        "",
                        "=" * 72,
                        f"HOMEWORK ID: {homework_id}",
                        f"Source: {detail_response.url}",
                        "",
                        record["details_text"],
                    ]
                )
                try:
                    _save_record(client, record)
                    upsert_successes += 1
                    if homework_id in existing_records:
                        updated += 1
                        print(f"Updated homework {homework_id} in Supabase.")
                    else:
                        inserted += 1
                        print(f"Inserted homework {homework_id} in Supabase.")
                except Exception as exc:
                    database_failures.append(homework_id)
                    print(
                        f"Could not save homework {homework_id} to Supabase: {exc}",
                        file=sys.stderr,
                    )
            except PermissionError as exc:
                print(
                    f"Stopped at homework {homework_id}: {exc}",
                    file=sys.stderr,
                )
                failed_ids.append(homework_id)
                break
            except (requests.RequestException, ValueError) as exc:
                print(f"Could not fetch homework {homework_id}: {exc}", file=sys.stderr)
                failed_ids.append(homework_id)

        if failed_ids:
            sections.extend(
                [
                    "",
                    "DETAILS NOT FETCHED",
                    ", ".join(failed_ids),
                ]
            )
        print(
            f"Sync summary: discovered={len(detail_urls)}, "
            f"successfully_upserted={upsert_successes}, "
            f"inserted={inserted}, updated={updated}, detail_failed={len(failed_ids)}, "
            f"database_failed={len(database_failures)}."
        )

    output_path = Path(__file__).resolve().parent / "homework_page.txt"
    try:
        output_path.write_text("\n".join(sections) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Could not write the output file: {exc}", file=sys.stderr)
        return 1

    print(f"Saved the homework page text to: {output_path}")
    print(f"Found {len(detail_urls)} assignments; {len(failed_ids)} detail page(s) failed.")
    print("The output may contain private student information; do not share it publicly.")
    return 1 if failed_ids or database_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
