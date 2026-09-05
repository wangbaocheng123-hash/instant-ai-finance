const state = {
  dashboard: null,
  works: [],
  selectedCreatorId: localStorage.getItem("collector.selectedCreatorId") || "",
  selectedSettings: null,
  monitoring: false,
  singleVideo: null,
  videoLookupVersion: 0,
  videoLookupTimer: null,
  videoLookupController: null,
  singleVideoSubmitting: false,
};

const $ = (selector) => document.querySelector(selector);
const APP_PREFIX = window.location.pathname.startsWith("/collector") ? "/collector" : "";

function appUrl(path) {
  const value = String(path || "");
  if (!APP_PREFIX || !value.startsWith("/")) return value;
  if (value === APP_PREFIX || value.startsWith(`${APP_PREFIX}/`)) return value;
  return `${APP_PREFIX}${value}`;
}

async function api(path, options = {}) {
  const response = await fetch(appUrl(path), {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function formatDate(value) {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 19);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
  return `${(size / 1024 ** 3).toFixed(1)} GB`;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 3600);
}

function currentCreator() {
  return (state.dashboard?.creators || []).find((item) => item.id === state.selectedCreatorId)
    || state.dashboard?.creators?.[0]
    || null;
}

function pendingTransfers(counts) {
  return ["pending", "manifest_accepted", "media_uploading", "finalizing", "retry_wait"]
    .reduce((total, name) => total + Number(counts?.[name] || 0), 0);
}

function renderSummary() {
  const dashboard = state.dashboard || {};
  const status = dashboard.status || {};
  $("#serviceState").textContent = status.service === "running" ? "正常待命" : "已停止";
  $("#worksCount").textContent = Number(dashboard.counts?.works || 0).toLocaleString("zh-CN");
  $("#commentsCount").textContent = Number(dashboard.counts?.comments || 0).toLocaleString("zh-CN");
  $("#pendingCount").textContent = pendingTransfers(dashboard.transfer_counts).toLocaleString("zh-CN");
}

function renderCreators() {
  const tabs = $("#creatorTabs");
  const creators = state.dashboard?.creators || [];
  const ids = creators.map((item) => item.id);
  if (!ids.includes(state.selectedCreatorId)) state.selectedCreatorId = ids[0] || "";
  localStorage.setItem("collector.selectedCreatorId", state.selectedCreatorId);
  tabs.replaceChildren();
  for (const creator of creators) {
    const tab = element("button", "creator-tab", creator.name);
    tab.type = "button";
    tab.role = "tab";
    tab.dataset.creatorId = creator.id;
    tab.setAttribute("aria-selected", String(creator.id === state.selectedCreatorId));
    if (creator.id === state.selectedCreatorId) tab.classList.add("active");
    if (creator.sync?.busy || creator.sync?.queued) {
      const indicator = element("span", "tab-indicator", "运行中");
      tab.append(indicator);
    }
    tabs.append(tab);
  }
  renderCreatorHeadline();
  renderSyncStatus();
}

function renderCreatorHeadline() {
  const creator = currentCreator();
  $("#libraryTitle").textContent = creator ? `${creator.name}作品库` : "尚未新增博主";
  $("#creatorDescription").textContent = creator
    ? `${creator.name}已采集作品展示；查看不会打开抖音浏览器。`
    : "点击加号新增第一个博主。";
  $("#settingsButton").disabled = !creator;
  const count = Number(creator?.history_limit || 1);
  $("#runButton").textContent = `抓取最近 ${count} 条`;
}

function renderSyncStatus() {
  const creator = currentCreator();
  const line = $("#syncStatus");
  const button = $("#runButton");
  if (!creator) {
    line.textContent = "尚未配置博主。";
    line.className = "status-line error";
    button.disabled = true;
    return;
  }
  const sync = creator.sync || {};
  const busy = Boolean(sync.busy || sync.queued);
  button.disabled = busy || !creator.profile_configured;
  line.className = `status-line${busy ? " busy" : (sync.has_error ? " error" : "")}`;
  if (!creator.profile_configured) {
    line.textContent = `${creator.name} 尚未配置抖音主页，请先打开“设置”。`;
  } else if (busy) {
    line.textContent = `正在处理：${sync.message || "采集任务运行中"}`;
  } else if (sync.has_error) {
    line.textContent = sync.message || "上次采集未正常完成，请查看脱敏诊断状态。";
  } else if (sync.last_finished_at) {
    line.textContent = `上次完成 ${formatDate(sync.last_finished_at)}；当前等待手动操作。`;
  } else {
    line.textContent = "当前没有采集任务。只有点击抓取按钮并确认后才会运行。";
  }
}

function mediaPreview(work) {
  const preview = element("div", "work-preview");
  const asset = work.primary_asset;
  if (asset?.mime_type?.startsWith("video/")) {
    const video = document.createElement("video");
    video.src = appUrl(asset.content_url);
    video.muted = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", `${work.title || "作品"}视频预览`);
    preview.append(video);
  } else if (asset?.mime_type?.startsWith("image/")) {
    const image = document.createElement("img");
    image.src = appUrl(asset.content_url);
    image.alt = work.title || "作品图片";
    image.loading = "lazy";
    preview.append(image);
  } else {
    preview.append(element("span", "preview-placeholder", "暂无预览"));
  }
  const type = asset?.mime_type?.startsWith("image/") ? "图文" : "视频";
  preview.append(element("span", "media-type", type));
  return preview;
}

function renderWorks() {
  const list = $("#worksList");
  list.replaceChildren();
  $("#visibleWorksCount").textContent = `${state.works.length} 条`;
  if (!state.works.length) {
    list.append(element("div", "empty", "当前博主还没有已采集作品。可在上方设置抓取条数后手动启动。"));
    return;
  }
  for (const work of state.works) {
    const card = element("article", "work-card");
    const openMedia = element("button", "media-button");
    openMedia.type = "button";
    openMedia.dataset.workId = String(work.id);
    openMedia.setAttribute("aria-label", `查看${work.title || "作品"}`);
    openMedia.append(mediaPreview(work));

    const body = element("div", "work-card-body");
    body.append(element("div", "work-date", formatDate(work.published_at || work.discovered_at)));
    body.append(element("h3", "work-title", work.title || "未命名作品"));
    if (work.description && work.description !== work.title) {
      body.append(element("p", "work-description", work.description));
    }
    const stats = element("div", "work-stats");
    stats.append(
      element("span", "", `评论 ${Number(work.comment_count || 0).toLocaleString("zh-CN")}`),
      element("span", "", `媒体 ${work.asset_count || 0}`),
      element("span", "", work.status || "已入库"),
    );
    const actions = element("div", "work-actions");
    const detailButton = element("button", "text-button", "查看作品与评论");
    detailButton.type = "button";
    detailButton.dataset.workId = String(work.id);
    actions.append(detailButton);
    body.append(stats, actions);
    card.append(openMedia, body);
    list.append(card);
  }
}

function stateClass(name) {
  if (["delivered", "verified"].includes(name)) return "good";
  if (["retry_wait", "pending", "manifest_accepted", "media_uploading", "finalizing"].includes(name)) return "warn";
  if (["dead_letter", "sender_error"].includes(name)) return "bad";
  return "";
}

function renderTransfers() {
  const counts = state.dashboard?.transfer_counts || {};
  const summary = $("#transferSummary");
  summary.replaceChildren();
  const labels = {
    delivered: "已送达",
    pending: "待发送",
    retry_wait: "等待重试",
    dead_letter: "人工处理",
    manifest_accepted: "清单已接收",
    media_uploading: "媒体上传中",
    finalizing: "等待确认",
    superseded: "已被新版本替代",
  };
  for (const [name, label] of Object.entries(labels)) {
    const count = Number(counts[name] || 0);
    if (!count && name !== "delivered") continue;
    summary.append(element("span", `state-chip ${stateClass(name)}`, `${label} ${count}`));
  }
  const list = $("#transferList");
  list.replaceChildren();
  const transfers = state.dashboard?.recent_transfers || [];
  if (!transfers.length) {
    list.append(element("div", "empty", "暂无传输记录。"));
    return;
  }
  for (const item of transfers) {
    const row = element("div", "transfer-row");
    row.append(
      element("strong", "", `作品 ${item.source_work_id || "—"}`),
      element("span", "", `${labels[item.status] || item.status} · ${formatDate(item.updated_at)}`),
    );
    if (item.last_error_code) row.append(element("div", "transfer-error", `错误代码：${item.last_error_code}`));
    list.append(row);
  }
}

async function loadWorks() {
  if (!state.selectedCreatorId) {
    state.works = [];
    renderWorks();
    return;
  }
  const result = await api(`/api/collector/works?limit=200&creator_id=${encodeURIComponent(state.selectedCreatorId)}`);
  state.works = result.works || [];
  renderWorks();
}

async function refresh(options = {}) {
  const keepCreator = state.selectedCreatorId;
  state.dashboard = await api("/api/collector/dashboard");
  state.selectedCreatorId = keepCreator;
  renderSummary();
  renderCreators();
  await loadWorks();
  renderTransfers();
  if (!options.quiet) showToast("采集端已刷新");
}

async function selectCreator(creatorId) {
  state.selectedCreatorId = creatorId;
  localStorage.setItem("collector.selectedCreatorId", creatorId);
  renderCreators();
  await loadWorks();
}

function settingsPayload() {
  return {
    name: $("#settingsName").value.trim(),
    profile_url: $("#settingsProfileUrl").value.trim(),
    history_limit: Number($("#settingsHistoryLimit").value),
    comments_enabled: $("#settingsCommentsEnabled").checked,
    comment_limit: Number($("#settingsCommentLimit").value),
    comment_tracking_hours: Number($("#settingsTrackingHours").value),
  };
}

async function openSettings() {
  const creator = currentCreator();
  if (!creator) return;
  const settings = await api(`/api/collector/creators/${encodeURIComponent(creator.id)}/settings`);
  state.selectedSettings = settings;
  $("#settingsName").value = settings.name || "";
  $("#settingsProfileUrl").value = settings.profile_url || "";
  $("#settingsHistoryLimit").value = String(settings.history_limit || 1);
  $("#settingsCommentsEnabled").checked = Boolean(settings.comments_enabled);
  $("#settingsCommentLimit").value = String(settings.comment_limit || 5000);
  $("#settingsTrackingHours").value = String(settings.comment_tracking_hours || 24);
  state.videoLookupVersion += 1;
  window.clearTimeout(state.videoLookupTimer);
  state.videoLookupController?.abort();
  state.singleVideo = null;
  $("#settingsVideoUrl").value = "";
  $("#singleVideoPreview").textContent = `链接将保存到“${creator.name}”。识别链接不会启动采集。`;
  $("#singleVideoPreview").dataset.error = "false";
  toggleCommentOptions();
  $("#settingsDialog").showModal();
}

async function saveSettings({ close = true } = {}) {
  const creator = currentCreator();
  if (!creator) throw new Error("请先选择博主");
  if (!$("#settingsForm").reportValidity()) throw new Error("请完整填写采集设置");
  const saved = await api(`/api/collector/creators/${encodeURIComponent(creator.id)}/settings`, {
    method: "POST",
    body: JSON.stringify(settingsPayload()),
  });
  state.selectedSettings = saved;
  if (close) $("#settingsDialog").close();
  await refresh({ quiet: true });
  showToast("设置已保存，不会自动采集");
  return saved;
}

async function runSelected({ forceComments = false } = {}) {
  const creator = currentCreator();
  if (!creator) return;
  const count = Number(creator.history_limit || state.selectedSettings?.history_limit || 1);
  const action = forceComments
    ? `补抓“${creator.name}”最近 ${count} 条作品的当前可见评论`
    : `抓取“${creator.name}”最近 ${count} 条作品`;
  if (!window.confirm(`确认现在${action}吗？\n本次任务完成后会恢复等待，不会持续采集。`)) return;
  await api("/api/collector/run-once", {
    method: "POST",
    body: JSON.stringify({
      creator_id: creator.id,
      force_comments: Boolean(forceComments),
      videos_only: false,
    }),
  });
  showToast("已提交一次手动采集任务");
  await refresh({ quiet: true });
  monitorCollection();
}

function toggleCommentOptions() {
  const disabled = !$("#settingsCommentsEnabled").checked;
  $("#settingsCommentLimit").disabled = disabled;
  $("#settingsTrackingHours").disabled = disabled;
  $("#forceCommentsButton").disabled = disabled;
  updateSingleVideoControls();
}

function updateSingleVideoControls() {
  const hasInput = Boolean($("#settingsVideoUrl").value.trim());
  const ready = Boolean(state.singleVideo) && hasInput;
  const pending = state.singleVideoSubmitting || (hasInput && !ready);
  $("#settingsVideoUrl").disabled = state.singleVideoSubmitting;
  $("#runSingleVideoButton").disabled = !ready || state.singleVideoSubmitting;
  $("#saveAndRunButton").disabled = pending;
  $("#saveAndRunButton").textContent = hasInput ? "保存并抓取这条视频" : "保存并抓取";
  $("#forceCommentsButton").disabled = pending || !$("#settingsCommentsEnabled").checked;
  $("#forceCommentsButton").textContent = hasInput ? "补抓这条视频评论" : "补抓当前可见评论";
}

function scheduleVideoLookup() {
  const version = ++state.videoLookupVersion;
  const creator = currentCreator();
  const input = $("#settingsVideoUrl").value.trim();
  window.clearTimeout(state.videoLookupTimer);
  state.videoLookupController?.abort();
  state.singleVideo = null;
  $("#singleVideoPreview").dataset.error = "false";
  $("#singleVideoPreview").textContent = input ? "正在识别这条视频…" : "请确认链接属于当前博主。识别链接不会启动采集。";
  updateSingleVideoControls();
  if (!input || !creator) return;
  state.videoLookupTimer = window.setTimeout(async () => {
    const controller = new AbortController();
    state.videoLookupController = controller;
    try {
      const target = await api("/api/collector/resolve-video", {
        method: "POST", signal: controller.signal,
        body: JSON.stringify({ creator_id: creator.id, video_url: input }),
      });
      if (version !== state.videoLookupVersion || currentCreator()?.id !== creator.id) return;
      state.singleVideo = { ...target, input };
      $("#singleVideoPreview").textContent = `已识别视频 ${target.video_id}，将归入“${creator.name}”。点击抓取后自动下载并按设置采集评论。`;
    } catch (error) {
      if (version !== state.videoLookupVersion || error.name === "AbortError") return;
      $("#singleVideoPreview").textContent = error.message;
      $("#singleVideoPreview").dataset.error = "true";
    } finally {
      if (version === state.videoLookupVersion) updateSingleVideoControls();
    }
  }, 450);
}

async function runSingleVideo({ forceComments = false } = {}) {
  if (state.singleVideoSubmitting) return;
  const creator = currentCreator();
  const target = state.singleVideo;
  if (!creator || !target || target.creator_id !== creator.id || target.input !== $("#settingsVideoUrl").value.trim()) {
    throw new Error("请等待视频链接识别完成后再抓取。");
  }
  if (!$("#settingsForm").reportValidity()) return;
  if (!window.confirm(`确认只抓取视频 ${target.video_id}，并归入“${creator.name}”吗？\n请确认这是该博主的作品。本次不会抓取最近多条视频。`)) return;
  state.singleVideoSubmitting = true;
  updateSingleVideoControls();
  try {
    const saved = await saveSettings({ close: false });
    await api("/api/collector/run-once", {
      method: "POST",
      body: JSON.stringify({
        creator_id: creator.id, video_url: target.video_url,
        force_comments: Boolean(forceComments), videos_only: !saved.comments_enabled,
      }),
    });
    $("#settingsDialog").close();
    showToast("已提交指定视频任务，只处理这一条");
    await refresh({ quiet: true });
    monitorCollection();
  } finally {
    state.singleVideoSubmitting = false;
    updateSingleVideoControls();
  }
}

async function openWork(workId) {
  const dialog = $("#workDialog");
  $("#detailTitle").textContent = "正在读取…";
  $("#detailBody").replaceChildren();
  dialog.showModal();
  try {
    const detail = await api(`/api/collector/works/${workId}`);
    const work = detail.work || {};
    $("#detailTitle").textContent = work.title || "未命名作品";
    const body = $("#detailBody");
    const asset = detail.assets?.find((item) => item.mime_type?.startsWith("video/"))
      || detail.assets?.find((item) => item.mime_type?.startsWith("image/"));
    if (asset?.mime_type?.startsWith("video/")) {
      const video = document.createElement("video");
      video.className = "detail-media";
      video.controls = true;
      video.preload = "metadata";
      video.playsInline = true;
      video.src = appUrl(asset.content_url);
      body.append(video);
    } else if (asset?.mime_type?.startsWith("image/")) {
      const image = document.createElement("img");
      image.className = "detail-media";
      image.src = appUrl(asset.content_url);
      image.alt = work.title || "作品图片";
      body.append(image);
    }
    const meta = element("div", "detail-meta");
    meta.append(
      element("span", "", work.creator || "未知博主"),
      element("span", "", formatDate(work.published_at || work.discovered_at)),
      element("span", "", `${work.comment_count || 0} 条评论`),
      element("span", "", `${detail.assets?.length || 0} 个媒体文件`),
      element("span", "", asset ? formatBytes(asset.size_bytes) : ""),
    );
    body.append(meta);
    if (work.description) body.append(element("p", "detail-description", work.description));
    body.append(element("h3", "comments-heading", `评论（${detail.comments?.length || 0}）`));
    const commentList = element("div", "comment-list");
    if (!detail.comments?.length) commentList.append(element("div", "empty", "这条作品暂时没有已采集评论。"));
    for (const comment of detail.comments || []) {
      const row = element("article", "comment");
      const top = element("div", "comment-topline");
      top.append(element("strong", "", comment.author || "匿名用户"));
      if (comment.kind === "creator_reply") top.append(element("span", "state-chip good", "作者回复"));
      if (comment.author_liked) top.append(element("span", "state-chip warn", "作者点赞"));
      if (comment.risk_level && comment.risk_level !== "normal") top.append(element("span", "state-chip bad", comment.risk_level));
      row.append(top, element("p", "", comment.text), element("small", "", `赞 ${comment.like_count || 0} · 回复 ${comment.reply_count || 0} · ${formatDate(comment.published_at || comment.captured_at)}`));
      commentList.append(row);
    }
    body.append(commentList);
  } catch (error) {
    $("#detailTitle").textContent = "读取失败";
    $("#detailBody").append(element("div", "empty", error.message));
  }
}

async function monitorCollection() {
  if (state.monitoring) return;
  state.monitoring = true;
  let seenBusy = false;
  try {
    for (let attempt = 0; attempt < 900; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      await refresh({ quiet: true });
      const sync = currentCreator()?.sync || {};
      seenBusy = seenBusy || Boolean(sync.busy || sync.queued);
      if (seenBusy && !sync.busy && !sync.queued) {
        showToast(sync.has_error ? "采集结束，请查看错误状态" : "本次采集已完成");
        break;
      }
    }
  } finally {
    state.monitoring = false;
  }
}

$("#refreshButton").addEventListener("click", () => refresh().catch((error) => showToast(error.message)));
$("#creatorTabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-creator-id]");
  if (tab) selectCreator(tab.dataset.creatorId).catch((error) => showToast(error.message));
});
$("#addCreatorButton").addEventListener("click", () => {
  $("#addCreatorForm").reset();
  $("#addCreatorDialog").showModal();
});
$("#addCreatorForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const created = await api("/api/collector/creators", {
      method: "POST",
      body: JSON.stringify({
        name: $("#newCreatorName").value.trim(),
        profile_url: $("#newCreatorProfileUrl").value.trim(),
      }),
    });
    state.selectedCreatorId = created.id;
    $("#addCreatorDialog").close();
    await refresh({ quiet: true });
    showToast("博主已新增；当前仍是手动待命状态");
  } catch (error) {
    showToast(error.message);
  }
});
$("#settingsButton").addEventListener("click", () => openSettings().catch((error) => showToast(error.message)));
$("#settingsCommentsEnabled").addEventListener("change", toggleCommentOptions);
$("#settingsVideoUrl").addEventListener("input", scheduleVideoLookup);
$("#runSingleVideoButton").addEventListener("click", () => runSingleVideo().catch((error) => showToast(error.message)));
$("#settingsForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveSettings().catch((error) => showToast(error.message));
});
$("#saveAndRunButton").addEventListener("click", async () => {
  try {
    if ($("#settingsVideoUrl").value.trim()) {
      await runSingleVideo();
      return;
    }
    await saveSettings({ close: true });
    await runSelected();
  } catch (error) {
    showToast(error.message);
  }
});
$("#forceCommentsButton").addEventListener("click", async () => {
  try {
    if ($("#settingsVideoUrl").value.trim()) {
      await runSingleVideo({ forceComments: true });
      return;
    }
    await saveSettings({ close: true });
    await runSelected({ forceComments: true });
  } catch (error) {
    showToast(error.message);
  }
});
$("#runButton").addEventListener("click", () => runSelected().catch((error) => showToast(error.message)));
$("#retryButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/collector/transfers/retry", { method: "POST", body: "{}" });
    showToast(`已唤醒传输队列，提前重试 ${result.expedited || 0} 条`);
    window.setTimeout(() => refresh({ quiet: true }), 1200);
  } catch (error) {
    showToast(error.message);
  }
});
$("#worksList").addEventListener("click", (event) => {
  const target = event.target.closest("[data-work-id]");
  if (target) openWork(target.dataset.workId);
});
$("#closeDialog").addEventListener("click", () => $("#workDialog").close());
document.addEventListener("click", (event) => {
  const closeButton = event.target.closest("[data-close-dialog]");
  if (closeButton) document.getElementById(closeButton.dataset.closeDialog)?.close();
});
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register(appUrl("/service-worker.js")).catch(() => {}));
}

if (APP_PREFIX) {
  $("#suiteHomeLink").hidden = false;
  $("#modelDownloaderLink").hidden = false;
}

refresh({ quiet: true }).catch((error) => {
  $("#syncStatus").textContent = `采集端读取失败：${error.message}`;
  $("#syncStatus").className = "status-line error";
});
