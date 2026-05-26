import os
import httpx
import asyncio
import logging

logger = logging.getLogger(__name__)

def get_openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()

def get_openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

async def generate_openai_content(system_instruction: str, user_prompt: str, custom_key: str = None, max_attempts: int = 3) -> str:
    """
    Sends a chat completion request to OpenAI's ChatGPT completions API endpoint
    with robust retries and exponential backoff on rate limits.
    """
    api_key = custom_key.strip() if (custom_key and custom_key.strip()) else get_openai_api_key()
    model = get_openai_model()
    
    if not api_key:
        raise ValueError("OpenAI ChatGPT API key is not configured.")

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
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers=headers
                )
                
                # Check for rate-limiting (429)
                if response.status_code == 429:
                    try:
                        err_data = response.json()
                        err_msg = err_data.get("error", {}).get("message", "").lower()
                    except Exception:
                        err_msg = response.text.lower()
                    
                    is_insufficient = any(x in err_msg for x in ["insufficient balance", "balance", "quota", "billing", "credit"])
                    if is_insufficient:
                        raise ValueError(
                            "Your OpenAI ChatGPT account is suspended due to insufficient balance or expired trial credits. Please recharge your OpenAI account at https://platform.openai.com/ or configure a working Gemini key."
                        )
                        
                    if attempt < max_attempts:
                        sleep_time = 3.0 * (2 ** (attempt - 1))
                        logger.warning(f"OpenAI API rate limited (attempt {attempt}/{max_attempts}). Retrying in {sleep_time}s...")
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
                
        except ValueError as exc:
            raise exc
        except Exception as exc:
            if attempt >= max_attempts:
                logger.error(f"OpenAI API query failed after {max_attempts} attempts: {exc}")
                raise exc
            sleep_time = 3.0 * (2 ** (attempt - 1))
            logger.warning(f"OpenAI API request error (attempt {attempt}/{max_attempts}). Retrying in {sleep_time}s: {exc}")
            await asyncio.sleep(sleep_time)
