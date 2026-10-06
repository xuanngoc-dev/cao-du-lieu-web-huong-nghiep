# -*- coding: utf-8 -*-
"""Gọi Ollama hoặc API miễn phí (Pollinations, Gemini, Groq, Grok, OpenRouter)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

_PROVIDERS = (
    ("local", "Quy tắc nội bộ"),
    ("ollama", "Ollama"),
    ("pollinations", "Pollinations"),
    ("gemini", "Gemini"),
    ("groq", "Groq"),
    ("grok", "Grok"),
    ("openrouter", "OpenRouter"),
)
_ROW_KEYS = (
    "ten_truong", "ma_truong", "ten_nganh", "ma_xet_tuyen", "to_hop",
    "phuong_thuc", "chi_tieu", "diem_chuan", "nam", "chenh", "trang_thai_nhan",
    "diem", "khu_vuc", "loai_truong", "ten", "ma_chuan", "so_nganh",
    "khoang", "vai_tro",
)
_DEFAULTS = {
    "provider": "local",
    "ollama": {"base_url": "http://127.0.0.1:11434", "model": ""},
    "pollinations": {"model": "openai"},
    "gemini": {"model": "gemini-2.5-flash", "api_key": ""},
    "groq": {"model": "llama-3.3-70b-versatile", "api_key": ""},
    "grok": {"model": "grok-3-mini", "api_key": ""},
    "openrouter": {"model": "openrouter/free", "api_key": ""},
}
_KEY_ENV = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "aistudio.google.com/apikey"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "console.groq.com/keys"),
    "grok": ("XAI_API_KEY", "GROK_MODEL", "console.x.ai"),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "openrouter.ai/keys"),
}


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if key == "ghi_chu":
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            nested = dict(out[key])
            nested.update(value)
            out[key] = nested
        else:
            out[key] = value
    return out


def ai_config(root: str) -> Dict[str, Any]:
    """Đọc config/ai.example.json rồi config/ai.json. Biến môi trường ghi đè."""
    cfg = _merge(_DEFAULTS, _read_json(os.path.join(root, "config", "ai.example.json")))
    cfg = _merge(cfg, _read_json(os.path.join(root, "config", "ai.json")))
    provider = os.environ.get("TU_VAN_AI", "").strip().lower()
    if provider in dict(_PROVIDERS):
        cfg["provider"] = provider
    ollama = dict(cfg.get("ollama") or {})
    if os.environ.get("OLLAMA_BASE_URL", "").strip():
        ollama["base_url"] = os.environ["OLLAMA_BASE_URL"].strip()
    if os.environ.get("OLLAMA_MODEL", "").strip():
        ollama["model"] = os.environ["OLLAMA_MODEL"].strip()
    cfg["ollama"] = ollama
    pollinations = dict(cfg.get("pollinations") or {})
    if os.environ.get("POLLINATIONS_MODEL", "").strip():
        pollinations["model"] = os.environ["POLLINATIONS_MODEL"].strip()
    cfg["pollinations"] = pollinations
    for name, (key_env, model_env, _signup) in _KEY_ENV.items():
        block = dict(cfg.get(name) or {})
        if name == "grok":
            key = os.environ.get("XAI_API_KEY", "").strip() or os.environ.get("GROK_API_KEY", "").strip()
        else:
            key = os.environ.get(key_env, "").strip()
        if key:
            block["api_key"] = key
        if os.environ.get(model_env, "").strip():
            block["model"] = os.environ[model_env].strip()
        cfg[name] = block
    return cfg


def _ollama_base(cfg: Dict[str, Any]) -> str:
    return str((cfg.get("ollama") or {}).get("base_url") or "http://127.0.0.1:11434").rstrip("/")


def _ollama_models(base: str) -> Tuple[List[str], str]:
    try:
        response = requests.get(f"{base}/api/tags", timeout=3)
    except requests.ConnectionError:
        return [], (
            f"Không kết nối được Ollama tại {base}. "
            "Cài Ollama, chạy `ollama serve`, rồi `ollama pull` một model."
        )
    except requests.Timeout:
        return [], f"Ollama tại {base} không phản hồi."
    except requests.RequestException as exc:
        return [], f"Không kiểm tra được Ollama: {exc.__class__.__name__}."
    if response.status_code != 200:
        return [], f"Ollama trả về mã {response.status_code}."
    try:
        payload = response.json()
    except ValueError:
        return [], "Ollama không trả danh sách model."
    names = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names, ""


def ai_status(root: str) -> Dict[str, Any]:
    """Trạng thái từng nguồn để hiện trên ô chọn."""
    cfg = ai_config(root)
    base = _ollama_base(cfg)
    models, ollama_error = _ollama_models(base)
    chosen = str((cfg.get("ollama") or {}).get("model") or "").strip()
    if ollama_error:
        ollama_detail = ollama_error
        ollama_ready = False
    elif not models:
        ollama_detail = "Ollama đang chạy nhưng chưa có model. Chạy `ollama pull qwen2.5:7b`."
        ollama_ready = False
    elif chosen and chosen not in models:
        ollama_detail = f"Model «{chosen}» chưa có trên Ollama. Đang có: {', '.join(models[:6])}."
        ollama_ready = False
    else:
        using = chosen or models[0]
        ollama_detail = f"Model {using} tại {base}."
        ollama_ready = True
    options = [
        {
            "id": "local",
            "label": "Quy tắc nội bộ",
            "ready": True,
            "detail": "Đối chiếu dữ liệu tuyển sinh đã thu thập, không gọi AI.",
        },
        {
            "id": "ollama",
            "label": "Ollama",
            "ready": ollama_ready,
            "detail": ollama_detail,
            "models": models,
            "model": chosen or (models[0] if models else ""),
        },
        _open_option(
            "pollinations", "Pollinations",
            str((cfg.get("pollinations") or {}).get("model") or "openai"),
            "Không cần khóa. Dùng API miễn phí của Pollinations.",
        ),
    ]
    for name, label in _PROVIDERS:
        if name not in _KEY_ENV:
            continue
        _key_env, _model_env, signup = _KEY_ENV[name]
        block = cfg.get(name) or {}
        model = str(block.get("model") or "")
        if str(block.get("api_key") or "").strip():
            options.append(_open_option(name, label, model, f"Model {model}."))
        else:
            env_name = "XAI_API_KEY hoặc GROK_API_KEY" if name == "grok" else _key_env
            options.append({
                "id": name,
                "label": label,
                "ready": False,
                "model": model,
                "detail": (
                    f"Khóa miễn phí tại {signup}. "
                    f"Đặt {env_name} hoặc api_key trong config/ai.json."
                ),
            })
    provider = str(cfg.get("provider") or "local")
    if provider not in {item["id"] for item in options}:
        provider = "local"
    return {"ok": True, "provider": provider, "options": options}


def _open_option(provider_id: str, label: str, model: str, detail: str) -> Dict[str, Any]:
    return {
        "id": provider_id,
        "label": label,
        "ready": True,
        "model": model,
        "detail": detail,
    }


def _compact_context(question: str, payload: Dict[str, Any], local: Dict[str, Any]) -> str:
    rows = []
    for row in (local.get("rows") or [])[:40]:
        if not isinstance(row, dict):
            continue
        compact = {}
        for key in _ROW_KEYS:
            value = row.get(key)
            if value in (None, "", [], {}):
                continue
            compact[key] = value
        if compact:
            rows.append(compact)
    body = {
        "cau_hoi": question,
        "bo_loc": {
            "khu_vuc": payload.get("regions") or [],
            "loai_truong": payload.get("sectors") or [],
            "truong": payload.get("schools") or [],
            "to_hop": payload.get("combos") or [],
            "nguon_diem": payload.get("score_source") or "",
        },
        "ket_luan_noi_bo": local.get("summary") or "",
        "ghi_chu": local.get("notes") or [],
        "so_dong": len(local.get("rows") or []),
        "bang": rows,
        "diem_to_hop": local.get("cards") or [],
    }
    text = json.dumps(body, ensure_ascii=False)
    if len(text) > 24000:
        body["bang"] = rows[:15]
        body["cat_bot"] = True
        text = json.dumps(body, ensure_ascii=False)
    return text


def _system_prompt() -> str:
    return (
        "Bạn là cố vấn tuyển sinh đại học Việt Nam. Trả lời tiếng Việt, ngắn, rõ.\n"
        "Chỉ dùng số liệu trong JSON được cung cấp. Không bịa điểm chuẩn, chỉ tiêu, mã ngành hay tên trường.\n"
        "Nếu dữ liệu không đủ, nói thẳng phần nào chưa có.\n"
        "Không hứa chắc chắn trúng tuyển. Khi so điểm, nhắc đây là ước lượng theo điểm chuẩn đã thu thập."
    )


def _call_ollama(base: str, model: str, system: str, user: str) -> Tuple[Optional[str], str]:
    try:
        response = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {"temperature": 0.3},
            },
            timeout=120,
        )
    except requests.ConnectionError:
        return None, (
            f"Không kết nối được Ollama tại {base}. "
            "Chạy `ollama serve` rồi thử lại."
        )
    except requests.Timeout:
        return None, "Ollama phản hồi quá lâu."
    except requests.RequestException as exc:
        return None, f"Không gọi được Ollama: {exc.__class__.__name__}."
    if response.status_code != 200:
        detail = response.text.strip().replace("\n", " ")[:180]
        return None, f"Ollama trả về mã {response.status_code}. {detail}".strip()
    try:
        payload = response.json()
    except ValueError:
        return None, "Ollama không trả JSON."
    text = str(((payload.get("message") or {}).get("content")) or "").strip()
    if not text:
        return None, "Ollama trả về câu trả lời trống."
    return text, ""


def _call_gemini(key: str, model: str, system: str, user: str) -> Tuple[Optional[str], str]:
    if not key:
        return None, "Chưa có khóa Gemini. Đặt GEMINI_API_KEY hoặc api_key trong config/ai.json."
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    try:
        response = requests.post(
            url,
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200},
            },
            timeout=45,
        )
    except requests.Timeout:
        return None, "Gemini phản hồi quá lâu."
    except requests.RequestException as exc:
        return None, f"Không gọi được Gemini: {exc.__class__.__name__}."
    if response.status_code != 200:
        return None, f"Gemini trả về mã {response.status_code}."
    try:
        payload = response.json()
    except ValueError:
        return None, "Gemini không trả JSON."
    parts = ((((payload.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or [])
    text = str((parts[0].get("text") if parts else "") or "").strip()
    if not text:
        return None, "Gemini trả về câu trả lời trống."
    return text, ""


def _chat_text(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _call_chat(
    label: str,
    url: str,
    key: str,
    model: str,
    system: str,
    user: str,
    timeout: int = 60,
) -> Tuple[Optional[str], str]:
    """API dạng OpenAI. Khóa rỗng nghĩa là nguồn không cần khóa."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        response = requests.post(
            url,
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 1200,
            },
            timeout=timeout,
        )
    except requests.Timeout:
        return None, f"{label} phản hồi quá lâu."
    except requests.RequestException as exc:
        return None, f"Không gọi được {label}: {exc.__class__.__name__}."
    if response.status_code != 200:
        detail = response.text.strip().replace("\n", " ")[:160]
        return None, f"{label} trả về mã {response.status_code}. {detail}".strip()
    try:
        payload = response.json()
    except ValueError:
        plain = response.text.strip()
        return (plain, "") if plain else (None, f"{label} không trả JSON.")
    if isinstance(payload, str) and payload.strip():
        return payload.strip(), ""
    text = _chat_text(payload if isinstance(payload, dict) else {})
    if not text:
        return None, f"{label} trả về câu trả lời trống."
    return text, ""


def _clean_reply(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def attach_ai_answer(
    root: str,
    payload: Dict[str, Any],
    local: Dict[str, Any],
    provider: str,
    model_override: str = "",
) -> Dict[str, Any]:
    """Giữ bảng số liệu nội bộ và thêm lời diễn giải của AI đã chọn."""
    out = dict(local)
    chosen = str(provider or "").strip().lower()
    if chosen in {"", "local"}:
        out["ai_provider"] = "local"
        return out
    if chosen not in {item[0] for item in _PROVIDERS}:
        out["ai_provider"] = chosen
        out["ai_error"] = "Nguồn AI không được hỗ trợ."
        return out
    cfg = ai_config(root)
    question = str(payload.get("question") or "").strip()
    user = (
        "Hãy trả lời câu hỏi dựa trên JSON sau. "
        "Nếu có bảng, tóm tắt ý chính và nêu vài ngành hoặc trường tiêu biểu, không chép lại cả bảng.\n"
        + _compact_context(question, payload, local)
    )
    system = _system_prompt()
    model = str(model_override or "").strip()
    if chosen == "ollama":
        base = _ollama_base(cfg)
        if not model:
            model = str((cfg.get("ollama") or {}).get("model") or "").strip()
        if not model:
            names, error = _ollama_models(base)
            if error:
                out["ai_provider"] = "ollama"
                out["ai_error"] = error
                return out
            if not names:
                out["ai_provider"] = "ollama"
                out["ai_error"] = "Ollama chưa có model. Chạy `ollama pull qwen2.5:7b`."
                return out
            model = names[0]
        text, error = _call_ollama(base, model, system, user)
    elif chosen == "gemini":
        model = model or str((cfg.get("gemini") or {}).get("model") or "gemini-2.5-flash")
        text, error = _call_gemini(
            str((cfg.get("gemini") or {}).get("api_key") or "").strip(),
            model, system, user,
        )
    elif chosen == "pollinations":
        model = model or str((cfg.get("pollinations") or {}).get("model") or "openai")
        text, error = _call_chat(
            "Pollinations", "https://text.pollinations.ai/openai", "", model, system, user,
        )
    elif chosen == "groq":
        model = model or str((cfg.get("groq") or {}).get("model") or "llama-3.3-70b-versatile")
        key = str((cfg.get("groq") or {}).get("api_key") or "").strip()
        if not key:
            text, error = None, "Chưa có khóa Groq. Lấy khóa miễn phí tại console.groq.com/keys."
        else:
            text, error = _call_chat(
                "Groq", "https://api.groq.com/openai/v1/chat/completions", key, model, system, user,
            )
    elif chosen == "grok":
        model = model or str((cfg.get("grok") or {}).get("model") or "grok-3-mini")
        key = str((cfg.get("grok") or {}).get("api_key") or "").strip()
        if not key:
            text, error = None, "Chưa có khóa Grok. Lấy khóa tại console.x.ai rồi đặt XAI_API_KEY."
        else:
            text, error = _call_chat(
                "Grok", "https://api.x.ai/v1/chat/completions", key, model, system, user,
            )
    else:
        model = model or str((cfg.get("openrouter") or {}).get("model") or "openrouter/free")
        key = str((cfg.get("openrouter") or {}).get("api_key") or "").strip()
        if not key:
            text, error = None, "Chưa có khóa OpenRouter. Lấy khóa miễn phí tại openrouter.ai/keys."
        else:
            text, error = _call_chat(
                "OpenRouter", "https://openrouter.ai/api/v1/chat/completions", key, model, system, user,
            )
    out["ai_provider"] = chosen
    out["ai_model"] = model
    if text:
        out["ai_reply"] = _clean_reply(text)
    else:
        out["ai_error"] = error or "Không nhận được câu trả lời từ AI."
    return out
