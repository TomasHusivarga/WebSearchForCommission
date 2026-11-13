from website_hunter.screening.business import extract_business
from website_hunter.screening.contacts import extract_contacts
from website_hunter.screening.html_signals import (
    detect_bot_challenge,
    detect_forum_site,
    detect_legacy_signals,
    detect_parked_page,
    detect_replacement_url,
    has_replacement_notice,
)


def test_detects_multiple_legacy_signals_without_claiming_a_build_year():
    html = """<html><head><title>Autoservis Novák</title></head><body>
      <font>Internet Explorer</font><font>Classic font</font><font>Legacy navigation</font>
      <p>Optimalizované pre rozlíšenie 800x600</p>
      <p>Posledná aktualizácia: 2008</p><p>Copyright 2005</p>
      <table></table><table></table><table></table><table></table><table></table>
    </body></html>"""
    names = {item["name"] for item in detect_legacy_signals(html)}
    assert {"missing_viewport_meta", "presentational_font_tags", "table_heavy_markup", "old_last_updated", "old_copyright", "internet_explorer_text", "legacy_resolution_notice"}.issubset(names)
    assert not any("build" in item["evidence"].lower() for item in detect_legacy_signals(html))


def test_does_not_call_current_copyright_or_update_years_legacy():
    html = "<html><head><meta name='viewport' content='width=device-width'></head><body>Copyright 2026. Last updated 2026</body></html>"
    names = {item["name"] for item in detect_legacy_signals(html)}
    assert "old_copyright" not in names
    assert "old_last_updated" not in names


def test_identifies_parked_domain_copy():
    assert detect_parked_page("", "This domain is for sale")
    assert not detect_parked_page("Autoservis", "Repairs and vehicle servicing in Trnava")


def test_identifies_websupport_domain_placeholder():
    assert detect_parked_page(
        "Registrácia domén, hosting a servery :: Websupport.sk",
        "Na tejto doméne zatiaľ nič nie je. Nothing to see here yet.",
    )


def test_detects_standalone_bot_challenges_without_flagging_a_normal_captcha_form():
    challenge = """<html><head><title>Just a moment...</title></head><body>
      <h1>Checking your browser before accessing example.sk</h1>
      <div id='cf-challenge'>Enable JavaScript and cookies to continue.</div>
    </body></html>"""
    ordinary_form = """<html><head><title>Book a visit</title></head><body>
      <h1>Book a visit with our clinic</h1><p>We use reCAPTCHA on this form.</p>
      <form><div class='g-recaptcha'></div></form>
    </body></html>"""

    assert detect_bot_challenge("Just a moment...", "Checking your browser before accessing example.sk", challenge)
    assert not detect_bot_challenge("Book a visit", "Book a visit with our clinic. We use reCAPTCHA on this form.", ordinary_form)


def test_detects_replacement_website_notice_and_external_destination():
    title = "Merienail"
    text = "PRESŤAHOVALI SME SA NA https://merienail.sk/ POZOR: Spustili sme nový web!"
    links = [{"href": "https://merienail.sk/", "label": "https://merienail.sk/"}]

    assert has_replacement_notice(title, text)
    assert detect_replacement_url(links, "https://merienail6.webnode.sk/", text) == "https://merienail.sk/"


def test_detects_forum_or_community_homepage_from_multiple_forum_cues():
    assert detect_forum_site(
        "Raspberry Pi česky: návody a projekty",
        "Fóra Raspberry Pi. Nejnovější příspěvky. Registrace. Přihlásit se. Fórum / Komunita.",
        "<html><body><a href='/forum'>Fóra / Komunita</a></body></html>",
        [{"href": "/forum", "label": "Fóra / Komunita"}],
    )
    assert not detect_forum_site(
        "Autoservis Kováč",
        "Opravy a servis vozidiel. Kontaktujte nás.",
        "<html><body><a href='/kontakt'>Kontakt</a></body></html>",
        [{"href": "/kontakt", "label": "Kontakt"}],
    )


def test_site_screener_records_replacement_url_from_homepage_notice():
    from website_hunter.screening.service import SiteScreener

    class FakeResponse:
        def __init__(self, url, status_code, body, content_type="text/html"):
            self.url = url
            self.status_code = status_code
            self.body = body.encode("utf-8")
            self.headers = {"content-type": content_type}
            self.truncated = False

    class FakeFetcher:
        def get(self, url, max_redirects=4):
            if url.endswith("/robots.txt"):
                return FakeResponse(url, 404, "", "text/plain")
            return FakeResponse(
                url,
                200,
                "<html><head><title>Merienail</title></head><body>Presťahovali sme sa. "
                "Spustili sme nový web. <a href='https://merienail.sk/'>https://merienail.sk/</a></body></html>",
            )

    result = SiteScreener(FakeFetcher()).inspect("https://merienail6.webnode.sk/")

    assert result.replacement_notice is True
    assert result.replacement_url == "https://merienail.sk/"


def test_extracts_published_business_fields_and_does_not_guess_missing_values():
    html = """<html><head><title>Servis Kováč</title><meta name='description' content='Opravy strojov v Trnave'></head>
      <body><h1>Servis Kováč</h1><p>IČO: 12345678</p><p>919 01 Trnava</p></body></html>"""
    result = extract_business(html, "https://serviskovac.sk")
    assert result.name == "Servis Kováč"
    assert result.ico == "12345678"
    assert result.location == "919 01 Trnava"
    assert result.sources == ["https://serviskovac.sk"]
    assert result.verified_fields == []


def test_extracts_public_contacts_and_rejects_developer_email():
    html = """<html><body><p>Kontakt: <a href='mailto:info@firma.sk'>info@firma.sk</a> +421 905 123 456</p>
      <footer>Webdesign: <a href='mailto:studio@agency.sk'>studio@agency.sk</a></footer></body></html>"""
    contacts = extract_contacts(html, "https://firma.sk/kontakt")
    values = {item.value for item in contacts}
    assert "info@firma.sk" in values
    assert not any("agency.sk" in value for value in values)
    assert any(item.kind == "phone" for item in contacts)
    assert all(item.source_url == "https://firma.sk/kontakt" for item in contacts)


def test_contact_extraction_decodes_entities_and_skips_malformed_mailto_links():
    html = """<html><body>
      <a href="mailto:">Email</a>
      <a href="mailto:&amp;#112;&amp;#114;&amp;#101;&amp;#100;&amp;#97;&amp;#106;&amp;#64;&amp;#116;&amp;#97;&amp;#110;&amp;#101;&amp;#120;&amp;#46;&amp;#115;&amp;#107;">Predaj</a>
      <a href="mailto:not-an-email">Invalid</a>
    </body></html>"""

    contacts = extract_contacts(html, "https://tanex.sk/")

    assert [(item.value, item.classification) for item in contacts] == [
        ("predaj@tanex.sk", "general_business_contact")
    ]


def test_contact_page_discovery_stays_on_the_candidate_host():
    from website_hunter.screening.contacts import contact_page_urls

    html = "<a href='/kontakt'>Kontakt</a><a href='https://elsewhere.sk/kontakt'>Kontakt</a><a href='mailto:info@firma.sk'>Email</a>"
    assert contact_page_urls(html, "https://firma.sk/", 4) == ["https://firma.sk/kontakt"]
