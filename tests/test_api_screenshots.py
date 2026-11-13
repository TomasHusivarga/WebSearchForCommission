from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from website_hunter import api


@pytest.mark.parametrize(
    ("filename", "expected_type"),
    [("desktop.jpg", "image/jpeg"), ("desktop.png", "image/png")],
)
def test_screenshot_route_uses_the_file_image_type(monkeypatch, tmp_path, filename, expected_type):
    screenshot = tmp_path / filename
    screenshot.write_bytes(b"image")
    monkeypatch.setattr(api, "settings", SimpleNamespace(screenshot_dir=tmp_path))
    monkeypatch.setattr(api.repository, "get_candidate", lambda candidate_id: {"screenshot_desktop": str(screenshot)})

    response = api.screenshot(7, "desktop")

    assert response.media_type == expected_type


def test_screenshot_route_rejects_unrecognized_image_formats(monkeypatch, tmp_path):
    screenshot = tmp_path / "desktop.svg"
    screenshot.write_text("<svg></svg>", encoding="utf-8")
    monkeypatch.setattr(api, "settings", SimpleNamespace(screenshot_dir=tmp_path))
    monkeypatch.setattr(api.repository, "get_candidate", lambda candidate_id: {"screenshot_desktop": str(screenshot)})

    with pytest.raises(HTTPException, match="Screenshot format is not supported"):
        api.screenshot(7, "desktop")
