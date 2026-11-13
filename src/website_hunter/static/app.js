const VERDICTS = ["GOOD PROSPECT", "OUTDATED BUT NOT USEFUL", "NOT OUTDATED", "NOT A BUSINESS / IRRELEVANT", "UNCERTAIN", "DEAD / UNREACHABLE"];
let candidateFilter = "all";
let candidateSort = "prospects";
let isBusy = false;
let pageSpeedConfigured = false;
let mockupConfigured = false;
let aiAnalysisConfigured = false;
const selectedCandidateIds = new Set();

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function number(value) {
  return new Intl.NumberFormat().format(value || 0);
}

function draftOutreach(candidate) {
  const business = candidate.business || {};
  const opportunity = candidate.opportunity || {};
  const strengths = opportunity.strengths || [];
  const issues = opportunity.issues_detail || [];
  const name = business.name || candidate.title || candidate.canonical_domain;
  const positive = strengths.find(item => item.key === "services")
    || strengths.find(item => item.key === "booking")
    || strengths.find(item => item.key === "public_email")
    || strengths.find(item => item.key === "social_links");
  const positiveLine = positive?.key === "services"
    ? `Zoznam služieb na vašej stránke dáva návštevníkom dobrý prehľad o vašej ponuke: ${positive.evidence}.`
    : positive?.key === "booking"
      ? "Je výborné, že návštevníkom umožňujete rezerváciu online."
      : positive?.key === "public_email"
        ? "Oceňujem, že návštevníci majú na stránke uvedený priamy kontakt."
        : positive?.key === "social_links"
          ? "Je užitočné, že návštevníci nájdu na stránke aj odkazy na vaše sociálne profily."
          : "";
  const issue = issues[0];
  const improvement = issue
    ? `Pri obnove webu by sa dalo ${issue.recommendation.charAt(0).toLowerCase()}${issue.recommendation.slice(1)} To by mohlo pomôcť takto: ${issue.potential_benefit.charAt(0).toLowerCase()}${issue.potential_benefit.slice(1)}`
    : "Pri obnove webu by sa dala zlepšiť orientácia na stránke a zobrazenie na mobiloch.";
  const body = [
    "Dobrý deň,",
    "",
    `z vlastnej iniciatívy som si pozrel web spoločnosti ${name}.`,
    positiveLine,
    issue ? `Všimol som si jednu konkrétnu príležitosť: ${issue.label.toLowerCase()}.` : "Napadlo mi niekoľko možností, ako web ďalej rozvíjať.",
    improvement,
    "",
    candidate.mockup?.status === "ready"
      ? "Pripravil som nezáväzný vizuálny koncept ako ukážku možného smerovania. Ak vás zaujme, rád vám ho prepošlem. Nemusíte mi vôbec odpovedať, ak to pre vás teraz nie je aktuálne."
      : "V prípade záujmu vám rád pripravím krátky vizuálny návrh novej podoby stránky. Nemusíte mi vôbec odpovedať, ak to pre vás teraz nie je aktuálne.",
    "",
    "S pozdravom,",
    "[Vaše meno]",
  ].filter(Boolean).join("\n");
  return { subject: `Nápad na zlepšenie webu ${name}`, body };
}

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status}).`);
  return payload;
}

function setAction(message, error = false) {
  const target = document.querySelector("#action-status");
  target.textContent = message;
  target.classList.toggle("error", error);
}

async function withBusy(button, action) {
  if (isBusy) return;
  isBusy = true;
  button.disabled = true;
  try {
    await action();
  } catch (error) {
    setAction(error.message, true);
  } finally {
    button.disabled = false;
    isBusy = false;
    await refreshAll();
  }
}

async function refreshStatus() {
  const status = await request("/api/status");
  pageSpeedConfigured = status.pagespeed_configured;
  mockupConfigured = status.mockup_configured;
  aiAnalysisConfigured = status.ai_configured;
  document.querySelector("#html-report-link").hidden = !status.reports_available;
  document.querySelector("#csv-report-link").hidden = !status.reports_available;
  document.querySelector("#health-status").textContent = "Local workspace ready";
  document.querySelector("#health-status").classList.add("ready");
  document.querySelector("#count-candidates").textContent = number(status.counts.candidates);
  document.querySelector("#count-reviewed").textContent = number(status.counts.reviews);
  document.querySelector("#count-inventory").textContent = number(status.inventory.domains);
  document.querySelector("#inventory-summary").textContent = status.inventory.domains ? `${number(status.inventory.domains)} domains imported` : "Official daily registry list";
  const town = document.querySelector("#town-select");
  if (!town.options.length) {
    town.innerHTML = status.towns.map(name => `<option>${escapeHtml(name)}</option>`).join("");
  }
  const categoryOptions = document.querySelector("#category-options");
  if (!categoryOptions.children.length) {
    categoryOptions.innerHTML = Object.entries(status.business_categories).map(([key, item]) => `<label><input type="checkbox" value="${escapeHtml(key)}" checked> ${escapeHtml(item.label)}</label>`).join("");
  }
  const aiNotice = document.querySelector("#ai-notice");
  aiNotice.hidden = status.ai_configured;
  aiNotice.textContent = `Fast scoring works without AI. Set OPENAI_API_KEY to analyze selected websites with their desktop and mobile screenshots${status.model ? ` (${escapeHtml(status.model)})` : ""}.`;
  updateAnalysisControls();
}

async function refreshCandidates() {
  const reviewed = candidateFilter === "unreviewed" ? "&reviewed=false" : "";
  const candidates = await request(`/api/candidates?limit=80&sort=${candidateSort}${reviewed}`);
  const list = document.querySelector("#candidate-list");
  const state = document.querySelector("#queue-state");
  list.replaceChildren();
  const pending = candidates.filter(candidate => ["queued", "processing"].includes(candidate.status)).length;
  state.textContent = `${number(candidates.length)} shown${pending ? ` · ${number(pending)} processing or queued` : ""}`;
  if (!candidates.length) {
    list.innerHTML = `<div class="queue-empty">No candidates yet. Start with a small discovery batch or add a website URL above.</div>`;
    updateAnalysisControls();
    return;
  }
  for (const candidate of candidates) list.append(buildCandidate(candidate));
  updateAnalysisControls();
}

function buildCandidate(candidate) {
  const template = document.querySelector("#candidate-template");
  const card = template.content.firstElementChild.cloneNode(true);
  const classification = candidate.classification || {};
  const category = classification.category || candidate.stage || "QUEUED";
  const badge = card.querySelector(".classification-badge");
  badge.textContent = category.replaceAll("_", " ");
  badge.classList.add(`badge-${String(category).toLowerCase()}`);
  const name = candidate.business?.name || candidate.title || candidate.canonical_domain;
  card.querySelector(".candidate-name").textContent = name;
  const website = candidate.final_url || candidate.input_url;
  const websiteLink = card.querySelector(".candidate-url");
  websiteLink.href = safeExternalUrl(website);
  websiteLink.textContent = website;
  const business = candidate.business || {};
  const meta = [business.industry, business.location, candidate.status, candidate.review?.verdict].filter(Boolean);
  card.querySelector(".candidate-meta").innerHTML = meta.map(value => `<span>${escapeHtml(value)}</span>`).join("");
  const select = card.querySelector(".analysis-select");
  const candidateLeadScore = (candidate.opportunity || {}).lead_score ?? (candidate.opportunity || {}).opportunity_score;
  select.checked = selectedCandidateIds.has(candidate.id);
  select.disabled = candidate.status !== "complete" || !Number.isFinite(candidateLeadScore);
  card.querySelector(".analysis-select-label").textContent = `Select ${candidate.canonical_domain} for AI analysis`;
  select.addEventListener("change", () => {
    if (select.checked) selectedCandidateIds.add(candidate.id);
    else selectedCandidateIds.delete(candidate.id);
    updateAnalysisControls();
  });
  const antiquity = candidate.antiquity || {};
  card.querySelector(".antiquity-label").textContent = antiquity.label || "Not visually assessed";
  card.querySelector(".antiquity-reasons").innerHTML = (antiquity.reasons || []).length
    ? antiquity.reasons.map(item => `<li>${escapeHtml(item)}</li>`).join("")
    : "<li>No antiquity evidence recorded yet.</li>";
  card.querySelector(".priority-reasons").innerHTML = (candidate.priority_reasons || []).length
    ? candidate.priority_reasons.map(item => `<li>${escapeHtml(item)}</li>`).join("")
    : "<li>No business or contact context recorded yet.</li>";
  setScreenshot(card.querySelector(".desktop-shot"), candidate, "desktop");
  setScreenshot(card.querySelector(".mobile-shot"), candidate, "mobile");
  const evidence = classification.evidence || [];
  card.querySelector(".visual-evidence").innerHTML = evidence.length ? evidence.map(item => `<li>${escapeHtml(item)}</li>`).join("") : `<li>${escapeHtml(candidate.last_error || "Visual evidence is not available yet.")}</li>`;
  card.querySelector(".mobile-observation").textContent = classification.mobile_experience || (candidate.render?.horizontal_overflow === true ? "Mobile page has horizontal overflow." : "Mobile experience not assessed yet.");
  const legacy = candidate.legacy_signals || [];
  card.querySelector(".legacy-list").innerHTML = legacy.length ? legacy.map(item => `<li><strong>${escapeHtml(item.name.replaceAll("_", " "))}:</strong> ${escapeHtml(item.evidence)}</li>`).join("") : "<li>No legacy HTML signals detected.</li>";
  card.querySelector(".business-summary").innerHTML = business.name
    ? `<div class="business-name">${escapeHtml(business.name)}</div>${business.industry ? `<div>${escapeHtml(business.industry)}</div>` : ""}${business.location ? `<div>${escapeHtml(business.location)}</div>` : ""}${business.address ? `<div>${escapeHtml(business.address)}</div>` : ""}${business.ico ? `<div>IČO ${escapeHtml(business.ico)}</div>` : ""}<div class="muted">Source-linked; not registry-verified.${business.inferred_fields?.length ? ` Inferred: ${escapeHtml(business.inferred_fields.join(", "))}.` : ""}${business.sources?.length ? ` · ${business.sources.map(source => `<a href="${safeExternalUrl(source)}" target="_blank" rel="noopener noreferrer">source</a>`).join(" · ")}` : ""}</div>`
    : `<span class="muted">No business identity found. The candidate stays available for review.</span>`;
  const contacts = candidate.contacts || [];
  card.querySelector(".contact-list").innerHTML = contacts.length ? contacts.map(contact => {
    const link = contact.kind === "email" ? `mailto:${encodeURIComponent(contact.value)}` : `tel:${encodeURIComponent(contact.value)}`;
    return `<div class="contact-entry"><a href="${link}">${escapeHtml(contact.value)}</a><div>${escapeHtml(contact.classification.replaceAll("_", " "))} · <a href="${safeExternalUrl(contact.source_url)}" target="_blank" rel="noopener noreferrer">source</a></div></div>`;
  }).join("") : `<span class="muted">CONTACT_NOT_FOUND</span>`;
  const sources = candidate.sources || [];
  card.querySelector(".source-list").innerHTML = sources.length ? sources.map(source => `<span class="source-chip">${escapeHtml(source.source.replaceAll("_", " "))}</span>`).join("") : "—";
  const history = candidate.history || {};
  const historyRow = card.querySelector(".history-row");
  if (history.oldest_sampled_capture) {
    const stamp = history.oldest_sampled_capture;
    historyRow.textContent = `Common Crawl sample: ${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6, 8)} · ${history.capture_count_sampled} sampled capture(s)`;
  }
  const opportunity = candidate.opportunity || {};
  const leadScore = opportunity.lead_score ?? opportunity.opportunity_score;
  card.querySelector(".opportunity-score").textContent = Number.isFinite(leadScore) ? `${leadScore}/100` : "—";
  card.querySelector(".neglect-score").textContent = Number.isFinite(opportunity.neglect_score) ? `${opportunity.neglect_score}/100` : "—";
  const moneyScore = opportunity.money_score ?? opportunity.activity_score;
  card.querySelector(".activity-score").textContent = Number.isFinite(moneyScore) ? `${moneyScore}/100` : "—";
  card.querySelector(".opportunity-issues").innerHTML = (opportunity.issues_detail || []).length
    ? opportunity.issues_detail.map(item => `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.evidence)}</span><span><b>Redesign option:</b> ${escapeHtml(item.recommendation)}</span><span><b>Possible benefit:</b> ${escapeHtml(item.potential_benefit)}</span></li>`).join("")
    : "<li>No rule-based website issues were detected in the available evidence.</li>";
  card.querySelector(".opportunity-strengths").innerHTML = (opportunity.strengths || []).length
    ? opportunity.strengths.map(item => `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.evidence)}</span></li>`).join("")
    : "<li>No positive business or marketing signals were recorded yet.</li>";
  card.querySelector(".opportunity-method").textContent = opportunity.method || "This assessment appears after the website has been inspected. Findings come from observable page evidence and need human review.";
  const aiAnalysis = opportunity.ai_analysis;
  const aiPanel = card.querySelector(".ai-analysis-panel");
  if (aiAnalysis) {
    aiPanel.hidden = false;
    card.querySelector(".ai-analysis-category").textContent = (aiAnalysis.visual_category || "UNCERTAIN").replaceAll("_", " ");
    card.querySelector(".ai-analysis-summary").textContent = aiAnalysis.summary || "";
    card.querySelector(".ai-analysis-positives").innerHTML = (aiAnalysis.positives || []).length
      ? aiAnalysis.positives.map(item => `<li><strong>${escapeHtml(item.point)}</strong><span>${escapeHtml(item.evidence)}</span></li>`).join("")
      : "<li>No positive points were recorded.</li>";
    card.querySelector(".ai-analysis-improvements").innerHTML = (aiAnalysis.improvements || []).length
      ? aiAnalysis.improvements.map(item => `<li><strong>${escapeHtml(item.issue)}</strong><span>${escapeHtml(item.evidence)}</span><span><b>Option:</b> ${escapeHtml(item.recommendation)}</span><span><b>Possible benefit:</b> ${escapeHtml(item.possible_benefit)}</span></li>`).join("")
      : "<li>No additional improvement options were recorded.</li>";
    card.querySelector(".ai-redesign-direction").textContent = aiAnalysis.redesign_direction || "";
  }
  const pageSpeed = opportunity.page_speed || {};
  const pageSpeedStatus = card.querySelector(".page-speed-status");
  pageSpeedStatus.textContent = pageSpeed.performance_score == null
    ? (pageSpeed.status === "error" ? pageSpeed.error || "PageSpeed measurement failed." : "Mobile PageSpeed not measured")
    : `Mobile PageSpeed ${pageSpeed.performance_score}/100${pageSpeed.lcp_ms == null ? "" : ` · LCP ${(pageSpeed.lcp_ms / 1000).toFixed(1)} s`}${pageSpeed.field_data ? " · field data available" : ""}`;
  const pageSpeedButton = card.querySelector(".pagespeed-button");
  pageSpeedButton.textContent = pageSpeedConfigured ? "Measure mobile speed" : "PageSpeed API key required";
  pageSpeedButton.disabled = !pageSpeedConfigured || !candidate.final_url;
  pageSpeedButton.addEventListener("click", async () => {
    pageSpeedButton.disabled = true;
    pageSpeedButton.textContent = "Measuring…";
    try {
      const result = await request(`/api/candidates/${candidate.id}/pagespeed`, { method: "POST" });
      const score = result.page_speed.performance_score;
      setAction(score == null ? "PageSpeed returned no mobile score." : `Mobile PageSpeed result saved: ${score}/100.`);
    } catch (error) {
      setAction(error.message, true);
    }
    await refreshAll();
  });
  const mockup = candidate.mockup || {};
  const mockupButton = card.querySelector(".generate-mockup");
  const mockupStatus = card.querySelector(".mockup-status");
  const mockupPreview = card.querySelector(".mockup-preview");
  if (mockup.status === "ready") {
    const mockupUrl = `/api/candidates/${candidate.id}/mockup`;
    mockupPreview.src = mockupUrl;
    mockupPreview.hidden = false;
    const download = card.querySelector(".download-mockup");
    download.href = mockupUrl;
    download.hidden = false;
    mockupStatus.textContent = "Concept ready. Review the generated copy and visual details before sharing.";
  } else if (mockup.status === "error") {
    mockupStatus.textContent = mockup.error || "Concept generation failed.";
  } else {
    mockupStatus.textContent = mockup.status === "generating" ? "Generation in progress…" : "Uses the local Ming designer and the current desktop screenshot.";
  }
  mockupButton.disabled = !mockupConfigured || !candidate.screenshot_desktop || mockup.status === "generating";
  mockupButton.textContent = mockupConfigured ? (mockup.status === "ready" ? "Generate another concept" : "Generate concept") : "Ming runtime unavailable";
  mockupButton.title = !mockupConfigured
    ? "The local Ming designer runtime could not be found."
    : !candidate.screenshot_desktop
      ? "Inspect the website to capture a desktop screenshot first."
      : "Creates a local visual concept from the current desktop screenshot.";
  mockupButton.addEventListener("click", async () => {
    mockupButton.disabled = true;
    mockupButton.textContent = "Generating…";
    mockupStatus.textContent = "Ming is preparing a concept from the desktop screenshot. This may take several minutes.";
    try {
      await request(`/api/candidates/${candidate.id}/mockup`, { method: "POST" });
      setAction("Redesign concept generated and saved in this project.");
    } catch (error) {
      setAction(error.message, true);
    }
    await refreshAll();
  });
  const outreach = candidate.outreach || {};
  const statusSelect = card.querySelector(".outreach-status");
  const subjectInput = card.querySelector(".outreach-subject");
  const bodyInput = card.querySelector(".outreach-body");
  const emailContact = (candidate.contacts || []).find(contact => contact.kind === "email" && ["general_business_contact", "named_or_external_business_contact"].includes(contact.classification));
  statusSelect.value = outreach.status || "new";
  subjectInput.value = outreach.subject || "";
  bodyInput.value = outreach.body || "";
  const saveOutreach = async status => {
    const saved = await request("/api/outreach", { method: "POST", body: JSON.stringify({
      candidate_id: candidate.id,
      status,
      subject: subjectInput.value,
      body: bodyInput.value,
    }) });
    card.querySelector(".outreach-saved").textContent = "Outreach updated.";
    return saved;
  };
  card.querySelector(".generate-draft").addEventListener("click", () => {
    const draft = draftOutreach(candidate);
    subjectInput.value = draft.subject;
    bodyInput.value = draft.body;
    if (statusSelect.value === "new") statusSelect.value = "drafted";
  });
  card.querySelector(".save-draft").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await saveOutreach(statusSelect.value);
    } catch (error) {
      card.querySelector(".outreach-saved").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  const openEmail = card.querySelector(".open-email");
  openEmail.disabled = !emailContact;
  openEmail.title = emailContact ? "Open a draft in your default email app. It will not be sent automatically." : "No public business email suitable for outreach was found.";
  openEmail.addEventListener("click", async () => {
    try {
      await saveOutreach("drafted");
      const href = `mailto:${encodeURIComponent(emailContact.value)}?subject=${encodeURIComponent(subjectInput.value)}&body=${encodeURIComponent(bodyInput.value)}`;
      window.location.href = href;
    } catch (error) {
      card.querySelector(".outreach-saved").textContent = error.message;
    }
  });
  const form = card.querySelector(".review-form");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const verdict = card.querySelector(".verdict-select").value;
    if (!VERDICTS.includes(verdict)) return;
    const submit = card.querySelector(".save-review");
    submit.disabled = true;
    try {
      await request("/api/reviews", { method: "POST", body: JSON.stringify({ candidate_id: candidate.id, verdict, note: card.querySelector(".review-note").value }) });
      card.querySelector(".review-saved").textContent = "Review saved.";
      await refreshAll();
    } catch (error) {
      card.querySelector(".review-saved").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  if (candidate.review) {
    card.querySelector(".verdict-select").value = candidate.review.verdict;
    card.querySelector(".review-note").value = candidate.review.note || "";
  }
  card.querySelector(".history-button").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Checking archive…";
    try {
      await request(`/api/candidates/${candidate.id}/history`, { method: "POST" });
      setAction("Historical evidence updated.");
      await refreshAll();
    } catch (error) {
      setAction(error.message, true);
      button.textContent = "Check archive history";
      button.disabled = false;
    }
  });
  return card;
}

function setScreenshot(image, candidate, kind) {
  const stored = candidate.screenshot_desktop && kind === "desktop" || candidate.screenshot_mobile && kind === "mobile";
  if (!stored) {
    image.removeAttribute("src");
    image.parentElement.classList.add("screenshot-empty");
    return;
  }
  image.src = `/api/candidates/${candidate.id}/screenshots/${kind}`;
}

function updateAnalysisControls() {
  const count = document.querySelector("#analysis-selection-count");
  if (!count) return;
  count.textContent = `${selectedCandidateIds.size} selected`;
  const button = document.querySelector("#analyze-selected");
  button.disabled = !aiAnalysisConfigured || selectedCandidateIds.size === 0 || isBusy;
  button.title = !aiAnalysisConfigured
    ? "Set OPENAI_API_KEY in .env to enable selected AI analysis."
    : selectedCandidateIds.size === 0
      ? "Select scored candidates from the queue first."
      : "Capture screenshots and run a detailed AI review for each selected website.";
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
}

async function refreshMetrics() {
  const metrics = await request("/api/metrics");
  const body = document.querySelector("#metrics-body");
  if (!metrics.length) {
    body.innerHTML = `<tr><td colspan="14">No source results yet.</td></tr>`;
    return;
  }
  body.innerHTML = metrics.map(row => `<tr><td>${escapeHtml(row.source.replaceAll("_", " "))}</td><td>${number(row.domains_discovered)}</td><td>${number(row.domains_screened_before_discovery)}</td><td>${number(row.successfully_screened)}</td><td>${number(row.successfully_inspected)}</td><td>${number(row.business_websites)}</td><td>${number(row.vintage_or_dated)}</td><td>${number(row.good_prospects)}</td><td>${number(row.with_usable_email)}</td><td>${number(row.duplicates)}</td><td>${number(row.failed_or_unreachable)}</td><td>${row.acceptance_rate == null ? "—" : `${(row.acceptance_rate * 100).toFixed(1)}%`}</td><td>${row.qualified_per_inspected_domain == null ? "—" : `${(row.qualified_per_inspected_domain * 100).toFixed(1)}%`}</td><td>${row.processing_minutes}</td><td>${row.qualified_per_processing_hour == null ? "—" : number(row.qualified_per_processing_hour)}</td></tr>`).join("");
}

async function refreshRuns() {
  const [runs, jobs] = await Promise.all([request("/api/runs"), request("/api/jobs")]);
  const list = document.querySelector("#runs-list");
  const pending = jobs.filter(job => ["queued", "running"].includes(job.status)).length;
  if (!runs.length) {
    list.innerHTML = `<span class="muted">No batches yet.</span>`;
    return;
  }
  list.innerHTML = runs.slice(0, 10).map(run => `<div class="run-row"><span class="run-source">${escapeHtml(run.source.replaceAll("_", " "))}</span><span class="run-detail">${number(run.discovered)} found · ${escapeHtml(run.status)}</span><span class="run-detail">${escapeHtml(new Date(run.started_at).toLocaleString())}</span></div>`).join("");
  if (pending) list.insertAdjacentHTML("afterbegin", `<div class="muted">${number(pending)} job(s) still running.</div>`);
}

async function refreshAll() {
  try {
    await Promise.all([refreshStatus(), refreshCandidates(), refreshMetrics(), refreshRuns()]);
  } catch (error) {
    document.querySelector("#health-status").textContent = "Connection issue";
    setAction(error.message, true);
  }
}

document.querySelector("#import-sknic").addEventListener("click", event => withBusy(event.currentTarget, async () => {
  setAction("Downloading and importing the official domain list…");
  const result = await request("/api/discover/sknic/import", { method: "POST" });
  setAction(`${number(result.inventory_domains)} domains stored from SK-NIC.`);
}));
document.querySelector("#sample-sknic").addEventListener("click", event => withBusy(event.currentTarget, async () => {
  const result = await request("/api/discover/sknic/sample", { method: "POST", body: JSON.stringify({ limit: Number(document.querySelector("#sknic-limit").value), strategy: document.querySelector("#sknic-strategy").value }) });
  setAction(`${result.queued} domain(s) queued from the SK-NIC sample.`);
}));
document.querySelector("#targeted-discovery").addEventListener("click", event => withBusy(event.currentTarget, async () => {
  const categories = [...document.querySelectorAll("#category-options input:checked")].map(input => input.value);
  const result = await request("/api/discover/targeted", { method: "POST", body: JSON.stringify({ town: document.querySelector("#town-select").value, limit: Number(document.querySelector("#osm-limit").value), categories }) });
  setAction(`${result.discovered} listed website(s) found; ${result.queued} queued for inspection.`);
}));
document.querySelector("#legacy-discovery").addEventListener("click", event => withBusy(event.currentTarget, async () => {
  setAction("Screening a bounded inventory sample for legacy HTML signals…");
  const result = await request("/api/discover/legacy", { method: "POST", body: JSON.stringify({ limit: Number(document.querySelector("#legacy-limit").value), strategy: document.querySelector("#legacy-strategy").value }) });
  setAction(`${number(result.domains_screened)} domains screened; ${number(result.discovered)} showed legacy signals.`);
}));
document.querySelector("#historical-discovery").addEventListener("click", event => withBusy(event.currentTarget, async () => {
  setAction("Checking a bounded sample against Common Crawl history…");
  const result = await request("/api/discover/historical", { method: "POST", body: JSON.stringify({ limit: Number(document.querySelector("#history-limit").value), strategy: document.querySelector("#history-strategy").value }) });
  setAction(`${number(result.domains_checked)} domains checked; ${number(result.discovered)} had sampled captures older than the configured cutoff.${result.index_errors ? ` ${number(result.index_errors)} index lookup(s) had errors.` : ""}`);
}));
document.querySelector("#manual-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  await withBusy(button, async () => {
    const result = await request("/api/discover/manual", { method: "POST", body: JSON.stringify({ url: document.querySelector("#manual-url").value }) });
    document.querySelector("#manual-url").value = "";
    setAction(`${result.queued} website queued for the full pipeline.`);
  });
});
document.querySelectorAll(".filter-button").forEach(button => button.addEventListener("click", async () => {
  candidateFilter = button.dataset.filter;
  document.querySelectorAll(".filter-button").forEach(item => item.classList.toggle("active", item === button));
  await refreshCandidates();
}));
document.querySelector("#candidate-sort").addEventListener("change", async event => {
  candidateSort = event.currentTarget.value;
  await refreshCandidates();
});
document.querySelector("#analyze-selected").addEventListener("click", async event => {
  const button = event.currentTarget;
  const ids = [...selectedCandidateIds];
  if (!ids.length || !aiAnalysisConfigured || isBusy) return;
  isBusy = true;
  button.disabled = true;
  const failures = [];
  let completed = 0;
  try {
    for (let index = 0; index < ids.length; index += 1) {
      setAction(`Analyzing selected website ${index + 1}/${ids.length}…`);
      try {
        await request(`/api/candidates/${ids[index]}/analyze`, { method: "POST" });
        completed += 1;
      } catch (error) {
        failures.push(error.message);
      }
    }
    if (completed) {
      const report = await request("/api/reports/generate", {
        method: "POST",
        body: JSON.stringify({ candidate_ids: ids }),
      });
      document.querySelector("#html-report-link").href = report.html_url;
      document.querySelector("#html-report-link").hidden = false;
      document.querySelector("#csv-report-link").href = report.csv_url;
      document.querySelector("#csv-report-link").hidden = false;
      setAction(`${report.count} analyzed website(s) saved in the HTML report and CSV.${failures.length ? ` ${failures.length} website(s) could not be analyzed.` : ""}`);
    } else {
      setAction(failures.join(" ") || "No selected website could be analyzed.", true);
    }
  } catch (error) {
    setAction(error.message, true);
  } finally {
    isBusy = false;
    updateAnalysisControls();
    await refreshAll();
  }
});
document.querySelector("#refresh-button").addEventListener("click", refreshAll);

refreshAll();
window.setInterval(refreshAll, 4000);
