import html as html_lib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from website_hunter.contracts import ContactRecord


EMAIL_PATTERN = re.compile(r"(?i)(?<![\w.+-])[\w.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+421[\s().-]*|00421[\s().-]*|0)(?:[1-9][\d\s().-]{7,13})(?!\w)")
REJECT_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "postmaster", "abuse", "webmaster"}
DEVELOPER_CONTEXT = re.compile(r"web(?:site)?\s*(?:design|development)|tvorba\s+(?:webu|str[aá]nok)|vytvoril|created\s+by|design\s+by|realiz[aá]cia\s+webu", re.I)


def extract_contacts(html: str, page_url: str) -> list[ContactRecord]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    link_matches: dict[str, str] = {}
    for link in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        address = link["href"].split(":", 1)[1].split("?", 1)[0].strip().lower()
        for _ in range(3):
            decoded = html_lib.unescape(address)
            if decoded == address:
                break
            address = decoded
        context = link.parent.get_text(" ", strip=True)[:300] if link.parent else ""
        if EMAIL_PATTERN.fullmatch(address):
            link_matches[address] = context
    email_values = set(match.lower().rstrip(".,;:)") for match in EMAIL_PATTERN.findall(text)) | set(link_matches)
    contacts: list[ContactRecord] = []
    observed = datetime.now(timezone.utc).isoformat()
    host = (urlsplit(page_url).hostname or "").lower().removeprefix("www.")
    for address in sorted(email_values):
        local, domain = address.rsplit("@", 1)
        if local.split("+", 1)[0] in REJECT_LOCAL_PARTS:
            continue
        context = link_matches.get(address, "")
        if not context:
            at = text.lower().find(address)
            context = text[max(0, at - 120) : at + len(address) + 120] if at >= 0 else ""
        if DEVELOPER_CONTEXT.search(context):
            continue
        if domain == host or local.lower() in {"info", "kontakt", "obchod", "office", "sales", "recepcia"}:
            contact_class = "general_business_contact"
        elif re.match(r"^[a-z][a-z0-9._-]*$", local, re.I):
            contact_class = "named_or_external_business_contact"
        else:
            contact_class = "uncertain"
        contacts.append(ContactRecord(address, "email", contact_class, page_url, observed, context))
    for phone in sorted(set(re.sub(r"\s+", " ", item).strip(" .,-") for item in PHONE_PATTERN.findall(text))):
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 9 or len(digits) > 13:
            continue
        contacts.append(ContactRecord(phone, "phone", "public_phone", page_url, observed, phone))
    return contacts


def contact_page_urls(html: str, base_url: str, limit: int) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    host = (urlsplit(base_url).hostname or "").lower().removeprefix("www.")
    selected: list[str] = []
    for link in soup.find_all("a", href=True):
        label = f"{link.get_text(' ', strip=True)} {link['href']}".lower()
        if not re.search(r"kontakt|contact|o-nas|o_nas|about|sluzby|služby", label):
            continue
        url = urljoin(base_url, link["href"])
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or (parts.hostname or "").lower().removeprefix("www.") != host:
            continue
        if url.split("#", 1)[0] != base_url.split("#", 1)[0] and url not in selected:
            selected.append(url)
        if len(selected) >= limit:
            break
    return selected
