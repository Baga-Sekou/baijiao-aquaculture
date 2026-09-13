"""SiliconFlow feeding advice. Credentials stay on the server; no device tools."""
import json
import math
import os
import threading

import requests

PROMPT_VERSION = "llm-feed-v1"
ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"
_slot = threading.BoundedSemaphore(1)


class AdviceError(Exception):
    """Only fixed, user-safe errors leave this adapter."""


def recommend(context, minimum, maximum):
    key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    model = os.getenv("SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4-Flash").strip()
    if not key:
        raise AdviceError("大模型未配置：请在后端本机 .env 设置 SILICONFLOW_API_KEY")
    if not model or len(model) > 160 or key in model:
        raise AdviceError("大模型名称配置无效")
    if not _slot.acquire(blocking=False):
        raise AdviceError("大模型正在生成建议，请稍后手动重试")
    try:
        prompt = (
            "你是水产养殖课程演示中的投喂建议助手。数据及规则是演示输入，不是经实地验证的养殖标准。"
            "根据给定摘要判断本次是否投喂。不能声称诊断疾病、证明节料效果或已执行设备。"
            "不得调用工具或发出设备指令，始终由人核实并确认。未知和缺测必须说明，不能编造。"
            "仅返回 JSON 对象：decision 为 feed 或 hold；amount_kg 为数字（hold 时为 null）；"
            "unit 必须是 kg；reason 为 1 至 800 字的中文理由。"
            "feed 的数量必须在 allowed_min_kg 和 allowed_max_kg 之间，保留最多三位小数。"
            "allowed_max_kg 是本演示的保守限制，不允许超过规则参考量，不能把该限制当作真实安全阈值。"
            "认为数据不足或应该暂缓时返回 hold，不要为凑数强行建议投喂。"
        )
        try:
            observations = json.dumps({
                "observations": context, "allowed_min_kg": minimum,
                "allowed_max_kg": maximum,
            }, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, OverflowError):
            raise AdviceError("环境摘要含无效数值，本次未请求大模型") from None
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": observations}],
            "stream": False, "enable_thinking": False,
            "temperature": 0.2, "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }
        try:
            with requests.post(ENDPOINT, headers={"Authorization": "Bearer " + key},
                               json=payload, timeout=(5, 35), allow_redirects=False) as response:
                if response.status_code != 200:
                    if response.status_code in (401, 403):
                        raise AdviceError("大模型认证失败，请检查后端密钥与模型权限")
                    if response.status_code == 429:
                        raise AdviceError("大模型额度不足或请求过多，请稍后手动重试")
                    raise AdviceError("大模型服务不可用，请检查模型配置或稍后手动重试")
                body = response.json()
            choice = body["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise AdviceError("大模型输出未完整结束，本次未生成投喂建议")
            content = choice["message"]["content"]
            if not isinstance(content, str) or len(content) > 6000 or key in content:
                raise AdviceError("大模型输出无效，本次未生成投喂建议")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("object required")
            decision, amount = result.get("decision"), result.get("amount_kg")
            reason = result.get("reason")
            if (decision not in ("feed", "hold") or result.get("unit") != "kg"
                    or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 800):
                raise ValueError("invalid fields")
            if decision == "hold":
                if amount is not None:
                    raise ValueError("hold must have null amount")
            elif (isinstance(amount, bool) or not isinstance(amount, (int, float))
                  or not math.isfinite(amount) or not minimum <= amount <= maximum
                  or not minimum <= round(amount, 3) <= maximum):
                raise AdviceError("大模型建议量超出允许范围或不是有效数值，本次未保存")
            return {"provider": "SiliconFlow", "model": model,
                    "prompt_version": PROMPT_VERSION, "decision": decision,
                    "amount_kg": round(amount, 3) if decision == "feed" else None,
                    "unit": "kg", "reason": reason.strip()}
        except requests.Timeout:
            raise AdviceError("大模型请求超时，本次未生成建议；请手动重试") from None
        except requests.RequestException:
            raise AdviceError("大模型网络请求失败，本次未生成建议") from None
        except (ValueError, KeyError, IndexError, TypeError, OverflowError):
            raise AdviceError("大模型返回格式无效，本次未生成投喂建议") from None
    finally:
        _slot.release()
