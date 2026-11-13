from website_hunter.reporting import _csv_safe, render_csv, render_html, write_reports


def test_reports_escape_untrusted_page_data_embed_screenshots_and_protect_csv_formulas(tmp_path):
    data_root = tmp_path / "data"
    screenshot = data_root / "screenshots" / "mobile.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"fake-png-data")
    candidate = {
        "canonical_domain": "firma.sk",
        "input_url": "https://firma.sk/",
        "final_url": "https://firma.sk/",
        "business": {"name": "=HYPERLINK(\"https://bad.example\",\"Firma\")"},
        "contacts": [{"kind": "email", "value": "info@firma.sk"}],
        "screenshot_mobile": str(screenshot),
        "screenshot_desktop": None,
        "opportunity": {
            "lead_score": 48,
            "neglect_score": 40,
            "money_score": 20,
            "issues_detail": [{"label": "Missing title", "weight": 3, "evidence": "<script>alert(1)</script>"}],
            "strengths": [{"label": "Booking", "weight": 10, "evidence": "Online booking"}],
            "ai_analysis": {
                "summary": "The business has a clear service offer.",
                "visual_category": "DATED",
                "positives": [{"point": "Service list", "evidence": "Three services are shown."}],
                "improvements": [{"issue": "Mobile layout", "evidence": "Text overflows.", "recommendation": "Use responsive widths.", "possible_benefit": "Easier reading."}],
                "redesign_direction": "Keep the existing colors.",
                "analyzed_at": "2026-11-13T12:00:00+00:00",
            },
        },
    }

    csv_content = render_csv([candidate]).decode("utf-8-sig")
    html_content = render_html([candidate], data_root)
    html_path, csv_path, count = write_reports([candidate], data_root)

    assert count == 1
    assert csv_path.is_file() and html_path.is_file()
    assert "'=HYPERLINK" in csv_content
    assert _csv_safe("  =1+1") == "'  =1+1"
    assert "data:image/png;base64," in html_content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_content
    assert 'href="https://bad.example"' not in html_content


def test_report_generation_requires_at_least_one_ai_analyzed_candidate(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="Analyze at least one selected website"):
        write_reports([{"opportunity": {"lead_score": 75}}], tmp_path)
