import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


LEGACY_PATTERNS = (
    ("legacy_resolution_notice", re.compile(r"optimalizovan.{0,20}rozl[ií][sš]en", re.I)),
    ("internet_explorer_text", re.compile(r"internet explorer|iba pre.{0,20}explorer", re.I)),
    ("old_last_updated", re.compile(r"(posledn.{0,15}aktualiz|last updated|updated on).{0,35}(19\d{2}|20\d{2})", re.I)),
    ("old_copyright", re.compile(r"(?:copyright|©|\(c\)).{0,40}(19\d{2}|20\d{2})", re.I)),
    ("frontpage_reference", re.compile(r"frontpage|frontpage extensions|_vti_", re.I)),
    ("dreamweaver_reference", re.compile(r"dreamweaver|mm_menu\.js", re.I)),
    ("frameset_markup", re.compile(r"<\s*frameset\b|<\s*frame\b", re.I)),
    ("fixed_wide_layout", re.compile(r"(?:width\s*[:=]\s*['\"]?)(?:9\d\d|[1-9]\d{3,})\s*px", re.I)),
)
PARKED_PATTERNS = re.compile(
    r"domain (?:is )?for sale|buy this domain|dom[eé]na (?:je )?na predaj|under construction|webhosting.{0,30}default|parked free|this site is parked|str[aá]nka sa pripravuje|na tejto dom[eé]ne zat[ií]m ni[cč] nie je|nothing to see here yet|registr[aá]cia dom[eé]n.{0,80}websupport",
    re.I,
)
REPLACEMENT_NOTICE_PATTERNS = re.compile(
    r"pres[tť]ahovali sme sa|presunuli sme sa|spustili sme nov[yý] (?:web|webstr[aá]nku|str[aá]nku)|"
    r"na[sš] nov[yý] web(?:ov[uú])? str[aá]nku|nov[aá] adresa.{0,40}(?:web|str[aá]n)|"
    r"we (?:have )?moved to (?:our )?new (?:website|site)|our new website|new website at",
    re.I,
)
FORUM_ENGINE_PATTERNS = re.compile(
    r"phpbb|vbulletin|xenforo|invision community|simple machines forum|bbpress|wpforo|discourse",
    re.I,
)
FORUM_LINK_PATTERNS = re.compile(r"forum|f[oó]ra|diskusi|community forum|komunita / f[oó]ra", re.I)
BOT_TITLE_PATTERNS = re.compile(r"just a moment|checking your browser|attention required|security verification", re.I)
BOT_CHALLENGE_PATTERNS = re.compile(
    r"checking your browser before accessing|verify(?:ing)? that you are human|"
    r"please verify you are human|enable javascript and cookies to continue|"
    r"performing security verification|complete the security check|browser integrity check",
    re.I,
)
BOT_MARKUP_PATTERNS = re.compile(r"cf-challenge|cf-browser-verification|__cf_chl|challenge-platform|captcha-container", re.I)
FORUM_ACTIVITY_PATTERNS = (
    re.compile(r"nejnov[eě]j[sš][ií] (?:p[rř][ií]sp[eě]vk|koment[aá][rř])|latest (?:posts|replies)", re.I),
    re.compile(r"(?:p[rř][ií]hl[aá]sit|p[rř]ihl[aá][sš]en[ií]|log ?in|sign in)", re.I),
    re.compile(r"registrace|register|sign up", re.I),
    re.compile(r"(?:vl[aá]kna|threads|replies|members|[cč]lenov[eé])", re.I),
)
BUSINESS_TERMS = re.compile(
    r"(?:s\s*r\s*o|s\.\s*r\.\s*o\.|a\s*s|i[cč]o|kontakt|služby|sluzby|výroba|vyroba|servis|ubytovanie|predaj|obchod|autorizovaný|zámočníctvo|stolárstvo)",
    re.I,
)


def detect_legacy_signals(html: str, soup: BeautifulSoup | None = None) -> list[dict[str, str]]:
    parsed = soup or BeautifulSoup(html, "html.parser")
    text = parsed.get_text(" ", strip=True)
    lower = html.lower()
    signals: dict[str, str] = {}
    for name, pattern in LEGACY_PATTERNS:
        match = pattern.search(html if name in {"frameset_markup", "fixed_wide_layout"} else text + " " + html)
        if match:
            year_match = re.search(r"(?:19|20)\d{2}", match.group(0))
            if name in {"old_last_updated", "old_copyright"} and year_match and int(year_match.group(0)) >= 2016:
                continue
            signals[name] = match.group(0)[:180]
    if parsed.find("meta", attrs={"name": re.compile("generator", re.I)}):
        generator = parsed.find("meta", attrs={"name": re.compile("generator", re.I)})
        content = (generator.get("content") or "").strip()
        if re.search(r"frontpage|dreamweaver|microsoft visual studio|nvu|composer", content, re.I):
            signals["legacy_generator_metadata"] = content[:180]
    table_count = len(parsed.find_all("table"))
    if table_count >= 5:
        signals["table_heavy_markup"] = f"{table_count} HTML tables on one page"
    fonts = len(parsed.find_all("font"))
    if fonts >= 3:
        signals["presentational_font_tags"] = f"{fonts} presentational font tags"
    if not parsed.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}):
        signals["missing_viewport_meta"] = "No viewport meta tag found"
    if len(parsed.find_all("a")) >= 3:
        image_links = [link for link in parsed.find_all("a") if link.find("img") and not link.get_text(" ", strip=True)]
        if len(image_links) >= 3 and len(image_links) / max(1, len(parsed.find_all("a"))) > 0.45:
            signals["image_based_navigation"] = f"{len(image_links)} image-only links"
    if re.search(r"<html[^>]*\s(?:bgcolor|text|link)=", lower):
        signals["obsolete_html_presentation"] = "HTML root element contains presentation attributes"
    return [{"name": name, "evidence": evidence} for name, evidence in signals.items()]


def detect_parked_page(title: str, text: str) -> bool:
    return bool(PARKED_PATTERNS.search(f"{title} {text[:5000]}"))


def detect_bot_challenge(title: str, text: str, html: str) -> bool:
    body = text[:5000]
    title_match = bool(BOT_TITLE_PATTERNS.search(title))
    challenge_match = bool(BOT_CHALLENGE_PATTERNS.search(body))
    markup_match = bool(BOT_MARKUP_PATTERNS.search(html[:12000]))
    return (title_match and challenge_match) or (challenge_match and markup_match and len(body) < 2500)


def has_replacement_notice(title: str, text: str) -> bool:
    return bool(REPLACEMENT_NOTICE_PATTERNS.search(f"{title} {text[:12000]}"))


def detect_replacement_url(links: list[dict[str, str]], current_url: str, text: str) -> str | None:
    current_host = (urlsplit(current_url).hostname or "").lower().removeprefix("www.")
    normalized_text = re.sub(r"\s+", " ", text).casefold()
    for link in links:
        href = str(link.get("href") or "").strip()
        label = str(link.get("label") or "").strip()
        absolute = urljoin(current_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            continue
        link_host = parts.hostname.lower().removeprefix("www.")
        if link_host == current_host:
            continue
        visible_target = label.casefold() in normalized_text or absolute.rstrip("/").casefold() in normalized_text
        descriptive_label = bool(re.search(r"nov[yý]|new|web|str[aá]n|site|adrese|address|moved|pres[tť]ah", label, re.I))
        if visible_target or descriptive_label:
            return absolute
    return None


def detect_forum_site(title: str, text: str, html: str, links: list[dict[str, str]]) -> bool:
    if FORUM_ENGINE_PATTERNS.search(html):
        return True
    link_context = " ".join(f"{item.get('href', '')} {item.get('label', '')}" for item in links)
    has_forum_link = bool(FORUM_LINK_PATTERNS.search(f"{title} {link_context}"))
    activity_matches = sum(bool(pattern.search(text)) for pattern in FORUM_ACTIVITY_PATTERNS)
    return has_forum_link and activity_matches >= 2


def detect_business_signals(title: str, text: str, soup: BeautifulSoup) -> list[str]:
    signals: list[str] = []
    if title.strip():
        signals.append("page_title")
    if BUSINESS_TERMS.search(text[:12000]):
        signals.append("business_terms")
    if soup.find("a", href=re.compile(r"^(?:mailto:|tel:)", re.I)):
        signals.append("direct_contact_link")
    if any(re.search(r"kontakt|contact|o-nas|about", anchor.get_text(" ", strip=True), re.I) for anchor in soup.find_all("a")):
        signals.append("contact_or_about_navigation")
    return signals
