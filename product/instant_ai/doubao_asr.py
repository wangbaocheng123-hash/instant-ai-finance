from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ASR_API_BASE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
ASR_SUBMIT_URL = f"{ASR_API_BASE_URL}/submit"
ASR_QUERY_URL = f"{ASR_API_BASE_URL}/query"
DEFAULT_RESOURCE_ID = "volc.seedasr.auc"


class DoubaoAsrUnavailable(RuntimeError):
    pass


def is_configured() -> bool:
    api_key = os.environ.get("INSTANT_AI_DOUBAO_ASR_API_KEY", "").strip()
    app_id = os.environ.get("INSTANT_AI_DOUBAO_ASR_APP_ID", "").strip()
    access_key = os.environ.get("INSTANT_AI_DOUBAO_ASR_ACCESS_KEY", "").strip()
    return bool(api_key or (app_id and access_key))


def transcribe_video(source: Path, work_id: int, *, scope: str = "model-mr", max_duration_seconds: int | None = None) -> dict[str, Any]:
    if not is_configured():
        raise DoubaoAsrUnavailable("云端尚未安全配置豆包语音凭据。")
    source = source.resolve()
    if not source.is_file():
        raise DoubaoAsrUnavailable("这条作品没有可识别的本地视频。")
    ffmpeg = _find_ffmpeg()
    safe_scope = "blogger" if scope == "blogger" else "model-mr"
    with tempfile.TemporaryDirectory(prefix=f"instant-ai-{safe_scope}-doubao-{work_id}-") as temp:
        audio_path = Path(temp) / "audio.wav"
        _extract_audio(ffmpeg, source, audio_path)
        if max_duration_seconds is not None:
            with wave.open(str(audio_path), "rb") as audio:
                if audio.getnframes() / audio.getframerate() > max_duration_seconds:
                    raise DoubaoAsrUnavailable("视频超过自动识别时长上限，未提交付费识别。")
        if audio_path.stat().st_size > 100 * 1024 * 1024:
            raise DoubaoAsrUnavailable("提取后的音频超过 100MB，暂时不能提交豆包识别。")
        task_id = str(uuid.uuid4())
        payload = {
            "user": {"uid": f"instant-ai-{safe_scope}-{work_id}"},
            "audio": {
                "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
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
        headers = _headers(task_id)
        submit_headers, submit = _post_json(ASR_SUBMIT_URL, headers, payload, timeout=180.0)
        if _status_code(submit_headers, submit) != "20000000":
            raise DoubaoAsrUnavailable(_error_message("豆包识别任务提交失败", submit_headers, submit))
        result = _wait_for_result(task_id, headers)
    result_payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    utterances = [
        item for item in result_payload.get("utterances", [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ] if isinstance(result_payload.get("utterances"), list) else []
    text = "\n".join(str(item.get("text") or "").strip() for item in utterances).strip()
    if not text:
        text = str(result_payload.get("text") or "").strip()
    if not text:
        raise DoubaoAsrUnavailable("豆包没有返回可确认的识别文字。")
    audio_info = result.get("audio_info") if isinstance(result.get("audio_info"), dict) else {}
    try:
        duration_seconds = round(float(audio_info.get("duration") or 0) / 1000.0, 3) or None
    except (TypeError, ValueError):
        duration_seconds = None
    return {
        "text": text,
        "engine": "doubao-recording-asr-2.0",
        "cached": False,
        "message": f"豆包识别完成，共 {len(utterances)} 个分句；结果尚未保存，请核对后点击“保存正式原文”。",
        "utterance_count": len(utterances),
        "duration_seconds": duration_seconds,
    }


def _find_ffmpeg() -> str:
    configured = os.environ.get("INSTANT_AI_FFMPEG", "").strip()
    candidate = (shutil.which(configured) if configured else None) or configured or shutil.which("ffmpeg") or ""
    if not candidate or not Path(candidate).is_file():
        raise DoubaoAsrUnavailable("服务器没有可用的 ffmpeg，无法提取视频音频。")
    return candidate


def _extract_audio(ffmpeg: str, source: Path, output: Path) -> None:
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DoubaoAsrUnavailable("服务器无法执行视频音频提取。") from error
    if result.returncode != 0 or not output.is_file():
        if "does not contain any stream" in (result.stderr or "").casefold():
            raise DoubaoAsrUnavailable("视频没有音轨，无法进行语音识别。")
        raise DoubaoAsrUnavailable("视频音频提取失败。")


def _headers(task_id: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "X-Api-Resource-Id": os.environ.get("INSTANT_AI_DOUBAO_ASR_RESOURCE_ID", DEFAULT_RESOURCE_ID).strip() or DEFAULT_RESOURCE_ID,
        "X-Api-Request-Id": task_id,
        "X-Api-Sequence": "-1",
    }
    api_key = os.environ.get("INSTANT_AI_DOUBAO_ASR_API_KEY", "").strip()
    if api_key:
        headers["X-Api-Key"] = api_key
        return headers
    app_id = os.environ.get("INSTANT_AI_DOUBAO_ASR_APP_ID", "").strip()
    access_key = os.environ.get("INSTANT_AI_DOUBAO_ASR_ACCESS_KEY", "").strip()
    if not app_id or not access_key:
        raise DoubaoAsrUnavailable("云端尚未安全配置豆包语音凭据。")
    headers["X-Api-App-Key"] = app_id
    headers["X-Api-Access-Key"] = access_key
    return headers


def _post_json(url: str, headers: Mapping[str, str], payload: dict[str, Any], timeout: float) -> tuple[Mapping[str, str], dict[str, Any]]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            value = json.loads(raw) if raw.strip() else {}
            if not isinstance(value, dict):
                raise DoubaoAsrUnavailable("豆包语音接口返回格式不正确。")
            return dict(response.headers.items()), value
    except HTTPError as error:
        raise DoubaoAsrUnavailable(f"豆包语音接口请求失败（HTTP {error.code}）。") from error
    except (URLError, TimeoutError, OSError) as error:
        raise DoubaoAsrUnavailable("当前无法连接豆包语音接口。") from error


def _wait_for_result(task_id: str, headers: Mapping[str, str], timeout_seconds: float = 900.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        query_headers, response = _post_json(ASR_QUERY_URL, headers, {}, timeout=90.0)
        code = _status_code(query_headers, response)
        if code == "20000000":
            return response
        if code not in {"20000001", "20000002"}:
            raise DoubaoAsrUnavailable(_error_message("豆包识别失败", query_headers, response))
        time.sleep(2.0)
    raise DoubaoAsrUnavailable("豆包识别等待超过 15 分钟，请稍后重试。")


def _status_code(headers: Mapping[str, str], payload: Mapping[str, Any]) -> str:
    for key, value in headers.items():
        if key.casefold() == "x-api-status-code":
            return str(value).strip()
    return str(payload.get("code") or "").strip()


def _error_message(prefix: str, headers: Mapping[str, str], payload: Mapping[str, Any]) -> str:
    code = _status_code(headers, payload) or "未知状态"
    message = str(payload.get("message") or "").strip()
    return f"{prefix}（{code}）{f'：{message}' if message else ''}"
