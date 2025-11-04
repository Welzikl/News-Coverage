#!/usr/bin/env python3
"""Daily PR coverage digest via FreshRSS Google Reader API."""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import smtplib
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element, SubElement

import requests
from dateutil import tz
from dotenv import load_dotenv
from zoneinfo import ZoneInfo


load_dotenv()


@dataclass
class Client:
    name: str
    aliases: List[str]
    context_any: List[str]


CLIENTS: List[Client] = [
    Client("4PB", ["4PB", "4 Paper Buildings", "Four Paper Buildings"], ["barristers", "family", "chambers", "court", "law"]),
    Client("Bolt Burdon Kemp", ["Bolt Burdon Kemp", "BBK"], ["law", "solicitors", "firm", "claims", "clinical negligence", "PI"]),
    Client("Cooke Young & Keidan", ["Cooke Young & Keidan", "CYK"], ["law", "litigation", "disputes", "London"]),
    Client("FOIL", ["FOIL", "Forum of Insurance Lawyers"], ["insurance", "law", "solicitors", "claims"]),
    Client("London Market FOIL", ["London Market FOIL"], ["insurance", "London Market", "law"]),
    Client("LSLA", ["LSLA", "London Solicitors Litigation Association"], ["litigation", "solicitors", "law"]),
    Client("Nottingham Law School", ["Nottingham Law School", "NLS"], ["Nottingham", "students", "legal", "university", "Trent"]),
    Client("Oury Clark", ["Oury Clark", "OuryClark"], ["law", "accounting", "solicitors", "firm"]),
    Client("Alto Claritas", ["Alto Claritas"], ["legal", "law", "solicitors"]),
    Client("SA Law", ["SA Law", "SALaw"], ["law", "solicitors", "St Albans", "Watford"]),
    Client("Wilsons", ["Wilsons Solicitors", "Wilsons LLP", "Wilsons (Salisbury)", "Wilsons"], ["law", "solicitors", "firm", "Salisbury"]),
]

BLOCKLIST_PHRASES: List[str] = []

POSITIVE_WORDS = {
    "wins",
    "award",
    "growth",
    "record",
    "approves",
    "success",
    "surge",
    "raises",
    "backs",
    "confirms",
    "expands",
    "appoints",
}

NEGATIVE_WORDS = {
    "fraud",
    "scandal",
    "probe",
    "lawsuit",
    "ban",
    "cuts",
    "warning",
    "fall",
    "drop",
    "decline",
    "sacked",
    "fined",
    "charged",
    "collapse",
    "sanction",
    "risk",
}


@dataclass
class DigestItem:
    client: Client
    title: str
    url: str
    source: str
    published_at: datetime
    sentiment: str


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def getenv_str(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def load_blocklist() -> List[str]:
    blocklist_env = os.getenv("BLOCKLIST_PHRASES", "")
    phrases = BLOCKLIST_PHRASES.copy()
    env_phrases = [p.strip() for p in blocklist_env.split(",") if p.strip()]
    phrases.extend(env_phrases)
    # Remove duplicates while preserving order
    seen: set[str] = set()
    unique_phrases: List[str] = []
    for phrase in phrases:
        lower = phrase.lower()
        if lower in seen:
            continue
        seen.add(lower)
        unique_phrases.append(phrase)
    return unique_phrases


def parse_bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def simple_sentiment(title: str) -> str:
    text = title.lower()
    if any(word in text for word in POSITIVE_WORDS):
        return "positive"
    if any(word in text for word in NEGATIVE_WORDS):
        return "negative"
    return "neutral"


def choose_url(item: dict) -> Optional[str]:
    for key in ("canonical", "alternate"):
        if key in item and item[key]:
            href = item[key][0].get("href")
            if href:
                return href.strip()
    link = item.get("link")
    if link:
        return str(link).strip()
    return None


def canonicalize_url(url: str) -> str:
    return url.strip()


def resolve_source(item: dict, url: str) -> str:
    origin = item.get("origin", {})
    title = origin.get("title")
    if title:
        return str(title)
    parsed = urlparse(url)
    return parsed.netloc or "Unknown Source"


def convert_published(published: int, tzinfo: ZoneInfo) -> datetime:
    # FreshRSS publishes in Unix seconds.
    dt = datetime.fromtimestamp(published, tz=timezone.utc)
    return dt.astimezone(tzinfo)


def matches_client(item_text: str, client: Client) -> bool:
    lowered = item_text.lower()
    alias_match = any(alias.lower() in lowered for alias in client.aliases)
    if not alias_match:
        return False
    if not client.context_any:
        return True
    return any(context.lower() in lowered for context in client.context_any)


def filter_by_label(items: Iterable[dict], label: Optional[str]) -> Iterable[dict]:
    if not label:
        return items
    normalized = label
    if not normalized.startswith("user/-/label/"):
        normalized = f"user/-/label/{normalized}"
    for item in items:
        categories = item.get("categories", []) or []
        if normalized in categories:
            yield item


def fetch_items(base_url: str, username: str, api_password: str, max_items: int, oldest_ts: int, label: Optional[str]) -> List[dict]:
    api_url = base_url.rstrip("/") + "/api/greader.php/reader/api/0/stream/contents/reading-list"
    params = {"n": max_items, "ot": oldest_ts}
    response = requests.get(api_url, params=params, auth=(username, api_password), timeout=30)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        print(f"FreshRSS API error: {exc}")
        sys.exit(1)

    payload = response.json()
    items = payload.get("items", [])
    if label:
        items = list(filter_by_label(items, label))
    return items


def build_digest_items(raw_items: List[dict], tzinfo: ZoneInfo, blocklist: List[str]) -> Dict[str, List[DigestItem]]:
    seen_hashes: set[str] = set()
    digest_map: Dict[str, List[DigestItem]] = defaultdict(list)

    for raw in raw_items:
        try:
            title = str(raw.get("title", "")).strip()
            url = choose_url(raw)
            if not title or not url:
                continue
            canonical_url = canonicalize_url(url)
            url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
            if url_hash in seen_hashes:
                continue
            seen_hashes.add(url_hash)

            lower_title = title.lower()
            if any(phrase.lower() in lower_title for phrase in blocklist):
                continue

            source = resolve_source(raw, canonical_url)
            text_for_match = f"{title} {source}".lower()

            published_ts = raw.get("published") or raw.get("updated")
            if isinstance(published_ts, (int, float)):
                published = convert_published(int(published_ts), tzinfo)
            else:
                published = datetime.now(tz=tzinfo)

            for client in CLIENTS:
                if matches_client(text_for_match, client):
                    digest_map[client.name].append(
                        DigestItem(
                            client=client,
                            title=title,
                            url=canonical_url,
                            source=source,
                            published_at=published,
                            sentiment=simple_sentiment(title),
                        )
                    )
                    break
        except Exception as exc:  # pragma: no cover - defensive
            print(f"Skipping item due to parse error: {exc}")
            continue

    for items in digest_map.values():
        items.sort(key=lambda item: item.published_at, reverse=True)
    return digest_map


def format_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def build_email_html(digest_items: Dict[str, List[DigestItem]], report_date: datetime) -> str:
    title = report_date.strftime("Daily PR Coverage — %A, %d %B %Y")
    parts = [f"<h2>{html.escape(title)}</h2>"]

    any_items = False
    for client in CLIENTS:
        items = digest_items.get(client.name)
        if not items:
            continue
        any_items = True
        parts.append(f"<h3>{html.escape(client.name)}</h3>")
        parts.append("<ul>")
        for item in items:
            parts.append(
                "<li><strong>{source}</strong> · <em>{published}</em> · "
                "<span>{sentiment}</span><br>"
                "<a href=\"{url}\">{title}</a></li>".format(
                    source=html.escape(item.source),
                    published=html.escape(format_datetime(item.published_at)),
                    sentiment=html.escape(item.sentiment),
                    url=html.escape(item.url),
                    title=html.escape(item.title),
                )
            )
        parts.append("</ul>")

    if not any_items:
        parts.append("<p>No coverage found in the last 24 hours.</p>")

    return "\n".join(parts)


def send_email(html_body: str, subject_date: datetime, smtp_config: dict, from_email: str, to_emails: List[str]) -> None:
    subject = subject_date.strftime("Daily PR Coverage — %A, %d %B %Y")
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = ", ".join(to_emails)
    message.attach(MIMEText(html_body, "html"))

    host = smtp_config["host"]
    port = smtp_config["port"]
    username = smtp_config.get("username")
    password = smtp_config.get("password")
    use_tls = smtp_config.get("use_tls", True)

    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.sendmail(from_email, to_emails, message.as_string())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a daily PR coverage digest email.")
    parser.add_argument("--hours", type=float, help="Override lookback window in hours")
    parser.add_argument("--dry-run", action="store_true", help="Print the email HTML instead of sending")
    parser.add_argument(
        "--opml",
        metavar="PATH",
        help="Write the matched coverage to an OPML file for FreshRSS import",
    )
    return parser.parse_args()


def write_opml(digest_items: Dict[str, List[DigestItem]], report_date: datetime, output_path: str) -> None:
    title = report_date.strftime("Daily PR Coverage — %A, %d %B %Y")
    root = Element("opml", version="2.0")
    head = SubElement(root, "head")
    SubElement(head, "title").text = title
    SubElement(head, "dateCreated").text = report_date.isoformat()

    body = SubElement(root, "body")
    total_items = 0
    for client in CLIENTS:
        items = digest_items.get(client.name)
        if not items:
            continue
        client_outline = SubElement(body, "outline", text=client.name, title=client.name)
        for item in items:
            total_items += 1
            SubElement(
                client_outline,
                "outline",
                text=item.title,
                title=item.title,
                type="link",
                url=item.url,
                htmlUrl=item.url,
                created=item.published_at.isoformat(),
                sentiment=item.sentiment,
                source=item.source,
            )

    if total_items == 0:
        SubElement(
            body,
            "outline",
            text="No coverage found in the last 24 hours.",
            title="No coverage found in the last 24 hours.",
        )

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - Python < 3.9 fallback
        pass
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    args = parse_args()
    try:
        base_url = getenv_str("FRESHRSS_BASE_URL")
        username = getenv_str("FRESHRSS_USERNAME")
        api_password = getenv_str("FRESHRSS_API_PASSWORD")
        timezone_name = os.getenv("TIMEZONE", "Europe/London")
        lookback_hours = float(args.hours) if args.hours is not None else float(os.getenv("LOOKBACK_HOURS", "24"))
        max_items = int(os.getenv("MAX_ITEMS", "1000"))
        label = os.getenv("FRESHRSS_LABEL") or None

        from_email = getenv_str("FROM_EMAIL")
        to_emails_raw = getenv_str("TO_EMAILS")
        to_emails = [email.strip() for email in to_emails_raw.split(",") if email.strip()]
        if not to_emails:
            raise ConfigError("TO_EMAILS must contain at least one address")

        smtp_config = {
            "host": getenv_str("SMTP_HOST"),
            "port": int(os.getenv("SMTP_PORT", "587")),
            "username": os.getenv("SMTP_USERNAME"),
            "password": os.getenv("SMTP_PASSWORD"),
            "use_tls": parse_bool(os.getenv("SMTP_USE_TLS", "true"), True),
        }
    except ConfigError as exc:
        print(exc)
        sys.exit(1)

    try:
        tzinfo = ZoneInfo(timezone_name)
    except Exception:
        tzinfo = tz.gettz(timezone_name)  # Fallback for unusual names
        if tzinfo is None:
            print(f"Invalid TIMEZONE: {timezone_name}")
            sys.exit(1)

    now = datetime.now(tz=timezone.utc)
    oldest_ts = int((now - timedelta(hours=lookback_hours)).timestamp())

    blocklist = load_blocklist()

    raw_items = fetch_items(base_url, username, api_password, max_items, oldest_ts, label)
    digest_items = build_digest_items(raw_items, tzinfo, blocklist)
    report_date = datetime.now(tz=tzinfo)
    html_body = build_email_html(digest_items, report_date)

    if args.opml:
        try:
            write_opml(digest_items, report_date, args.opml)
        except Exception as exc:
            print(f"Failed to write OPML: {exc}")
            sys.exit(1)

    if args.dry_run:
        print(html_body)
        return

    try:
        send_email(html_body, report_date, smtp_config, from_email, to_emails)
    except Exception as exc:
        print(f"Failed to send email: {exc}")
        sys.exit(1)

    print("Sent.")


if __name__ == "__main__":
    main()
