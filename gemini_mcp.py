#!/usr/bin/env python3
import sys
import json
import os
import re
import base64
import time
import logging
import urllib.request
import urllib.parse
import urllib.error

LOG_FILE = os.environ.get("GEMINI_LOG_FILE", "/root/scripts/deep_research.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("GeminiMCP")
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.handlers.clear()
logger.addHandler(file_handler)

API_BASE = os.environ.get("GEMINI_PROXY_URL", "http://127.0.0.1:8317/v1/chat/completions")
API_KEY = os.environ.get("GEMINI_PROXY_KEY", "sk-8fK9xL2mQ7vP4wN1jR6tY3uI5oA8sD0f")
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash-high")

def call_gemini(messages, tools=None, model=DEFAULT_MODEL, timeout=120):
    payload = {
        "model": model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        API_BASE,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["choices"][0]["message"]["content"]

def is_local_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in ["localhost", "127.0.0.1", "0.0.0.0", "::1"]:
            return True
        if host.startswith("192.168.") or host.startswith("10."):
            return True
        if re.match(r"^172\.(1[6-9]|2[0-9]|3[0-1])\.", host):
            return True
        return False
    except Exception:
        return False

def local_http_fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "OpenCode-Local-Fetch/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
        content_type = resp.headers.get_content_type()
        
        if "json" in content_type or "text/plain" in content_type:
            return data.decode("utf-8", errors="replace")
        
        text = data.decode("utf-8", errors="replace")
        text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        return f"[Local Fetch from {url}]:\n\n{text[:25000]}"

def handle_web_search(query: str) -> str:
    logger.info(f"[web_search] Запрос: {query}")
    prompt = (
        f"Найди актуальную, достоверную и подробную информацию по запросу: \"{query}\".\n"
        f"Предоставь исчерпывающий ответ со всеми фактами, деталями, цифрами и ссылками на первоисточники."
    )
    messages = [{"role": "user", "content": prompt}]
    tools = [{"google_search": {}}, {"url_context": {}}]
    res = call_gemini(messages, tools=tools)
    logger.info(f"[web_search] Получен ответ ({len(res)} симв.)")
    return res

def handle_web_fetch(url: str, prompt: str = "") -> str:
    logger.info(f"[web_fetch] URL: {url}, Prompt: {prompt}")
    if is_local_url(url):
        try:
            content = local_http_fetch(url)
            if prompt:
                return f"{content}\n\n(Запрос к содержимому: {prompt})"
            return content
        except Exception as e:
            return f"Ошибка при локальном обращении к {url}: {e}"

    user_prompt = prompt if prompt else "Изучи эту страницу и предоставь исчерпывающее изложение её содержимого, включая весь код, таблицы, факты и технические детали без сокращений."
    full_content = f"Прочитай страницу по адресу: {url}\n\nЗадача: {user_prompt}"
    messages = [{"role": "user", "content": full_content}]
    tools = [{"url_context": {}}]
    res = call_gemini(messages, tools=tools)
    logger.info(f"[web_fetch] Прочитана страница {url} ({len(res)} симв.)")
    return res

def handle_deep_research(topic: str) -> str:
    logger.info(f"=== [deep_research] СТАРТ РЕСЕРЧА (Минимум от 12 поисков, от 12 страниц, пакетами от 4 за кон): {topic} ===")
    
    system_prompt = (
        "Ты — ведущий автономный исследователь (Deep Research Specialist).\n"
        "СТРОГИЕ ПРАВИЛА И РЕГЛАМЕНТ ИССЛЕДОВАНИЯ:\n"
        "1. Одиночные поиски (по 1 запросу) ЗАПРЕЩЕНЫ. Работай ТОЛЬКО пакетами: исследуй и просматривай минимум от 4 поисковых направлений или от 4 веб-страниц за один вызов/кон.\n"
        "2. ОБЯЗАТЕЛЬНЫЙ МИНИМУМ ИССЛЕДОВАНИЯ: провести не менее чем от 12 поисковых запросов (от 3 раундов по от 4 запросов) и изучить не менее чем от 12 уникальных веб-страниц (от 3 раундов по от 4 страниц).\n"
        "3. На каждом поисковом шаге собирай технические детали, замеры, код и ОБЯЗАТЕЛЬНО выводи полные URL-адреса найденных страниц в формате 'URL: https://...'.\n"
        "4. На шагах чтения страниц анализируй за 1 вызов минимум от 4 страниц через url_context.\n"
        "5. Финальный отчет разрешено выдавать только на этапе итогового синтеза — отчет должен быть максимально полным, глубоким и исчерпывающим со всеми деталями без сокращений."
    )
    
    messages = [{"role": "system", "content": system_prompt}]
    tools = [{"google_search": {}}, {"url_context": {}}]

    # Шаг 1: Формирование от 12 разносторонних поисковых запросов (от 3 пакетов по от 4 запросов)
    plan_prompt = (
        f"Для глубокого исследования темы \"{topic}\" сформулируй от 12 разносторонних поисковых запросов, "
        f"разбитых минимум на 3 логических пакета по от 4 запросов в каждом (batch1, batch2, batch3).\n"
        f"Ответь строго валидным JSON-объектом формата:\n"
        f"{{\"batch1\": [\"q1\", \"q2\", \"q3\", \"q4\"], \"batch2\": [\"q5\", \"q6\", \"q7\", \"q8\"], \"batch3\": [\"q9\", \"q10\", \"q11\", \"q12\"]}}"
    )
    logger.info("[deep_research] Этап 0: Планирование от 12 поисковых запросов (от 3 пакетов по от 4 запросов)...")
    plan_res = call_gemini([{"role": "user", "content": plan_prompt}])
    
    batches = {}
    match = re.search(r"\{.*\}", plan_res, re.DOTALL)
    if match:
        try:
            batches = json.loads(match.group(0))
        except Exception:
            pass
            
    search_batches = [
        batches.get("batch1", [f"{topic} overview", f"{topic} architecture", f"{topic} core concepts", f"{topic} specifications"]),
        batches.get("batch2", [f"{topic} performance", f"{topic} benchmarks", f"{topic} real world tests", f"{topic} internals"]),
        batches.get("batch3", [f"{topic} edge cases", f"{topic} best practices", f"{topic} comparisons", f"{topic} changelog"])
    ]

    collected_urls = set()

    # Этап 1: Выполнение от 12 поисковых запросов (от 3 раундов по от 4 запросов за раз)
    for b_idx, batch in enumerate(search_batches, 1):
        logger.info(f"[deep_research] Поисковый пакет {b_idx}/{len(search_batches)} (от 4 запросов): {batch}")
        b_prompt = (
            f"[Этап 1: Поисковый раунд {b_idx}/{len(search_batches)} — минимум от 4 направлений за 1 кон]\n"
            f"Одновременно выполни нативный поиск Google по следующим направлениям (минимум от 4 запросов):\n" +
            "\n".join([f"{idx+1}. {q}" for idx, q in enumerate(batch)]) +
            f"\n\nСобери по каждому направлению факты, цифры, код и ОБЯЗАТЕЛЬНО перечисли полные URL-адреса найденных страниц в формате 'URL: https://...'."
        )
        messages.append({"role": "user", "content": b_prompt})
        t0 = time.time()
        res_text = call_gemini(messages, tools=tools)
        dt = time.time() - t0
        messages.append({"role": "assistant", "content": res_text})
        
        for u in re.findall(r"https?://[^\s\)\]\>\"\'\,]+", res_text):
            clean_u = u.rstrip(".,;`")
            if "google.com" not in clean_u and not clean_u.endswith((".png", ".jpg", ".svg", ".gif")):
                collected_urls.add(clean_u)
                
        logger.info(f"[deep_research] Поисковый раунд {b_idx}/{len(search_batches)} завершен за {dt:.2f}s. Всего ссылок в пуле: {len(collected_urls)}")

    # Этап 2: Чтение от 12 уникальных страниц (пакетами минимум от 4 страниц за раз)
    all_urls = list(collected_urls)
    logger.info(f"[deep_research] Найдено уникальных страниц для чтения: {len(all_urls)}")
    
    target_urls = all_urls[:12] if len(all_urls) >= 12 else all_urls
    page_batches = [target_urls[i:i+4] for i in range(0, len(target_urls), 4)]
    
    if not page_batches:
        page_batches = [[f"https://en.wikipedia.org/wiki/{urllib.parse.quote(topic)}"]]

    for p_idx, p_batch in enumerate(page_batches, 1):
        logger.info(f"[deep_research] Этап 2: Чтение страниц — Раунд {p_idx}/{len(page_batches)} (от {len(p_batch)} страниц за кон): {p_batch}")
        page_prompt = (
            f"[Этап 2: Чтение веб-страниц — Раунд {p_idx}/{len(page_batches)} (минимум от {len(p_batch)} страниц за 1 вызов)]\n"
            f"Используя нативный url_context, подробно изучи следующие страницы (минимум от 4 страниц за раз):\n" +
            "\n".join([f"{i+1}. {u}" for i, u in enumerate(p_batch)]) +
            f"\n\nИзвлеки подробные выдержки, фрагменты кода, таблицы замеров, параметры и выводы."
        )
        messages.append({"role": "user", "content": page_prompt})
        t0 = time.time()
        page_res = call_gemini(messages, tools=tools)
        dt = time.time() - t0
        messages.append({"role": "assistant", "content": page_res})
        logger.info(f"[deep_research] Чтение страниц раунд {p_idx}/{len(page_batches)} завершено за {dt:.2f}s ({len(page_res)} симв.)")

    # Этап 3: Финальный исчерпывающий синтез
    final_prompt = (
        "[Этап 3: Финальный исчерпывающий технический отчет]\n"
        "Все этапы исследования (от 12 поисковых запросов и чтение от 12 веб-страниц) завершены. "
        "На основе всего огромного накопленного контекста диалога составь монументальный, глубокий технический отчет со всеми цифрами, кодом, таблицами сравнения и списком первоисточников. Не урезай деталей!"
    )
    messages.append({"role": "user", "content": final_prompt})
    logger.info("[deep_research] Этап 3: Запуск итогового синтеза...")
    
    t0 = time.time()
    final_report = call_gemini(messages, tools=tools, timeout=180)
    dt = time.time() - t0
    logger.info(f"[deep_research] Финальный отчет сгенерирован за {dt:.2f}s ({len(final_report)} симв.)")
    
    return final_report

def handle_analyze_image(path_or_url: str, prompt: str = "") -> str:
    user_prompt = prompt if prompt else "Опиши и проанализируй всё, что изображено на картинке в мельчайших деталях."
    logger.info(f"[analyze_image] Источник: {path_or_url}")
    
    if path_or_url.startswith("data:image/"):
        data_uri = path_or_url
    elif path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        data_uri = path_or_url
    else:
        if not os.path.exists(path_or_url):
            return f"Ошибка: Файл не найден: {path_or_url}"
        
        with open(path_or_url, "rb") as f:
            raw_bytes = f.read()
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        ext = os.path.splitext(path_or_url)[1].lower().replace(".", "")
        mime = "image/png" if ext == "png" else "image/jpeg" if ext in ["jpg", "jpeg"] else "image/webp" if ext == "webp" else "image/png"
        data_uri = f"data:{mime};base64,{b64}"

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": data_uri}}
            ]
        }
    ]
    res = call_gemini(messages)
    logger.info(f"[analyze_image] Анализ завершен ({len(res)} симв.)")
    return res

TOOLS_DEFINITIONS = [
    {
        "name": "web_search",
        "description": "Поиск актуальной информации в интернете через Google Search с получением ссылок и цитат из источников.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_fetch",
        "description": "Посетить и прочитать веб-страницу по URL. Для внешних сайтов использует нативный Gemini url_context, для локальных адресов (localhost/127.0.0.1) читает напрямую с сервера.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Полный URL веб-страницы (http:// или https://)"
                },
                "prompt": {
                    "type": "string",
                    "description": "Необязательный конкретный вопрос или задача по содержимому страницы"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "deep_research",
        "description": "Автономное глубокое исследование темы (Deep Research). Выполняет от 12 поисковых запросов и изучает от 12 страниц (пакетами минимум от 4 за один вызов), накапливая контекст диалога, и формирует исчерпывающий аналитический отчет со всеми деталями, цифрами, кодом и источниками.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Тема или вопрос для глубокого исследования"
                }
            },
            "required": ["topic"]
        }
    },
    {
        "name": "analyze_image",
        "description": "Анализ и распознавание изображения (с локального пути на диске или URL) через Gemini Vision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_or_url": {
                    "type": "string",
                    "description": "Путь к файлу картинки на диске (например /root/screenshot.png) или URL"
                },
                "prompt": {
                    "type": "string",
                    "description": "Вопрос или инструкция по анализу изображения"
                }
            },
            "required": ["path_or_url"]
        }
    }
]

def process_message(msg):
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "gemini-research-tools",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "notifications/initialized":
        return None

    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {}
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": TOOLS_DEFINITIONS
            }
        }

    elif method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})

        try:
            if tool_name == "web_search":
                res_text = handle_web_search(args.get("query", ""))
            elif tool_name == "web_fetch":
                res_text = handle_web_fetch(args.get("url", ""), args.get("prompt", ""))
            elif tool_name == "deep_research":
                res_text = handle_deep_research(args.get("topic", ""))
            elif tool_name == "analyze_image":
                res_text = handle_analyze_image(args.get("path_or_url", ""), args.get("prompt", ""))
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool not found: {tool_name}"
                    }
                }

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": res_text
                        }
                    ]
                }
            }
        except Exception as e:
            logger.exception(f"Ошибка при вызове {tool_name}: {e}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error executing tool {tool_name}: {e}"
                        }
                    ]
                }
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": f"Method not supported: {method}"
        }
    }

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            resp = process_message(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as e:
            logger.exception(f"Критическая ошибка JSON-RPC: {e}")
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Parse error: {e}"
                }
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
