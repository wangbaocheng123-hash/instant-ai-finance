from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .media_tools import find_ffmpeg
from .settings import (
    CONFIG_DIR,
    DATA_DIR,
    Settings,
    doubao_api_is_enabled,
    doubao_speech_is_configured,
    doubao_text_is_configured,
    doubao_vision_is_configured,
)
from .storage import Storage


RUNTIME_DIR = Path(
    os.getenv("MX_AGENT_RUNTIME_DIR")
    or Path(os.getenv("LOCALAPPDATA", str(DATA_DIR))) / "MXAgent"
)
ASR_API_BASE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
ASR_SUBMIT_URL = f"{ASR_API_BASE_URL}/submit"
ASR_QUERY_URL = f"{ASR_API_BASE_URL}/query"
DEFAULT_ASR_RESOURCE_ID = "volc.seedasr.auc"
FINANCIAL_CONTEXT_PATH = CONFIG_DIR / "financial_asr_context.txt"
Transport = Callable[
    [str, Mapping[str, str], dict[str, Any], float],
    tuple[Mapping[str, str], dict[str, Any]],
]

KEYWORD_PROMPT = """
你是“模型先生智能体”的视频原文关键词归类器。只依据输入中的
video_original 提取短关键词，不联网，不补充原文没有的概念。
title_reference 只用于识别作品，不得提取只在标题出现、原文没有的词。

你不得概括观点，不得总结内容，不得输出核心要点、投资结论、解释、证据或置信度。
每项只能是便于搜索的短关键词或短词组，不能是完整句子。同义词和重复词合并，
同一个关键词只放在最合适的一个分类。原文没谈到的分类必须返回空数组。
不得为了凑数量造词；每类最多8个，全部最多40个。

只返回下面结构的 JSON 对象，不要输出 Markdown 或其他字段：
{
  "行业与板块": [],
  "企业、个股与产业链": [],
  "基本面与估值": [],
  "时间周期与走势状态": [],
  "投资战略、战术与选股方法": [],
  "宏观、政策与事件": [],
  "市场、指数与资金": [],
  "技术面与交易信号": [],
  "交易管理与风险控制": [],
  "投资心理、学习与适用人群": []
}
""".strip()

COVER_PROMPT = """
识别图片中作为视频主题的最大、最醒目的中文封面标题。
忽略用户名、时间、点赞评论、按钮、平台水印和免责声明。
不要解释，不要改写，只返回 JSON：
{"title":"封面原文标题","confidence":0.95}
看不清时 title 返回空字符串。
""".strip()


class DoubaoFoundationService:
    """Domestic AI provider for speech, routine text, and cover vision."""

    def __init__(
        self,
        settings: Settings | object,
        storage: Storage,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self._transport = transport or self._http_json

    def status(self) -> dict[str, Any]:
        configured = any(
            (
                doubao_speech_is_configured(self.settings),
                doubao_text_is_configured(self.settings),
                doubao_vision_is_configured(self.settings),
            )
        )
        enabled = doubao_api_is_enabled(self.settings)
        return {
            "provider": "doubao",
            "configured": configured,
            "enabled": enabled,
            "api_switch_enabled": bool(
                getattr(self.settings, "doubao_api_enabled", False)
            ),
            "capabilities": {
                "speech": {
                    "configured": doubao_speech_is_configured(self.settings),
                    "enabled": enabled
                    and doubao_speech_is_configured(self.settings),
                    "engine": "doubao-recording-asr-2.0",
                    "resource_id": str(
                        getattr(
                            self.settings,
                            "doubao_asr_resource_id",
                            DEFAULT_ASR_RESOURCE_ID,
                        )
                        or DEFAULT_ASR_RESOURCE_ID
                    ),
                    "billing": "pay-as-you-go",
                },
                "text": {
                    "configured": doubao_text_is_configured(self.settings),
                    "enabled": enabled
                    and doubao_text_is_configured(self.settings),
                    "model": str(
                        getattr(self.settings, "doubao_text_model", "") or ""
                    ),
                },
                "vision": {
                    "configured": doubao_vision_is_configured(self.settings),
                    "enabled": enabled
                    and doubao_vision_is_configured(self.settings),
                    "model": str(
                        getattr(self.settings, "doubao_vision_model", "") or ""
                    ),
                },
            },
            "message": self._status_message(configured, enabled),
        }

    def speech_enabled(self) -> bool:
        return doubao_api_is_enabled(
            self.settings
        ) and doubao_speech_is_configured(self.settings)

    def text_enabled(self) -> bool:
        return doubao_api_is_enabled(
            self.settings
        ) and doubao_text_is_configured(self.settings)

    def vision_enabled(self) -> bool:
        return doubao_api_is_enabled(
            self.settings
        ) and doubao_vision_is_configured(self.settings)

    def transcribe_video_text(self, video_id: int) -> dict[str, Any]:
        self._require_capability("speech")
        asset = self._primary_asset(video_id, "video")
        source = Path(str(asset.get("local_path") or "")).resolve()
        if not source.is_file():
            raise ValueError("视频源文件不存在，无法识别语音文字。")

        work_dir = RUNTIME_DIR / "doubao-asr-2" / f"video_{video_id}"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        audio_path = work_dir / "audio.wav"
        try:
            self._extract_audio(source, audio_path)
            if audio_path.stat().st_size > 100 * 1024 * 1024:
                raise RuntimeError("提取后的音频超过100MB，暂时不能直接提交识别。")

            task_id = str(uuid.uuid4())
            request_payload: dict[str, Any] = {
                "user": {"uid": f"mx-agent-video-{video_id}"},
                "audio": {
                    "data": base64.b64encode(
                        audio_path.read_bytes()
                    ).decode("ascii"),
                    "format": "wav",
                    "codec": "raw",
                    "rate": 16000,
                    "bits": 16,
                    "channel": 1,
                },
                "request": {
                    "model_name": "bigmodel",
                    "enable_itn": True,
                    "enable_punc": True,
                    "enable_ddc": False,
                    "enable_speaker_info": False,
                    "enable_channel_split": False,
                    "show_utterances": True,
                    "vad_segment": False,
                    "sensitive_words_filter": "",
                },
            }
            context = self._financial_context(video_id)
            if context:
                request_payload["request"]["corpus"] = {
                    "context": context,
                }

            headers = self._asr_headers(task_id)
            submit_headers, submit_body = self._asr_post_json(
                ASR_SUBMIT_URL,
                headers,
                request_payload,
                180.0,
            )
            if self._asr_status_code(
                submit_headers,
                submit_body,
            ) != "20000000":
                raise RuntimeError(
                    self._asr_error_message(
                        "豆包录音识别任务提交失败",
                        submit_headers,
                        submit_body,
                    )
                )

            result = self._wait_for_asr(task_id, headers)
            result_payload = result.get("result") or {}
            utterances = [
                item
                for item in (result_payload.get("utterances") or [])
                if isinstance(item, dict)
                and str(item.get("text") or "").strip()
            ]
            text = "\n".join(
                str(item.get("text") or "").strip()
                for item in utterances
            ).strip()
            if not text:
                text = str(result_payload.get("text") or "").strip()
            if not text:
                raise RuntimeError("豆包录音文件识别2.0没有返回可用文字。")

            audio_info = result.get("audio_info") or {}
            duration_ms = self._safe_float(audio_info.get("duration"))
            return {
                "video_id": video_id,
                "asset_id": asset.get("id"),
                "text": text,
                "engine": "doubao-recording-asr-2.0",
                "provider": "domestic",
                "task_id": task_id,
                "resource_id": str(
                    getattr(
                        self.settings,
                        "doubao_asr_resource_id",
                        DEFAULT_ASR_RESOURCE_ID,
                    )
                    or DEFAULT_ASR_RESOURCE_ID
                ),
                "billing": "pay-as-you-go",
                "saved": False,
                "save_required": True,
                "utterance_count": len(utterances),
                "duration_seconds": (
                    round(duration_ms / 1000.0, 3)
                    if duration_ms
                    else None
                ),
            }
        finally:
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def _wait_for_asr(
        self,
        task_id: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float = 900.0,
        poll_seconds: float = 2.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            query_headers, response = self._asr_post_json(
                ASR_QUERY_URL,
                headers,
                {},
                90.0,
            )
            code = self._asr_status_code(query_headers, response)
            if code == "20000000":
                return response
            if code in {"20000001", "20000002"}:
                time.sleep(poll_seconds)
                continue
            raise RuntimeError(
                self._asr_error_message(
                    "豆包录音文件识别失败",
                    query_headers,
                    response,
                )
            )
        raise RuntimeError(
            f"豆包录音文件识别等待超过{int(timeout_seconds // 60)}分钟，请稍后重试。"
        )

    def extract_keywords(self, material: dict[str, Any]) -> dict[str, Any]:
        self._require_capability("text")
        model = str(getattr(self.settings, "doubao_text_model", "") or "")
        response = self._ark_responses_json(
            model=model,
            prompt=KEYWORD_PROMPT,
            content=json.dumps(material, ensure_ascii=False),
        )
        return {
            "payload": response["payload"],
            "model": f"doubao:{model}",
            "response_id": response.get("response_id"),
        }

    def recognize_cover_title(
        self,
        video_id: int,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        self._require_capability("vision")
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        current = self.storage.get_video_title(video_id)
        if current and current.get("active_title") and not force:
            return {
                "video_id": video_id,
                "recognized": False,
                "skipped": True,
                "reason": "title already exists",
                "title_info": current,
            }

        asset = self._primary_asset(video_id, "visual")
        source = Path(str(asset.get("local_path") or "")).resolve()
        work_dir = RUNTIME_DIR / "doubao-cover" / f"video_{video_id}"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            if str(asset.get("mime_type") or "").startswith("image/"):
                image_path = source
            else:
                image_path = work_dir / "cover.jpg"
                self._extract_cover(source, image_path)
            mime_type = (
                mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
            )
            data_url = (
                f"data:{mime_type};base64,"
                + base64.b64encode(image_path.read_bytes()).decode("ascii")
            )
            model = str(
                getattr(self.settings, "doubao_vision_model", "") or ""
            )
            response = self._ark_responses_json(
                model=model,
                prompt=COVER_PROMPT,
                image_data_url=data_url,
            )
            payload = response["payload"]
            title = re.sub(
                r"\s+",
                "",
                str(payload.get("title") or ""),
            ).strip()
            if not title:
                raise RuntimeError("豆包视觉模型没有识别到可靠封面标题。")
            confidence = self._confidence(payload.get("confidence"))
            title_info = self.storage.save_ocr_title(
                video_id,
                title,
                confidence=confidence,
                frame_timestamp=0.2,
                frame_path=str(source),
                raw={
                    "provider": "doubao",
                    "engine": "doubao-vision",
                    "model": model,
                    "asset_id": asset.get("id"),
                    "source_path": str(source),
                    "response_id": response.get("response_id"),
                },
            )
            return {
                "video_id": video_id,
                "recognized": True,
                "skipped": False,
                "ocr_title": title,
                "confidence": confidence,
                "provider": "domestic",
                "model": model,
                "title_info": title_info,
            }
        finally:
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def _ark_responses_json(
        self,
        *,
        model: str,
        prompt: str,
        content: str = "",
        image_data_url: str = "",
    ) -> dict[str, Any]:
        api_key = str(
            getattr(self.settings, "doubao_ark_api_key", "") or ""
        )
        base_url = str(
            getattr(
                self.settings,
                "doubao_ark_base_url",
                "https://ark.cn-beijing.volces.com/api/v3",
            )
        ).rstrip("/")
        if image_data_url:
            input_items: list[dict[str, Any]] = [{
                "role": "user",
                "content": [
                {"type": "input_text", "text": prompt},
                {
                    "type": "input_image",
                    "image_url": image_data_url,
                },
                ],
            }]
        else:
            input_items = [
                {
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": prompt},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": content},
                    ],
                },
            ]
        _, response = self._transport(
            f"{base_url}/responses",
            {"Authorization": f"Bearer {api_key}"},
            {
                "model": model,
                "input": input_items,
                "thinking": {"type": "disabled"},
                "max_output_tokens": 3000,
            },
            120.0,
        )
        raw = self._ark_response_text(response)
        if not raw:
            raise RuntimeError("豆包文本模型没有返回可用内容。")
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        first_brace = raw.find("{")
        last_brace = raw.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            raw = raw[first_brace:last_brace + 1]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("豆包返回内容不是有效JSON。") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("豆包返回内容不是JSON对象。")
        return {
            "payload": payload,
            "response_id": response.get("id"),
        }

    @staticmethod
    def _ark_response_text(response: Mapping[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        parts: list[str] = []
        for output in response.get("output") or []:
            if not isinstance(output, Mapping):
                continue
            for item in output.get("content") or []:
                if not isinstance(item, Mapping):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts)

        # Keep compatibility with older Ark-compatible chat responses.
        choices = response.get("choices") or []
        message = choices[0].get("message") if choices else {}
        return str((message or {}).get("content") or "").strip()

    def _status_message(self, configured: bool, enabled: bool) -> str:
        capabilities: list[str] = []
        if doubao_speech_is_configured(self.settings):
            capabilities.append("语音识别")
        if doubao_text_is_configured(self.settings):
            capabilities.append("文本提炼")
        if doubao_vision_is_configured(self.settings):
            capabilities.append("封面识别")
        capability_text = "、".join(capabilities)
        if enabled:
            return f"国内 API 已开启：{capability_text}。"
        if configured:
            return f"国内 API 已配置（{capability_text}），当前关闭。"
        return "国内 API 尚未配置。"

    def _financial_context(self, video_id: int) -> str:
        video = self.storage.get_video(video_id) or {}
        context_items = [
            {
                "text": (
                    "这是一段模型先生讲解金融市场、上市公司、行业、估值和"
                    "投资逻辑的中文视频。请严格按照原音转写，不要总结、改写"
                    "或补充原文没有的观点。"
                )
            }
        ]
        material = "；".join(
            item
            for item in (
                str(video.get("title") or "").strip(),
                str(video.get("description") or "").strip(),
            )
            if item
        )
        if material:
            context_items.append({"text": material[:1000]})
        if FINANCIAL_CONTEXT_PATH.is_file():
            domain_context = FINANCIAL_CONTEXT_PATH.read_text(
                encoding="utf-8"
            ).strip()
            if domain_context:
                context_items.append({"text": domain_context[:5000]})
        return json.dumps(
            {
                "context_type": "dialog_ctx",
                "context_data": list(reversed(context_items)),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _asr_headers(self, task_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Api-Resource-Id": str(
                getattr(
                    self.settings,
                    "doubao_asr_resource_id",
                    DEFAULT_ASR_RESOURCE_ID,
                )
                or DEFAULT_ASR_RESOURCE_ID
            ).strip(),
            "X-Api-Request-Id": task_id,
            "X-Api-Sequence": "-1",
        }
        api_key = str(
            getattr(self.settings, "doubao_asr_api_key", "") or ""
        ).strip()
        if api_key:
            headers["X-Api-Key"] = api_key
            return headers

        app_id = str(
            getattr(self.settings, "doubao_asr_app_id", "") or ""
        ).strip()
        access_key = str(
            getattr(self.settings, "doubao_asr_access_key", "") or ""
        ).strip()
        if not app_id or not access_key:
            raise RuntimeError("尚未配置豆包录音文件识别2.0 API Key。")
        headers["X-Api-App-Key"] = app_id
        headers["X-Api-Access-Key"] = access_key
        return headers

    @staticmethod
    def _asr_post_json(
        url: str,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> tuple[Mapping[str, str], dict[str, Any]]:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                body = json.loads(raw) if raw.strip() else {}
                if not isinstance(body, dict):
                    raise RuntimeError("豆包语音接口返回了无法识别的数据格式。")
                return dict(response.headers.items()), body
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = {"message": raw}
            headers_out = dict(exc.headers.items()) if exc.headers else {}
            log_id = DoubaoFoundationService._header(
                headers_out,
                "X-Tt-Logid",
            )
            message = (
                body.get("message")
                if isinstance(body, dict)
                else str(body)
            )
            detail = f"：{message}" if message else ""
            trace = f"；日志ID {log_id}" if log_id else ""
            raise RuntimeError(
                f"豆包语音接口请求失败（HTTP {exc.code}）{detail}{trace}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"无法连接豆包录音文件识别接口：{exc.reason}"
            ) from exc

    @classmethod
    def _asr_status_code(
        cls,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> str:
        code = cls._header(headers, "X-Api-Status-Code").strip()
        if code:
            return code
        value = payload.get("code")
        return str(value).strip() if value is not None else ""

    @classmethod
    def _asr_error_message(
        cls,
        prefix: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> str:
        code = cls._asr_status_code(headers, payload) or "未知状态"
        message = (
            cls._header(headers, "X-Api-Message")
            or str(payload.get("message") or "")
        ).strip()
        log_id = cls._header(headers, "X-Tt-Logid").strip()
        detail = f"：{message}" if message else ""
        trace = f"；日志ID {log_id}" if log_id else ""
        return f"{prefix}（{code}）{detail}{trace}"

    def _require_capability(self, capability: str) -> None:
        configured = {
            "speech": doubao_speech_is_configured,
            "text": doubao_text_is_configured,
            "vision": doubao_vision_is_configured,
        }[capability](self.settings)
        labels = {
            "speech": "豆包录音文件识别2.0",
            "text": "豆包文本模型",
            "vision": "豆包视觉模型",
        }
        if not configured:
            raise RuntimeError(f"{labels[capability]}尚未配置。")
        if not bool(
            getattr(self.settings, "doubao_api_enabled", False)
        ):
            raise RuntimeError(
                "国内 API 当前已关闭，请先在右上角开启。"
            )

    def _primary_asset(
        self,
        video_id: int,
        kind: str,
    ) -> dict[str, Any]:
        if not self.storage.get_video(video_id):
            raise ValueError("video not found")
        for asset in self.storage.list_assets(video_id):
            mime_type = str(asset.get("mime_type") or "")
            if kind == "video" and mime_type.startswith("video/"):
                return asset
            if kind == "visual" and (
                mime_type.startswith("video/")
                or mime_type.startswith("image/")
            ):
                return asset
        raise ValueError("这条作品没有可用的本地媒体文件。")

    @staticmethod
    def _extract_audio(source: Path, output: Path) -> None:
        result = subprocess.run(
            [
                str(find_ffmpeg()),
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0 or not output.is_file():
            stderr = result.stderr or ""
            if "does not contain any stream" in stderr.casefold():
                raise RuntimeError(
                    "源视频文件没有音频轨，无法进行语音识别。"
                    "请重新下载包含声音的完整视频。"
                )
            raise RuntimeError(
                f"无法从视频提取豆包识别音频：{stderr[-600:]}"
            )

    @staticmethod
    def _extract_cover(source: Path, output: Path) -> None:
        result = subprocess.run(
            [
                str(find_ffmpeg()),
                "-y",
                "-ss",
                "0.2",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=960:-2:force_original_aspect_ratio=decrease",
                "-q:v",
                "2",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError(
                f"无法提取豆包封面识别画面：{result.stderr[-600:]}"
            )

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            return round(max(0.0, min(1.0, float(value))), 3)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        target = name.casefold()
        for key, value in headers.items():
            if str(key).casefold() == target:
                return str(value)
        return ""

    @staticmethod
    def _http_json(
        url: str,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> tuple[Mapping[str, str], dict[str, Any]]:
        request_headers = {
            "Content-Type": "application/json; charset=utf-8",
            **dict(headers),
        }
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                body = json.loads(raw) if raw.strip() else {}
                return dict(response.headers.items()), body
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                message = raw
            error = message.get("error") if isinstance(message, dict) else {}
            if isinstance(error, dict) and error.get("code") == "ModelNotOpen":
                model_name = str(error.get("message") or "")
                match = re.search(r"model\s+([A-Za-z0-9._:-]+)", model_name)
                model_label = (
                    match.group(1).rstrip(".")
                    if match
                    else str(
                        getattr(self.settings, "doubao_text_model", "")
                        or "所选模型"
                    )
                )
                raise RuntimeError(
                    f"方舟账号尚未开通模型 {model_label}。"
                    "请先在火山方舟控制台开通该模型的按量调用服务，"
                    "再重新点击“AI提炼”。"
                ) from exc
            raise RuntimeError(
                f"豆包接口请求失败（HTTP {exc.code}）：{message}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"无法连接豆包接口：{exc.reason}"
            ) from exc
