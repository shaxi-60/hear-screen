"""调用豆包（火山方舟）或其他 OpenAI 兼容视觉模型。"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import Any

import requests

from .config import PROVIDER_DOUBAO

log = logging.getLogger(__name__)


class AIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.user_message = message
        self.status = status
        self.retryable = retryable


def _normalize_endpoint(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise AIError("还没有填写 API 地址。请在“设置 → AI 服务”中填写。")
    if not url.lower().startswith(("http://", "https://")):
        raise AIError("API 地址需要以 http:// 或 https:// 开头。")
    for suffix in ("/chat/completions", "/responses"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url + "/chat/completions"


def _extract_error(response: requests.Response) -> tuple[str, str]:
    detail = ""
    code = ""
    try:
        data = response.json()
        err = data.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
            code = str(err.get("code") or err.get("type") or "")
        elif isinstance(err, str):
            detail = err
        if not detail:
            detail = str(data.get("message") or data.get("msg") or "")
    except Exception:
        detail = (response.text or "")[:300]
    return code, detail.strip()


def _friendly_http_error(response: requests.Response) -> AIError:
    code, detail = _extract_error(response)
    status = response.status_code
    low = (code + " " + detail).lower()
    if status == 401:
        msg = "API Key 无效或已过期。请到火山方舟控制台重新创建，并粘贴完整 Key。"
    elif status == 403:
        msg = "这个 API Key 没有权限，或对应模型尚未开通。请检查模型开通状态和 Key 权限。"
    elif status == 404:
        msg = ("接口地址或模型不存在。请检查 API 地址，并填写控制台中的模型 ID"
               "（如 doubao-seed-1-6-vision-250815）或推理接入点 ID（ep- 开头）。")
    elif status == 429:
        msg = "请求太频繁或账户额度不足。请稍等一会儿，或检查火山方舟账户余额。"
    elif status in (400, 422):
        if "image" in low or "vision" in low or "multimodal" in low:
            msg = "当前模型不支持图片输入。请换成豆包视觉模型，或检查模型 ID。"
        elif "thinking" in low:
            msg = "当前模型不支持“快速模式”参数。请在设置中关闭快速模式后重试。"
        elif "model" in low:
            msg = "模型 ID 不正确，或该模型未开通。请在火山方舟控制台复制准确的模型 ID。"
        else:
            msg = "请求参数被服务端拒绝。请检查模型 ID 和 API 地址。"
    elif status >= 500:
        msg = "AI 服务暂时不可用，请稍后再试。"
    else:
        msg = f"AI 服务返回错误（HTTP {status}）。"
    if detail:
        # 保留服务端原始信息的一小段，便于排查，同时避免刷屏。
        detail = re.sub(r"\s+", " ", detail)[:160]
        msg += f"\n服务端信息：{detail}"
    return AIError(msg, status=status, retryable=status == 429 or status >= 500)


def clean_model_text(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"^```(?:\w+)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    value = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", value)
    value = re.sub(r"[*#`>]+", "", value)
    value = re.sub(r"^\s*(好的[，,。]?\s*)", "", value)
    value = re.sub(r"^\s*(画面解说|解说|描述|回答)\s*[:：]\s*", "", value)
    value = value.strip().strip("\"'“”‘’")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{2,}", "\n", value).strip()
    return value


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


SYSTEM_PROMPT = """你是一位专业的“口述影像”解说员，为看不到屏幕的视障用户解说电脑上正在播放的视频或当前画面。
用户能听到设备里视频原本的声音，所以不要逐字复述能听到的对话，重点补充画面信息。

必须遵守：
1. 只说你确实看到的内容。看不清、被遮挡、画面很暗时，直接说明，不猜测、不编造人物身份、品牌或剧情。
2. 用自然、口语化的简体中文短句，像坐在旁边的人一样解说。先交代场景和人物，再说动作、表情和变化，最后补充重要的屏幕文字。
3. 不要说“画面中、如图所示、你可以看到、这张图、本次画面”等词，直接讲内容。
4. 不要输出 Markdown、表情符号、列表、编号、括号里的舞台说明或英文单词（专有名词除外）。
5. 不要预测接下来会发生什么，只解说当前这一帧。
6. 如果用户听得到原声，不要重复台词；如果用户要求读字幕，再用引号读出台词或字幕。
7. 屏幕上如果有对理解内容很关键的大字、标题、比分、价格、警告，要一起说出来。
"""


class AIClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.endpoint = _normalize_endpoint(str(config.get("base_url", "")))
        self.model = str(config.get("model", "")).strip()
        self.api_key = str(config.get("api_key", "")).strip()
        self.timeout = max(10, min(90, int(config.get("timeout", 35) or 35)))
        self.fast_mode = bool(config.get("fast_mode", True))
        self.read_dialogue = bool(config.get("read_dialogue", False))
        self.provider = str(config.get("provider", PROVIDER_DOUBAO))
        if not self.model:
            raise AIError("还没有填写模型 ID。请在“设置 → AI 服务”中填写。")
        if not self.api_key:
            raise AIError("还没有填写 API Key。请先在“设置 → AI 服务”中填写豆包或兼容服务的 Key。")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "HearScreen/1.0 (Windows; accessibility)",
        })

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ 提示词

    def _system_prompt(self, detailed: bool) -> str:
        if self.read_dialogue:
            dialogue = "用户听不到原声或需要字幕，请把屏幕上的关键台词、字幕用引号读出来。"
        else:
            dialogue = "用户能听到视频原声，不要复述台词；重点描述画面、动作、表情、场景和关键屏幕文字。"
        if detailed:
            length = "可以完整一些，但最多 6 句、180 字左右。"
        else:
            length = "控制在 2 到 3 句、75 个汉字以内，只讲此刻最重要的信息。"
        return SYSTEM_PROMPT + f"\n8. {dialogue}\n9. {length}\n"

    def _user_prompt(self, *, previous: str, index: int, detailed: bool,
                     source: str) -> str:
        if detailed:
            head = (
                "用户主动请求描述当前画面。请完整解说这一帧，让看不到屏幕的人"
                "能理解此刻发生了什么、在哪里、有谁、正在做什么。"
            )
        else:
            head = "这是实时解说中的一帧，请解说此刻的画面。"
        lines = [head]
        if source:
            lines.append(f"截取范围：{source}。")
        lines.append(f"这是第 {index} 次观察。")
        if previous:
            lines.append(f"你上一次的解说：{previous}")
            lines.append("如果与上一次相比没有新的、值得听的变化，只回复：无变化。"
                         "如果有变化，重点说变化，不要重复上一次已经说过的内容。")
        else:
            lines.append("这是第一次观察，请先交代场景和人物。")
        if self.read_dialogue:
            lines.append("请同时读出屏幕上清晰可见的关键字幕或台词。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 请求

    def _payload(self, image_bytes: bytes, prompt: str, *, detailed: bool,
                 with_thinking_flag: bool) -> dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        max_tokens = 420 if detailed else 190
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt(detailed)},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{encoded}",
                            },
                        },
                    ],
                },
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "top_p": 0.9,
            "stream": False,
        }
        if with_thinking_flag:
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                timeout=(8, self.timeout),
            )
        except requests.Timeout as exc:
            raise AIError("请求 AI 超时。请检查网络，或在设置里把超时时间调大。",
                          retryable=True) from exc
        except requests.ConnectionError as exc:
            raise AIError("无法连接 AI 服务。请检查网络、代理和 API 地址。",
                          retryable=True) from exc
        except requests.RequestException as exc:
            log.exception("AI 请求异常")
            raise AIError("请求 AI 时发生网络错误，请稍后重试。") from exc

        if response.status_code >= 400:
            raise _friendly_http_error(response)
        try:
            data = response.json()
        except Exception as exc:
            raise AIError("AI 返回了无法解析的内容，请检查接口地址是否为 OpenAI 兼容格式。") from exc
        log.info("AI 请求成功：%.1fs，HTTP %s", time.monotonic() - started, response.status_code)
        usage = data.get("usage") if isinstance(data, dict) else None
        if usage:
            log.info("token 用量：%s", usage)
        return data

    def _request(self, image_bytes: bytes, prompt: str, *, detailed: bool) -> str:
        if os.environ.get("HEARSCREEN_MOCK_AI") == "1":
            time.sleep(0.45)
            if "连接测试" in prompt:
                return "测试画面是电脑桌面，连接成功。"
            samples = [
                "夜晚的城市街头，一个穿深色外套的年轻人快步穿过人群，霓虹灯映在湿漉漉的地面上。",
                "镜头切到室内，桌上摊着一张地图，一只手正用红笔圈出右上角的区域。",
                "画面变成近景，女孩抬头看向门口，表情从惊讶转为微笑，背景里有人举起了手机。",
                "屏幕中央出现一行大字：明天下午三点，老地方见。",
            ]
            index = sum(1 for ch in prompt if ch == "第") + int(time.time() / 5)
            return samples[index % len(samples)]
        want_thinking = (
            self.fast_mode
            and self.provider == PROVIDER_DOUBAO
            and any(tag in self.model.lower() for tag in ("seed-1-6", "seed-2", "seed-1.6"))
        )
        attempts = 2
        last_error: AIError | None = None
        for attempt in range(attempts):
            payload = self._payload(
                image_bytes, prompt, detailed=detailed,
                with_thinking_flag=want_thinking and attempt == 0,
            )
            try:
                data = self._post(payload)
                return self._parse_response(data)
            except AIError as exc:
                last_error = exc
                # 老模型不认识 thinking 参数：去掉再试一次。
                if exc.status == 400 and want_thinking and attempt == 0:
                    # 不同模型对 thinking 参数支持不一，去掉后重试一次。
                    log.info("模型可能不支持 thinking 参数，自动重试")
                    continue
                if exc.retryable and attempt == 0:
                    time.sleep(0.8)
                    continue
                raise
        raise last_error or AIError("AI 请求失败。")

    def _parse_response(self, data: dict[str, Any]) -> str:
        try:
            choices = data.get("choices") or []
            if not choices:
                raise KeyError("choices")
            message = choices[0].get("message") or {}
            text = _content_to_text(message.get("content"))
            if not text:
                # 部分模型把内容放在 output_text 或 output 里
                text = _content_to_text(data.get("output_text"))
            if not text:
                refusal = message.get("refusal")
                if refusal:
                    raise AIError("AI 拒绝描述这张图片。请换一张画面或检查内容政策。")
                raise AIError("AI 没有返回文字。请稍后重试，或换一个视觉模型。")
        except AIError:
            raise
        except Exception as exc:
            raise AIError("AI 返回格式不正确，请确认接口是 OpenAI 兼容的视觉接口。") from exc
        cleaned = clean_model_text(text)
        if not cleaned:
            raise AIError("AI 返回了空内容，请稍后重试。")
        return cleaned

    # ------------------------------------------------------------------ 对外方法

    def describe(self, image_bytes: bytes, *, previous: str = "", index: int = 1,
                 detailed: bool = False, source: str = "") -> str:
        prompt = self._user_prompt(
            previous=previous, index=index, detailed=detailed, source=source
        )
        text = self._request(image_bytes, prompt, detailed=detailed)
        text = clean_model_text(text)
        # 统一“无变化”的各种说法，便于上层跳过朗读。
        compact = re.sub(r"[\s。，,.！!；;：:]+", "", text)
        if compact in {"无变化", "没有变化", "无明显变化", "画面无变化", "没有新变化",
                       "无新的变化", "保持不变", "与上次相同", "与上一次相同"}:
            return "无变化"
        if len(text) > 420:
            cut = text[:420]
            end = max(cut.rfind(ch) for ch in "。！？")
            text = cut[: end + 1] if end > 260 else cut.rstrip() + "。"
        return text

    def test_connection(self, image_bytes: bytes) -> str:
        prompt = (
            "这是一次连接测试。请用一句简短中文说出你看到的主要内容，"
            "并在句尾加上“连接成功”。不要输出其他解释。"
        )
        payload_text = self._request(image_bytes, prompt, detailed=False)
        if not payload_text.strip():
            raise AIError("模型没有返回内容，连接测试失败。")
        return payload_text.strip()
