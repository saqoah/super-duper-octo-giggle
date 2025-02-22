import nest_asyncio
import json
import random
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union, Any, TypedDict
from playwright.async_api import async_playwright, Page, BrowserContext, BrowserType
from urllib.parse import urljoin
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s UTC - %(levelname)s - %(message)s - [Thread: %(threadName)s] - [File: %(pathname)s:%(lineno)d]',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SCRIPT_VERSION = "3.0.1"  # Bumped for syntax fix
CURRENT_USER = "saqoah"
LAST_UPDATED = "2025-02-22 20:00:00"

class ScrapingError(Exception):
    pass

class ActionConfig(TypedDict, total=False):
    type: str
    selector_type: str
    selector: str
    match_text: Optional[str]
    value: Optional[str]
    duration: Optional[float]
    retries: Optional[int]
    x: Optional[int]
    y: Optional[int]

class PropertyConfig(TypedDict, total=False):
    type: str
    selector_type: str
    selector: str
    attribute: Optional[str]
    items: Optional[Dict[str, Any]]
    pattern: Optional[str]
    filter: Optional[Dict[str, str]]
    max_depth: Optional[int]
    extract_children: Optional[bool]

class PostActionConfig(TypedDict, total=False):
    type: Optional[str]
    selector_type: Optional[str]
    selector: Optional[str]
    attribute: Optional[str]
    methods: Optional[List[str]]
    pattern: Optional[str]
    capture_response: Optional[bool]

class ScrapingSchema(TypedDict):
    url: str
    properties: Dict[str, PropertyConfig]
    actions: Optional[List[ActionConfig]]
    post_actions: Optional[Dict[str, PostActionConfig]]
    headers: Optional[Dict[str, str]]
    proxy: Optional[str]
    max_retries: Optional[int]
    timeout: Optional[int]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
]

BROWSER_ARGS = [
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-software-rasterizer',
    '--disable-extensions',
    '--disable-blink-features=AutomationControlled'
]

async def create_browser_context(playwright: BrowserType, schema: ScrapingSchema) -> BrowserContext:
    proxy = schema.get("proxy")
    browser = await playwright.chromium.launch(
        headless=True,
        args=BROWSER_ARGS,
        proxy={"server": proxy} if proxy else None
    )
    headers = schema.get("headers", {})
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={'width': 1920, 'height': 1080},
        ignore_https_errors=True,
        extra_http_headers=headers
    )
    return context

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def scrape_website(schema: ScrapingSchema):
    start_time = datetime.now(timezone.utc)
    url = schema.get("url", "https://www.google.com/")
    logger.info(f"Starting scraping job for {url}")
    async with async_playwright() as p:
        context = await create_browser_context(p, schema)
        page = await context.new_page()
        network_data = {"requests": [], "responses": []}

        async def handle_request(request):
            network_data["requests"].append({"method": request.method, "url": request.url, "headers": dict(request.headers)})

        async def handle_response(response):
            try:
                body = await response.text()
            except Exception:
                body = None
            network_data["responses"].append({"url": response.url, "status": response.status, "body": body})

        page.on("request", handle_request)
        page.on("response", handle_response)

        try:
            timeout = schema.get("timeout", 60) * 1000
            page.set_default_timeout(timeout)
            for attempt in range(schema.get("max_retries", 3)):
                try:
                    response = await page.goto(url, wait_until='domcontentloaded')
                    if not response or not response.ok:
                        raise ScrapingError(f"Failed to load page: {response.status}")
                    break
                except Exception as e:
                    if attempt == schema.get("max_retries", 3) - 1:
                        logger.error(f"Failed after {attempt + 1} attempts: {str(e)}")
                        raise
                    logger.warning(f"Attempt {attempt + 1} failed, retrying...")
                    await asyncio.sleep(2 ** attempt)

            data = await extract_data(page, schema)
            if "actions" in schema:
                for action in schema["actions"]:
                    await perform_action(page, action)
            if "post_actions" in schema:
                for key, config in schema["post_actions"].items():
                    if config.get("type") == "network":
                        data[key] = extract_network_data(network_data, config)
                    else:
                        data[key] = await extract_post_action(page, config)
            data["network"] = network_data
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            logger.info(f"Scraping completed in {duration:.2f} seconds")
            return {
                "metadata": {
                    "start_time": start_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "duration": duration,
                    "url": url
                },
                "data": data
            }
        except Exception as e:
            logger.error(f"Error during scraping: {str(e)}", exc_info=True)
            return None
        finally:
            await context.close()

def extract_network_data(network_data: Dict[str, List], config: PostActionConfig):
    pattern = re.compile(config["pattern"], re.IGNORECASE)
    methods = config.get("methods", ["GET", "POST"])
    results = []
    for req in network_data["requests"]:
        if req["method"] in methods and pattern.search(req["url"]):
            resp = next((r for r in network_data["responses"] if r["url"] == req["url"]), None)
            entry = {"url": req["url"], "method": req["method"]}
            if resp and config.get("capture_response", False):
                entry["response"] = {"status": resp["status"], "body": resp["body"]}
            results.append(entry)
    return list({entry["url"]: entry for entry in results}.values()) if results else None

async def extract_data(page: Page, schema: ScrapingSchema):
    data = {key: None for key in schema["properties"]}
    data["metadata"] = {"url": page.url, "title": await page.title()}
    for key, value in schema["properties"].items():
        try:
            data[key] = await extract_property(page, key, value)
        except Exception as e:
            logger.error(f"Error extracting {key}: {str(e)}")
            data[key] = None
    return data

async def extract_property(page: Page, key: str, value: PropertyConfig, depth: int = 0):
    try:
        max_depth = value.get("max_depth", 1)
        if depth > max_depth:
            return None

        if value["type"] == "regex":
            pattern = value.get("pattern")
            if not pattern:
                raise ValueError(f"Regex pattern required for {key}")
            elements = await page.query_selector_all(value["selector"])
            results = []
            regex = re.compile(pattern, re.IGNORECASE)
            for element in elements:
                text = await element.inner_html() if "script" in value["selector"] or "iframe" in value["selector"] else await element.inner_text()
                matches = regex.findall(text)
                if matches:
                    results.extend(matches)
            return list(set(results))

        selector_type = value.get("selector_type", "css")
        selector = value["selector"]
        if selector_type == "css":
            await page.wait_for_selector(selector, state="attached", timeout=20000)
            elements = await page.query_selector_all(selector)
        elif selector_type == "xpath":
            elements = await page.locator(selector).all()
        else:
            raise ValueError(f"Invalid selector type: {selector_type}")

        if value["type"] == "string":
            element = await page.query_selector(selector)
            if element:
                if "attribute" in value:
                    if value["attribute"] == "innerHTML":
                        return await element.inner_html()
                    return await element.get_attribute(value["attribute"])
                return await element.inner_text()
            return None

        elif value["type"] == "array":
            result = []
            filter_pattern = re.compile(value["filter"]["pattern"], re.IGNORECASE) if "filter" in value else None
            for element in elements:
                item = {}
                for sub_key, sub_value in value["items"]["properties"].items():
                    sub_selector = sub_value["selector"]
                    if sub_selector == "self":
                        if "attribute" in sub_value:
                            if sub_value["attribute"] == "innerHTML":
                                attr_value = await element.inner_html()
                            else:
                                attr_value = await element.get_attribute(sub_value["attribute"])
                            if sub_value["attribute"] in ["href", "src"]:
                                attr_value = urljoin(page.url, attr_value)
                            item[sub_key] = attr_value
                        else:
                            item[sub_key] = (await element.inner_text()).strip()
                    else:
                        sub_element = await element.query_selector(sub_selector)
                        if sub_element:
                            if "attribute" in sub_value:
                                if sub_value["attribute"] == "innerHTML":
                                    attr_value = await sub_element.inner_html()
                                else:
                                    attr_value = await sub_element.get_attribute(sub_value["attribute"])
                                if sub_value["attribute"] in ["href", "src"]:
                                    attr_value = urljoin(page.url, attr_value)
                                item[sub_key] = attr_value
                            else:
                                item[sub_key] = (await sub_element.inner_text()).strip()
                        elif sub_value.get("extract_children", False):
                            item[sub_key] = await extract_property(page, sub_key, {**sub_value, "selector": sub_selector}, depth + 1)
                if filter_pattern:
                    filter_attr = item.get(value["filter"]["attribute"], "")
                    if not filter_pattern.search(filter_attr):
                        continue
                if value.get("extract_children", False):
                    children = await element.query_selector_all("*")
                    item["children"] = [await extract_element_content(child) for child in children]
                result.append(item)
            return result if result else None
    except Exception as e:
        logger.error(f"Error extracting {key}: {str(e)}")
        return None

async def extract_element_content(element):
    return {
        "tag": await element.evaluate("el => el.tagName.toLowerCase()"),  # Fixed: Added closing quote and parenthesis
        "text": await element.inner_text(),
        "html": await element.inner_html(),
        "attributes": await element.evaluate("el => Object.fromEntries([...el.attributes].map(attr => [attr.name, attr.value]))")  # Removed extra parenthesis
    }

async def perform_action(page: Page, action: ActionConfig):
    action_type = action.get("type")
    selector_type = action.get("selector_type", "css")
    selector = action.get("selector")
    match_text = action.get("match_text")
    duration = action.get("duration", 0)
    retries = action.get("retries", 3)
    x = action.get("x")
    y = action.get("y")
    for attempt in range(retries):
        try:
            if selector:
                if selector_type == "css":
                    elements = await page.query_selector_all(selector)
                    target_element = None
                    if match_text:
                        regex = re.compile(match_text, re.IGNORECASE)
                        for element in elements:
                            text = await element.inner_text()
                            if text and regex.search(text):
                                target_element = element
                                break
                    else:
                        target_element = elements[0] if elements else None
                    if not target_element:
                        raise ValueError(f"No matching element found for {selector}")
                    if action_type == "click":
                        if x is not None and y is not None:
                            await page.mouse.click(x, y)
                        else:
                            await target_element.click()
            if action_type == "wait":
                await asyncio.sleep(duration)
            break
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Action failed after {retries} attempts: {str(e)}")
                raise
            logger.warning(f"Attempt {attempt + 1} failed, retrying...")
            await asyncio.sleep(2 ** attempt)

async def extract_post_action(page: Page, config: PostActionConfig):
    selector_type = config.get("selector_type", "css")
    selector = config.get("selector")
    attribute = config.get("attribute")
    if selector_type == "css":
        elements = await page.query_selector_all(selector)
        results = []
        for element in elements:
            if attribute:
                if attribute == "innerHTML":
                    value = await element.inner_html()
                else:
                    value = await element.get_attribute(attribute)
                if value:
                    results.append(urljoin(page.url, value))
        return list(set(results)) if results else None
    return None

async def main():
    start_time = datetime.now(timezone.utc)
    logger.info(f"Script started at {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    try:
        with open("schema.json", "r", encoding="utf-8") as f:
            schema = json.load(f)
    except FileNotFoundError:
        logger.error("Error: schema.json not found.")
        return
    except json.JSONDecodeError:
        logger.error("Error: Invalid JSON in schema.json.")
        return
    try:
        data = await scrape_website(schema)
        if data is None:
            data = {key: None for key in schema["properties"]}
        output_file = "output.json"
        with open(output_file, "w", encoding="utf-8") as outfile:
            json.dump({
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                    "version": SCRIPT_VERSION,
                    "url": schema.get("url"),
                    "user": CURRENT_USER,
                    "duration": (datetime.now(timezone.utc) - start_time).total_seconds()
                },
                "data": data
            }, outfile, indent=2, ensure_ascii=False)
        logger.info(f"Data successfully saved to {output_file}")
    except Exception as e:
        logger.error(f"An error occurred during execution: {str(e)}", exc_info=True)

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())