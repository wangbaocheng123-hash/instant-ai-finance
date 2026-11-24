"""Doubao's dedicated, single-headline translation adapter.

Only public title text is sent. Credentials stay in the service environment;
there is no browser credential, background job, retry, or article fetch.
"""
from __future__ import annotations

import html
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
MODEL = "doubao-seed-translation-250915"
MAX_INPUT_BYTES = 1_000
DAILY_CHARACTER_LIMIT = 50_000


class HeadlineTranslationUnavailable(RuntimeError):
    pass


def source_language(text: str) -> str | None:
    """Recognize supported headline scripts; Hangul can coexist with Hanja/Latin."""
    if re.search(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]", text):
        return "ko"
    if not re.search(r"[\u3400-\u9fff]", text) and len(re.findall(r"[A-Za-z]", text)) >= 6:
        return "en"
    # Official agenda titles may already have a Chinese speaker prefix but an
    # untranslated English topic. Short acronyms in Chinese remain untouched.
    if re.search(r"[\u3400-\u9fff]", text) and re.search(r"\b(?:economic|economy|outlook|monetary|policy|interest|rates?|inflation|employment|unemployment|productivity|jobs|financial|stability|artificial|intelligence|conference|speech|testimony|regulation|banking|finance|payments?|innovation|technology|liquidity|risks?|remarks|welcoming|community|development|education|conversation)\b", text, re.I | re.ASCII):
        return "en"
    return None


def configured() -> bool:
    return bool(_api_key())


def _api_key() -> str:
    # An independent translation key takes precedence. The existing Ark text
    # credential can be reused without changing the video keyword model.
    return (os.environ.get("INSTANT_AI_HEADLINE_ARK_API_KEY", "").strip()
            or os.environ.get("INSTANT_AI_DOUBAO_ARK_API_KEY", "").strip())


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HeadlineTranslationUnavailable("豆包翻译接口重定向，已停止请求。")


def translate_headline(text: str) -> str:
    if not configured():
        raise HeadlineTranslationUnavailable("豆包标题翻译尚未配置方舟 API Key。")
    if not text.strip() or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise HeadlineTranslationUnavailable("标题为空或超过翻译长度上限，已保留原题。")
    language = source_language(text)
    if language is None:
        raise HeadlineTranslationUnavailable("标题不属于待翻译的英文或韩文，已保留原题。")
    payload = {
        "model": MODEL,
        "input": [{"role": "user", "content": [{
            "type": "input_text", "text": text,
            "translation_options": {"source_language": language, "target_language": "zh"},
        }]}],
    }
    request = Request(ARK_URL, method="POST", data=json.dumps(payload).encode("utf-8"),
                      headers={"Authorization": "Bearer " + _api_key(),
                               "Content-Type": "application/json", "User-Agent": "Instant-AI"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(65_537)
        if len(raw) > 65_536:
            raise HeadlineTranslationUnavailable("豆包翻译响应过大，已保留原题。")
        result = json.loads(raw)
    except HTTPError as error:
        # Never return provider bodies, request headers, or credential values.
        message = ("豆包翻译凭据或模型权限未通过，请核对方舟配置。"
                   if error.code in {401, 403} else "豆包翻译暂不可用，已保留原题。")
        raise HeadlineTranslationUnavailable(message) from None
    except (OSError, URLError, ValueError):
        raise HeadlineTranslationUnavailable("豆包翻译未确认完成，已保留原题；不会自动重试。") from None
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise HeadlineTranslationUnavailable("豆包翻译未完整完成，已保留原题。")
    output = result.get("output")
    texts = [part.get("text", "") for message in output if isinstance(message, dict)
             and message.get("type") == "message" and isinstance(message.get("content"), list)
             for part in message["content"]
             if isinstance(part, dict) and part.get("type") == "output_text"] if isinstance(output, list) else []
    if not texts or any(not isinstance(value, str) for value in texts):
        raise HeadlineTranslationUnavailable("豆包翻译结果无效，已保留原题。")
    translated = html.unescape(" ".join(" ".join(texts).split())).strip()
    if (not re.search(r"[\u3400-\u9fff]", translated) or len(translated) > 2_000
            or translated.casefold() == text.casefold()):
        raise HeadlineTranslationUnavailable("豆包未返回有效中文标题，已保留原题。")
    return translated
