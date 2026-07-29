"""Mock OpenAI + Anthropic server for keyless demos and tests.

  uvicorn scripts.mock_provider:app --port 9100

Behavior hooks (driven by the prompt text so the simulator can script failures):
  contains "FAIL"   -> 500 error (exercises retry detection)
  contains "FLAKY"  -> 500 on the first attempt for that prompt, 200 after
  contains "SLOW"   -> 1.5s delay
"""
import asyncio
import json
import time
from collections import Counter

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="mock-provider")
_flaky_attempts: Counter = Counter()


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


async def _maybe_misbehave(prompt: str):
    if "SLOW" in prompt:
        await asyncio.sleep(1.5)
    if "FAIL" in prompt:
        return JSONResponse({"error": {"message": "mock upstream error"}}, status_code=500)
    if "FLAKY" in prompt:
        _flaky_attempts[prompt] += 1
        if _flaky_attempts[prompt] == 1:
            return JSONResponse({"error": {"message": "transient mock error"}}, status_code=500)
    return None


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    prompt = " ".join(str(m.get("content", "")) for m in body.get("messages", []))
    bad = await _maybe_misbehave(prompt)
    if bad:
        return bad
    answer = f"[mock:{body.get('model')}] answered {len(prompt)} chars at {time.time():.0f}"
    usage = {"prompt_tokens": _tokens(prompt), "completion_tokens": _tokens(answer),
             "total_tokens": _tokens(prompt) + _tokens(answer)}
    if body.get("stream"):
        async def gen():
            words = answer.split(" ")
            for w in words:
                chunk = {"choices": [{"delta": {"content": w + " "}, "index": 0}]}
                yield f"data: {json.dumps(chunk)}\n\n"
            yield f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"id": "mock", "object": "chat.completion", "model": body.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                         "finish_reason": "stop"}],
            "usage": usage}


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    prompt = str(body.get("system", "")) + " ".join(
        str(m.get("content", "")) for m in body.get("messages", []))
    bad = await _maybe_misbehave(prompt)
    if bad:
        return bad
    answer = f"[mock:{body.get('model')}] answered {len(prompt)} chars"
    usage = {"input_tokens": _tokens(prompt), "output_tokens": _tokens(answer)}
    if body.get("stream"):
        async def gen():
            yield f"data: {json.dumps({'type': 'message_start', 'message': {'usage': {'input_tokens': usage['input_tokens']}}})}\n\n"
            for w in answer.split(" "):
                yield f"data: {json.dumps({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': w + ' '}})}\n\n"
            yield f"data: {json.dumps({'type': 'message_delta', 'usage': {'output_tokens': usage['output_tokens']}})}\n\n"
            yield f"data: {json.dumps({'type': 'message_stop'})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"id": "mock", "type": "message", "model": body.get("model"),
            "content": [{"type": "text", "text": answer}], "usage": usage}
