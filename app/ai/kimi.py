import os
import httpx
import asyncio
import logging

logger = logging.getLogger(__name__)

def get_kimi_api_key() -> str:
    return os.getenv("KIMI_API_KEY", "").strip()

def get_kimi_model() -> str:
    return os.getenv("KIMI_MODEL", "moonshot-v1-8k").strip()

async def generate_kimi_content(system_instruction: str, user_prompt: str, custom_key: str = None, max_attempts: int = 3) -> str:
    """
    Sends a chat completion request to Kimi's OpenAI-compatible API endpoint
    with robust retries and exponential backoff on rate limits.
    """
    api_key = custom_key.strip() if (custom_key and custom_key.strip()) else get_kimi_api_key()
    model = get_kimi_model()
    
    if not api_key:
        raise ValueError("Kimi API key is not configured.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1
    }

    attempt = 0
    while True:
        attempt += 1
        try:
            # dev-only workaround: verify=False handles SSL issues with proxy/AV
            async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
                response = await client.post(
                    "https://api.moonshot.cn/v1/chat/completions",
                    json=payload,
                    headers=headers
                )
                
                # Check for rate-limiting (429)
                if response.status_code == 429 and attempt < max_attempts:
                    sleep_time = 3.0 * (2 ** (attempt - 1))
                    logger.warning(f"Kimi API rate limited (attempt {attempt}/{max_attempts}). Retrying in {sleep_time}s...")
                    await asyncio.sleep(sleep_time)
                    continue
                    
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                
                # Strip markdown code blocks if the model wrapped it (e.g. ```json ... ```)
                content_str = content.strip()
                if content_str.startswith("```"):
                    lines = content_str.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines[-1].startswith("```"):
                        lines = lines[:-1]
                    content_str = "\n".join(lines).strip()
                    
                return content_str
                
        except Exception as exc:
            if attempt >= max_attempts:
                logger.error(f"Kimi API query failed after {max_attempts} attempts: {exc}")
                raise exc
            sleep_time = 3.0 * (2 ** (attempt - 1))
            logger.warning(f"Kimi API request error (attempt {attempt}/{max_attempts}). Retrying in {sleep_time}s: {exc}")
            await asyncio.sleep(sleep_time)
