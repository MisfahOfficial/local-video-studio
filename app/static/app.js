const state = {
  themes: [], motions: [], settings: {}, projects: [], current: null,
  scenes: [], timelineClips: [], assets: [], scenePage: 1, pageSize: 40, generationTimer: null, renderTimer: null,
  planningTimer: null, planningStartedAt: 0, previewClockFrame: null,
  selectedSceneIds: new Set(), planWarnings: [], activeTimelineSceneId: null, activeTimelineClipId: null,
  previewTime: 0, manualPreviewFrame: null, manualPreviewStartedAt: 0, draggedClipId: null,
  editTool: "select", snapEnabled: true, trimSession: null, trimFrame: null,
  timelineUndo: [], timelineRedo: [], isPreviewPlaying: false,
  exportProjectId: null, lastRenderOutputPath: "",
  timelineSync: null,
  captionDrafts: {},
  fonts: [], captionFlags: { bold: true, italic: false, underline: false }, captionCase: "normal", captionAlignment: "center",
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

function timecode(seconds, fps = 30) {
  const safe = Math.max(0, Number(seconds || 0));
  const whole = Math.floor(safe);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const frames = Math.min(fps - 1, Math.floor((safe - whole) * fps));
  return [hours, minutes, secs, frames].map(value => String(value).padStart(2, "0")).join(":");
}

function startPlanningProgress(mode) {
  clearInterval(state.planningTimer);
  state.planningStartedAt = performance.now();
  const panel = $("#planningProgress");
  panel.hidden = false;
  panel.classList.remove("error");
  const update = () => {
    const elapsed = (performance.now() - state.planningStartedAt) / 1000;
    $("#planningElapsed").textContent = clock(elapsed);
    if (mode !== "precision") {
      $("#planningStage").textContent = mode === "gemini" ? "Directing visual prompts…" : "Building the local scene plan…";
      $("#planningDetail").textContent = "Scene order, prompts and timing are being prepared.";
      return;
    }
    let stage = "Securely uploading the voice-over…";
    let detail = "The VO is sent only to Gemini for this plan and removed after processing.";
    if (elapsed >= 8) { stage = "Listening and matching the script…"; detail = "Gemini is locating spoken phrases, pauses and topic changes in the real audio."; }
    if (elapsed >= 30) { stage = "Planning scenes and checking Gemini capacity…"; detail = "Temporary capacity errors are retried automatically before a stable fallback model is used."; }
    if (elapsed >= 75) { stage = "Finishing image direction…"; detail = "Accurate boundaries, specific subjects and chronological visual instructions are being validated."; }
    $("#planningStage").textContent = stage;
    $("#planningDetail").textContent = detail;
  };
  update();
  state.planningTimer = setInterval(update, 250);
}

function finishPlanningProgress(message = "", error = false) {
  clearInterval(state.planningTimer);
  state.planningTimer = null;
  const panel = $("#planningProgress");
  if (!message) { panel.hidden = true; return; }
  panel.hidden = false;
  panel.classList.toggle("error", error);
  $("#planningStage").textContent = message;
  $("#planningDetail").textContent = error ? "The existing scenes were not replaced." : "The timestamped visual plan is ready for review.";
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
    const [health, themeData, settings, projectData, fontData] = await Promise.all([
      api("/api/health"), api("/api/themes"), api("/api/settings"), api("/api/projects"), api("/api/fonts"),
    ]);
    state.themes = themeData.themes;
    state.motions = themeData.motions;
    state.settings = settings;
    state.projects = projectData.projects;
    state.fonts = fontData.fonts || [];
    $("#healthBadge").textContent = health.ffmpeg ? "Local engine ready" : "FFmpeg missing";
    $("#healthBadge").classList.toggle("ok", health.ffmpeg);
    $("#appVersion").textContent = `v${health.version || "0.6.6"}`;
    fillThemeOptions();
    fillEmotionFilter();
    $("#bulkMotion").insertAdjacentHTML("beforeend", state.motions.map(item => `<option value="${item}">${item.replaceAll("_", " ")}</option>`).join(""));
    $("#timelineMotion").innerHTML = state.motions.map(item => `<option value="${item}">${item.replaceAll("_", " ")}</option>`).join("");
    loadFontOptions();
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
  state.timelineClips = payload.timeline_clips || [];
  state.timelineSync = payload.timeline_sync || null;
  state.assets = payload.assets;
  state.planWarnings = payload.warnings || [];
  if (projectChanged) {
    state.selectedSceneIds.clear();
    pausePreview();
    state.activeTimelineSceneId = null;
    state.activeTimelineClipId = null;
    state.previewTime = 0;
    state.timelineUndo = [];
    state.timelineRedo = [];
    state.captionDrafts = {};
  }
  if (!state.scenes.some(scene => scene.id === state.activeTimelineSceneId)) {
    state.activeTimelineSceneId = state.scenes[0]?.id || null;
  }
  if (!state.timelineClips.some(clip => clip.id === state.activeTimelineClipId)) {
    state.activeTimelineClipId = state.timelineClips.find(clip => clip.scene_id === state.activeTimelineSceneId)?.id
      || state.timelineClips[0]?.id || null;
  }
  state.scenePage = 1;
  $("#emptyState").hidden = true;
  $("#workspace").hidden = false;
  $("#projectTitle").textContent = state.current.name;
  $("#editorProjectName").textContent = state.current.name;
  $("#exportProjectName").textContent = state.current.name;
  $("#playerTitle").textContent = `${state.current.name} · Preview`;
  const theme = state.themes.find(item => item.id === state.current.theme_id);
  $("#projectThemeLabel").textContent = theme?.name || state.current.theme_id;
  $("#themeSelect").value = state.current.theme_id;
  $("#scriptInput").value = state.current.script || "";
  $("#durationInput").value = state.current.duration_seconds ? (state.current.duration_seconds / 60).toFixed(2) : 149;
  $("#imageCountInput").value = state.current.requested_scene_count || "";
  $("#voiceoverStatus").textContent = state.current.voiceover_path ? `Voice-over ready · ${clock(state.current.duration_seconds)}` : "No voice-over uploaded";
  updateMetrics();
  renderProjects();
  renderScenes();
  renderPlanWarnings();
  fillCaptionStyle();
  configurePreviewAudio();
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
  document.body.classList.toggle("editor-mode", tabName === "timeline");
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
  const planner = $("#plannerSelect").value;
  button.disabled = true;
  button.textContent = planner === "precision" ? "Syncing VO and scenes…" : "Directing scenes…";
  startPlanningProgress(planner);
  try {
    const result = await api(`/api/projects/${state.current.id}/plan`, {
      method: "POST",
      body: JSON.stringify({
        script: $("#scriptInput").value,
        theme_id: $("#themeSelect").value,
        planner,
        image_count: Number($("#imageCountInput").value || 0),
        duration_seconds: Number($("#durationInput").value || 0) * 60,
      }),
    });
    state.selectedSceneIds.clear();
    state.planWarnings = result.warnings || [];
    finishPlanningProgress(result.timing_source === "gemini_audio" ? "Precision Sync complete" : "Visual plan complete");
    const pacing = result.auto_pacing ? "auto-paced" : "manual target";
    toast(`${result.scenes.length} scenes created · ${result.timing_source === "gemini_audio" ? "VO-synced" : "estimated timing"} · ${pacing}`);
    await refreshProjects();
    await openProject(state.current.id);
    renderPlanWarnings();
    activateTab("scenes");
  } catch (error) { finishPlanningProgress(error.message, true); toast(error.message, true); }
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
  state.current = payload.project; state.scenes = payload.scenes;
  state.timelineClips = payload.timeline_clips || state.timelineClips; state.assets = payload.assets;
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
    state.current = payload.project; state.scenes = payload.scenes;
    state.timelineClips = payload.timeline_clips || state.timelineClips; state.assets = payload.assets;
    updateMetrics(); renderScenes(); renderTimeline(); await refreshProjects();
  }
}

function startGenerationPolling() {
  clearInterval(state.generationTimer);
  refreshGenerationStatus();
  state.generationTimer = setInterval(() => refreshGenerationStatus().catch(error => toast(error.message, true)), 2200);
}

function selectedAssetForScene(scene) {
  return state.assets.find(asset => asset.id === scene?.selected_asset_id) || null;
}

function videoTrackDuration() {
  return state.timelineClips.length
    ? Math.max(...state.timelineClips.map(clip => Number(clip.end_seconds || 0)))
    : 0;
}

function voiceoverDuration() {
  return state.current?.voiceover_path ? Number(state.current.duration_seconds || 0) : 0;
}

function timelineSyncDetails() {
  const video = videoTrackDuration();
  const voice = voiceoverDuration();
  const difference = voice - video;
  let status = "no_voiceover";
  if (voice > 0 && !state.timelineClips.length) status = "missing";
  else if (voice > 0 && difference > 0.05) status = "short";
  else if (voice > 0 && difference < -0.05) status = "long";
  else if (voice > 0) status = "synced";
  return { status, video_duration: video, voiceover_duration: voice, difference_seconds: difference };
}

function timelineDuration() {
  const captionEnd = state.scenes.length ? Number(state.scenes[state.scenes.length - 1].end_seconds || 0) : 0;
  return Math.max(videoTrackDuration(), captionEnd, Number(state.current?.duration_seconds || 0), 0.1);
}

function loadFontOptions(selected = null) {
  const select = $("#captionFont");
  const current = selected || select.value || "Arial";
  select.innerHTML = state.fonts.map(font => `<option value="${escapeHtml(font.family)}">${escapeHtml(font.family)}${font.custom ? " · Custom" : ""}</option>`).join("");
  if ([...select.options].some(option => option.value === current)) select.value = current;
  for (const font of state.fonts.filter(item => item.custom && item.url)) {
    if (!document.fonts.check(`12px "${font.family}"`)) {
      const face = new FontFace(font.family, `url("${font.url}")`);
      face.load().then(loaded => document.fonts.add(loaded)).catch(() => {});
    }
  }
}

function captionStyleFromInputs() {
  return {
    font: $("#captionFont").value,
    size: Number($("#captionSize").value),
    bold: state.captionFlags.bold,
    italic: state.captionFlags.italic,
    underline: state.captionFlags.underline,
    case: state.captionCase,
    position: $("#captionPosition").value,
    alignment: state.captionAlignment,
    text_color: $("#captionTextColor").value.toUpperCase(),
    character_spacing: Number($("#captionCharacterSpacing").value),
    line_spacing: Number($("#captionLineSpacing").value),
    preset: $("#captionPresets .preset.active")?.dataset.captionPreset || "custom",
    scale: Number($("#captionScale").value),
    position_x: Number($("#captionPositionX").value),
    position_y: Number($("#captionPositionY").value),
    rotation: Number($("#captionRotation").value),
    opacity: Number($("#captionOpacity").value),
    stroke_enabled: $("#captionStrokeEnabled").checked,
    stroke_color: $("#captionStrokeColor").value.toUpperCase(),
    stroke_width: Number($("#captionStrokeWidth").value),
    background_enabled: $("#captionBackgroundEnabled").checked,
    background_color: $("#captionBackgroundColor").value.toUpperCase(),
    background_opacity: Number($("#captionBackgroundOpacity").value),
    glow_enabled: $("#captionGlowEnabled").checked,
    glow_color: $("#captionGlowColor").value.toUpperCase(),
    glow_radius: Number($("#captionGlowRadius").value),
    shadow_enabled: $("#captionShadowEnabled").checked,
    shadow_color: $("#captionShadowColor").value.toUpperCase(),
    shadow_blur: Number($("#captionShadowBlur").value),
    shadow_x: Number($("#captionShadowX").value),
    shadow_y: Number($("#captionShadowY").value),
    max_lines: Number($("#captionMaxLines").value),
    words_per_line: Number($("#captionWordsPerLine").value),
  };
}

function fillCaptionStyle() {
  const style = state.current?.caption_style || {};
  if (![...$("#captionFont").options].some(option => option.value === style.font) && style.font) {
    $("#captionFont").insertAdjacentHTML("beforeend", `<option>${escapeHtml(style.font)}</option>`);
  }
  $("#captionFont").value = style.font || "Arial";
  $("#captionSize").value = style.size || 54;
  $("#captionSizeRange").value = style.size || 54;
  state.captionFlags = { bold: style.bold ?? true, italic: style.italic ?? false, underline: style.underline ?? false };
  state.captionCase = style.case || "normal";
  state.captionAlignment = style.alignment || "center";
  $("#captionPosition").value = style.position || "bottom";
  $("#captionTextColor").value = style.text_color || "#FFFFFF";
  $("#captionCharacterSpacing").value = style.character_spacing ?? 0;
  $("#captionLineSpacing").value = style.line_spacing ?? 1.2;
  $("#captionScale").value = style.scale ?? 100;
  $("#captionPositionX").value = style.position_x ?? 0;
  $("#captionPositionY").value = style.position_y ?? 0;
  $("#captionRotation").value = style.rotation ?? 0;
  $("#captionOpacity").value = style.opacity ?? 1;
  $("#captionStrokeEnabled").checked = style.stroke_enabled ?? false;
  $("#captionStrokeColor").value = style.stroke_color || "#000000";
  $("#captionStrokeWidth").value = style.stroke_width ?? 3;
  $("#captionBackgroundEnabled").checked = style.background_enabled ?? true;
  $("#captionBackgroundColor").value = style.background_color || "#000000";
  $("#captionBackgroundOpacity").value = style.background_opacity ?? 0.72;
  $("#captionGlowEnabled").checked = style.glow_enabled ?? false;
  $("#captionGlowColor").value = style.glow_color || "#FFFFFF";
  $("#captionGlowRadius").value = style.glow_radius ?? 8;
  $("#captionShadowEnabled").checked = style.shadow_enabled ?? false;
  $("#captionShadowColor").value = style.shadow_color || "#000000";
  $("#captionShadowBlur").value = style.shadow_blur ?? 5;
  $("#captionShadowX").value = style.shadow_x ?? 2;
  $("#captionShadowY").value = style.shadow_y ?? 3;
  $("#captionMaxLines").value = style.max_lines ?? 2;
  $("#captionWordsPerLine").value = style.words_per_line ?? 7;
  $$("[data-style-toggle]").forEach(button => button.classList.toggle("active", Boolean(state.captionFlags[button.dataset.styleToggle])));
  $$("[data-caption-case]").forEach(button => button.classList.toggle("active", button.dataset.captionCase === state.captionCase));
  $$("[data-caption-align]").forEach(button => button.classList.toggle("active", button.dataset.captionAlign === state.captionAlignment));
  $$("[data-caption-preset]").forEach(button => button.classList.toggle("active", button.dataset.captionPreset === (style.preset || "clean")));
  updateCaptionPreviewStyle();
}

function hexToRgb(value) {
  return value.match(/[A-Fa-f0-9]{2}/g)?.map(item => parseInt(item, 16)) || [0, 0, 0];
}

function updateCaptionPreviewStyle() {
  const caption = $("#previewCaption");
  const style = captionStyleFromInputs();
  caption.style.fontFamily = `"${style.font}", sans-serif`;
  caption.style.fontSize = `${Math.max(12, style.size * 0.42)}px`;
  caption.style.fontWeight = style.bold ? "800" : "400";
  caption.style.fontStyle = style.italic ? "italic" : "normal";
  caption.style.textDecoration = style.underline ? "underline" : "none";
  caption.style.textTransform = ({ upper: "uppercase", lower: "lowercase", title: "capitalize" })[style.case] || "none";
  caption.style.textAlign = style.alignment;
  caption.style.letterSpacing = `${style.character_spacing}px`;
  caption.style.lineHeight = style.line_spacing;
  caption.style.color = style.text_color;
  const color = hexToRgb(style.background_color);
  caption.style.backgroundColor = style.background_enabled ? `rgba(${color[0]},${color[1]},${color[2]},${style.background_opacity})` : "transparent";
  caption.style.padding = style.background_enabled ? ".16em .38em" : "0";
  caption.style.webkitTextStroke = style.stroke_enabled ? `${style.stroke_width * 0.42}px ${style.stroke_color}` : "0 transparent";
  const shadows = [];
  if (style.shadow_enabled) shadows.push(`${style.shadow_x}px ${style.shadow_y}px ${style.shadow_blur}px ${style.shadow_color}`);
  if (style.glow_enabled) shadows.push(`0 0 ${style.glow_radius}px ${style.glow_color}`);
  caption.style.textShadow = shadows.join(", ") || "none";
  caption.style.opacity = style.opacity;
  caption.classList.remove("position-top", "position-middle", "position-bottom");
  caption.classList.add(`position-${style.position}`);
  const middleOffset = style.position === "middle" ? "translateY(-50%) " : "";
  caption.style.transform = `${middleOffset}translate(${style.position_x}%, ${style.position_y}%) scale(${style.scale / 100}) rotate(${style.rotation}deg)`;
  $("#captionScaleValue").textContent = `${Math.round(style.scale)}%`;
  $("#captionOpacityValue").textContent = `${Math.round(style.opacity * 100)}%`;
  $("#captionWordsPerLineValue").textContent = style.words_per_line;
}

function configurePreviewAudio() {
  const audio = $("#previewAudio");
  const source = state.current?.voiceover_media_url || "";
  if (source && audio.getAttribute("src") !== source) {
    setPreviewLoadState("Loading VO…", "loading");
    audio.src = source;
    audio.load();
  } else if (!source && audio.getAttribute("src")) {
    audio.removeAttribute("src");
    audio.load();
    setPreviewLoadState("No VO", "");
  } else if (!source) {
    setPreviewLoadState("No VO", "");
  }
  const duration = timelineDuration();
  $("#previewScrubber").max = duration;
  $("#previewTotalTime").textContent = timecode(duration);
  updatePreviewSeekVisual(state.previewTime, duration);
}

function setPreviewLoadState(message, status = "") {
  const element = $("#previewLoadState");
  element.textContent = message;
  element.classList.toggle("loading", status === "loading");
  element.classList.toggle("error", status === "error");
}

function updatePreviewSeekVisual(time, duration = timelineDuration()) {
  const scrubber = $("#previewScrubber");
  const safeDuration = Math.max(0.01, Number(duration || 0));
  const played = Math.max(0, Math.min(100, Number(time || 0) / safeDuration * 100));
  scrubber.style.setProperty("--seek-progress", `${played}%`);
  scrubber.setAttribute("aria-valuetext", `${timecode(time)} of ${timecode(safeDuration)}`);
}

function updatePreviewBuffered() {
  const audio = $("#previewAudio");
  const duration = timelineDuration();
  let buffered = 0;
  if (audio.buffered?.length) buffered = audio.buffered.end(audio.buffered.length - 1);
  const percent = Math.max(0, Math.min(100, Math.max(buffered, state.previewTime) / Math.max(0.01, duration) * 100));
  $("#previewScrubber").style.setProperty("--seek-buffered", `${percent}%`);
}

function activeCaptionSceneAt(time) {
  return state.scenes.find(scene => time >= Number(scene.start_seconds) && time < Number(scene.end_seconds))
    || (time >= timelineDuration() - 0.05 ? state.scenes[state.scenes.length - 1] : null);
}

function sceneForClip(clip) {
  return state.scenes.find(scene => scene.id === clip?.scene_id) || null;
}

function activeVideoClipAt(time) {
  return state.timelineClips.find(clip => time >= Number(clip.start_seconds) && time < Number(clip.end_seconds))
    || (time >= videoTrackDuration() - 0.05 ? state.timelineClips[state.timelineClips.length - 1] : null);
}

function captionCards(text, style = captionStyleFromInputs()) {
  const cleaned = String(text || "").replace(/\s+/g, " ").trim();
  if (!cleaned) return [];
  const maxLines = Number(style.max_lines || 0);
  if (maxLines <= 0) return [cleaned];
  const words = cleaned.split(" ");
  const wordsPerLine = Math.max(2, Number(style.words_per_line || 7));
  const perCard = maxLines * wordsPerLine;
  const cards = [];
  for (let index = 0; index < words.length; index += perCard) {
    const card = words.slice(index, index + perCard);
    const lines = [];
    for (let line = 0; line < card.length; line += wordsPerLine) lines.push(card.slice(line, line + wordsPerLine).join(" "));
    cards.push(lines.join("\n"));
  }
  return cards;
}

function captionTextAt(scene, time) {
  if (!scene) return "";
  const source = state.captionDrafts[scene.id] ?? scene.caption_text ?? scene.narration ?? "";
  const style = captionStyleFromInputs();
  const cards = captionCards(source, style);
  if (!cards.length) return "";
  const duration = Math.max(0.01, Number(scene.end_seconds) - Number(scene.start_seconds));
  const progress = Math.max(0, Math.min(0.999999, (time - Number(scene.start_seconds)) / duration));
  if (Number(style.max_lines || 0) <= 0) return cards[0];
  const totalWords = String(source).trim().split(/\s+/).filter(Boolean).length;
  const wordsPerCard = Number(style.max_lines) * Number(style.words_per_line);
  return cards[Math.min(cards.length - 1, Math.floor(progress * totalWords / wordsPerCard))];
}

function actionFor(scene, type, defaults) {
  return (scene?.timeline_actions || []).find(action => action.type === type)?.params || defaults;
}

function fillTimelineInspector(scene, clip = null) {
  const controls = ["#timelineDuration", "#timelineCaption", "#timelineMotion", "#timelineTransition", "#timelineTransitionDuration", "#replaceMediaButton", "#saveTimelineButton"];
  controls.forEach(selector => { $(selector).disabled = !scene; });
  $("#timelineSceneTitle").textContent = scene
    ? `Scene ${scene.position}${clip ? ` · Clip ${clip.position} · ${clock(clip.start_seconds)}–${clock(clip.end_seconds)}` : ""}`
    : "Select a scene";
  if (!scene) {
    $("#timelineDuration").value = "";
    $("#timelineCaption").value = "";
    return;
  }
  const motion = actionFor(scene, "motion", { preset: "slow_push" });
  const transition = actionFor(scene, "transition", { preset: "fade", duration: 0.32 });
  $("#timelineDuration").value = clip
    ? (Number(clip.end_seconds) - Number(clip.start_seconds)).toFixed(2)
    : (Number(scene.end_seconds) - Number(scene.start_seconds)).toFixed(2);
  $("#timelineCaption").value = state.captionDrafts[scene.id] ?? scene.caption_text ?? scene.narration ?? "";
  $("#timelineMotion").value = motion.preset || "slow_push";
  $("#timelineTransition").value = transition.preset || "fade";
  $("#timelineTransitionDuration").value = transition.preset === "cut" ? 0 : Number(transition.duration ?? 0.32);
  $("#timelineTransitionDuration").disabled = transition.preset === "cut";
}

function applyPreviewMotion(scene, progress, clip = null) {
  const media = $("#previewImage").hidden ? $("#previewVideo") : $("#previewImage");
  const preset = actionFor(scene, "motion", { preset: "slow_push" }).preset;
  let transform = "scale(1)";
  if (preset === "slow_push") transform = `scale(${1 + 0.08 * progress})`;
  if (preset === "detail_push") transform = `scale(${1 + 0.12 * progress})`;
  if (preset === "pop_in") transform = `scale(${1 + 0.10 * Math.sin(Math.PI * Math.min(1, progress / 0.30))})`;
  if (preset === "slow_pull") transform = `scale(${1.08 - 0.08 * progress})`;
  if (preset === "pan_left") transform = `scale(1.08) translateX(${4 - 8 * progress}%)`;
  if (preset === "pan_right") transform = `scale(1.08) translateX(${-4 + 8 * progress}%)`;
  media.style.transform = transform;
  const transition = actionFor(scene, "transition", { preset: "fade", duration: 0.32 });
  const duration = Math.max(0.1, Number(clip?.end_seconds ?? scene.end_seconds) - Number(clip?.start_seconds ?? scene.start_seconds));
  const elapsed = Math.max(0, state.previewTime - Number(clip?.start_seconds ?? scene.start_seconds));
  const fade = transition.preset === "fade" ? Math.min(Number(transition.duration || 0.32), duration / 2) : 0;
  media.style.opacity = state.isPreviewPlaying && fade ? Math.min(1, elapsed / fade, (duration - elapsed) / fade) : 1;
}

function updatePreviewAt(time, selectScene = true) {
  const duration = timelineDuration();
  state.previewTime = Math.max(0, Math.min(Number(time || 0), duration));
  $("#previewScrubber").value = state.previewTime;
  $("#previewCurrentTime").textContent = timecode(state.previewTime);
  updatePreviewSeekVisual(state.previewTime, duration);
  const zoom = Number($("#timelineZoom").value || 8);
  $("#timelinePlayhead").style.left = `calc(var(--track-label-width) + ${state.previewTime * zoom}px)`;
  const videoClip = activeVideoClipAt(state.previewTime);
  const scene = sceneForClip(videoClip);
  if (videoClip && selectScene && state.activeTimelineClipId !== videoClip.id) {
    state.activeTimelineClipId = videoClip.id;
    state.activeTimelineSceneId = videoClip.scene_id;
    $$(".timeline-clip").forEach(element => element.classList.toggle("active", element.dataset.clipId === videoClip.id));
    $$(".caption-clip").forEach(element => element.classList.toggle("active", element.dataset.sceneId === videoClip.scene_id));
    fillTimelineInspector(scene, videoClip);
  }
  const activeClip = videoClip || state.timelineClips.find(item => item.id === state.activeTimelineClipId);
  const active = sceneForClip(activeClip) || state.scenes.find(item => item.id === state.activeTimelineSceneId);
  const asset = selectedAssetForScene(active);
  const image = $("#previewImage");
  const video = $("#previewVideo");
  const empty = $("#previewEmpty");
  image.hidden = true;
  video.hidden = true;
  empty.hidden = Boolean(asset);
  if (asset?.media_kind === "video") {
    video.hidden = false;
    if (video.getAttribute("src") !== asset.media_url) { video.src = asset.media_url; video.load(); }
    const rawLocalTime = Math.max(0, Number(activeClip?.source_in_seconds || 0) + state.previewTime - Number(activeClip?.start_seconds || 0));
    const localTime = Number.isFinite(video.duration) && video.duration > 0 ? rawLocalTime % video.duration : rawLocalTime;
    if (video.readyState >= 1 && Math.abs(video.currentTime - localTime) > 0.35) video.currentTime = localTime;
    if (state.isPreviewPlaying && video.paused) video.play().catch(() => {});
  } else if (asset) {
    if (!video.paused) video.pause();
    image.hidden = false;
    if (image.getAttribute("src") !== asset.media_url) image.src = asset.media_url;
  }
  $("#previewSceneBadge").textContent = active ? `Scene ${active.position}` : "No scene";
  const caption = $("#previewCaption");
  const captionScene = activeCaptionSceneAt(state.previewTime);
  caption.textContent = captionTextAt(captionScene, state.previewTime);
  caption.hidden = !caption.textContent;
  updateCaptionPreviewStyle();
  if (active && asset) {
    const clipStart = Number(activeClip?.start_seconds || 0);
    const sceneDuration = Math.max(0.1, Number(activeClip?.end_seconds || 0.1) - clipStart);
    const progress = Math.max(0, Math.min(1, (state.previewTime - clipStart) / sceneDuration));
    applyPreviewMotion(active, progress, activeClip);
  }
  updateTimelineControls();
}

function renderMediaBin() {
  const scene = state.scenes.find(item => item.id === state.activeTimelineSceneId);
  const assets = state.assets.filter(asset => asset.scene_id === scene?.id);
  $("#mediaBin").innerHTML = assets.map(asset => {
    const visual = asset.media_kind === "video"
      ? `<video src="${escapeHtml(asset.media_url)}" muted preload="metadata"></video>`
      : `<img src="${escapeHtml(asset.media_url)}" alt="" loading="lazy">`;
    return `<button class="media-bin-card ${asset.id === scene?.selected_asset_id ? "active" : ""}" type="button" data-asset-id="${asset.id}" data-scene-id="${scene.id}">${visual}<span>${escapeHtml(asset.provider)} · option ${Number(asset.candidate_index) + 1}</span></button>`;
  }).join("") || `<p class="library-help">No media for this scene yet. Use Import or generate an image from Visual plan.</p>`;
}

function rulerStep(zoom) {
  if (zoom >= 20) return 10;
  if (zoom >= 8) return 30;
  if (zoom >= 4) return 60;
  return 120;
}

function renderTimeline() {
  if (!state.timelineClips.some(clip => clip.id === state.activeTimelineClipId)) {
    state.activeTimelineClipId = state.timelineClips[0]?.id || null;
  }
  const selectedClip = state.timelineClips.find(clip => clip.id === state.activeTimelineClipId) || null;
  if (selectedClip) state.activeTimelineSceneId = selectedClip.scene_id;
  const zoom = Number($("#timelineZoom").value || 8);
  const duration = timelineDuration();
  const contentWidth = Math.max(900, Math.ceil(duration * zoom) + 40);
  $("#timelineCanvas").style.setProperty("--content-width", `${contentWidth}px`);
  $("#timelineList").classList.toggle("razor-mode", state.editTool === "razor");
  $("#timelineList").innerHTML = state.timelineClips.map(clip => {
    const scene = sceneForClip(clip);
    const asset = selectedAssetForScene(scene);
    const clipDuration = Math.max(0.25, Number(clip.end_seconds) - Number(clip.start_seconds));
    const width = Math.max(18, clipDuration * zoom);
    const left = Number(clip.start_seconds) * zoom;
    const media = asset?.media_kind === "video"
      ? `<div class="timeline-clip-placeholder">VIDEO</div>`
      : asset ? `<img src="${escapeHtml(asset.media_url)}" alt="" loading="lazy">` : `<div class="timeline-clip-placeholder">No media</div>`;
    return `<div class="timeline-clip ${clip.id === state.activeTimelineClipId ? "active" : ""}" style="left:${left}px;width:${width}px" draggable="${state.editTool === "select"}" tabindex="0" role="button" data-clip-id="${clip.id}" data-scene-id="${scene?.id || ""}">
      <button class="trim-handle trim-left" type="button" data-trim-edge="left" aria-label="Trim clip start"></button>
      <div class="timeline-clip-media">${media}<span class="timeline-clip-number">${clip.position}</span></div>
      <strong class="timeline-clip-title">${escapeHtml(scene?.caption_text || scene?.narration || "Clip")}</strong>
      <div class="timeline-clip-info"><span>${clock(clip.start_seconds)}</span><span>${clipDuration.toFixed(1)}s</span></div>
      <button class="trim-handle trim-right" type="button" data-trim-edge="right" aria-label="Trim clip end"></button>
    </div>`;
  }).join("") || `<div class="queue-status">Create the visual plan first.</div>`;
  $("#captionTrack").innerHTML = state.scenes.map(scene => {
    const width = Math.max(18, (Number(scene.end_seconds) - Number(scene.start_seconds)) * zoom);
    const left = Number(scene.start_seconds) * zoom;
    return `<button class="caption-clip ${scene.id === state.activeTimelineSceneId ? "active" : ""}" type="button" style="left:${left}px;width:${width}px" data-scene-id="${scene.id}">${escapeHtml(scene.caption_text || scene.narration)}</button>`;
  }).join("");
  const voiceDuration = voiceoverDuration();
  $("#audioTrack").innerHTML = state.current?.voiceover_path ? `<div class="audio-wave" style="left:0;width:${Math.max(18, voiceDuration * zoom)}px"></div>` : `<span class="library-help">No voice-over attached</span>`;
  const step = rulerStep(zoom);
  const marks = [];
  for (let second = 0; second <= duration; second += step) {
    marks.push(`<span class="ruler-mark" style="left:${second * zoom}px">${clock(second)}</span>`);
    if (step * zoom >= 80) marks.push(`<span class="ruler-mark minor" style="left:${(second + step / 2) * zoom}px"></span>`);
  }
  $("#timelineRuler").innerHTML = marks.join("");
  const sync = timelineSyncDetails();
  $("#timelineSummary").textContent = state.timelineClips.length
    ? `${state.timelineClips.length} clips · Video ${clock(sync.video_duration)}${sync.voiceover_duration ? ` · VO ${clock(sync.voiceover_duration)}` : ""}`
    : "No clips yet";
  const syncBanner = $("#timelineSyncBanner");
  const needsFit = ["short", "long"].includes(sync.status);
  syncBanner.hidden = !needsFit;
  if (needsFit) {
    const difference = Math.abs(sync.difference_seconds);
    $("#timelineSyncTitle").textContent = sync.status === "short" ? "Visual track ends before the voice-over" : "Visual track is longer than the voice-over";
    $("#timelineSyncMessage").textContent = `${clock(difference)} difference. Fit the clips to the measured VO before export.`;
  }
  $("#previewScrubber").max = duration;
  $("#previewTotalTime").textContent = timecode(duration);
  fillTimelineInspector(sceneForClip(selectedClip), selectedClip);
  renderMediaBin();
  updateTimelineControls();
  updatePreviewAt(state.previewTime, false);
}

function selectTimelineClip(clipId, seek = true) {
  const clip = state.timelineClips.find(item => item.id === clipId);
  const scene = sceneForClip(clip);
  if (!clip || !scene) return;
  state.activeTimelineClipId = clip.id;
  state.activeTimelineSceneId = clip.scene_id;
  if (seek) {
    const audio = $("#previewAudio");
    state.previewTime = Number(clip.start_seconds);
    if (audio.getAttribute("src")) audio.currentTime = state.previewTime;
  }
  $$(".timeline-clip").forEach(element => element.classList.toggle("active", element.dataset.clipId === clip.id));
  $$(".caption-clip").forEach(element => element.classList.toggle("active", element.dataset.sceneId === scene.id));
  fillTimelineInspector(scene, clip);
  renderMediaBin();
  updateTimelineControls();
  updatePreviewAt(state.previewTime, false);
}

function selectTimelineScene(sceneId, seek = true) {
  const scene = state.scenes.find(item => item.id === sceneId);
  if (!scene) return;
  state.activeTimelineSceneId = scene.id;
  const matchingClip = state.timelineClips.find(clip => clip.scene_id === scene.id);
  if (matchingClip) state.activeTimelineClipId = matchingClip.id;
  if (seek) {
    state.previewTime = Number(scene.start_seconds);
    const audio = $("#previewAudio");
    if (audio.getAttribute("src")) audio.currentTime = state.previewTime;
  }
  $$(".caption-clip").forEach(element => element.classList.toggle("active", element.dataset.sceneId === scene.id));
  fillTimelineInspector(scene, matchingClip || null);
  renderMediaBin();
  updateTimelineControls();
  updatePreviewAt(state.previewTime, false);
}

function updateTimelineControls() {
  const clip = state.timelineClips.find(item => item.id === state.activeTimelineClipId);
  const canSplit = clip && state.previewTime - Number(clip.start_seconds) >= 0.25
    && Number(clip.end_seconds) - state.previewTime >= 0.25;
  $("#timelineSplitButton").disabled = !canSplit;
  $("#timelineDeleteButton").disabled = !clip || state.timelineClips.length <= 1;
  $("#timelineUndoButton").disabled = !state.timelineUndo.length;
  $("#timelineRedoButton").disabled = !state.timelineRedo.length;
}

async function saveTimelineScene() {
  const scene = state.scenes.find(item => item.id === state.activeTimelineSceneId);
  const clip = state.timelineClips.find(item => item.id === state.activeTimelineClipId);
  if (!scene) throw new Error("Select a scene first");
  const transitionPreset = $("#timelineTransition").value;
  const body = {
    caption_text: $("#timelineCaption").value.trim(),
    timeline_actions: [
      { type: "motion", params: { preset: $("#timelineMotion").value, strength: 0.55 } },
      { type: "transition", params: { preset: transitionPreset, duration: transitionPreset === "cut" ? 0 : Number($("#timelineTransitionDuration").value) } },
    ],
  };
  await api(`/api/scenes/${scene.id}`, { method: "PATCH", body: JSON.stringify(body) });
  delete state.captionDrafts[scene.id];
  if (clip) {
    rememberTimeline();
    await api(`/api/timeline-clips/${clip.id}`, {
      method: "PATCH", body: JSON.stringify({ duration_seconds: Number($("#timelineDuration").value) }),
    });
  }
  await openProject(state.current.id, true);
  activateTab("timeline");
  toast(`Scene ${scene.position} timeline saved`);
}

async function uploadReplacementMedia(file) {
  const scene = state.scenes.find(item => item.id === state.activeTimelineSceneId);
  if (!scene || !file) return;
  const button = $("#replaceMediaButton");
  button.disabled = true;
  button.textContent = "Uploading…";
  try {
    await api(`/api/scenes/${scene.id}/asset`, { method: "POST", body: file, headers: { "X-Filename": file.name } });
    await openProject(state.current.id, true);
    activateTab("timeline");
    toast(`Replacement selected for scene ${scene.position}`);
  } finally {
    button.disabled = false;
    button.textContent = "Upload replacement";
    $("#replacementMediaInput").value = "";
  }
}

async function saveCaptionStyle() {
  const scene = state.scenes.find(item => item.id === state.activeTimelineSceneId);
  if (scene && $("#timelineCaption").value.trim() !== (scene.caption_text ?? scene.narration ?? "")) {
    const updated = await api(`/api/scenes/${scene.id}`, {
      method: "PATCH", body: JSON.stringify({ caption_text: $("#timelineCaption").value.trim() }),
    });
    state.scenes = state.scenes.map(item => item.id === updated.id ? updated : item);
    delete state.captionDrafts[scene.id];
  }
  const result = await api(`/api/projects/${state.current.id}/caption-style`, {
    method: "POST", body: JSON.stringify(captionStyleFromInputs()),
  });
  state.current.caption_style = result.caption_style;
  renderTimeline();
  $("#autosaveStatus").textContent = "Caption preset saved";
  toast("Caption and style saved for this project");
}

const CAPTION_PRESETS = {
  clean: { text_color: "#FFFFFF", background_enabled: false, stroke_enabled: false, glow_enabled: false, shadow_enabled: false, bold: true },
  outline: { text_color: "#FFFFFF", background_enabled: false, stroke_enabled: true, stroke_color: "#000000", stroke_width: 4, glow_enabled: false, shadow_enabled: false, bold: true },
  boxed: { text_color: "#FFFFFF", background_enabled: true, background_color: "#000000", background_opacity: .78, stroke_enabled: false, glow_enabled: false, shadow_enabled: false, bold: true },
  yellow: { text_color: "#FFF200", background_enabled: false, stroke_enabled: true, stroke_color: "#000000", stroke_width: 3, glow_enabled: false, shadow_enabled: true, shadow_color: "#000000", shadow_blur: 2, shadow_x: 2, shadow_y: 2, bold: true },
  red: { text_color: "#FFFFFF", background_enabled: false, stroke_enabled: true, stroke_color: "#E33535", stroke_width: 5, glow_enabled: false, shadow_enabled: false, bold: true },
  shadow: { text_color: "#FFFFFF", background_enabled: false, stroke_enabled: false, glow_enabled: false, shadow_enabled: true, shadow_color: "#000000", shadow_blur: 7, shadow_x: 4, shadow_y: 4, bold: true },
};

function applyCaptionPreset(name) {
  const preset = CAPTION_PRESETS[name];
  if (!preset) return;
  state.captionFlags.bold = preset.bold;
  $("#captionTextColor").value = preset.text_color;
  $("#captionBackgroundEnabled").checked = preset.background_enabled;
  if (preset.background_color) $("#captionBackgroundColor").value = preset.background_color;
  if (preset.background_opacity != null) $("#captionBackgroundOpacity").value = preset.background_opacity;
  $("#captionStrokeEnabled").checked = preset.stroke_enabled;
  if (preset.stroke_color) $("#captionStrokeColor").value = preset.stroke_color;
  if (preset.stroke_width != null) $("#captionStrokeWidth").value = preset.stroke_width;
  $("#captionGlowEnabled").checked = preset.glow_enabled;
  $("#captionShadowEnabled").checked = preset.shadow_enabled;
  if (preset.shadow_color) $("#captionShadowColor").value = preset.shadow_color;
  if (preset.shadow_blur != null) $("#captionShadowBlur").value = preset.shadow_blur;
  if (preset.shadow_x != null) $("#captionShadowX").value = preset.shadow_x;
  if (preset.shadow_y != null) $("#captionShadowY").value = preset.shadow_y;
  $$("[data-caption-preset]").forEach(button => button.classList.toggle("active", button.dataset.captionPreset === name));
  $$("[data-style-toggle]").forEach(button => button.classList.toggle("active", Boolean(state.captionFlags[button.dataset.styleToggle])));
  updateCaptionPreviewStyle();
}

async function refreshFonts(selected) {
  state.fonts = (await api("/api/fonts")).fonts || [];
  loadFontOptions(selected);
  updateCaptionPreviewStyle();
}

async function uploadFont(file) {
  if (!file) return;
  const result = await api("/api/fonts/upload", { method: "POST", body: file, headers: { "X-Filename": file.name } });
  await refreshFonts(result.font.family);
  $("#fontDialog").close();
  $("#fontUploadInput").value = "";
  toast(`${result.font.family} installed locally`);
}

async function downloadFont(event) {
  event.preventDefault();
  const url = $("#fontUrlInput").value.trim();
  if (!url) throw new Error("Paste a direct TTF or OTF font link");
  const button = $("#confirmDownloadFont");
  button.disabled = true;
  button.textContent = "Downloading…";
  try {
    const result = await api("/api/fonts/download", { method: "POST", body: JSON.stringify({ url }) });
    await refreshFonts(result.font.family);
    $("#fontDialog").close();
    $("#fontUrlInput").value = "";
    toast(`${result.font.family} installed locally`);
  } finally {
    button.disabled = false;
    button.textContent = "Download font";
  }
}

const EXPORT_PRESETS = {
  "youtube-1080p": { resolution: "1920x1080", fps: 30, bitrate: "12000", audio: "192" },
  "youtube-1080p60": { resolution: "1920x1080", fps: 60, bitrate: "16000", audio: "192" },
  "youtube-1440p": { resolution: "2560x1440", fps: 30, bitrate: "24000", audio: "192" },
  "youtube-4k": { resolution: "3840x2160", fps: 30, bitrate: "35000", audio: "256" },
  "compact-720p": { resolution: "1280x720", fps: 30, bitrate: "6000", audio: "128" },
};

function applyExportPreset() {
  const preset = EXPORT_PRESETS[$("#exportPreset").value];
  if (preset) {
    $("#exportResolution").value = preset.resolution;
    $("#exportFps").value = String(preset.fps);
    $("#exportVideoBitrate").value = preset.bitrate;
    $("#exportAudioBitrate").value = preset.audio;
  }
  updateExportFields();
}

function exportDimensions() {
  const resolution = $("#exportResolution").value;
  if (resolution === "custom") return [Number($("#exportWidth").value), Number($("#exportHeight").value)];
  return resolution.split("x").map(Number);
}

function exportBitrate() {
  return $("#exportVideoBitrate").value === "custom"
    ? Number($("#exportCustomBitrate").value)
    : Number($("#exportVideoBitrate").value);
}

function updateExportFields(markCustom = false) {
  const customResolution = $("#exportResolution").value === "custom";
  $$(".custom-dimension").forEach(element => { element.hidden = !customResolution; });
  const customBitrate = $("#exportVideoBitrate").value === "custom";
  $(".custom-bitrate").hidden = !customBitrate;
  if (markCustom) $("#exportPreset").value = "custom";
  const [width, height] = exportDimensions();
  const bitrate = exportBitrate();
  $("#exportSummary").textContent = `${width} × ${height} · ${$("#exportFps").value} fps · ${(bitrate / 1000).toFixed(bitrate % 1000 ? 1 : 0)} Mbps · H.264 MP4`;
}

async function browseExportDirectory() {
  const result = await api(`/api/projects/${state.current.id}/choose-export-folder`, { method: "POST", body: "{}" });
  if (!result.cancelled) {
    $("#exportDirectory").value = result.path;
    toast("Export location selected");
  }
}

function openExportDialog() {
  if (!state.current) return;
  $("#exportProjectName").textContent = state.current.name;
  if (state.exportProjectId !== state.current.id) {
    $("#exportName").value = state.current.name;
    $("#exportDirectory").value = "";
    $("#exportPreset").value = "youtube-1080p";
    state.exportProjectId = state.current.id;
    applyExportPreset();
  } else {
    updateExportFields();
  }
  $("#exportDialog").showModal();
  refreshRenderStatus().catch(error => toast(error.message, true));
}

function showInspector(name) {
  $$("[data-inspector-tab]").forEach(button => button.classList.toggle("active", button.dataset.inspectorTab === name));
  $$("[data-inspector-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.inspectorPanel === name));
}

function pausePreview() {
  cancelAnimationFrame(state.manualPreviewFrame);
  state.manualPreviewFrame = null;
  cancelAnimationFrame(state.previewClockFrame);
  state.previewClockFrame = null;
  const audio = $("#previewAudio");
  if (!audio.paused) audio.pause();
  const video = $("#previewVideo");
  if (!video.paused) video.pause();
  state.isPreviewPlaying = false;
  $("#previewPlayButton").textContent = "▶";
}

function startAudioPreviewClock() {
  cancelAnimationFrame(state.previewClockFrame);
  const audio = $("#previewAudio");
  const tick = () => {
    if (audio.paused || audio.ended) { state.previewClockFrame = null; return; }
    updatePreviewAt(audio.currentTime);
    state.previewClockFrame = requestAnimationFrame(tick);
  };
  state.previewClockFrame = requestAnimationFrame(tick);
}

function startManualPreview() {
  state.manualPreviewStartedAt = performance.now() - state.previewTime * 1000;
  $("#previewPlayButton").textContent = "❚❚";
  state.isPreviewPlaying = true;
  const tick = now => {
    const current = (now - state.manualPreviewStartedAt) / 1000;
    if (current >= timelineDuration()) { pausePreview(); updatePreviewAt(0); return; }
    updatePreviewAt(current);
    state.manualPreviewFrame = requestAnimationFrame(tick);
  };
  state.manualPreviewFrame = requestAnimationFrame(tick);
}

async function togglePreview() {
  const audio = $("#previewAudio");
  if (audio.getAttribute("src")) {
    if (audio.paused) {
      if (state.previewTime >= timelineDuration() - 0.05) audio.currentTime = 0;
      else if (Math.abs(audio.currentTime - state.previewTime) > 0.05) audio.currentTime = state.previewTime;
      setPreviewLoadState(audio.readyState >= 3 ? "Playing" : "Loading…", audio.readyState >= 3 ? "" : "loading");
      await audio.play();
      state.isPreviewPlaying = true;
      $("#previewPlayButton").textContent = "❚❚";
      startAudioPreviewClock();
    } else {
      audio.pause();
      state.isPreviewPlaying = false;
      $("#previewPlayButton").textContent = "▶";
    }
    return;
  }
  if (state.manualPreviewFrame) pausePreview(); else startManualPreview();
}

function timelineSnapshot() {
  return state.timelineClips.map(clip => ({
    id: clip.id,
    scene_id: clip.scene_id,
    start_seconds: Number(clip.start_seconds),
    end_seconds: Number(clip.end_seconds),
    source_in_seconds: Number(clip.source_in_seconds || 0),
  }));
}

function rememberTimeline(snapshot = timelineSnapshot()) {
  state.timelineUndo.push(snapshot);
  if (state.timelineUndo.length > 30) state.timelineUndo.shift();
  state.timelineRedo = [];
  updateTimelineControls();
}

async function restoreTimelineSnapshot(snapshot) {
  const result = await api(`/api/projects/${state.current.id}/timeline/restore`, {
    method: "POST", body: JSON.stringify({ timeline_clips: snapshot }),
  });
  state.timelineClips = result.timeline_clips;
  if (!state.timelineClips.some(clip => clip.id === state.activeTimelineClipId)) {
    state.activeTimelineClipId = state.timelineClips[0]?.id || null;
  }
  renderTimeline();
}

async function undoTimeline() {
  if (!state.timelineUndo.length) return;
  const previous = state.timelineUndo.pop();
  state.timelineRedo.push(timelineSnapshot());
  await restoreTimelineSnapshot(previous);
  toast("Timeline change undone");
}

async function redoTimeline() {
  if (!state.timelineRedo.length) return;
  const next = state.timelineRedo.pop();
  state.timelineUndo.push(timelineSnapshot());
  await restoreTimelineSnapshot(next);
  toast("Timeline change restored");
}

async function reorderTimeline(sourceId, targetId, insertAfter) {
  if (!sourceId || !targetId || sourceId === targetId) return;
  const before = timelineSnapshot();
  const ordered = state.timelineClips.map(clip => clip.id).filter(id => id !== sourceId);
  let targetIndex = ordered.indexOf(targetId);
  if (insertAfter) targetIndex += 1;
  ordered.splice(targetIndex, 0, sourceId);
  const result = await api(`/api/projects/${state.current.id}/timeline/reorder`, {
    method: "POST", body: JSON.stringify({ clip_ids: ordered }),
  });
  rememberTimeline(before);
  state.timelineClips = result.timeline_clips;
  state.activeTimelineClipId = sourceId;
  renderTimeline();
  toast("Timeline order updated");
}

async function splitTimelineClip(clipId = state.activeTimelineClipId, atTime = state.previewTime) {
  const clip = state.timelineClips.find(item => item.id === clipId);
  if (!clip) throw new Error("Select a video clip first");
  const offset = Number(atTime) - Number(clip.start_seconds);
  const before = timelineSnapshot();
  const result = await api(`/api/timeline-clips/${clip.id}/split`, {
    method: "POST", body: JSON.stringify({ offset_seconds: offset }),
  });
  rememberTimeline(before);
  state.timelineClips = result.timeline_clips;
  state.activeTimelineClipId = result.new_clip_id;
  renderTimeline();
  toast("Clip split at playhead");
}

async function deleteTimelineClip() {
  const clip = state.timelineClips.find(item => item.id === state.activeTimelineClipId);
  if (!clip) throw new Error("Select a video clip first");
  const before = timelineSnapshot();
  const result = await api(`/api/timeline-clips/${clip.id}/delete`, { method: "POST", body: "{}" });
  rememberTimeline(before);
  state.timelineClips = result.timeline_clips;
  state.activeTimelineClipId = state.timelineClips[Math.min(clip.position - 1, state.timelineClips.length - 1)]?.id || null;
  renderTimeline();
  toast("Clip deleted; timeline closed the gap");
}

function retimeLocalTimeline() {
  let cursor = 0;
  state.timelineClips.forEach((clip, index) => {
    const duration = Math.max(0.25, Number(clip.end_seconds) - Number(clip.start_seconds));
    clip.position = index + 1;
    clip.start_seconds = cursor;
    clip.end_seconds = cursor + duration;
    cursor += duration;
  });
}

function beginClipTrim(event) {
  const handle = event.target.closest("[data-trim-edge]");
  const element = event.target.closest(".timeline-clip");
  if (!handle || !element || state.editTool !== "select") return;
  event.preventDefault();
  event.stopPropagation();
  pausePreview();
  const clip = state.timelineClips.find(item => item.id === element.dataset.clipId);
  if (!clip) return;
  selectTimelineClip(clip.id, false);
  state.trimSession = {
    clipId: clip.id,
    edge: handle.dataset.trimEdge,
    startX: event.clientX,
    duration: Number(clip.end_seconds) - Number(clip.start_seconds),
    sourceIn: Number(clip.source_in_seconds || 0),
    before: timelineSnapshot(),
    changed: false,
  };
  document.body.classList.add("is-trimming");
}

function moveClipTrim(event) {
  const session = state.trimSession;
  if (!session) return;
  const clip = state.timelineClips.find(item => item.id === session.clipId);
  if (!clip) return;
  const zoom = Number($("#timelineZoom").value || 8);
  let delta = (event.clientX - session.startX) / zoom;
  if (state.snapEnabled) delta = Math.round(delta * 10) / 10;
  let duration;
  let sourceIn = session.sourceIn;
  if (session.edge === "right") {
    duration = Math.max(0.25, session.duration + delta);
  } else {
    const scene = sceneForClip(clip);
    const isVideo = selectedAssetForScene(scene)?.media_kind === "video";
    if (isVideo) delta = Math.max(-session.sourceIn, delta);
    duration = Math.max(0.25, session.duration - delta);
    const appliedDelta = session.duration - duration;
    sourceIn = isVideo ? Math.max(0, session.sourceIn + appliedDelta) : 0;
  }
  clip.end_seconds = Number(clip.start_seconds) + duration;
  clip.source_in_seconds = sourceIn;
  retimeLocalTimeline();
  session.changed = Math.abs(duration - session.duration) > 0.001 || Math.abs(sourceIn - session.sourceIn) > 0.001;
  cancelAnimationFrame(state.trimFrame);
  state.trimFrame = requestAnimationFrame(renderTimeline);
}

async function endClipTrim() {
  const session = state.trimSession;
  if (!session) return;
  state.trimSession = null;
  document.body.classList.remove("is-trimming");
  cancelAnimationFrame(state.trimFrame);
  const clip = state.timelineClips.find(item => item.id === session.clipId);
  if (!session.changed || !clip) { renderTimeline(); return; }
  try {
    const result = await api(`/api/timeline-clips/${clip.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        duration_seconds: Number(clip.end_seconds) - Number(clip.start_seconds),
        source_in_seconds: Number(clip.source_in_seconds || 0),
      }),
    });
    rememberTimeline(session.before);
    state.timelineClips = result.timeline_clips;
    renderTimeline();
    toast("Clip duration saved");
  } catch (error) {
    state.timelineClips = session.before;
    renderTimeline();
    toast(error.message, true);
  }
}

async function fitTimelineToVoiceover() {
  if (!state.current) return;
  const before = timelineSnapshot();
  const button = $("#fitTimelineButton");
  button.disabled = true;
  button.textContent = "Fitting…";
  try {
    const result = await api(`/api/projects/${state.current.id}/timeline/fit-voiceover`, {
      method: "POST", body: "{}",
    });
    rememberTimeline(before);
    state.timelineClips = result.timeline_clips;
    state.timelineSync = result.timeline_sync;
    renderTimeline();
    toast(`Visual track now ends with the VO at ${clock(result.timeline_sync.voiceover_duration)}`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Fit visuals to VO";
  }
}

async function startRender() {
  if (!state.current) return;
  const [width, height] = exportDimensions();
  const outputName = $("#exportName").value.trim();
  if (!outputName) return toast("Enter a name for the exported video", true);
  const button = $("#renderButton");
  button.disabled = true;
  button.textContent = "Starting export…";
  try {
    const result = await api(`/api/projects/${state.current.id}/render`, { method: "POST", body: JSON.stringify({
      width, height,
      fps: Number($("#exportFps").value), burn_captions: $("#burnCaptions").checked,
      output_name: outputName,
      output_directory: $("#exportDirectory").value,
      preset: $("#exportPreset").value,
      video_bitrate_kbps: exportBitrate(),
      audio_bitrate_kbps: Number($("#exportAudioBitrate").value),
      caption_style: captionStyleFromInputs(),
    }) });
    if (result.timeline_clips) state.timelineClips = result.timeline_clips;
    state.timelineSync = result.timeline_sync || state.timelineSync;
    renderTimeline();
    toast(result.auto_fitted ? "Visuals fitted to the VO; local render started" : "Local render started");
    clearInterval(state.renderTimer);
    state.renderTimer = setInterval(() => refreshRenderStatus().catch(error => toast(error.message, true)), 2500);
    await refreshRenderStatus();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Export final MP4"; }
}

async function refreshRenderStatus() {
  if (!state.current) return;
  const result = await api(`/api/projects/${state.current.id}/render-status`);
  const render = result.render;
  if (!render) return;
  $("#renderState").textContent = render.status[0].toUpperCase() + render.status.slice(1);
  $("#renderProgress").value = Math.round(Number(render.progress || 0) * 100);
  $("#renderMessage").textContent = render.error || render.output_path || `${Math.round(Number(render.progress || 0) * 100)}% complete`;
  state.lastRenderOutputPath = render.output_path || state.lastRenderOutputPath;
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
$$(".tab[data-tab]").forEach(tab => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
$("#workflowExportButton").addEventListener("click", openExportDialog);
$("#editorExportButton").addEventListener("click", openExportDialog);
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
$("#timelineList").addEventListener("click", event => {
  const clip = event.target.closest(".timeline-clip");
  if (!clip || event.target.closest("[data-trim-edge]")) return;
  if (state.editTool === "razor") {
    const bounds = clip.getBoundingClientRect();
    const item = state.timelineClips.find(candidate => candidate.id === clip.dataset.clipId);
    const atTime = Number(item.start_seconds) + ((event.clientX - bounds.left) / bounds.width) * (Number(item.end_seconds) - Number(item.start_seconds));
    state.previewTime = atTime;
    splitTimelineClip(item.id, atTime).catch(error => toast(error.message, true));
  } else {
    selectTimelineClip(clip.dataset.clipId);
  }
});
$("#timelineList").addEventListener("pointerdown", beginClipTrim);
window.addEventListener("pointermove", moveClipTrim);
window.addEventListener("pointerup", () => endClipTrim().catch(error => toast(error.message, true)));
window.addEventListener("pointercancel", () => endClipTrim().catch(error => toast(error.message, true)));
$("#captionTrack").addEventListener("click", event => {
  const clip = event.target.closest(".caption-clip");
  if (clip) { selectTimelineScene(clip.dataset.sceneId); showInspector("text"); }
});
$("#mediaBin").addEventListener("click", event => {
  const asset = event.target.closest("[data-asset-id]");
  if (asset) selectAsset(asset.dataset.sceneId, asset.dataset.assetId).catch(error => toast(error.message, true));
});
$("#timelineList").addEventListener("keydown", event => {
  const clip = event.target.closest(".timeline-clip");
  if (clip && event.key === "Enter") { event.preventDefault(); selectTimelineClip(clip.dataset.clipId); }
});
$("#timelineList").addEventListener("dragstart", event => {
  const clip = event.target.closest(".timeline-clip");
  if (!clip || state.editTool !== "select") { event.preventDefault(); return; }
  state.draggedClipId = clip.dataset.clipId;
  clip.classList.add("dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", state.draggedClipId);
});
$("#timelineList").addEventListener("dragend", event => {
  event.target.closest(".timeline-clip")?.classList.remove("dragging");
  state.draggedClipId = null;
});
$("#timelineList").addEventListener("dragover", event => {
  if (event.target.closest(".timeline-clip")) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; }
});
$("#timelineList").addEventListener("drop", event => {
  const target = event.target.closest(".timeline-clip");
  if (!target) return;
  event.preventDefault();
  const sourceId = state.draggedClipId || event.dataTransfer.getData("text/plain");
  const insertAfter = event.clientX > target.getBoundingClientRect().left + target.getBoundingClientRect().width / 2;
  reorderTimeline(sourceId, target.dataset.clipId, insertAfter).catch(error => toast(error.message, true));
});
$("#timelineZoom").addEventListener("input", renderTimeline);
$("#timelineSelectTool").addEventListener("click", () => {
  state.editTool = "select";
  $("#timelineSelectTool").classList.add("active");
  $("#timelineRazorTool").classList.remove("active");
  renderTimeline();
});
$("#timelineRazorTool").addEventListener("click", () => {
  state.editTool = "razor";
  $("#timelineRazorTool").classList.add("active");
  $("#timelineSelectTool").classList.remove("active");
  renderTimeline();
  toast("Razor ready — click a video clip to cut it");
});
$("#timelineSplitButton").addEventListener("click", () => splitTimelineClip().catch(error => toast(error.message, true)));
$("#timelineDeleteButton").addEventListener("click", () => deleteTimelineClip().catch(error => toast(error.message, true)));
$("#timelineUndoButton").addEventListener("click", () => undoTimeline().catch(error => toast(error.message, true)));
$("#timelineRedoButton").addEventListener("click", () => redoTimeline().catch(error => toast(error.message, true)));
$("#timelineSnapButton").addEventListener("click", () => {
  state.snapEnabled = !state.snapEnabled;
  $("#timelineSnapButton").classList.toggle("active", state.snapEnabled);
  $("#timelineSnapButton").setAttribute("aria-pressed", String(state.snapEnabled));
  toast(`Timeline snapping ${state.snapEnabled ? "enabled" : "disabled"}`);
});
$("#timelineCanvas").addEventListener("click", event => {
  if (event.target.closest(".timeline-clip,.caption-clip")) return;
  const scroll = $("#timelineScroll");
  const labelWidth = parseFloat(getComputedStyle($(".studio-editor")).getPropertyValue("--track-label-width")) || 128;
  const x = event.clientX - scroll.getBoundingClientRect().left + scroll.scrollLeft - labelWidth;
  const value = Math.max(0, x / Number($("#timelineZoom").value || 8));
  const audio = $("#previewAudio");
  if (audio.getAttribute("src")) audio.currentTime = value;
  updatePreviewAt(value);
});
$("#saveTimelineButton").addEventListener("click", () => saveTimelineScene().catch(error => toast(error.message, true)));
$("#replaceMediaButton").addEventListener("click", () => $("#replacementMediaInput").click());
[$("#editorImportButton"), $("#libraryImportButton"), $("#libraryUploadButton"), $("#timelineAddButton")].forEach(button => button.addEventListener("click", () => {
  if (!state.activeTimelineSceneId) return toast("Select a scene before importing media", true);
  $("#replacementMediaInput").click();
}));
$("#replacementMediaInput").addEventListener("change", event => uploadReplacementMedia(event.target.files[0]).catch(error => toast(error.message, true)));
$("#timelineTransition").addEventListener("change", event => { $("#timelineTransitionDuration").disabled = event.target.value === "cut"; updatePreviewAt(state.previewTime, false); });
$("#timelineCaption").addEventListener("input", event => {
  if (state.activeTimelineSceneId) state.captionDrafts[state.activeTimelineSceneId] = event.target.value;
  updatePreviewAt(state.previewTime, false);
});
$("#timelineCaption").addEventListener("input", () => { $("#autosaveStatus").textContent = "Unsaved caption changes"; });
$("#timelineMotion").addEventListener("change", () => updatePreviewAt(state.previewTime, false));
$("#previewPlayButton").addEventListener("click", () => togglePreview().catch(error => toast(error.message, true)));
$("#previewBackButton").addEventListener("click", () => {
  const index = Math.max(0, state.timelineClips.findIndex(clip => clip.id === state.activeTimelineClipId) - 1);
  if (state.timelineClips[index]) selectTimelineClip(state.timelineClips[index].id);
});
$("#previewForwardButton").addEventListener("click", () => {
  const current = state.timelineClips.findIndex(clip => clip.id === state.activeTimelineClipId);
  const index = Math.min(state.timelineClips.length - 1, current + 1);
  if (state.timelineClips[index]) selectTimelineClip(state.timelineClips[index].id);
});
$("#previewFullscreenButton").addEventListener("click", () => $("#previewStage").requestFullscreen?.());
$("#previewScrubber").addEventListener("input", event => {
  const value = Number(event.target.value);
  const audio = $("#previewAudio");
  if (audio.getAttribute("src")) audio.currentTime = value;
  updatePreviewAt(value);
});
$("#previewAudio").addEventListener("loadedmetadata", event => {
  const duration = timelineDuration();
  $("#previewScrubber").max = duration;
  $("#previewTotalTime").textContent = timecode(duration);
  updatePreviewSeekVisual(state.previewTime, duration);
  updatePreviewBuffered();
  setPreviewLoadState("Ready");
  if (Number.isFinite(event.target.duration) && event.target.duration > 0 && Math.abs(event.target.duration - Number(state.current?.duration_seconds || 0)) > 0.25) {
    $("#previewTotalTime").title = `Audio metadata: ${timecode(event.target.duration)}`;
  }
});
$("#previewAudio").addEventListener("durationchange", () => {
  $("#previewTotalTime").textContent = timecode(timelineDuration());
  updatePreviewSeekVisual(state.previewTime);
});
$("#previewAudio").addEventListener("progress", updatePreviewBuffered);
$("#previewAudio").addEventListener("timeupdate", event => {
  if (!state.previewClockFrame) updatePreviewAt(event.target.currentTime);
});
$("#previewAudio").addEventListener("loadstart", () => setPreviewLoadState("Loading VO…", "loading"));
$("#previewAudio").addEventListener("waiting", () => setPreviewLoadState("Buffering…", "loading"));
$("#previewAudio").addEventListener("stalled", () => setPreviewLoadState("Waiting…", "loading"));
$("#previewAudio").addEventListener("canplay", () => setPreviewLoadState("Ready"));
$("#previewAudio").addEventListener("seeked", () => setPreviewLoadState(state.isPreviewPlaying ? "Playing" : "Ready"));
$("#previewAudio").addEventListener("error", () => setPreviewLoadState("VO error", "error"));
$("#previewAudio").addEventListener("play", () => {
  state.isPreviewPlaying = true;
  $("#previewPlayButton").textContent = "❚❚";
  setPreviewLoadState("Playing");
  startAudioPreviewClock();
});
$("#previewAudio").addEventListener("pause", () => {
  cancelAnimationFrame(state.previewClockFrame);
  state.previewClockFrame = null;
  state.isPreviewPlaying = false;
  if (!$("#previewVideo").paused) $("#previewVideo").pause();
  $("#previewPlayButton").textContent = "▶";
  if (!$("#previewAudio").ended) setPreviewLoadState("Ready");
});
$("#previewAudio").addEventListener("ended", event => {
  state.isPreviewPlaying = false;
  $("#previewPlayButton").textContent = "▶";
  setPreviewLoadState("Ended");
  updatePreviewAt(Math.min(timelineDuration(), Number(event.target.duration || timelineDuration())));
});
$("#saveCaptionStyleButton").addEventListener("click", () => saveCaptionStyle().catch(error => toast(error.message, true)));
$$("[data-inspector-tab]").forEach(button => button.addEventListener("click", () => showInspector(button.dataset.inspectorTab)));
$$("[data-style-toggle]").forEach(button => button.addEventListener("click", () => {
  const key = button.dataset.styleToggle;
  state.captionFlags[key] = !state.captionFlags[key];
  button.classList.toggle("active", state.captionFlags[key]);
  updateCaptionPreviewStyle();
}));
$$("[data-caption-case]").forEach(button => button.addEventListener("click", () => {
  state.captionCase = button.dataset.captionCase;
  $$("[data-caption-case]").forEach(item => item.classList.toggle("active", item === button));
  updateCaptionPreviewStyle();
}));
$$("[data-caption-align]").forEach(button => button.addEventListener("click", () => {
  state.captionAlignment = button.dataset.captionAlign;
  $$("[data-caption-align]").forEach(item => item.classList.toggle("active", item === button));
  updateCaptionPreviewStyle();
}));
$$("[data-caption-preset]").forEach(button => button.addEventListener("click", () => applyCaptionPreset(button.dataset.captionPreset)));
$("#captionSizeRange").addEventListener("input", event => { $("#captionSize").value = event.target.value; updateCaptionPreviewStyle(); });
$("#captionSize").addEventListener("input", event => { $("#captionSizeRange").value = event.target.value; updateCaptionPreviewStyle(); });
$$('#captionFont,#captionPosition,#captionTextColor,#captionCharacterSpacing,#captionLineSpacing,#captionScale,#captionPositionX,#captionPositionY,#captionRotation,#captionOpacity,#captionStrokeEnabled,#captionStrokeColor,#captionStrokeWidth,#captionBackgroundEnabled,#captionBackgroundColor,#captionBackgroundOpacity,#captionGlowEnabled,#captionGlowColor,#captionGlowRadius,#captionShadowEnabled,#captionShadowColor,#captionShadowBlur,#captionShadowX,#captionShadowY,#captionMaxLines,#captionWordsPerLine').forEach(input => input.addEventListener("input", () => { updateCaptionPreviewStyle(); updatePreviewAt(state.previewTime, false); }));
$("#addFontButton").addEventListener("click", () => $("#fontDialog").showModal());
$("#downloadFontButton").addEventListener("click", () => $("#fontDialog").showModal());
$("#dialogUploadFontButton").addEventListener("click", () => $("#fontUploadInput").click());
$("#fontUploadInput").addEventListener("change", event => uploadFont(event.target.files[0]).catch(error => toast(error.message, true)));
$("#fontDownloadForm").addEventListener("submit", event => downloadFont(event).catch(error => toast(error.message, true)));
$$("[data-editor-tool]").forEach(button => button.addEventListener("click", () => {
  $$("[data-editor-tool]").forEach(item => item.classList.toggle("active", item === button));
  const tool = button.dataset.editorTool;
  $("#libraryTitle").textContent = tool[0].toUpperCase() + tool.slice(1);
  if (tool === "captions") showInspector("text");
  if (["effects", "transitions"].includes(tool)) showInspector("animation");
  if (tool === "media") renderMediaBin();
}));
$("#renderButton").addEventListener("click", startRender);
$("#fitTimelineButton").addEventListener("click", fitTimelineToVoiceover);
$("#browseExportDirectory").addEventListener("click", () => browseExportDirectory().catch(error => toast(error.message, true)));
$("#exportPreset").addEventListener("change", applyExportPreset);
$("#exportResolution").addEventListener("change", () => updateExportFields(true));
$("#exportFps").addEventListener("change", () => updateExportFields(true));
$("#exportVideoBitrate").addEventListener("change", () => updateExportFields(true));
$("#exportAudioBitrate").addEventListener("change", () => updateExportFields(true));
$$('#exportWidth,#exportHeight,#exportCustomBitrate').forEach(input => input.addEventListener("input", () => updateExportFields(true)));
$("#openOutputButton").addEventListener("click", async () => {
  try {
    const result = await api(`/api/projects/${state.current.id}/open-folder`, {
      method: "POST", body: JSON.stringify({ kind: "renders", path: $("#exportDirectory").value }),
    });
    toast(`Opened ${result.path}`);
  } catch (error) { toast(error.message, true); }
});
document.addEventListener("keydown", event => {
  if (event.defaultPrevented || !document.body.classList.contains("editor-mode") || event.target.closest("input,textarea,select,button,[contenteditable='true']")) return;
  if (event.code === "Space") { event.preventDefault(); togglePreview().catch(error => toast(error.message, true)); }
  if (!event.metaKey && !event.ctrlKey && event.key.toLowerCase() === "v") {
    event.preventDefault(); $("#timelineSelectTool").click();
  }
  if (!event.metaKey && !event.ctrlKey && event.key.toLowerCase() === "b") {
    event.preventDefault(); $("#timelineRazorTool").click();
  }
  if ((event.key === "Backspace" || event.key === "Delete") && state.activeTimelineClipId) {
    event.preventDefault(); deleteTimelineClip().catch(error => toast(error.message, true));
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    (event.shiftKey ? redoTimeline() : undoTimeline()).catch(error => toast(error.message, true));
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const next = Math.max(0, Math.min(timelineDuration(), state.previewTime + direction));
    const audio = $("#previewAudio");
    if (audio.getAttribute("src")) audio.currentTime = next;
    updatePreviewAt(next);
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault(); saveCaptionStyle().catch(error => toast(error.message, true));
  }
});

boot();
