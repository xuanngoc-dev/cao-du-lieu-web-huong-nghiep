# -*- coding: utf-8 -*-
"""Gọi Ollama, Gemini hoặc Groq để diễn giải câu trả lời tư vấn."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

_PROVIDERS = (
    ("local", "Quy tắc nội bộ"),
    ("ollama", "Ollama"),
    ("gemini", "Gemini"),
    ("groq", "Groq"),
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
    "gemini": {"model": "gemini-2.0-flash", "api_key": ""},
    "groq": {"model": "llama-3.3-70b-versatile", "api_key": ""},
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
    gemini = dict(cfg.get("gemini") or {})
    if os.environ.get("GEMINI_API_KEY", "").strip():
        gemini["api_key"] = os.environ["GEMINI_API_KEY"].strip()
    if os.environ.get("GEMINI_MODEL", "").strip():
        gemini["model"] = os.environ["GEMINI_MODEL"].strip()
    cfg["gemini"] = gemini
    groq = dict(cfg.get("groq") or {})
    if os.environ.get("GROQ_API_KEY", "").strip():
        groq["api_key"] = os.environ["GROQ_API_KEY"].strip()
    if os.environ.get("GROQ_MODEL", "").strip():
        groq["model"] = os.environ["GROQ_MODEL"].strip()
    cfg["groq"] = groq
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
    gemini_key = str((cfg.get("gemini") or {}).get("api_key") or "").strip()
    groq_key = str((cfg.get("groq") or {}).get("api_key") or "").strip()
    gemini_model = str((cfg.get("gemini") or {}).get("model") or "gemini-2.0-flash")
    groq_model = str((cfg.get("groq") or {}).get("model") or "llama-3.3-70b-versatile")
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
        {
            "id": "gemini",
            "label": "Gemini",
            "ready": bool(gemini_key),
            "detail": (
                f"Model {gemini_model}."
                if gemini_key else
                "Thiếu khóa. Đặt GEMINI_API_KEY hoặc api_key trong config/ai.json."
            ),
            "model": gemini_model,
        },
        {
            "id": "groq",
            "label": "Groq",
            "ready": bool(groq_key),
            "detail": (
                f"Model {groq_model}."
                if groq_key else
                "Thiếu khóa. Đặt GROQ_API_KEY hoặc api_key trong config/ai.json."
            ),
            "model": groq_model,
        },
    ]
    provider = str(cfg.get("provider") or "local")
    if provider not in {item["id"] for item in options}:
        provider = "local"
    return {"ok": True, "provider": provider, "options": options}


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


def _call_groq(key: str, model: str, system: str, user: str) -> Tuple[Optional[str], str]:
    if not key:
        return None, "Chưa có khóa Groq. Đặt GROQ_API_KEY hoặc api_key trong config/ai.json."
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 1200,
            },
            timeout=45,
        )
    except requests.Timeout:
        return None, "Groq phản hồi quá lâu."
    except requests.RequestException as exc:
        return None, f"Không gọi được Groq: {exc.__class__.__name__}."
    if response.status_code != 200:
        return None, f"Groq trả về mã {response.status_code}."
    try:
        payload = response.json()
    except ValueError:
        return None, "Groq không trả JSON."
    text = str(((((payload.get("choices") or [{}])[0]).get("message") or {}).get("content")) or "").strip()
    if not text:
        return None, "Groq trả về câu trả lời trống."
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
        model = model or str((cfg.get("gemini") or {}).get("model") or "gemini-2.0-flash")
        text, error = _call_gemini(
            str((cfg.get("gemini") or {}).get("api_key") or "").strip(),
            model, system, user,
        )
    else:
        model = model or str((cfg.get("groq") or {}).get("model") or "llama-3.3-70b-versatile")
        text, error = _call_groq(
            str((cfg.get("groq") or {}).get("api_key") or "").strip(),
            model, system, user,
        )
    out["ai_provider"] = chosen
    out["ai_model"] = model
    if text:
        out["ai_reply"] = _clean_reply(text)
    else:
        out["ai_error"] = error or "Không nhận được câu trả lời từ AI."
    return out
