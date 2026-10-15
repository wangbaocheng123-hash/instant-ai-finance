"use strict";

const DOUYIN_VIDEO_HOSTS = new Set(["douyin.com", "www.douyin.com"]);
const VALID_VIEWS = new Set(["exchange", "sizes", "joints"]);
const MESSAGE_REFRESH_INTERVAL = 15000;

let activeView = "exchange";
let collectionLoaded = false;
let messagesLoaded = false;
let lastTrackedView = "";
let analyticsQueue = Promise.resolve();

function recordPageview(view) {
  analyticsQueue = analyticsQueue.catch(() => undefined).then(() => fetch("/api/analytics/pageview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ view, referrer: document.referrer || "" }),
    credentials: "same-origin",
    keepalive: true,
  })).catch(() => {
    // Analytics must never interrupt the public community experience.
  });
}

function isTrustedVideoUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && DOUYIN_VIDEO_HOSTS.has(url.hostname)
      && /^\/video\/[0-9]{15,22}$/.test(url.pathname)
      && !url.username
      && !url.password
      && !url.port;
  } catch (_error) {
    return false;
  }
}

function isTrustedCoverUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && (url.hostname === "douyinpic.com" || url.hostname.endsWith(".douyinpic.com"))
      && !url.username
      && !url.password
      && !url.port;
  } catch (_error) {
    return false;
  }
}

function renderVideo(video) {
  const article = document.createElement("article");
  article.className = "video-card";
  const link = document.createElement("a");
  link.className = "video-link";
  link.href = video.video_url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", `${video.title}，在抖音打开原视频`);
  const coverFrame = document.createElement("div");
  coverFrame.className = "cover-frame";
  const image = document.createElement("img");
  image.src = video.cover_url;
  image.alt = `${video.title}封面`;
  image.loading = video.episode <= 4 ? "eager" : "lazy";
  image.decoding = "async";
  image.referrerPolicy = "no-referrer";
  if (video.episode <= 2) image.fetchPriority = "high";
  const title = document.createElement("h3");
  title.className = "video-title";
  title.textContent = video.title;
  coverFrame.append(image);
  link.append(coverFrame, title);
  article.append(link);
  return article;
}

async function loadCollection() {
  if (collectionLoaded) return;
  collectionLoaded = true;
  const grid = document.querySelector("#videoGrid");
  const message = document.querySelector("#loadMessage");
  const count = document.querySelector("#videoCount");
  try {
    const response = await fetch("./videos.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const videos = Array.isArray(payload.videos)
      ? payload.videos.filter((video) => (
        Number.isInteger(video.episode)
        && typeof video.title === "string"
        && video.title.trim()
        && isTrustedVideoUrl(video.video_url)
        && isTrustedCoverUrl(video.cover_url)
      ))
      : [];
    if (!videos.length) throw new Error("empty collection");
    const fragment = document.createDocumentFragment();
    videos.sort((left, right) => left.episode - right.episode)
      .forEach((video) => fragment.append(renderVideo(video)));
    grid.replaceChildren(fragment);
    grid.setAttribute("aria-busy", "false");
    count.textContent = String(videos.length);
    message.hidden = true;
  } catch (_error) {
    collectionLoaded = false;
    grid.setAttribute("aria-busy", "false");
    message.classList.add("error");
    message.textContent = "作品索引暂时没有加载成功，请稍后刷新。";
  }
}

function formatMessageTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function renderMessage(message) {
  const article = document.createElement("article");
  article.className = "message-card";
  article.dataset.messageId = message.id;
  const header = document.createElement("header");
  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = message.nickname.trim().slice(0, 1) || "木";
  const identity = document.createElement("div");
  const nickname = document.createElement("strong");
  nickname.textContent = message.nickname;
  const time = document.createElement("time");
  time.dateTime = message.created_at;
  time.textContent = formatMessageTime(message.created_at);
  identity.append(nickname, time);
  header.append(avatar, identity);
  const body = document.createElement("p");
  body.textContent = message.body;
  article.append(header, body);
  return article;
}

function validMessage(value) {
  return value
    && typeof value.id === "string"
    && typeof value.nickname === "string"
    && typeof value.body === "string"
    && typeof value.created_at === "string";
}

async function loadMessages({ quiet = false } = {}) {
  const feed = document.querySelector("#messageFeed");
  const status = document.querySelector("#feedStatus");
  if (!quiet) {
    feed.setAttribute("aria-busy", "true");
    status.classList.remove("error");
    status.textContent = "正在看看大家的新想法…";
  }
  try {
    const response = await fetch("./api/messages?limit=80", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const messages = Array.isArray(payload.messages) ? payload.messages.filter(validMessage) : [];
    const fragment = document.createDocumentFragment();
    messages.forEach((message) => fragment.append(renderMessage(message)));
    feed.replaceChildren(fragment);
    feed.setAttribute("aria-busy", "false");
    messagesLoaded = true;
    status.classList.remove("error");
    status.textContent = messages.length
      ? `已显示 ${messages.length} 条公开交流`
      : "还没有人发言。你可以成为第一个分享想法的木友。";
  } catch (_error) {
    feed.setAttribute("aria-busy", "false");
    status.classList.add("error");
    status.textContent = "交流内容暂时没有加载成功，请稍后刷新。";
  }
}

function updateCounter(input, counter) {
  counter.textContent = String(input.value.length);
}

function messageErrorText(error, retryAfter) {
  if (error === "rate_limited") return `发送得有点快，请等待 ${retryAfter || 15} 秒再试。`;
  if (error === "invalid_nickname") return "称呼需要填写，并控制在 20 个字以内。";
  if (error === "invalid_message") return "想法需要填写，并控制在 500 个字以内。";
  return "暂时没有发送成功，请稍后再试。";
}

async function submitMessage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const nicknameInput = form.elements.nickname;
  const bodyInput = form.elements.body;
  const submitButton = document.querySelector("#submitMessage");
  const status = document.querySelector("#formStatus");
  status.classList.remove("error", "success");
  submitButton.disabled = true;
  submitButton.textContent = "正在发表…";
  try {
    const response = await fetch("./api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: nicknameInput.value.trim(), body: bodyInput.value.trim() }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error("message rejected"), payload);
    bodyInput.value = "";
    updateCounter(bodyInput, document.querySelector("#messageCount"));
    status.classList.add("success");
    status.textContent = "已经发表，其他木友现在可以看到。";
    await loadMessages({ quiet: true });
  } catch (error) {
    status.classList.add("error");
    status.textContent = messageErrorText(error.error, error.retry_after);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "发表到交流区";
  }
}

function setActiveView(view, { updateHash = true } = {}) {
  activeView = VALID_VIEWS.has(view) ? view : "exchange";
  document.querySelectorAll("[data-view]").forEach((section) => {
    section.hidden = section.dataset.view !== activeView;
  });
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    const selected = button.dataset.viewTarget === activeView;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-selected", selected ? "true" : "false");
  });
  if (updateHash && window.location.hash !== `#${activeView}`) {
    window.history.replaceState(null, "", `#${activeView}`);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (activeView === "joints") loadCollection();
  if (activeView === "exchange" && !messagesLoaded) loadMessages();
  if (lastTrackedView !== activeView) {
    lastTrackedView = activeView;
    recordPageview(activeView);
  }
}

function reloadPage() {
  const bodyInput = document.querySelector("#messageBody");
  if (bodyInput.value.trim()
    && !window.confirm("还有未发表的内容，刷新后会丢失。确定刷新吗？")) return;
  const button = document.querySelector("#reloadPage");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  document.querySelector("#reloadLabel").textContent = "刷新中…";
  window.location.reload();
}

function initialize() {
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => setActiveView(button.dataset.viewTarget));
  });
  document.querySelector(".brand").addEventListener("click", (event) => {
    event.preventDefault();
    setActiveView("exchange");
  });
  const nicknameInput = document.querySelector("#nickname");
  const bodyInput = document.querySelector("#messageBody");
  nicknameInput.addEventListener("input", () => updateCounter(nicknameInput, document.querySelector("#nicknameCount")));
  bodyInput.addEventListener("input", () => updateCounter(bodyInput, document.querySelector("#messageCount")));
  document.querySelector("#messageForm").addEventListener("submit", submitMessage);
  document.querySelector("#reloadPage").addEventListener("click", reloadPage);
  document.querySelector("#refreshMessages").addEventListener("click", () => loadMessages());
  window.addEventListener("hashchange", () => setActiveView(window.location.hash.slice(1), { updateHash: false }));
  setInterval(() => {
    if (activeView === "exchange" && !document.hidden) loadMessages({ quiet: true });
  }, MESSAGE_REFRESH_INTERVAL);
  setActiveView(window.location.hash.slice(1), { updateHash: true });
}

initialize();
