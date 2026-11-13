import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

JQ_PATH = re.compile(r"jquery[/-](\d)\.(\d{1,2})(?:\.(\d{1,2}))?(?!\d)", re.I)
JQ_VER = re.compile(r"(?<![\w.-])jquery(?:\.min)?\.js\?ver=(\d)\.(\d{1,2})(?:\.(\d{1,2}))?(?!\d)", re.I)
COPY_AFTER = re.compile(r"(?:©|\(c\)|copyright)\s*(?:(?:19|20)\d{2}\s*[-–—]\s*)?((?:19|20)\d{2})", re.I)
COPY_BEFORE = re.compile(r"((?:19|20)\d{2})\s*(?:©|\(c\))", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ICO = re.compile(r"\bIČO?\s*[:.]?\s*((?:\d\s?){8})(?!\d)", re.I)
DOCTYPE = re.compile(r"<!doctype\s+([^>]*)>", re.I)
META_CHARSET = re.compile(r"<meta[^>]+charset\s*=\s*[\"']?\s*([\w-]+)", re.I)
PHP = re.compile(r"PHP/(\d+)\.(\d+)", re.I)
RE_GA4 = re.compile(r"\bG-[A-Z0-9]{8,12}\b")
RE_UA = re.compile(r"\bUA-\d{4,10}-\d{1,3}\b")
RE_GTM = re.compile(r"googletagmanager\.com/gtm\.js|\bGTM-[A-Z0-9]{5,8}\b")
RE_ADS = re.compile(r"\bAW-\d{8,12}\b|googleadservices\.com|google_conversion_id")
RE_SKLIK = re.compile(r"c\.seznam\.cz|c\.imedia\.cz|seznam_retargeting_id|seznam_cId")
RE_FB = re.compile(r"connect\.facebook\.net/[\w_]+/fbevents\.js|fbq\(\s*['\"]init")
CONTACT_LINK = re.compile(r"kontakt|contact", re.I)

LEGACY_TAGS = ("font", "center", "marquee", "frameset", "frame")
SECTION_TAGS = ("header", "nav", "main", "section", "article", "footer")
BAD_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
BAD_EMAIL_DOMAINS = ("example.com", "sentry.io", "wixpress.com")


def _bare(host):
    host = (host or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def parse_soup(raw, header_charset=None):
    """Return (soup, html_text) for raw response bytes."""
    soup = BeautifulSoup(raw, "html.parser", from_encoding=header_charset or None)
    try:
        html = raw.decode(soup.original_encoding or "utf-8", errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")
    return soup, html


def jquery_version(src):
    """Return [major, minor(, patch)] for a jQuery core script URL, else None."""
    m = JQ_PATH.search(src) or JQ_VER.search(src)
    return [int(x) for x in m.groups() if x is not None] if m else None


def copyright_year(text, current_year):
    years = [int(y) for y in COPY_AFTER.findall(text) + COPY_BEFORE.findall(text)]
    years = [y for y in years if 1995 <= y <= current_year + 1]
    return max(years) if years else None


def decode_cfemail(hexstr):
    try:
        data = bytes.fromhex(hexstr)
    except ValueError:
        return None
    if len(data) < 2:
        return None
    return bytes(b ^ data[0] for b in data[1:]).decode("utf-8", errors="replace")


def find_emails(soup, text):
    """Return candidate e-mails from one page, strongest source first."""
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            found += [unquote(x) for x in href[7:].split("?")[0].split(",")]
        elif "/cdn-cgi/l/email-protection#" in href:
            found.append(decode_cfemail(href.split("#", 1)[1]))
    for el in soup.find_all(attrs={"data-cfemail": True}):
        found.append(decode_cfemail(el["data-cfemail"]))
    found += EMAIL.findall(text)
    return [x for x in found if x]


def clean_emails(candidates, limit=5):
    out = []
    for c in candidates:
        e = c.strip().strip(".").lower()
        if not EMAIL.fullmatch(e) or e.endswith(BAD_EMAIL_SUFFIXES):
            continue
        if e.split("@", 1)[1] in BAD_EMAIL_DOMAINS or e in out:
            continue
        out.append(e)
    return out[:limit]


def find_contact_link(soup):
    """Return the raw href of the first link that looks like a contact page, else None."""
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        if CONTACT_LINK.search(href + " " + a.get_text(" ", strip=True)):
            return href
    return None


def extract_facts(raw, header_charset, headers, lists, site_host, final_url, current_year,
                  contact_raw=None, osm_emails=()):
    """All facts about one homepage. `headers` has lower-case keys. `lists` is config [lists]."""
    soup, html = parse_soup(raw, header_charset)
    low = html.lower()
    text = soup.get_text(" ", strip=True)
    f = {}

    f["has_viewport"] = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}) is not None
    m = DOCTYPE.search(html[:2000])
    if not m:
        f["doctype"] = "missing"
    elif re.search(r"xhtml|html 4|html 3|dtd", m.group(1), re.I):
        f["doctype"] = "legacy"
    else:
        f["doctype"] = "html5"
    mc = META_CHARSET.search(html[:5000])
    f["declared_charset"] = (mc.group(1) if mc else (header_charset or "")).lower()
    f["nested_tables"] = len(soup.select("table table"))
    legacy = {t: len(soup.find_all(t)) for t in LEGACY_TAGS}
    legacy["bgcolor"] = len(soup.find_all(attrs={"bgcolor": True}))
    legacy["swf"] = low.count(".swf")
    f["legacy_tags"] = {k: v for k, v in legacy.items() if v}

    versions = [v for v in (jquery_version(t["src"]) for t in soup.find_all("script", src=True)) if v]
    f["jquery_version"] = min(versions) if versions else None
    f["generators"] = [(t.get("content") or "").strip()
                       for t in soup.find_all("meta", attrs={"name": re.compile(r"^generator$", re.I)})]
    gens = " | ".join(f["generators"]).lower()
    if "wix.com" in gens:
        f["platform"] = "wix"
    elif "webnode" in gens:
        f["platform"] = "webnode"
    elif "wordpress" in gens or "/wp-content/" in low:
        f["platform"] = "wordpress"
    elif "joomla" in gens:
        f["platform"] = "joomla"
    elif "drupal" in gens:
        f["platform"] = "drupal"
    else:
        f["platform"] = None

    f["copyright_year"] = copyright_year(text, current_year)
    d = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    f["has_meta_description"] = bool(d and (d.get("content") or "").strip())
    f["title"] = (soup.title.get_text(" ", strip=True) if soup.title else "")[:200]
    f["has_og"] = soup.find("meta", attrs={"property": re.compile(r"^og:title$", re.I)}) is not None
    f["has_structured_data"] = (
        soup.find("script", attrs={"type": re.compile(r"ld\+json", re.I)}) is not None
        or soup.find(attrs={"itemscope": True}) is not None
    )
    mp = PHP.search(headers.get("x-powered-by", ""))
    f["php_version"] = [int(mp.group(1)), int(mp.group(2))] if mp else None
    f["html5_sections"] = sum(len(soup.find_all(t)) for t in SECTION_TAGS)

    f["ga4"] = bool(RE_GA4.search(html))
    f["ua"] = bool(RE_UA.search(html))
    f["gtm"] = bool(RE_GTM.search(html))
    f["google_ads"] = bool(RE_ADS.search(html))
    f["sklik"] = bool(RE_SKLIK.search(html))
    f["fb_pixel"] = bool(RE_FB.search(html))
    f["has_consent_tool"] = any(w in low for w in lists["consent_words"])
    f["booking"] = [w for w in lists["booking_words"] if w in low]
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    f["social_links"] = [h for h in ("facebook.com", "instagram.com") if any(h in x for x in hrefs)]

    candidates = list(osm_emails) + find_emails(soup, text)
    mi = ICO.search(text)
    if contact_raw:
        csoup, _ = parse_soup(contact_raw)
        ctext = csoup.get_text(" ", strip=True)
        candidates += find_emails(csoup, ctext)
        mi = mi or ICO.search(ctext)
    f["emails"] = clean_emails(candidates)
    f["ico"] = re.sub(r"\s", "", mi.group(1)) if mi else None

    f["text_len"] = len(text)
    f["is_parked"] = len(text) < 600 and any(w in low for w in lists["parked_words"])
    f["redirected_offsite"] = _bare(urlsplit(final_url).hostname) != _bare(site_host)
    return f
