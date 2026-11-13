from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SiteCategory = Literal["VINTAGE", "DATED", "MODERN_POOR", "MODERN_OK", "UNCERTAIN"]
ReviewVerdict = Literal[
    "GOOD PROSPECT",
    "OUTDATED BUT NOT USEFUL",
    "NOT OUTDATED",
    "NOT A BUSINESS / IRRELEVANT",
    "UNCERTAIN",
    "DEAD / UNREACHABLE",
]


@dataclass(frozen=True)
class DiscoveredDomain:
    url: str
    source: str
    source_url: str | None = None
    business_hint: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScreeningResult:
    input_url: str
    final_url: str | None
    status_code: int | None
    reachable: bool
    title: str = ""
    description: str = ""
    content_type: str = ""
    text: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    emails: list[dict[str, str]] = field(default_factory=list)
    phones: list[dict[str, str]] = field(default_factory=list)
    legacy_signals: list[dict[str, str]] = field(default_factory=list)
    business_signals: list[str] = field(default_factory=list)
    parked: bool = False
    replacement_notice: bool = False
    replacement_url: str | None = None
    site_kind: str | None = None
    javascript_likely: bool = False
    robots_allowed: bool | None = None
    failure_reason: str | None = None
    discovery_hint: dict[str, Any] = field(default_factory=dict)
    html: str = field(default="", repr=False)
    raw_html: bytes = field(default=b"", repr=False)
    analysis_headers: dict[str, str] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("html", None)
        value.pop("raw_html", None)
        value.pop("analysis_headers", None)
        return value


@dataclass
class RenderResult:
    desktop_path: str | None = None
    mobile_path: str | None = None
    final_url: str | None = None
    horizontal_overflow: bool | None = None
    broken_images: int = 0
    trackers_before_consent: list[str] = field(default_factory=list)
    failed_load: bool = False
    browser_errors: list[str] = field(default_factory=list)
    uncertain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WebsiteClassification:
    category: SiteCategory
    evidence: list[str]
    mobile_experience: str
    redesign_candidate: bool | None
    sufficient_evidence: bool
    provider: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BusinessIdentity:
    name: str | None
    industry: str | None
    services: list[str]
    location: str | None
    ico: str | None
    address: str | None
    sources: list[str]
    verified_fields: list[str]
    inferred_fields: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContactRecord:
    value: str
    kind: Literal["email", "phone"]
    classification: str
    source_url: str
    observed_at: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
