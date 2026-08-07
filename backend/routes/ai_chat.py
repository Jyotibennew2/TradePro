"""AI Assistant proxy route (Claude → OpenAI → Gemini free fallback)."""

from flask import Blueprint, jsonify, request

from backend.ai_service import chat as ai_chat_fn

ai_chat_bp = Blueprint("ai_chat", __name__)


@ai_chat_bp.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    payload       = request.json or {}
    messages      = payload.get("messages", [])
    system_prompt = payload.get("system", "")
    provider      = payload.get("provider")
    result = ai_chat_fn(messages, system_prompt, provider=provider)
    if result.get("success"):
        return jsonify({"success": True, "text": result["text"], "provider": result.get("provider")})
    return jsonify({"success": False, "error": result.get("error", "AI request failed")}), 502
