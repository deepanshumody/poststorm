"""Hour-0 de-risk: confirm key, model, strict structured output, image path, timing.

Run from this directory:  python smoke_test.py
"""

import httpx
from config import get_settings

# 1x1 transparent PNG — just to confirm the multimodal request shape is accepted.
ONE_PX_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1"
    "HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"ok": {"type": "boolean"}, "note": {"type": "string"}},
    "required": ["ok", "note"],
}


def call(messages):
    s = get_settings()
    r = httpx.post(
        f"{s.cerebras_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {s.cerebras_api_key}"},
        json={
            "model": s.cerebras_model,
            "messages": messages,
            "reasoning_effort": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "ok", "strict": True, "schema": SCHEMA},
            },
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    s = get_settings()
    print(f"key set: {bool(s.cerebras_api_key)} | model: {s.cerebras_model} | base: {s.cerebras_base_url}")

    txt = call([{"role": "user", "content": "Reply with ok=true and note='text works'."}])
    print("TEXT :", txt["choices"][0]["message"]["content"], "| time_info:", txt.get("time_info"))

    img = call(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Reply ok=true and note='image accepted'."},
                    {"type": "image_url", "image_url": {"url": ONE_PX_PNG}},
                ],
            }
        ]
    )
    print("IMAGE:", img["choices"][0]["message"]["content"], "| time_info:", img.get("time_info"))
