const state = {
  themes: [], motions: [], settings: {}, projects: [], current: null,
  scenes: [], assets: [], scenePage: 1, pageSize: 40, generationTimer: null, renderTimer: null,
  selectedSceneIds: new Set(), planWarnings: [],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.body instanceof Blob || options.body instanceof File ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function money(value) { return `$${Number(value || 0).toFixed(2)}`; }
function clock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${hours ? String(hours).padStart(2, "0") + ":" : ""}${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

let toastTimer;
function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 3200);
}

async function boot() {
  try {
    const [health, themeData, settings, projectData] = await Promise.all([
      api("/api/health"), api("/api/themes"), api("/api/settings"), api("/api/projects"),
    ]);
    state.themes = themeData.themes;
    state.motions = themeData.motions;
    state.settings = settings;
    state.projects = projectData.projects;
    $("#healthBadge").textContent = health.ffmpeg ? "Local engine ready" : "FFmpeg missing";
    $("#healthBadge").classList.toggle("ok", health.ffmpeg);
    $("#appVersion").textContent = `v${health.version || "0.2.0"}`;
    fillThemeOptions();
    fillEmotionFilter();
    $("#bulkMotion").insertAdjacentHTML("beforeend", state.motions.map(item => `<option value="${item}">${item.replaceAll("_", " ")}</option>`).join(""));
    renderProjects();
    fillSettings();
    if (state.projects.length) await openProject(state.projects[0].id);
  } catch (error) {
    toast(error.message, true);
    $("#healthBadge").textContent = "Engine unavailable";
  }
}

function fillThemeOptions() {
  const options = state.themes.map(theme => `<option value="${escapeHtml(theme.id)}">${escapeHtml(theme.name)}</option>`).join("");
  $("#themeSelect").innerHTML = options;
  $("#newProjectTheme").innerHTML = options;
}

function fillEmotionFilter() {
  const emotions = ["nostalgia", "joy", "loss", "suspense", "reveal", "urgency", "neutral"];
  $("#emotionFilter").insertAdjacentHTML("beforeend", emotions.map(item => `<option value="${item}">${item[0].toUpperCase() + item.slice(1)}</option>`).join(""));
}

function renderProjects() {
  $("#projectList").innerHTML = state.projects.map(project => `
    <button class="project-item ${state.current?.id === project.id ? "active" : ""}" data-project-id="${project.id}">
      <strong>${escapeHtml(project.name)}</strong><span>${Number(project.target_scene_count || 0)} scenes · ${escapeHtml(project.status)}</span>
    </button>`).join("") || `<p class="sidebar-note">No projects yet.</p>`;
}

async function refreshProjects() {
  state.projects = (await api("/api/projects")).projects;
  renderProjects();
}

async function openProject(projectId, keepTab = false) {
  const projectChanged = state.current?.id !== projectId;
  const payload = await api(`/api/projects/${projectId}`);
  state.current = payload.project;
  state.scenes = payload.scenes;
  state.assets = payload.assets;
  state.planWarnings = payload.warnings || [];
  if (projectChanged) {
    state.selectedSceneIds.clear();
  }
  state.scenePage = 1;
  $("#emptyState").hidden = true;
  $("#workspace").hidden = false;
  $("#projectTitle").textContent = state.current.name;
  const theme = state.themes.find(item => item.id === state.current.theme_id);
  $("#projectThemeLabel").textContent = theme?.name || state.current.theme_id;
  $("#themeSelect").value = state.current.theme_id;
  $("#scriptInput").value = state.current.script || "";
  $("#durationInput").value = state.current.duration_seconds ? (state.current.duration_seconds / 60).toFixed(2) : 149;
  $("#imageCountInput").value = state.current.requested_scene_count || state.current.target_scene_count || 715;
  $("#voiceoverStatus").textContent = state.current.voiceover_path ? `Voice-over ready · ${clock(state.current.duration_seconds)}` : "No voice-over uploaded";
  updateMetrics();
  renderProjects();
  renderScenes();
  renderPlanWarnings();
  renderTimeline();
  if (!keepTab) activateTab(state.scenes.length ? "scenes" : "script");
  await refreshGenerationStatus();
  await refreshRenderStatus();
}

function updateMetrics() {
  $("#sceneCount").textContent = state.scenes.length;
  $("#estimatedCost").textContent = money(state.current?.estimated_cost);
  $("#actualCost").textContent = money(state.current?.actual_cost);
}

function activateTab(tabName) {
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === tabName));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.dataset.panel === tabName));
  if (tabName === "timeline") renderTimeline();
}

function showNewProject() {
  $("#projectNameInput").value = "";
  $("#newProjectDialog").showModal();
  setTimeout(() => $("#projectNameInput").focus(), 50);
}

async function createProject(event) {
  event.preventDefault();
  const name = $("#projectNameInput").value.trim();
  if (!name) return;
  try {
    const project = await api("/api/projects", { method: "POST", body: JSON.stringify({ name, theme_id: $("#newProjectTheme").value }) });
    $("#newProjectDialog").close();
    await refreshProjects();
    await openProject(project.id);
    toast("Project created locally");
  } catch (error) { toast(error.message, true); }
}

async function uploadVoiceover(file) {
  if (!state.current || !file) return;
  const progress = $("#uploadProgress");
  progress.hidden = false;
  progress.value = 20;
  $("#voiceoverStatus").textContent = `Uploading ${file.name}…`;
  try {
    const result = await api(`/api/projects/${state.current.id}/voiceover`, {
      method: "POST", body: file, headers: { "X-Filename": file.name },
    });
    progress.value = 100;
    state.current = result.project;
    $("#durationInput").value = result.duration_seconds ? (result.duration_seconds / 60).toFixed(2) : $("#durationInput").value;
    $("#voiceoverStatus").textContent = `Voice-over ready · ${clock(result.duration_seconds)}`;
    toast("Voice-over saved locally");
  } catch (error) {
    $("#voiceoverStatus").textContent = "Voice-over upload failed";
    toast(error.message, true);
  } finally { setTimeout(() => { progress.hidden = true; progress.value = 0; }, 700); }
}

async function createPlan() {
  if (!state.current) return;
  const button = $("#createPlanButton");
  button.disabled = true;
  button.textContent = "Directing scenes…";
  try {
    const result = await api(`/api/projects/${state.current.id}/plan`, {
      method: "POST",
      body: JSON.stringify({
        script: $("#scriptInput").value,
        theme_id: $("#themeSelect").value,
        planner: $("#plannerSelect").value,
        image_count: Number($("#imageCountInput").value || 0),
        seconds_per_scene: Number($("#sceneSecondsInput").value || 12.5),
        duration_seconds: Number($("#durationInput").value || 0) * 60,
      }),
    });
    state.planWarnings = result.warnings || [];
    toast(`${result.scenes.length} scenes created · ${result.generation_count} planned image options`);
    await refreshProjects();
    await openProject(state.current.id);
    renderPlanWarnings();
    activateTab("scenes");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Create visual plan"; }
}

function assetsForScene(sceneId) { return state.assets.filter(asset => asset.scene_id === sceneId); }

function filteredScenes() {
  const filter = $("#emotionFilter").value;
  return filter ? state.scenes.filter(scene => scene.emotion === filter) : state.scenes;
}

function visibleScenes() {
  const filtered = filteredScenes();
  const start = (state.scenePage - 1) * state.pageSize;
  return filtered.slice(start, start + state.pageSize);
}

function renderPlanWarnings() {
  const panel = $("#planWarnings");
  panel.hidden = !state.planWarnings.length;
  panel.innerHTML = state.planWarnings.length ? `<strong>Please check the plan</strong><ul>${state.planWarnings.map(item => `<li>${escapeHtml(item.message)}</li>`).join("")}</ul>` : "";
}

function updateSelectionCount() {
  const count = state.selectedSceneIds.size;
  $("#selectedCount").textContent = `${count} scene${count === 1 ? "" : "s"} selected`;
  $("#generateSelectedButton").disabled = count === 0;
  $("#applySelectedButton").disabled = count === 0;
}

function renderScenes() {
  const filtered = filteredScenes();
  const pages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
  state.scenePage = Math.min(state.scenePage, pages);
  $("#scenePageSelect").innerHTML = Array.from({ length: pages }, (_, index) => `<option value="${index + 1}">${index + 1} / ${pages}</option>`).join("");
  $("#scenePageSelect").value = state.scenePage;
  const start = (state.scenePage - 1) * state.pageSize;
  const visible = filtered.slice(start, start + state.pageSize);
  $("#sceneRange").textContent = filtered.length ? `Showing ${start + 1}–${Math.min(start + state.pageSize, filtered.length)} of ${filtered.length}` : "No matching scenes";
  $("#sceneList").innerHTML = visible.map(sceneCard).join("");
  updateSelectionCount();
}

function sceneCard(scene) {
  const assets = assetsForScene(scene.id);
  const assetHtml = assets.length ? assets.map(asset => `
    <div class="asset ${scene.selected_asset_id === asset.id ? "selected" : ""}">
      <img src="${escapeHtml(asset.media_url)}" alt="Candidate ${Number(asset.candidate_index) + 1} for scene ${scene.position}">
      <button type="button" data-action="select-asset" data-scene-id="${scene.id}" data-asset-id="${asset.id}" aria-label="Select this candidate"></button>
      <span class="asset-label">${escapeHtml(asset.provider)} · ${money(asset.cost)}</span>
    </div>`).join("") : `<div class="asset-empty">No image generated yet</div>`;
  const motion = (scene.timeline_actions || []).find(action => action.type === "motion")?.params?.preset || "slow_push";
  return `<article class="scene-card" data-scene-id="${scene.id}">
    <header class="scene-card-head"><div class="meta"><input class="scene-selector" type="checkbox" data-action="select-scene" aria-label="Select scene ${scene.position}" ${state.selectedSceneIds.has(scene.id) ? "checked" : ""}><strong>Scene ${scene.position}</strong><span class="badge">${escapeHtml(scene.emotion)}</span><span class="badge">${escapeHtml(scene.narrative_role)}</span><span class="time">${clock(scene.start_seconds)} → ${clock(scene.end_seconds)}</span></div><span class="badge">${scene.candidate_count} option${scene.candidate_count === 1 ? "" : "s"}</span></header>
    <div class="scene-body">
      <div><p class="scene-narration">${escapeHtml(scene.narration)}</p><label class="scene-prompt">Image prompt<textarea data-field="prompt">${escapeHtml(scene.prompt)}</textarea></label>
        <div class="scene-controls">
          <label>Provider<select data-field="provider"><option value="runware" ${scene.provider === "runware" ? "selected" : ""}>Runware</option><option value="together" ${scene.provider === "together" ? "selected" : ""}>Together</option><option value="mock" ${scene.provider === "mock" ? "selected" : ""}>Offline test</option></select></label>
          <label>Model route<select data-field="model_role"><option value="photoreal" ${scene.model_role === "photoreal" ? "selected" : ""}>Photoreal default</option><option value="precise" ${scene.model_role === "precise" ? "selected" : ""}>Precise</option><option value="premium" ${scene.model_role === "premium" ? "selected" : ""}>Premium</option></select></label>
          <label>Options<select data-field="candidate_count"><option value="1" ${scene.candidate_count === 1 ? "selected" : ""}>1</option><option value="2" ${scene.candidate_count === 2 ? "selected" : ""}>2</option><option value="3" ${scene.candidate_count === 3 ? "selected" : ""}>3</option></select></label>
          <label>Motion<select data-field="motion">${state.motions.map(item => `<option value="${item}" ${motion === item ? "selected" : ""}>${item.replaceAll("_", " ")}</option>`).join("")}</select></label>
        </div>
        <div class="scene-buttons"><button class="button ghost small" data-action="save-scene" type="button">Save changes</button><button class="button primary small" data-action="generate-scene" type="button">Generate options</button></div>
      </div><div class="asset-grid">${assetHtml}</div>
    </div></article>`;
}

async function saveScene(card, quiet = false) {
  const sceneId = card.dataset.sceneId;
  const current = state.scenes.find(scene => scene.id === sceneId);
  const motion = $("[data-field='motion']", card).value;
  const transition = (current.timeline_actions || []).find(action => action.type === "transition") || { type: "transition", params: { preset: "fade", duration: 0.32 } };
  const patch = {
    prompt: $("[data-field='prompt']", card).value,
    provider: $("[data-field='provider']", card).value,
    model_role: $("[data-field='model_role']", card).value,
    candidate_count: Number($("[data-field='candidate_count']", card).value),
    timeline_actions: [{ type: "motion", params: { preset: motion, strength: 0.55 } }, transition],
  };
  const updated = await api(`/api/scenes/${sceneId}`, { method: "PATCH", body: JSON.stringify(patch) });
  state.scenes = state.scenes.map(scene => scene.id === sceneId ? updated : scene);
  if (!quiet) toast(`Scene ${updated.position} saved`);
  return updated;
}

async function generateScenes(sceneIds = null, force = false) {
  if (!state.current) return;
  try {
    const result = await api(`/api/projects/${state.current.id}/generate`, { method: "POST", body: JSON.stringify({ scene_ids: sceneIds, force }) });
    toast(`${result.queued} image job${result.queued === 1 ? "" : "s"} queued`);
    startGenerationPolling();
  } catch (error) { toast(error.message, true); }
}

function bulkChanges() {
  const values = {
    provider: $("#bulkProvider").value,
    model_role: $("#bulkModel").value,
    candidate_count: $("#bulkCandidates").value ? Number($("#bulkCandidates").value) : "",
    motion: $("#bulkMotion").value,
    transition: $("#bulkTransition").value,
    prompt_find: $("#bulkPromptFind").value,
    prompt_replace: $("#bulkPromptReplace").value,
  };
  return Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== "" || key === "prompt_replace" && values.prompt_find));
}

async function applyBulk(scope) {
  if (!state.current) return;
  const changes = bulkChanges();
  if (!Object.keys(changes).length) throw new Error("Choose at least one bulk change");
  const sceneIds = scope === "selected" ? [...state.selectedSceneIds] : null;
  if (scope === "selected" && !sceneIds.length) throw new Error("Select at least one scene");
  const result = await api(`/api/projects/${state.current.id}/scenes/bulk`, {
    method: "POST",
    body: JSON.stringify({ scene_ids: sceneIds, changes, save_as_default: scope === "all" }),
  });
  const payload = await api(`/api/projects/${state.current.id}`);
  state.current = payload.project; state.scenes = payload.scenes; state.assets = payload.assets;
  updateMetrics(); renderScenes(); renderTimeline();
  toast(`Updated ${result.updated} scene${result.updated === 1 ? "" : "s"}`);
}

async function retryFailed() {
  if (!state.current) return;
  const result = await api(`/api/projects/${state.current.id}/retry-failed`, { method: "POST", body: "{}" });
  toast(result.queued ? `Retrying ${result.queued} failed image job${result.queued === 1 ? "" : "s"}` : "No failed jobs to retry");
  if (result.queued) startGenerationPolling();
}

async function selectAsset(sceneId, assetId) {
  const updated = await api(`/api/scenes/${sceneId}/select-asset`, { method: "POST", body: JSON.stringify({ asset_id: assetId }) });
  state.scenes = state.scenes.map(scene => scene.id === sceneId ? updated : scene);
  renderScenes(); renderTimeline();
}

async function refreshGenerationStatus() {
  if (!state.current) return;
  const result = await api(`/api/projects/${state.current.id}/generation-status`);
  const counts = result.counts || {};
  $("#generationStatus").textContent = `Pending ${counts.pending || 0} · Running ${counts.running || 0} · Complete ${counts.complete || 0} · Failed ${counts.failed || 0}${result.running ? " · Queue active" : ""}`;
  const failures = result.failures || [];
  $("#retryFailedButton").hidden = failures.length === 0;
  $("#failurePanel").hidden = failures.length === 0;
  $("#failureList").innerHTML = failures.map(failure => `<div class="failure-item"><strong>Scene ${failure.position}, option ${Number(failure.candidate_index) + 1}</strong> · ${escapeHtml(failure.provider)} · attempt ${failure.attempts}<br>${escapeHtml(failure.error || "Unknown provider error")}</div>`).join("");
  if (!result.running && !(counts.pending > 0)) {
    clearInterval(state.generationTimer); state.generationTimer = null;
  }
  if ((counts.complete || 0) > state.assets.length || !result.running) {
    const payload = await api(`/api/projects/${state.current.id}`);
    state.current = payload.project; state.scenes = payload.scenes; state.assets = payload.assets;
    updateMetrics(); renderScenes(); renderTimeline(); await refreshProjects();
  }
}

function startGenerationPolling() {
  clearInterval(state.generationTimer);
  refreshGenerationStatus();
  state.generationTimer = setInterval(() => refreshGenerationStatus().catch(error => toast(error.message, true)), 2200);
}

function renderTimeline() {
  const selectedAssets = Object.fromEntries(state.assets.map(asset => [asset.id, asset]));
  $("#timelineList").innerHTML = state.scenes.map(scene => {
    const asset = selectedAssets[scene.selected_asset_id];
    const motion = (scene.timeline_actions || []).find(action => action.type === "motion")?.params?.preset || "slow_push";
    const transition = (scene.timeline_actions || []).find(action => action.type === "transition")?.params?.preset || "fade";
    return `<div class="timeline-row" data-scene-id="${scene.id}"><div class="timeline-scene">${asset ? `<img class="timeline-thumb" src="${escapeHtml(asset.media_url)}" alt="">` : `<div class="timeline-thumb"></div>`}<div class="timeline-title"><strong>Scene ${scene.position}</strong><span>${escapeHtml(scene.narration)}</span></div></div><span class="time">${clock(scene.start_seconds)} → ${clock(scene.end_seconds)}</span><select data-timeline="motion" aria-label="Motion for scene ${scene.position}">${state.motions.map(item => `<option value="${item}" ${item === motion ? "selected" : ""}>${item.replaceAll("_", " ")}</option>`).join("")}</select><select data-timeline="transition" aria-label="Transition for scene ${scene.position}"><option value="fade" ${transition === "fade" ? "selected" : ""}>fade</option><option value="cut" ${transition === "cut" ? "selected" : ""}>cut</option></select></div>`;
  }).join("") || `<div class="queue-status">Create the visual plan first.</div>`;
}

async function saveTimelineRow(row) {
  const sceneId = row.dataset.sceneId;
  const actions = [
    { type: "motion", params: { preset: $("[data-timeline='motion']", row).value, strength: 0.55 } },
    { type: "transition", params: { preset: $("[data-timeline='transition']", row).value, duration: 0.32 } },
  ];
  const updated = await api(`/api/scenes/${sceneId}`, { method: "PATCH", body: JSON.stringify({ timeline_actions: actions }) });
  state.scenes = state.scenes.map(scene => scene.id === sceneId ? updated : scene);
  toast(`Timeline action saved for scene ${updated.position}`);
}

async function startRender() {
  if (!state.current) return;
  try {
    await api(`/api/projects/${state.current.id}/render`, { method: "POST", body: JSON.stringify({
      width: Number($("#exportWidth").value), height: Number($("#exportHeight").value),
      fps: Number($("#exportFps").value), burn_captions: $("#burnCaptions").checked,
    }) });
    toast("Local render started");
    clearInterval(state.renderTimer);
    state.renderTimer = setInterval(() => refreshRenderStatus().catch(error => toast(error.message, true)), 2500);
    await refreshRenderStatus();
  } catch (error) { toast(error.message, true); }
}

async function refreshRenderStatus() {
  if (!state.current) return;
  const result = await api(`/api/projects/${state.current.id}/render-status`);
  const render = result.render;
  if (!render) return;
  $("#renderState").textContent = render.status[0].toUpperCase() + render.status.slice(1);
  $("#renderProgress").value = Math.round(Number(render.progress || 0) * 100);
  $("#renderMessage").textContent = render.error || render.output_path || `${Math.round(Number(render.progress || 0) * 100)}% complete`;
  const videoLink = $("#openVideoLink");
  videoLink.hidden = !(render.status === "complete" && render.media_url);
  if (!videoLink.hidden) videoLink.href = render.media_url;
  if (["complete", "failed"].includes(render.status)) { clearInterval(state.renderTimer); state.renderTimer = null; }
}

function fillSettings() {
  $("#runwareModel").value = state.settings.runware_default_model || "";
  $("#preciseModel").value = state.settings.runware_precise_model || "";
  $("#premiumModel").value = state.settings.runware_premium_model || "";
  $("#concurrencyInput").value = state.settings.generation_concurrency || 4;
  $("#unitCostInput").value = state.settings.estimated_unit_cost ?? 0.0013;
  $("#budgetInput").value = state.settings.max_project_cost || 3;
  $("#keyStatus").textContent = `Runware ${state.settings.runware_api_key_set ? "connected" : "not connected"} · Together ${state.settings.together_api_key_set ? "connected" : "not connected"} · Gemini ${state.settings.gemini_api_key_set ? "connected" : "not connected"}`;
}

async function saveSettings(event) {
  event.preventDefault();
  const body = {
    runware_default_model: $("#runwareModel").value.trim(),
    runware_precise_model: $("#preciseModel").value.trim(), runware_premium_model: $("#premiumModel").value.trim(),
    generation_concurrency: Number($("#concurrencyInput").value),
    estimated_unit_cost: Number($("#unitCostInput").value),
    max_project_cost: Number($("#budgetInput").value),
  };
  if ($("#runwareKey").value) body.runware_api_key = $("#runwareKey").value;
  if ($("#togetherKey").value) body.together_api_key = $("#togetherKey").value;
  if ($("#geminiKey").value) body.gemini_api_key = $("#geminiKey").value;
  try {
    state.settings = await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
    $("#settingsDialog").close();
    $$("#runwareKey,#togetherKey,#geminiKey").forEach(input => input.value = "");
    fillSettings(); toast("Settings saved locally");
  } catch (error) { toast(error.message, true); }
}

$("#newProjectButton").addEventListener("click", showNewProject);
$("#emptyCreateButton").addEventListener("click", showNewProject);
$("#newProjectForm").addEventListener("submit", createProject);
$("#settingsButton").addEventListener("click", () => { fillSettings(); $("#settingsDialog").showModal(); });
$("#settingsForm").addEventListener("submit", saveSettings);
$$('[data-close-dialog]').forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$("#projectList").addEventListener("click", event => { const button = event.target.closest("[data-project-id]"); if (button) openProject(button.dataset.projectId).catch(error => toast(error.message, true)); });
$$(".tab").forEach(tab => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
$("#voiceoverInput").addEventListener("change", event => uploadVoiceover(event.target.files[0]));
$("#createPlanButton").addEventListener("click", createPlan);
$("#emotionFilter").addEventListener("change", () => { state.scenePage = 1; renderScenes(); });
$("#scenePageSelect").addEventListener("change", event => { state.scenePage = Number(event.target.value); renderScenes(); });
$("#generateAllButton").addEventListener("click", () => generateScenes());
$("#retryFailedButton").addEventListener("click", () => retryFailed().catch(error => toast(error.message, true)));
$("#selectVisibleButton").addEventListener("click", () => { visibleScenes().forEach(scene => state.selectedSceneIds.add(scene.id)); renderScenes(); });
$("#clearSelectionButton").addEventListener("click", () => { state.selectedSceneIds.clear(); renderScenes(); });
$("#applySelectedButton").addEventListener("click", () => applyBulk("selected").catch(error => toast(error.message, true)));
$("#applyAllButton").addEventListener("click", () => applyBulk("all").catch(error => toast(error.message, true)));
$("#generateSelectedButton").addEventListener("click", () => generateScenes([...state.selectedSceneIds], true));
$("#pauseGenerationButton").addEventListener("click", async () => { await api(`/api/projects/${state.current.id}/pause`, { method: "POST", body: "{}" }); toast("Queue paused after active requests finish"); });
$("#resumeGenerationButton").addEventListener("click", async () => { await api(`/api/projects/${state.current.id}/resume`, { method: "POST", body: "{}" }); toast("Queue resumed"); startGenerationPolling(); });
$("#sceneList").addEventListener("click", async event => {
  const action = event.target.closest("[data-action]"); if (!action) return;
  const card = action.closest(".scene-card");
  try {
    if (action.dataset.action === "save-scene") await saveScene(card);
    if (action.dataset.action === "generate-scene") { const updated = await saveScene(card, true); await generateScenes([updated.id], true); }
    if (action.dataset.action === "select-asset") await selectAsset(action.dataset.sceneId, action.dataset.assetId);
  } catch (error) { toast(error.message, true); }
});
$("#sceneList").addEventListener("change", event => {
  const selector = event.target.closest("[data-action='select-scene']");
  if (!selector) return;
  const sceneId = selector.closest(".scene-card").dataset.sceneId;
  if (selector.checked) state.selectedSceneIds.add(sceneId); else state.selectedSceneIds.delete(sceneId);
  updateSelectionCount();
});
$("#timelineList").addEventListener("change", event => { const row = event.target.closest(".timeline-row"); if (row) saveTimelineRow(row).catch(error => toast(error.message, true)); });
$("#renderButton").addEventListener("click", startRender);
$("#openOutputButton").addEventListener("click", async () => {
  try {
    const result = await api(`/api/projects/${state.current.id}/open-folder`, { method: "POST", body: JSON.stringify({ kind: "renders" }) });
    toast(`Opened ${result.path}`);
  } catch (error) { toast(error.message, true); }
});

boot();
