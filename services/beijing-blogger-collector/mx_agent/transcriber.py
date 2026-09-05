from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import ctypes
from pathlib import Path
from typing import Any

from .comment_ocr import clean_ocr_line, ocr_image_payload
from .media_tools import find_ffmpeg
from .settings import DATA_DIR, ROOT_DIR, require_openai_api_enabled
from .storage import Storage
from .subtitle_ocr import (
    merge_visual_and_audio_segments,
    parse_srt,
    recognize_video_subtitles,
    stitch_segment_text,
)


WHISPER_DIR = ROOT_DIR / "tools" / "whispercpp"
RUNTIME_DIR = Path(os.getenv("MX_AGENT_RUNTIME_DIR") or Path(os.getenv("LOCALAPPDATA", str(DATA_DIR))) / "MXAgent")
RUNTIME_WHISPER_DIR = RUNTIME_DIR / "whispercpp"
DEFAULT_WHISPER_EXE = RUNTIME_WHISPER_DIR / "Release" / "whisper-cli.exe"
DEFAULT_WHISPER_MODEL = RUNTIME_WHISPER_DIR / "ggml-small.bin"
PROJECT_WHISPER_EXE = WHISPER_DIR / "Release" / "whisper-cli.exe"
PROJECT_WHISPER_MODEL = WHISPER_DIR / "ggml-small.bin"
WHISPER_MODEL_CANDIDATES = (
    RUNTIME_WHISPER_DIR / "ggml-large-v3-turbo-q5_0.bin",
    WHISPER_DIR / "ggml-large-v3-turbo-q5_0.bin",
    RUNTIME_WHISPER_DIR / "ggml-large-v3-turbo.bin",
    WHISPER_DIR / "ggml-large-v3-turbo.bin",
    RUNTIME_WHISPER_DIR / "ggml-medium.bin",
    WHISPER_DIR / "ggml-medium.bin",
    DEFAULT_WHISPER_MODEL,
    PROJECT_WHISPER_MODEL,
)

FINANCE_PROMPT = (
    "这是一段中文金融投资视频，可能包含股票、行业、估值和公司名称。"
    "常见词包括：浪潮信息、紫光股份、紫金矿业、长鑫存储、长电科技、中芯国际、寒武纪、工业富联、"
    "中际旭创、东山精密、兆易创新、中信证券、科创芯片、算力、芯片、半导体、AI上游、有色资源、"
    "市盈率、净利润、市值、估值、产业链、券商研报、市场共识、A股、港股、B浪、二浪探底、"
    "主升浪、弱转强、缩量、放量、量化、科创50、孙子兵法、智者之虑、辩证法、两点论。"
    "请使用完整的简体中文词语，不要把一个词拆成多行。"
)

FINANCE_CORRECTIONS = {
    "浪潮 信息": "浪潮信息",
    "浪差信息": "浪潮信息",
    "紫光 股份": "紫光股份",
    "紫光股分": "紫光股份",
    "紫金 矿业": "紫金矿业",
    "长兴存储": "长鑫存储",
    "长芯存储": "长鑫存储",
    "中芯 国际": "中芯国际",
    "寒武 纪": "寒武纪",
    "工业 富联": "工业富联",
    "市盈 率": "市盈率",
    "净 利润": "净利润",
    "A 股": "A股",
    "港 股": "港股",
    "AI 上游": "AI上游",
    "名牌": "明牌",
    "真当调整": "震荡调整",
    "振荡调整": "震荡调整",
    "服务器交换机": "服务器、交换机",
    "去组织的概率": "去主升的概率",
    "航情": "行情",
    "通煞": "通杀",
    "煞下来": "杀下来",
    "练画": "量化",
    "助长": "助涨",
    "助爹": "助跌",
    "帅航天": "商业航天",
    "晒行天": "商业航天",
    "杀爹": "杀跌",
    "热转墙": "弱转强",
    "莫能良可": "模棱两可",
    "尾牌": "尾盘",
    "阿朗": "二浪",
    "啊朗": "二浪",
    "逼浪": "B浪",
    "科状芯片": "科创芯片",
    "科创心片": "科创芯片",
    "终极蓄创": "中际旭创",
    "中际蓄创": "中际旭创",
    "中计旭创": "中际旭创",
    "寒午季": "寒武纪",
    "寒五纪": "寒武纪",
    "朝一创新": "兆易创新",
    "赵亦创新": "兆易创新",
    "赵亦": "兆易",
    "长新存储": "长鑫存储",
    "长新产业链": "长鑫产业链",
    "科状": "科创",
    "使用率": "市盈率",
    "见过后": "建国后",
    "常中": "长征",
    "常征": "长征",
    "冬山精密": "东山精密",
    "活力最大": "获利最大",
    "因因而异": "因人而异",
    "无头额看不扛": "无所谓扛不扛",
    "随你病法": "孙子兵法",
    "治治之虑": "智者之虑",
    "比杂与厉害": "必杂于利害",
    "杂与厉而无可深夜": "杂于利而务可信也",
    "杂与害而换可解也": "杂于害而患可解也",
    "编程法": "辩证法",
    "科上武林": "科创50",
    "反身向上": "反向向上",
}

LCMAP_SIMPLIFIED_CHINESE = 0x02000000
PORTABLE_TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "個": "个",
        "點": "点",
        "這": "这",
        "現": "现",
        "屬": "属",
        "於": "于",
        "為": "为",
        "從": "从",
        "體": "体",
        "對": "对",
        "說": "说",
        "難": "难",
        "來": "来",
    }
)
_BASIC_COMMA_MARKERS = (
    "但是",
    "所以",
    "不过",
    "然后",
    "同时",
    "另外",
    "反而",
    "因为",
    "其实",
    "这时候",
    "这个时候",
    "比如说",
)

DEFAULT_IMAGE_OCR_MODEL = "gpt-5.6-luna"
IMAGE_OCR_INSTRUCTIONS = """你是中文图片逐字转写助手。请只识别图片中真实可见的文字。
要求：
1. 按从上到下、从左到右的顺序转写。
2. 全部输出为简体中文，保留数字、英文、标题和自然段。
3. 可修正非常明确的繁简转换和常见金融名词，但不得补写图片中没有的内容。
4. 看不清的局部不要猜测；整张图没有可辨认文字时，text 返回空字符串。
5. 不要解释图片，不要总结观点，只返回逐字转写结果。"""
IMAGE_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "按图片阅读顺序整理的简体中文逐字转写文本",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}


class VideoTranscriber:
    def __init__(
        self,
        storage: Storage,
        settings: Any | None = None,
        client_factory: Any | None = None,
    ):
        self.storage = storage
        self.settings = settings
        self._client_factory = client_factory

    def transcribe_video_text(self, video_id: int) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")

        asset = self._primary_video_asset(video_id)
        source_path = Path(asset["local_path"]).resolve()
        if not source_path.exists():
            raise ValueError("视频文件不存在，无法识别")

        ffmpeg = self._ffmpeg_path()
        work_dir = RUNTIME_DIR / "transcribe" / f"video_{video_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        wav_path = work_dir / "audio.wav"
        output_prefix = work_dir / "whisper"
        txt_path = output_prefix.with_suffix(".txt")
        finance_prompt = self._finance_prompt(video_id)

        visual_result: dict[str, Any] = {"segments": [], "error": ""}
        try:
            visual_result = recognize_video_subtitles(ffmpeg, source_path, work_dir)
        except (OSError, RuntimeError, ValueError) as exc:
            visual_result = {"segments": [], "error": str(exc)}

        visual_segments = visual_result.get("segments", [])
        visual_text = normalize_transcript(stitch_segment_text(visual_segments)) if visual_segments else ""
        visual_transcript_id: int | None = None
        if visual_text:
            visual_transcript_id = self.storage.save_transcript(
                video_id,
                visual_text,
                source="video-subtitle-ocr",
                language="zh",
                raw={
                    "engine": "windows-media-ocr",
                    "priority": "primary",
                    "asset_id": asset.get("id"),
                    "source_path": str(source_path),
                    **visual_result,
                },
            )

        audio_segments: list[dict[str, Any]] = []
        audio_text = ""
        raw_text = ""
        audio_error = ""
        audio_transcript_id: int | None = None
        model: Path | None = None
        try:
            whisper = self._whisper_path()
            model = self._model_path()
            self._extract_audio(ffmpeg, source_path, wav_path)
            whisper_result = self._run_whisper(
                whisper,
                model,
                wav_path,
                output_prefix,
                prompt=finance_prompt,
            )
            raw_text = whisper_result["text"]
            audio_segments = whisper_result["segments"]
            audio_text = normalize_transcript(raw_text)
            if audio_text:
                audio_transcript_id = self.storage.save_transcript(
                    video_id,
                    audio_text,
                    source="whisper.cpp",
                    language="zh",
                    raw={
                        "engine": "whisper.cpp",
                        "priority": "gap-fill",
                        "model": str(model),
                        "asset_id": asset.get("id"),
                        "source_path": str(source_path),
                        "wav_path": str(wav_path),
                        "txt_path": str(txt_path),
                        "srt_path": str(output_prefix.with_suffix(".srt")),
                        "prompt": finance_prompt,
                        "segments": audio_segments,
                    },
                )
        except (OSError, RuntimeError, ValueError) as exc:
            audio_error = str(exc)

        fusion_audio_segments = [
            {**segment, "text": normalize_transcript(segment.get("text") or "")}
            for segment in audio_segments
        ]
        combined_segments = merge_visual_and_audio_segments(
            visual_segments,
            fusion_audio_segments,
        )
        text = normalize_transcript(stitch_segment_text(combined_segments))
        if not text:
            text = visual_text or audio_text
        if not text:
            error = visual_result.get("error") or audio_error or "没有识别到视频文字"
            self._cleanup_successful_work_dir(work_dir)
            raise RuntimeError(error)

        transcript_id = self.storage.save_transcript(
            video_id,
            text,
            source="hybrid-quality-fusion-v2",
            language="zh",
            raw={
                "engine": "hybrid-quality-fusion-v2",
                "asset_id": asset.get("id"),
                "source_path": str(source_path),
                "visual_transcript_id": visual_transcript_id,
                "audio_transcript_id": audio_transcript_id,
                "visual_segment_count": len(visual_segments),
                "audio_segment_count": len(audio_segments),
                "combined_segments": combined_segments,
                "visual_error": visual_result.get("error", ""),
                "audio_error": audio_error,
            },
        )

        cleanup = self._cleanup_successful_work_dir(work_dir)

        return {
            "video_id": video_id,
            "asset_id": asset.get("id"),
            "transcript_id": transcript_id,
            "visual_transcript_id": visual_transcript_id,
            "audio_transcript_id": audio_transcript_id,
            "text": text,
            "raw_text": raw_text,
            "visual_text": visual_text,
            "audio_text": audio_text,
            "visual_segment_count": len(visual_segments),
            "audio_segment_count": len(audio_segments),
            "engine": "hybrid-quality-fusion-v2",
            "model": str(model) if model else "",
            "saved": False,
            "save_required": True,
            "visual_error": visual_result.get("error", ""),
            "audio_error": audio_error,
            "cleanup": cleanup,
        }

    def recognize_image_text(self, video_id: int) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")

        asset = self._primary_image_asset(video_id)
        source_path = Path(asset["local_path"]).resolve()
        if not source_path.is_file():
            raise ValueError("图片文件不存在，无法识别")

        payload = ocr_image_payload(source_path)
        lines = [clean_ocr_line(item.get("text") or "") for item in payload.get("lines", [])]
        text = to_simplified_chinese("\n".join(line for line in lines if line).strip())
        if not text:
            raise ValueError("没有从图片中识别到文字")

        return {
            "video_id": video_id,
            "asset_id": asset.get("id"),
            "text": text,
            "raw_text": str(payload.get("text") or ""),
            "engine": "windows-media-ocr",
            "saved": False,
            "save_required": True,
        }

    def recognize_image_text_ai(self, video_id: int) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        require_openai_api_enabled(self.settings, "AI 识图")

        asset = self._primary_image_asset(video_id)
        source_path = Path(asset["local_path"]).resolve()
        if not source_path.is_file():
            raise ValueError("图片文件不存在，无法识别")

        mime_type = (
            str(asset.get("mime_type") or "").strip()
            or mimetypes.guess_type(source_path.name)[0]
            or "image/png"
        )
        image_data = base64.b64encode(source_path.read_bytes()).decode("ascii")
        model = os.getenv("MX_AGENT_IMAGE_OCR_MODEL", DEFAULT_IMAGE_OCR_MODEL)
        response = self._client().responses.create(
            model=model,
            reasoning={"effort": "low"},
            instructions=IMAGE_OCR_INSTRUCTIONS,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "请逐字识别这张图片中的全部正文，并按原有阅读顺序排版。",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{image_data}",
                            "detail": "high",
                        },
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "image_ocr_result",
                    "description": "图片中文字的简体中文逐字转写结果",
                    "schema": IMAGE_OCR_SCHEMA,
                    "strict": True,
                },
                "verbosity": "low",
            },
            max_output_tokens=4000,
            store=False,
        )
        raw = str(getattr(response, "output_text", "") or "").strip()
        if not raw:
            raise RuntimeError("AI没有返回图片文字，请重试。")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AI返回的图片文字格式无法解析，请重试。") from exc
        text = to_simplified_chinese(str(parsed.get("text") or "").strip())
        if not text:
            raise ValueError("AI没有从图片中识别到可用文字")

        return {
            "video_id": video_id,
            "asset_id": asset.get("id"),
            "text": text,
            "raw_text": raw,
            "engine": "openai-vision",
            "model": model,
            "response_id": getattr(response, "id", None),
            "saved": False,
            "save_required": True,
        }

    def _client(self) -> Any:
        api_key = str(getattr(self.settings, "openai_api_key", "") or "")
        if self._client_factory:
            return self._client_factory(api_key)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("缺少 openai 依赖，请运行项目已有的依赖安装流程。") from exc
        return OpenAI(api_key=api_key)

    def _finance_prompt(self, video_id: int) -> str:
        context: list[str] = []
        video = self.storage.get_video(video_id) or {}
        title_info = self.storage.get_video_title(video_id) or {}
        for value in (
            title_info.get("active_title"),
            video.get("title"),
        ):
            text = str(value or "").strip()
            if (
                text
                and re.search(r"[\u4e00-\u9fffA-Za-z]", text)
                and "none_content" not in text.casefold()
                and text not in context
            ):
                context.append(text[:120])
        keyword_note = self.storage.get_note(video_id, "ai_keywords") or {}
        keyword_text = str(keyword_note.get("text") or "").strip()
        if keyword_text:
            context.append(keyword_text[:300])
        if not context:
            return FINANCE_PROMPT
        return f"{FINANCE_PROMPT} 本条视频已知标题或关键词：{'；'.join(context)}。"

    def _primary_video_asset(self, video_id: int) -> dict[str, Any]:
        assets = self.storage.list_assets(video_id)
        for asset in assets:
            if str(asset.get("mime_type") or "").startswith("video/") and asset.get("local_path"):
                return asset
        raise ValueError("这条视频没有可识别的视频文件")

    def _primary_image_asset(self, video_id: int) -> dict[str, Any]:
        assets = self.storage.list_assets(video_id)
        for asset in assets:
            if str(asset.get("mime_type") or "").startswith("image/") and asset.get("local_path"):
                return asset
        raise ValueError("这条记录没有可识别的图片文件")

    def _ffmpeg_path(self) -> Path:
        return find_ffmpeg()

    def _whisper_path(self) -> Path:
        path = Path(os.getenv("MX_AGENT_WHISPER_EXE", str(DEFAULT_WHISPER_EXE)))
        if path.exists():
            return path.resolve()
        if PROJECT_WHISPER_EXE.exists():
            return PROJECT_WHISPER_EXE.resolve()
        raise ValueError("没有找到 whisper-cli.exe，请先安装本地识别引擎")

    def _model_path(self) -> Path:
        configured = os.getenv("MX_AGENT_WHISPER_MODEL", "").strip()
        if configured:
            path = Path(configured)
            if path.exists():
                return path.resolve()
            raise ValueError(f"配置的 Whisper 模型文件不存在：{path}")
        for path in WHISPER_MODEL_CANDIDATES:
            if path.exists():
                return path.resolve()
        raise ValueError("没有找到 Whisper 模型文件 ggml-small.bin")

    def _extract_audio(self, ffmpeg: Path, source_path: Path, wav_path: Path) -> None:
        command = [
            str(ffmpeg),
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-af",
            "highpass=f=70,lowpass=f=7800,dynaudnorm=f=150:g=15",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "音频提取失败").strip())

    def _run_whisper(
        self,
        whisper: Path,
        model: Path,
        wav_path: Path,
        output_prefix: Path,
        *,
        prompt: str,
    ) -> dict[str, Any]:
        txt_path = output_prefix.with_suffix(".txt")
        srt_path = output_prefix.with_suffix(".srt")
        for output_path in (txt_path, srt_path):
            if output_path.exists():
                output_path.unlink()
        command = [
            str(whisper),
            "-m",
            str(model),
            "-f",
            str(wav_path),
            "-l",
            "zh",
            "--prompt",
            prompt,
            "--carry-initial-prompt",
            "-sow",
            "-bo",
            "8",
            "-bs",
            "8",
            "-otxt",
            "-osrt",
            "-of",
            str(output_prefix),
            "-ml",
            "60",
            "-t",
            str(max(2, min(8, (os.cpu_count() or 4) - 1))),
        ]
        result = subprocess.run(
            command,
            cwd=str(whisper.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "语音识别失败").strip())
        text = txt_path.read_text(encoding="utf-8", errors="replace") if txt_path.exists() else result.stdout
        srt = srt_path.read_text(encoding="utf-8", errors="replace") if srt_path.exists() else ""
        return {"text": text, "srt": srt, "segments": parse_srt(srt)}

    @staticmethod
    def _cleanup_successful_work_dir(work_dir: Path) -> dict[str, Any]:
        """Remove regenerated OCR frames and audio after text is safely saved."""
        if not work_dir.exists():
            return {"removed": False, "files": 0, "bytes": 0}
        files = [path for path in work_dir.rglob("*") if path.is_file()]
        total_bytes = sum(path.stat().st_size for path in files)
        shutil.rmtree(work_dir)
        return {
            "removed": True,
            "files": len(files),
            "bytes": total_bytes,
        }


def normalize_transcript(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\[[^\]]+\]", "", value)
    value = value.replace("\ufeff", "")
    value = value.replace(" ,", "，").replace(",", "，")
    value = value.replace(" ?", "？").replace("?", "？")
    value = value.replace(" !", "！").replace("!", "！")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = to_simplified_chinese(value.strip())
    for wrong, right in FINANCE_CORRECTIONS.items():
        value = value.replace(wrong, right)
    value = re.sub(r"科创5(?!\d)", "科创50", value)
    return add_basic_punctuation(value)


def to_simplified_chinese(text: str) -> str:
    value = str(text or "")
    if not value:
        return value
    if os.name != "nt":
        return value.translate(PORTABLE_TRADITIONAL_TO_SIMPLIFIED)
    try:
        mapper = ctypes.windll.kernel32.LCMapStringEx
        mapper.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_long,
        ]
        mapper.restype = ctypes.c_int
        size = mapper(
            "zh-CN",
            LCMAP_SIMPLIFIED_CHINESE,
            value,
            len(value),
            None,
            0,
            None,
            None,
            0,
        )
        if size <= 0:
            return value
        buffer = ctypes.create_unicode_buffer(size)
        written = mapper(
            "zh-CN",
            LCMAP_SIMPLIFIED_CHINESE,
            value,
            len(value),
            buffer,
            size,
            None,
            None,
            0,
        )
        return buffer[:written] if written > 0 else value
    except (AttributeError, OSError, TypeError, ValueError):
        return value


def add_basic_punctuation(text: str) -> str:
    """Add only lightweight commas and full stops to Whisper segment lines."""
    paragraphs: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"\s+", "", raw_line).strip()
        if not line:
            continue
        line = line.replace("；", "，").replace(";", "，")
        line = re.sub(r"的就是", "的，就是", line)
        line = re.sub(r"的可以", "的，可以", line)
        line = re.sub(r"的这(?=[一时个些种])", "的，这", line)
        line = re.sub(r"来说(?![，。！？])", "来说，", line)
        line = re.sub(r"太困难(?=[这那])", "太困难，", line)
        line = re.sub(r"(?<![，。！？])换成", "，换成", line)
        for marker in _BASIC_COMMA_MARKERS:
            line = re.sub(
                rf"(?<=[\u4e00-\u9fffA-Za-z0-9])(?<![，。！？]){re.escape(marker)}",
                f"，{marker}",
                line,
            )
        line = _commas_for_long_clauses(line)
        line = re.sub(r"，{2,}", "，", line).strip("，")
        if not re.search(r"[。！？]$", line):
            line += "。"
        paragraphs.append(line)
    return "\n".join(paragraphs)


def _commas_for_long_clauses(text: str, max_length: int = 36) -> str:
    parts = re.split(r"([，。！？])", text)
    result: list[str] = []
    break_chars = "的了呢吧啊时后中上下来过"
    for part in parts:
        if not part or part in "，。！？":
            result.append(part)
            continue
        clause = part
        while len(clause) > max_length:
            window_start = 20
            window_end = min(max_length, len(clause) - 8)
            if window_end < window_start:
                break
            split_at = -1
            for index in range(window_end, window_start - 1, -1):
                if clause[index] in break_chars:
                    split_at = index + 1
                    break
            if split_at < 0:
                split_at = max_length
            result.append(clause[:split_at] + "，")
            clause = clause[split_at:]
        result.append(clause)
    return "".join(result)
