"use strict";

const DOUYIN_VIDEO_HOSTS = new Set(["douyin.com", "www.douyin.com"]);

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
  if (video.episode <= 2) {
    image.fetchPriority = "high";
  }

  const title = document.createElement("h3");
  title.className = "video-title";
  title.textContent = video.title;

  coverFrame.append(image);
  link.append(coverFrame, title);
  article.append(link);
  return article;
}

async function loadCollection() {
  const grid = document.querySelector("#videoGrid");
  const message = document.querySelector("#loadMessage");
  const count = document.querySelector("#videoCount");

  try {
    const response = await fetch("./videos.json", { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
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

    if (!videos.length) {
      throw new Error("empty collection");
    }

    const fragment = document.createDocumentFragment();
    videos.sort((left, right) => left.episode - right.episode).forEach((video) => {
      fragment.append(renderVideo(video));
    });
    grid.replaceChildren(fragment);
    grid.setAttribute("aria-busy", "false");
    count.textContent = String(videos.length);
    message.hidden = true;
  } catch (_error) {
    grid.setAttribute("aria-busy", "false");
    message.classList.add("error");
    message.textContent = "作品索引暂时没有加载成功，请稍后刷新。";
  }
}

loadCollection();
