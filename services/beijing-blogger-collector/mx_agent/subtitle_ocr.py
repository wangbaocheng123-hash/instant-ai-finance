from __future__ import annotations

import hashlib
import json
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .comment_ocr import clean_ocr_line, ocr_image_payload


SUBTITLE_FPS = 2.5
SUBTITLE_FILTER = (
    "fps={fps},"
    "crop=iw*0.96:ih*0.24:iw*0.02:ih*0.72,"
    "scale=iw*2.5:ih*2.5:flags=lanczos"
)
SUBTITLE_UI_PHRASES = (
    "点击推荐",
    "点击推",
    "理财有风险",
    "投资需谨慎",
    "发条评论",
    "和大家一起讨论",
    "相关搜索",
    "IP属地",
    "特别关注",
    "首页",
    "朋友",
    "消息",
    "模型先生",
    "模型哥看世界",
)
STANDALONE_OCR_RADICALS = set("亻讠扌彳彡犭艹宀冖廴辶阝饣钅纟忄刂卩廾尢屮丿乚丶")
FINANCE_CAPTION_TERMS = (
    "A股",
    "B浪",
    "二浪",
    "主升浪",
    "科技股",
    "算力",
    "芯片",
    "半导体",
    "有色资源",
    "市盈率",
    "净利润",
    "产业链",
    "科创芯片",
    "中际旭创",
    "寒武纪",
    "东山精密",
    "兆易创新",
    "长鑫存储",
    "紫金矿业",
    "中芯国际",
    "工业富联",
    "弱转强",
    "量化",
    "缩量",
    "放量",
)
SUBTITLE_CORRECTIONS = {
    "伺时也助跌": "同时也助跌",
    "这次日就杀跌": "次日就杀跌",
    "乡这个行的": "这个懂行的",
    "卣芑的这个观点": "自己的这个观点",
    "反正辶就是": "反正这就是",
    "反正这就皇": "反正这就是",
    "我说亻么": "我说什么",
    "我说亻+么": "我说什么",
    "为，我最在看多": "我最近在看多",
    "为我最在看多": "我最近在看多",
    "河没有任何": "没有任何",
    "量花杀跌": "量化杀跌",
    "喼其实": "其实",
    "过最近表现": "最近表现",
    "匚一最后呢": "最后呢",
    "乡呃后面": "呃后面",
    "我最后当时说了个弱转": "我最后当时说了一个弱转强",
    "后后面坚决": "然后后面坚决",
    "喼就是": "嗯就是",
    "那没有同样也是": "同样也是",
    "有让任何人去只": "没有让任何人去",
    "一最尸呢": "最后呢",
    "丿彡呃后面": "呃后面",
    "彡呃后面": "呃后面",
    "丿彡，呃后面": "呃后面",
    "彡，呃后面": "呃后面",
    "只是因为现在网上说你": "只是因为现在网上说我",
    "这个快速呃推升": "就快速推升",
    "在也没有卖出": "现在也没有卖出",
    "有珂以扛": "也可以扛",
    "无所讠胃": "无所谓",
    "你亡个投机客": "你一个投机客",
    "你等王是": "你等于是",
    "艹击推你得": "你觉得",
    "乖论": "无论",
    "模型先箐": "",
    "我门": "我们",
    "反身向上": "反向向上",
}


def recognize_video_subtitles(
    ffmpeg: Path,
    source_path: Path,
    work_dir: Path,
    *,
    fps: float = SUBTITLE_FPS,
) -> dict[str, Any]:
    """Read burned-in subtitles from the lower part of a portrait video."""
    frames_dir = work_dir / "subtitle_frames"
    # Cache the selected subtitle text, not the raw OCR payload.  Bump the
    # cache name whenever the line-selection rules change so stale interface
    # text can never survive a parser fix.
    cache_path = work_dir / "subtitle_ocr_cache_v6.json"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old_frame in frames_dir.glob("frame_*.png"):
        old_frame.unlink()

    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        SUBTITLE_FILTER.format(fps=fps),
        "-vsync",
        "vfr",
        str(frames_dir / "frame_%06d.png"),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "视频字幕画面提取失败").strip())

    cache = load_ocr_cache(cache_path)
    cache_changed = False
    samples: list[dict[str, Any]] = []
    ocr_errors: list[dict[str, Any]] = []
    for index, frame_path in enumerate(sorted(frames_dir.glob("frame_*.png"))):
        timestamp = index / fps
        try:
            frame_hash = hashlib.sha256(frame_path.read_bytes()).hexdigest()
            if frame_hash in cache:
                text = clean_subtitle_text(cache[frame_hash])
            else:
                payload = ocr_image_payload(frame_path)
                text = subtitle_text_from_payload(payload)
                cache[frame_hash] = text
                cache_changed = True
        except (OSError, RuntimeError, ValueError) as exc:
            ocr_errors.append({"timestamp": round(timestamp, 3), "error": str(exc)})
            continue
        if text:
            samples.append(
                {
                    "timestamp": round(timestamp, 3),
                    "text": text,
                    "frame_path": str(frame_path),
                }
            )

    if cache_changed:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    segments = collapse_subtitle_samples(samples, frame_interval=1.0 / fps)
    return {
        "fps": fps,
        "filter": SUBTITLE_FILTER.format(fps=fps),
        "frame_count": len(list(frames_dir.glob("frame_*.png"))),
        "recognized_frame_count": len(samples),
        "segments": segments,
        "ocr_errors": ocr_errors,
    }


def load_ocr_cache(cache_path: Path) -> dict[str, str]:
    if not cache_path.is_file():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return {str(key): str(value or "") for key, value in payload.items()} if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def subtitle_text_from_payload(payload: dict[str, Any]) -> str:
    candidates: list[tuple[str, float, float, float]] = []
    detected_bottom = 0.0
    detected_right = 0.0
    for item in payload.get("lines", []):
        for word in item.get("words") or []:
            try:
                detected_bottom = max(
                    detected_bottom,
                    float(word.get("y") or 0) + float(word.get("height") or 0),
                )
                detected_right = max(
                    detected_right,
                    float(word.get("x") or 0) + float(word.get("width") or 0),
                )
            except (TypeError, ValueError):
                continue

    for item in payload.get("lines", []):
        text = clean_ocr_line(item.get("text") or "")
        if not text or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
            continue
        words = item.get("words") or []
        heights = []
        tops = []
        lefts = []
        rights = []
        word_entries: list[tuple[str, float, float]] = []
        for word in words:
            try:
                height = float(word.get("height") or 0)
                top = float(word.get("y") or 0)
                left = float(word.get("x") or 0)
                right = left + float(word.get("width") or 0)
                heights.append(height)
                tops.append(top)
                lefts.append(left)
                rights.append(right)
                word_text = clean_ocr_line(word.get("text") or "")
                if word_text:
                    word_entries.append((word_text, height, left))
            except (TypeError, ValueError):
                continue
        font_height = max(heights, default=0.0)
        top = min(tops, default=0.0)
        left = min(lefts, default=0.0)
        right = max(rights, default=left)
        if word_entries and font_height > 0:
            # Douyin may place a small "点击推荐" label on the same OCR line as
            # the large burned-in caption. Rebuild the line from caption-sized
            # words so those controls never become transcript text.
            word_floor = max(24.0, font_height * 0.68)
            large_words = [
                (word_text, word_left)
                for word_text, word_height, word_left in word_entries
                if word_height >= word_floor
            ]
            if large_words:
                text = "".join(
                    word_text for word_text, _ in sorted(large_words, key=lambda word: word[1])
                )
        if is_subtitle_ui_text(text):
            continue
        if (
            detected_right >= 500
            and detected_bottom >= 300
            and left < detected_right * 0.08
            and right < detected_right * 0.22
            and top > detected_bottom * 0.25
        ):
            continue
        # A subtitle line is normally at least two useful characters.  This
        # also removes isolated OCR noise caused by hands or clothing.  A
        # single large Chinese character is retained because the second line
        # of a two-line caption is occasionally just "啊" or "呢".
        useful = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
        if len(useful) < 2 and not (
            len(useful) == 1
            and re.fullmatch(r"[\u4e00-\u9fff]", useful)
            and useful not in STANDALONE_OCR_RADICALS
            and font_height >= 36
        ):
            continue
        candidates.append((text, font_height, top, left))

    if not candidates:
        return ""

    # Douyin screen recordings contain stable UI labels below the actual
    # burned-in subtitle (account, risk notice, search and comment controls).
    # The real subtitle is the largest text in this cropped region.  Keep all
    # similarly large lines so two-line captions remain intact.
    max_height = max(height for _, height, _, _ in candidates)
    if max_height > 0:
        minimum_height = max(30.0, max_height * 0.76)
        selected = [item for item in candidates if item[1] >= minimum_height]
        # Select the vertical cluster around the largest caption line. This
        # supports captions at any height while excluding distant Douyin UI.
        anchor = max(candidates, key=lambda item: (item[1], len(item[0])))
        vertical_radius = max_height * 1.55
        selected = [
            item
            for item in selected
            if abs(item[2] - anchor[2]) <= vertical_radius
        ]
    else:
        selected = candidates[:1]
    selected.sort(key=lambda item: (item[2], item[3]))
    return clean_subtitle_text("".join(text for text, _, _, _ in selected))


def is_subtitle_ui_text(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    if any(phrase in text for phrase in SUBTITLE_UI_PHRASES):
        return True
    if re.search(r"\b20\d{2}[-一/]\d{1,2}[-一/]\d{1,2}\b", text):
        return True
    if re.fullmatch(r"\d{1,4}", text):
        return True
    return False


def clean_subtitle_text(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9，。！？%％+-]+", "", value)
    value = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9]+|[^\u4e00-\u9fffA-Za-z0-9]+$", "", value)
    for phrase in SUBTITLE_UI_PHRASES:
        value = value.replace(phrase, "")
    value = re.sub(r"(?:点击推(?:荐)?|理财有风险|投资需谨慎)+", "", value)
    value = re.sub(r"^勿(?=其实|是去年)", "", value)
    value = re.sub(r"^亻(?=其实)", "", value)
    value = re.sub(r"明确指出3(?:是|0)?咼?[，,]?点", "明确指出30号是高点", value)
    value = re.sub(r"明确指出3$", "明确指出30号是高点", value)
    for wrong, right in SUBTITLE_CORRECTIONS.items():
        value = value.replace(wrong, right)
    value = re.sub(r"科创5(?!\d)", "科创50", value)
    if re.search(r"[\u4e00-\u9fff]", value):
        value = re.sub(r"(?<=[\u4e00-\u9fff])[A-Za-z](?=[\u4e00-\u9fff]|$)", "", value)
    return value.strip()


def collapse_subtitle_samples(
    samples: list[dict[str, Any]],
    *,
    frame_interval: float,
) -> list[dict[str, Any]]:
    """Collapse repeated subtitle frames without losing subtitle changes."""
    segments: list[dict[str, Any]] = []
    for sample in samples:
        text = normalize_match_text(sample.get("text") or "")
        if not text:
            continue
        timestamp = float(sample.get("timestamp") or 0.0)
        if segments and same_subtitle(segments[-1]["text"], text):
            current = segments[-1]
            # Keep the more complete OCR reading from repeated frames.
            if len(text) > len(current["text"]):
                current["text"] = text
                current["frame_path"] = sample.get("frame_path", "")
            current["end"] = round(timestamp + frame_interval, 3)
            current["sample_count"] += 1
            continue
        segments.append(
            {
                "start": round(timestamp, 3),
                "end": round(timestamp + frame_interval, 3),
                "text": text,
                "frame_path": sample.get("frame_path", ""),
                "sample_count": 1,
                "source": "video-subtitle-ocr",
            }
        )
    return merge_adjacent_ocr_variants(segments, frame_interval=frame_interval)


def merge_adjacent_ocr_variants(
    segments: list[dict[str, Any]],
    *,
    frame_interval: float,
) -> list[dict[str, Any]]:
    """Merge imperfect readings of the same subtitle around frame changes."""
    result: list[dict[str, Any]] = []
    for segment in segments:
        current = dict(segment)
        current["variants"] = [current["text"]]
        if result and likely_same_caption_variant(result[-1], current, frame_interval):
            previous = result[-1]
            candidates = [
                (previous["text"], int(previous.get("sample_count") or 1)),
                (current["text"], int(current.get("sample_count") or 1)),
            ]
            previous["text"] = max(candidates, key=lambda item: subtitle_quality_score(*item))[0]
            previous["end"] = max(float(previous["end"]), float(current["end"]))
            previous["sample_count"] = int(previous.get("sample_count") or 1) + int(
                current.get("sample_count") or 1
            )
            previous["variants"] = list(dict.fromkeys(previous.get("variants", []) + current["variants"]))
            continue
        result.append(current)
    return result


def likely_same_caption_variant(
    left: dict[str, Any],
    right: dict[str, Any],
    frame_interval: float,
) -> bool:
    if float(right.get("start") or 0.0) - float(left.get("end") or 0.0) > frame_interval * 1.1:
        return False
    a = normalize_match_text(left.get("text") or "")
    b = normalize_match_text(right.get("text") or "")
    if min(len(a), len(b)) < 6:
        return False
    matcher = SequenceMatcher(None, a, b)
    longest = matcher.find_longest_match(0, len(a), 0, len(b)).size
    return matcher.ratio() >= 0.5 or longest >= min(6, min(len(a), len(b)) - 1)


def subtitle_quality_score(text: str, sample_count: int) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    useful = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
    unusual = len(re.findall(r"[^\u4e00-\u9fffA-Za-z0-9，。！？%％+-]", text))
    isolated_latin = len(re.findall(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", text))
    return chinese * 3 + useful + min(sample_count, 4) * 3 - unusual * 8 - isolated_latin * 5


def normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip("，。！？、·:：;；")


def same_subtitle(left: str, right: str) -> bool:
    a = normalize_match_text(left)
    b = normalize_match_text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 4 and shorter in longer:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.78


def parse_srt(value: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    blocks = re.split(r"\r?\n\s*\r?\n", str(value or "").strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            continue
        match = re.match(r"(.+?)\s*-->\s*(.+)", lines[timing_index])
        if not match:
            continue
        text = "".join(lines[timing_index + 1 :]).strip()
        if not text:
            continue
        segments.append(
            {
                "start": srt_timestamp_seconds(match.group(1)),
                "end": srt_timestamp_seconds(match.group(2)),
                "text": text,
                "source": "whisper.cpp",
            }
        )
    return segments


def srt_timestamp_seconds(value: str) -> float:
    match = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)", str(value or ""))
    if not match:
        return 0.0
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return round(hours * 3600 + minutes * 60 + seconds + millis / 1000.0, 3)


def merge_visual_and_audio_segments(
    visual_segments: list[dict[str, Any]],
    audio_segments: list[dict[str, Any]],
    *,
    overlap_tolerance: float = 0.35,
) -> list[dict[str, Any]]:
    """Fuse both streams instead of letting any partial OCR block the audio.

    Burned-in captions remain preferred when they are complete and clean.
    Whisper repairs a caption when OCR contains interface text, standalone
    radicals, or only a prefix of the spoken sentence.
    """
    useful_audio: list[dict[str, Any]] = []
    for item in audio_segments:
        text = normalize_match_text(item.get("text") or "")
        if useful_audio_text(text):
            audio = dict(item)
            audio["text"] = text
            useful_audio.append(audio)

    if not useful_audio:
        return [dict(item) for item in visual_segments if normalize_match_text(item.get("text") or "")]

    assignments: dict[int, list[int]] = {index: [] for index in range(len(useful_audio))}
    assigned_visual: set[int] = set()
    for visual_index, visual in enumerate(visual_segments):
        best_audio_index = -1
        best_overlap = 0.0
        for audio_index, audio in enumerate(useful_audio):
            overlap = segment_overlap_seconds(visual, audio)
            if overlap > best_overlap:
                best_overlap = overlap
                best_audio_index = audio_index
        if best_audio_index >= 0 and best_overlap > overlap_tolerance:
            assignments[best_audio_index].append(visual_index)
            assigned_visual.add(visual_index)

    merged: list[dict[str, Any]] = []
    for audio_index, audio in enumerate(useful_audio):
        visual_indexes = assignments[audio_index]
        overlapping_visual = [visual_segments[index] for index in visual_indexes]
        visual_text = clean_subtitle_text(
            stitch_segment_text(overlapping_visual)
        ) if overlapping_visual else ""
        audio_text = normalize_match_text(audio.get("text") or "")
        selected_text, selected_source = choose_caption_text(visual_text, audio_text)
        item = dict(audio)
        item["text"] = selected_text
        if selected_source == "visual":
            item["source"] = "video-subtitle-ocr-fused"
            if overlapping_visual:
                item["start"] = min(
                    float(item.get("start") or 0.0),
                    min(float(part.get("start") or 0.0) for part in overlapping_visual),
                )
                item["end"] = max(
                    float(item.get("end") or 0.0),
                    max(float(part.get("end") or 0.0) for part in overlapping_visual),
                )
        else:
            item["source"] = (
                "whisper.cpp-visual-repair" if overlapping_visual else "whisper.cpp-gap-fill"
            )
        item["visual_candidate"] = normalize_match_text(visual_text)
        item["audio_candidate"] = audio_text
        merged.append(item)

    for visual_index, visual in enumerate(visual_segments):
        if visual_index in assigned_visual:
            continue
        text = clean_subtitle_text(visual.get("text") or "")
        if not text or is_subtitle_ui_text(text):
            continue
        item = dict(visual)
        item["text"] = text
        merged.append(item)

    merged.sort(key=lambda item: float(item.get("start") or 0.0))
    return merged


def segment_overlap_seconds(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(
        0.0,
        min(float(left.get("end") or 0.0), float(right.get("end") or 0.0))
        - max(float(left.get("start") or 0.0), float(right.get("start") or 0.0)),
    )


def choose_caption_text(visual_text: str, audio_text: str) -> tuple[str, str]:
    visual = clean_subtitle_text(visual_text)
    audio = normalize_match_text(audio_text)
    if not visual:
        return audio, "audio"
    if not audio:
        return visual, "visual"

    penalty = ocr_artifact_penalty(visual)
    if penalty >= 7:
        return audio, "audio"

    if visual in audio and len(audio) <= max(len(visual) + 12, round(len(visual) * 1.8)):
        return audio, "audio"
    if audio in visual:
        return visual, "visual"

    similarity = SequenceMatcher(None, visual, audio).ratio()
    visual_terms = finance_term_count(visual)
    audio_terms = finance_term_count(audio)
    if similarity >= 0.3 and visual_terms > audio_terms:
        return visual, "visual"
    if similarity >= 0.3 and audio_terms > visual_terms:
        return audio, "audio"
    if similarity >= 0.42 and len(visual) < len(audio) * 0.72:
        return audio, "audio"
    if (
        similarity >= 0.72
        and len(audio) > len(visual)
        and audio[-1:] in "啊呀呢吧吗嘛了"
        and visual[-1:] not in "啊呀呢吧吗嘛了"
        and audio_terms >= visual_terms
    ):
        return audio, "audio"
    if (
        similarity >= 0.58
        and len(audio) >= len(visual) + 2
        and audio_terms >= visual_terms
    ):
        return audio, "audio"

    visual_score = caption_quality_score(visual, source="visual")
    audio_score = caption_quality_score(audio, source="audio")
    return (visual, "visual") if visual_score >= audio_score else (audio, "audio")


def finance_term_count(value: str) -> int:
    text = normalize_match_text(value)
    return sum(1 for term in FINANCE_CAPTION_TERMS if term.casefold() in text.casefold())


def ocr_artifact_penalty(value: str) -> int:
    text = normalize_match_text(value)
    penalty = sum(text.count(phrase) * 20 for phrase in SUBTITLE_UI_PHRASES)
    penalty += sum(1 for char in text if char in STANDALONE_OCR_RADICALS) * 7
    penalty += text.count("�") * 12
    penalty += len(re.findall(r"(?:点击推|理财有风险|投资需谨慎)", text)) * 20
    return penalty


def caption_quality_score(value: str, *, source: str) -> float:
    text = normalize_match_text(value)
    useful = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    score = useful * 1.5 + chinese + finance_term_count(text) * 12
    score -= ocr_artifact_penalty(text)
    if source == "visual":
        score += 4
    return score


def useful_audio_text(text: str) -> bool:
    useful = re.sub(r"[呃嗯啊哦诶唉额哈]", "", normalize_match_text(text))
    if useful in {"妈妈", "媽媽", "谢谢观看", "謝謝觀看", "感谢收看", "感謝收看"}:
        return False
    return len(useful) >= 2


def stitch_segment_text(segments: list[dict[str, Any]]) -> str:
    result = ""
    previous_end: float | None = None
    for segment in segments:
        text = normalize_match_text(segment.get("text") or "")
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        if not result:
            result = text
            previous_end = end
            continue
        overlap = longest_suffix_prefix_overlap(result, text)
        if overlap >= 1:
            result += text[overlap:]
        else:
            gap = max(0.0, start - float(previous_end or start))
            if result.endswith(("，", "。", "！", "？", "\n")):
                separator = ""
            elif gap >= 2.0:
                separator = "\n"
            elif gap >= 0.9 and len(last_text_clause(result)) >= 16:
                separator = "，"
            else:
                separator = ""
            result += separator + text
        previous_end = max(float(previous_end or end), end)
    return result.strip()


def last_text_clause(value: str) -> str:
    return re.split(r"[，。！？\n]", str(value or ""))[-1]


def longest_suffix_prefix_overlap(left: str, right: str, maximum: int = 20) -> int:
    limit = min(len(left), len(right), maximum)
    for size in range(limit, 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0
