from __future__ import annotations

import base64
import json
import os
import queue
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import requests

from .douyin_core import (
    CDPConnection,
    CHROME_PATH,
    DownloadCancelled,
    chromium_runtime_flags,
    close_stale_profile_browser,
)


COMMENT_API_RE = re.compile(
    r"/aweme/v1/web/comment/(?:list|list/reply)",
    re.IGNORECASE,
)
DETAIL_API_RE = re.compile(
    r"/aweme/v1/web/aweme/detail",
    re.IGNORECASE,
)


class CommentCollectError(RuntimeError):
    pass


class CommentCollector:
    """Collect public comments exposed by the work page's own network calls."""

    def __init__(
        self,
        profile_dir: Path,
        log: Callable[..., None] | None = None,
        cancel_event=None,
        chrome_path: Path = CHROME_PATH,
    ) -> None:
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.log = log or (lambda *_: None)
        self.cancel_event = cancel_event
        self.chrome_path = Path(chrome_path)
        self.chrome: subprocess.Popen | None = None
        self.cdp: CDPConnection | None = None
        self.last_summary: dict[str, object] = {}

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DownloadCancelled("任务已取消。")

    def _start(self) -> None:
        if not self.chrome_path.exists():
            raise CommentCollectError(
                f"未找到 Chromium：{self.chrome_path}"
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        active_port = self.profile_dir / "DevToolsActivePort"
        close_stale_profile_browser(self.profile_dir, self.log)
        active_port.unlink(missing_ok=True)
        args = [
            str(self.chrome_path),
            "--headless=new",
            "--remote-debugging-port=0",
            "--remote-allow-origins=http://localhost",
            f"--user-data-dir={self.profile_dir}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--disable-notifications",
            "--disable-features=Translate,OptimizationHints,MediaRouter",
            "--window-size=1365,1000",
            "about:blank",
        ]
        args[1:1] = chromium_runtime_flags()
        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            popen_kwargs.update(
                startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self.chrome = subprocess.Popen(args, **popen_kwargs)

        deadline = time.monotonic() + 15
        port: int | None = None
        while time.monotonic() < deadline:
            self._check_cancel()
            if self.chrome.poll() is not None:
                raise CommentCollectError("评论采集浏览器启动失败。")
            if active_port.exists():
                try:
                    port = int(
                        active_port.read_text(
                            encoding="utf-8"
                        ).splitlines()[0]
                    )
                    break
                except (OSError, ValueError, IndexError):
                    pass
            time.sleep(0.1)
        if port is None:
            raise CommentCollectError("等待评论采集浏览器超时。")

        deadline = time.monotonic() + 8
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                targets = requests.get(
                    f"http://127.0.0.1:{port}/json/list",
                    timeout=1,
                ).json()
                page = next(
                    row
                    for row in targets
                    if row.get("type") == "page"
                )
                self.cdp = CDPConnection(page["webSocketDebuggerUrl"])
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.15)
        raise CommentCollectError(
            f"连接评论采集浏览器失败：{last_error}"
        )

    def _evaluate(self, expression: str):
        if not self.cdp:
            raise CommentCollectError("评论采集浏览器尚未连接。")
        result = self.cdp.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        return result.get("result", {}).get("value")

    @staticmethod
    def _optional_bool(raw: dict, *keys: str) -> bool | None:
        for key in keys:
            if key not in raw:
                continue
            value = raw.get(key)
            if value is None:
                return None
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes"}
            return bool(value)
        return None

    @staticmethod
    def _label_text(raw: dict) -> str:
        direct = raw.get("label_text") or raw.get("label") or ""
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        labels: list[str] = []
        for item in raw.get("label_list") or []:
            if isinstance(item, str) and item.strip():
                labels.append(item.strip())
            elif isinstance(item, dict):
                value = item.get("text") or item.get("label_text") or ""
                if str(value).strip():
                    labels.append(str(value).strip())
        return " / ".join(dict.fromkeys(labels))

    @staticmethod
    def _video_metrics(payload: dict) -> dict[str, int]:
        detail = (
            payload.get("aweme_detail")
            or payload.get("aweme")
            or payload.get("item")
            or {}
        )
        statistics = detail.get("statistics") or {}
        aliases = {
            "play_count": ("play_count",),
            "digg_count": ("digg_count",),
            "comment_count": ("comment_count",),
            "collect_count": ("collect_count",),
            "share_count": ("share_count",),
        }
        result: dict[str, int] = {}
        for target, keys in aliases.items():
            for key in keys:
                if key not in statistics:
                    continue
                try:
                    result[target] = int(statistics.get(key) or 0)
                except (TypeError, ValueError):
                    pass
                break
        return result

    @staticmethod
    def _flatten_comment(
        raw: dict,
        parent_comment_id: str = "",
        creator_uid: str = "",
    ) -> list[dict]:
        user = raw.get("user") or {}
        comment_id = str(raw.get("cid") or raw.get("comment_id") or "")
        inferred_parent = str(
            raw.get("root_comment_id")
            or raw.get("reply_id")
            or ""
        )
        if inferred_parent in {"0", comment_id}:
            inferred_parent = ""
        effective_parent = parent_comment_id or inferred_parent
        label_text = CommentCollector._label_text(raw)
        author_digged = CommentCollector._optional_bool(
            raw,
            "is_author_digged",
            "author_digged",
            "is_aweme_author_digged",
        )
        if author_digged is None and re.search(
            r"(?:作者|博主)(?:赞过|点赞)",
            label_text,
        ):
            author_digged = True
        author_flag = CommentCollector._optional_bool(
            raw,
            "is_author",
            "is_aweme_author",
            "is_creator",
        )
        created_at = ""
        if raw.get("create_time"):
            try:
                created_at = datetime.fromtimestamp(
                    int(raw["create_time"])
                ).isoformat(timespec="seconds")
            except (OSError, OverflowError, TypeError, ValueError):
                created_at = ""
        row = {
            "comment_id": comment_id,
            "parent_comment_id": effective_parent,
            "author_name": str(user.get("nickname") or ""),
            "author_uid": str(
                user.get("sec_uid")
                or user.get("uid")
                or user.get("short_id")
                or ""
            ),
            "text": str(raw.get("text") or ""),
            "created_at": created_at,
            "digg_count": int(raw.get("digg_count") or 0),
            "reply_count": int(
                raw.get("reply_comment_total")
                or raw.get("reply_count")
                or 0
            ),
            "ip_label": str(raw.get("ip_label") or ""),
            "is_creator": bool(
                author_flag is True
                or (
                    creator_uid
                    and str(
                        user.get("sec_uid")
                        or user.get("uid")
                        or user.get("short_id")
                        or ""
                    ) == creator_uid
                )
            ),
            "is_author_digged": author_digged,
            "reply_to_comment_id": str(
                raw.get("reply_to_reply_id")
                or raw.get("reply_to_comment_id")
                or raw.get("reply_id")
                or ""
            ),
            "reply_to_user_name": str(
                (raw.get("reply_to_user") or {}).get("nickname")
                or raw.get("reply_to_user_name")
                or ""
            ),
            "label_text": label_text,
            "raw": raw,
        }
        rows = [row] if row["comment_id"] else []
        for reply in raw.get("reply_comment") or []:
            rows.extend(
                CommentCollector._flatten_comment(
                    reply,
                    parent_comment_id=row["comment_id"],
                    creator_uid=creator_uid,
                )
            )
        return rows

    @classmethod
    def _comments_from_payload(
        cls,
        payload: dict,
        creator_uid: str = "",
    ) -> list[dict]:
        rows: list[dict] = []
        for raw in payload.get("comments") or []:
            if isinstance(raw, dict):
                rows.extend(
                    cls._flatten_comment(
                        raw,
                        creator_uid=creator_uid,
                    )
                )
        return rows

    def collect(
        self,
        video_url: str,
        *,
        timeout: float = 25,
        limit: int = 200,
        creator_uid: str = "",
    ) -> list[dict]:
        self._start()
        assert self.cdp
        self.cdp.call(
            "Network.enable",
            {
                "maxTotalBufferSize": 20_000_000,
                "maxResourceBufferSize": 5_000_000,
            },
        )
        self.cdp.call("Page.enable")
        self.cdp.call("Runtime.enable")
        self.cdp.call("Page.navigate", {"url": video_url})
        self.log("正在采集公开评论数据…")

        deadline = time.monotonic() + timeout
        matching_requests: dict[str, tuple[str, str]] = {}
        completed: set[str] = set()
        by_id: dict[str, dict] = {}
        main_pages: dict[int, bool] = {}
        reply_pages = 0
        expected_total = 0
        video_metrics: dict[str, int] = {}
        last_new = time.monotonic()
        last_expand = time.monotonic()
        expand_clicks = 0
        expand_candidates = 0
        scrolling_more = True
        last_trigger = 0.0
        while time.monotonic() < deadline:
            self._check_cancel()
            if time.monotonic() - last_trigger > 0.9:
                last_trigger = time.monotonic()
                try:
                    interaction = self._evaluate(
                        """
                        (() => {
                          const state = window.__modelDownloaderCommentState ||=
                            { commentOpened: false };
                          const textOf = element => (
                            element.innerText ||
                            element.getAttribute?.('aria-label') ||
                            ''
                          ).replace(/\\s+/g, ' ').trim();
                          const elements = [...document.querySelectorAll(
                            'button,[role="button"],a,div,span,p'
                          )];

                          if (!state.commentOpened) {
                            const commentButton = elements.find(element => {
                              const text = textOf(element);
                              return text.length < 30 &&
                                /^(?:查看)?评论(?:\\s*\\d+)?$/.test(text);
                            });
                            if (commentButton) {
                              commentButton.click();
                              state.commentOpened = true;
                            }
                          }

                          const isReplyExpander = element => {
                            const text = textOf(element);
                            if (!text || text.length > 50) return false;
                            return /^(?:展开|查看|全部).{0,24}(?:回复|评论)$/.test(text) ||
                              /^(?:还有|剩余)\\s*\\d+\\s*条?\\s*(?:回复|评论)$/.test(text) ||
                              /^(?:展开更多|查看更多回复|更多回复)$/.test(text) ||
                              /^(?:回复|评论).{0,18}(?:展开|更多)$/.test(text);
                          };
                          const candidates = elements
                            .filter(isReplyExpander)
                            .filter(element =>
                              ![...element.children].some(isReplyExpander)
                            );
                          const now = Date.now();
                          let clicked = 0;
                          for (const element of candidates) {
                            if (clicked >= 20) break;
                            const previous = Number(
                              element.dataset?.modelDownloaderLastClick || 0
                            );
                            if (now - previous < 8000) continue;
                            if (element.dataset) {
                              element.dataset.modelDownloaderLastClick = String(now);
                            }
                            let target = element.closest(
                              'button,[role="button"],a'
                            );
                            if (!target) {
                              let parent = element;
                              for (let depth = 0; depth < 3 && parent; depth++) {
                                if (getComputedStyle(parent).cursor === 'pointer') {
                                  target = parent;
                                  break;
                                }
                                parent = parent.parentElement;
                              }
                            }
                            target ||= element;
                            target.scrollIntoView({block: 'center'});
                            target.click();
                            clicked += 1;
                          }
                          let scrollingMore = false;
                          const windowBottom = window.scrollY + window.innerHeight;
                          if (windowBottom < document.documentElement.scrollHeight - 8) {
                            window.scrollBy(
                              0,
                              Math.max(Math.floor(window.innerHeight * 0.85), 500)
                            );
                            scrollingMore = true;
                          }
                          const scrollables = [...document.querySelectorAll('*')]
                            .filter(element =>
                              element.scrollHeight > element.clientHeight + 120 &&
                              element.clientHeight > 160
                            )
                            .sort((a, b) =>
                              (b.clientWidth * b.clientHeight) -
                              (a.clientWidth * a.clientHeight)
                            )
                            .slice(0, 6);
                          for (const element of scrollables) {
                            const maximum = element.scrollHeight - element.clientHeight;
                            if (element.scrollTop < maximum - 8) {
                              element.scrollTop = Math.min(
                                maximum,
                                element.scrollTop + Math.max(
                                  Math.floor(element.clientHeight * 0.85),
                                  360
                                )
                              );
                              scrollingMore = true;
                            }
                          }
                          return {
                            clicked,
                            candidates: candidates.length,
                            scrollingMore,
                            loginVisible:
                              document.body?.innerText?.includes('登录') || false
                          };
                        })()
                        """
                    )
                    if isinstance(interaction, dict):
                        clicked = int(interaction.get("clicked") or 0)
                        expand_candidates = int(
                            interaction.get("candidates") or 0
                        )
                        scrolling_more = bool(
                            interaction.get("scrollingMore")
                        )
                        if clicked:
                            expand_clicks += clicked
                            last_expand = time.monotonic()
                except Exception:
                    pass
            try:
                event = self.cdp.events.get(timeout=0.25)
            except queue.Empty:
                now = time.monotonic()
                last_main_has_more = (
                    main_pages[max(main_pages)] if main_pages else None
                )
                if (
                    by_id
                    and now - last_new > 25
                    and now - last_expand > 15
                    and expand_candidates == 0
                    and not scrolling_more
                    and last_main_has_more is False
                ):
                    break
                continue

            method = event.get("method")
            params = event.get("params", {})
            if method == "Network.responseReceived":
                response = params.get("response", {})
                url = str(response.get("url") or "")
                if COMMENT_API_RE.search(url):
                    matching_requests[str(params.get("requestId"))] = (
                        "comments",
                        url,
                    )
                elif DETAIL_API_RE.search(url):
                    matching_requests[str(params.get("requestId"))] = (
                        "detail",
                        url,
                    )
            elif method == "Network.loadingFinished":
                request_id = str(params.get("requestId") or "")
                if (
                    request_id not in matching_requests
                    or request_id in completed
                ):
                    continue
                completed.add(request_id)
                request_kind, request_url = matching_requests[request_id]
                try:
                    body = self.cdp.call(
                        "Network.getResponseBody",
                        {"requestId": request_id},
                    )
                    text = str(body.get("body") or "")
                    if body.get("base64Encoded"):
                        text = base64.b64decode(text).decode(
                            "utf-8",
                            errors="replace",
                        )
                    payload = json.loads(text)
                except Exception:
                    continue
                if request_kind == "detail":
                    video_metrics.update(self._video_metrics(payload))
                    continue

                is_reply_page = "/list/reply" in urlparse(
                    request_url
                ).path
                if is_reply_page:
                    reply_pages += 1
                else:
                    query = parse_qs(urlparse(request_url).query)
                    try:
                        cursor = int((query.get("cursor") or [0])[0])
                    except (TypeError, ValueError):
                        cursor = len(main_pages)
                    main_pages[cursor] = bool(payload.get("has_more"))
                    try:
                        expected_total = max(
                            expected_total,
                            int(payload.get("total") or 0),
                        )
                    except (TypeError, ValueError):
                        pass

                for row in self._comments_from_payload(
                    payload,
                    creator_uid=creator_uid,
                ):
                    comment_id = row["comment_id"]
                    if comment_id not in by_id:
                        by_id[comment_id] = row
                        last_new = time.monotonic()
                    else:
                        existing = by_id[comment_id]
                        for key in (
                            "parent_comment_id",
                            "author_name",
                            "author_uid",
                            "text",
                            "created_at",
                            "ip_label",
                            "reply_to_comment_id",
                            "reply_to_user_name",
                            "label_text",
                        ):
                            if row.get(key):
                                existing[key] = row[key]
                        existing["digg_count"] = row.get(
                            "digg_count",
                            existing.get("digg_count", 0),
                        )
                        existing["reply_count"] = max(
                            int(existing.get("reply_count") or 0),
                            int(row.get("reply_count") or 0),
                        )
                        existing["is_creator"] = bool(
                            existing.get("is_creator")
                            or row.get("is_creator")
                        )
                        if row.get("is_author_digged") is not None:
                            existing["is_author_digged"] = row[
                                "is_author_digged"
                            ]
                        existing["raw"] = row.get("raw") or existing.get(
                            "raw",
                            {},
                        )
                if len(by_id) >= limit:
                    break

        rows = list(by_id.values())[:limit]
        replies_by_parent: dict[str, int] = {}
        for row in rows:
            parent_id = str(row.get("parent_comment_id") or "")
            if parent_id:
                replies_by_parent[parent_id] = (
                    replies_by_parent.get(parent_id, 0) + 1
                )
        incomplete_replies = sum(
            1
            for row in rows
            if not row.get("parent_comment_id")
            and int(row.get("reply_count") or 0)
            > replies_by_parent.get(str(row.get("comment_id") or ""), 0)
        )
        last_main_has_more = (
            main_pages[max(main_pages)] if main_pages else None
        )
        top_level_count = sum(
            1 for row in rows if not row.get("parent_comment_id")
        )
        complete = bool(
            rows
            and last_main_has_more is False
            and incomplete_replies == 0
            and (
                expected_total <= 0
                or top_level_count >= expected_total
            )
        )
        author_like_supported = any(
            row.get("is_author_digged") is not None
            for row in rows
        )
        reply_groups_seen = sum(
            1
            for row in rows
            if not row.get("parent_comment_id")
            and int(row.get("reply_count") or 0) > 0
        )
        creator_reply_rows = sum(
            bool(row.get("is_creator") and row.get("parent_comment_id"))
            for row in rows
        )
        self.last_summary = {
            "rows_seen": len(rows),
            "top_level_seen": top_level_count,
            "expected_total": expected_total,
            "main_pages": len(main_pages),
            "reply_pages": reply_pages,
            "reply_groups_seen": reply_groups_seen,
            "incomplete_replies": incomplete_replies,
            "expand_clicks": expand_clicks,
            "complete": complete,
            "author_like_supported": author_like_supported,
            "creator_rows": sum(bool(row.get("is_creator")) for row in rows),
            "creator_reply_rows": creator_reply_rows,
            "author_liked_rows": sum(
                row.get("is_author_digged") is True for row in rows
            ),
            "video_metrics": video_metrics,
        }
        if rows:
            self.log(
                "已采集 %d 条公开评论（主评论 %d，主动展开 %d 次，"
                "回复分页 %d，博主回复 %d 条，%s）。",
                len(rows),
                top_level_count,
                expand_clicks,
                reply_pages,
                creator_reply_rows,
                "已完整翻页" if complete else "仍有未展开内容",
            )
        else:
            self.log(
                "本次未取得公开评论；可能需要登录，稍后会自动重试。"
            )
        return rows

    def close(self) -> None:
        if self.cdp:
            self.cdp.close()
            self.cdp = None
        if self.chrome and self.chrome.poll() is None:
            try:
                self.chrome.terminate()
                self.chrome.wait(timeout=3)
            except Exception:
                try:
                    self.chrome.kill()
                except Exception:
                    pass
        self.chrome = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
