"use strict";

const DOUYIN_VIDEO_HOSTS = new Set(["douyin.com", "www.douyin.com"]);
const WECHAT_ARTICLE_HOST = "mp.weixin.qq.com";
const VALID_VIEWS = new Set(["exchange", "sizes", "joints"]);
const MESSAGE_REFRESH_INTERVAL = 15000;
const ANALYTICS_VISITOR_STORAGE_KEY = "sunmao.analytics.visitor.v1";
const ANALYTICS_VISITOR_TOKEN_PATTERN = /^[A-Za-z0-9_-]{24,96}$/;

let activeView = "exchange";
let collectionLoaded = false;
let sizeResourcesLoaded = false;
let messagesLoaded = false;
let lastTrackedView = "";
let analyticsQueue = Promise.resolve();

function storedAnalyticsVisitorToken() {
  try {
    const value = window.localStorage.getItem(ANALYTICS_VISITOR_STORAGE_KEY) || "";
    return ANALYTICS_VISITOR_TOKEN_PATTERN.test(value) ? value : "";
  } catch (_error) {
    return "";
  }
}

function storeAnalyticsVisitorToken(value) {
  if (!ANALYTICS_VISITOR_TOKEN_PATTERN.test(value || "")) return;
  try {
    window.localStorage.setItem(ANALYTICS_VISITOR_STORAGE_KEY, value);
  } catch (_error) {
    // Cookies remain the primary identity when local storage is unavailable.
  }
}

function recordPageview(view) {
  analyticsQueue = analyticsQueue.catch(() => undefined).then(async () => {
    const event = { view, referrer: document.referrer || "" };
    const visitorToken = storedAnalyticsVisitorToken();
    if (visitorToken) event.visitor_token = visitorToken;
    const response = await fetch("/api/analytics/pageview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
      credentials: "same-origin",
      keepalive: true,
    });
    if (!response.ok) return;
    const payload = await response.json().catch(() => ({}));
    storeAnalyticsVisitorToken(payload.visitor_token);
  }).catch(() => {
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

function isTrustedSizeResourceUrl(value) {
  try {
    const url = new URL(value);
    const safeOrigin = url.protocol === "https:"
      && url.hostname === WECHAT_ARTICLE_HOST
      && !url.username
      && !url.password
      && !url.port
      && !url.hash;
    if (!safeOrigin) return false;
    if (/^\/s\/[A-Za-z0-9_-]+$/.test(url.pathname)) return !url.search;
    if (url.pathname !== "/s") return false;
    const keys = [...url.searchParams.keys()].sort();
    return keys.join(",") === "__biz,idx,mid,sn"
      && /^[A-Za-z0-9_=-]{8,128}$/.test(url.searchParams.get("__biz") || "")
      && /^[0-9]{6,20}$/.test(url.searchParams.get("mid") || "")
      && /^[1-9][0-9]{0,2}$/.test(url.searchParams.get("idx") || "")
      && /^[0-9a-f]{32}$/i.test(url.searchParams.get("sn") || "");
  } catch (_error) {
    return false;
  }
}

function isTrustedSizeAssetUrl(value) {
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin
      && /^\/api\/size-assets\/[A-Za-z0-9_-]{20,64}$/.test(url.pathname)
      && !url.search
      && !url.hash;
  } catch (_error) {
    return false;
  }
}

function validSizeResource(resource) {
  const commonFieldsAreValid = resource
    && Number.isInteger(resource.sequence)
    && resource.sequence > 0
    && typeof resource.id === "string"
    && resource.id.trim()
    && typeof resource.title === "string"
    && resource.title.trim()
    && typeof resource.source === "string"
    && resource.source.trim()
    && typeof resource.summary === "string"
    && resource.summary.trim();
  if (!commonFieldsAreValid) return false;
  if (resource.type === "external") return isTrustedSizeResourceUrl(resource.url);
  return resource.type === "gallery"
    && Array.isArray(resource.assets)
    && resource.assets.length > 0
    && resource.assets.every((asset) => asset
      && Number.isInteger(asset.position)
      && isTrustedSizeAssetUrl(asset.url));
}

function renderSizeResource(resource) {
  const article = document.createElement("article");
  article.className = "size-resource-card";
  const content = document.createElement(resource.type === "external" ? "a" : "div");
  content.className = "size-resource-link";
  if (resource.type === "external") {
    content.href = resource.url;
    content.target = "_blank";
    content.rel = "noopener noreferrer";
    content.referrerPolicy = "no-referrer";
    content.setAttribute("aria-label", `${resource.title}，打开微信原文`);
  }
  const meta = document.createElement("div");
  meta.className = "size-resource-meta";
  const sequence = document.createElement("span");
  sequence.textContent = String(resource.sequence).padStart(2, "0");
  const source = document.createElement("span");
  source.textContent = resource.source;
  meta.append(sequence, source);
  const title = document.createElement("h3");
  title.textContent = resource.title;
  const summary = document.createElement("p");
  summary.textContent = resource.summary;
  if (resource.type === "gallery") {
    const gallery = document.createElement("div");
    gallery.className = "size-resource-gallery";
    resource.assets.forEach((asset, index) => {
      const imageLink = document.createElement("a");
      imageLink.href = asset.url;
      imageLink.target = "_blank";
      imageLink.rel = "noopener noreferrer";
      imageLink.setAttribute("aria-label", `${resource.title}，查看第 ${index + 1} 张原图`);
      const image = document.createElement("img");
      image.src = asset.url;
      image.alt = `${resource.title}第 ${index + 1} 张图`;
      image.loading = "lazy";
      image.decoding = "async";
      imageLink.append(image);
      gallery.append(imageLink);
    });
    content.append(meta, title, summary, gallery);
    const action = document.createElement("span");
    action.className = "size-resource-action";
    action.textContent = `社区图集 · ${resource.assets.length} 张原图`;
    content.append(action);
    article.append(content);
    return article;
  }
  const action = document.createElement("span");
  action.className = "size-resource-action";
  action.textContent = "打开原文 ↗";
  content.append(meta, title, summary, action);
  article.append(content);
  return article;
}

async function loadSizeResources() {
  if (sizeResourcesLoaded) return;
  sizeResourcesLoaded = true;
  const grid = document.querySelector("#sizeResourceGrid");
  const message = document.querySelector("#sizeLoadMessage");
  const count = document.querySelector("#sizeResourceCount");
  try {
    const response = await fetch("./api/size-resources", { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const resources = Array.isArray(payload.resources)
      ? payload.resources.filter(validSizeResource)
      : [];
    if (!resources.length) throw new Error("empty size library");
    const fragment = document.createDocumentFragment();
    resources.sort((left, right) => left.sequence - right.sequence)
      .forEach((resource) => fragment.append(renderSizeResource(resource)));
    grid.replaceChildren(fragment);
    grid.setAttribute("aria-busy", "false");
    count.textContent = String(resources.length);
    message.hidden = true;
  } catch (_error) {
    sizeResourcesLoaded = false;
    grid.setAttribute("aria-busy", "false");
    message.classList.add("error");
    message.textContent = "尺寸资料暂时没有加载成功，请稍后刷新。";
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
  if (error === "invalid_message") return "想法需要填写，并控制在 500 个字以内。";
  return "暂时没有发送成功，请稍后再试。";
}

async function submitMessage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const bodyInput = form.elements.body;
  const submitButton = document.querySelector("#submitMessage");
  const status = document.querySelector("#formStatus");
  status.classList.remove("error", "success");
  submitButton.disabled = true;
  submitButton.textContent = "发送中…";
  try {
    const response = await fetch("./api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: bodyInput.value.trim() }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error("message rejected"), payload);
    bodyInput.value = "";
    updateCounter(bodyInput, document.querySelector("#messageCount"));
    status.classList.add("success");
    status.textContent = "已经发送，其他木友现在可以看到。";
    await loadMessages({ quiet: true });
  } catch (error) {
    status.classList.add("error");
    status.textContent = messageErrorText(error.error, error.retry_after);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "发送";
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
  const exchangeActive = activeView === "exchange";
  document.body.classList.toggle("exchange-active", exchangeActive);
  document.querySelector("[data-exchange-composer]").hidden = !exchangeActive;
  if (updateHash && window.location.hash !== `#${activeView}`) {
    window.history.replaceState(null, "", `#${activeView}`);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (activeView === "sizes") loadSizeResources();
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
  const bodyInput = document.querySelector("#messageBody");
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
