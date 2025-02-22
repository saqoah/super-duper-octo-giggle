import nest_asyncio
import json
import random
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union, Any, TypedDict
from playwright.async_api import async_playwright, Page, BrowserContext
from urllib.parse import urljoin

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s UTC - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SCRIPT_VERSION = "2.1.0"
CURRENT_USER = "saqoah"
LAST_UPDATED = "2025-02-15 20:36:04"

class ScrapingError(Exception):
    pass

class ActionConfig(TypedDict, total=False):
    type: str
    selector_type: str
    selector: str
    value: Optional[str]
    duration: Optional[float]
    retries: Optional[int]

class PropertyConfig(TypedDict, total=False):
    type: str
    selector_type: str
    selector: str
    attribute: Optional[str]
    items: Optional[Dict[str, Any]]
    pattern: Optional[str]

class ScrapingSchema(TypedDict):
    url: str
    properties: Dict[str, PropertyConfig]
    actions: Optional[List[ActionConfig]]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
]

BROWSER_ARGS = [
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-software-rasterizer',
    '--disable-extensions'
]

async def create_browser_context(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=BROWSER_ARGS
    )
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={'width': 1920, 'height': 1080},
        ignore_https_errors=True
    )
    return context

def get_universal_schema(url):
    return {
        "url": url,
        "properties": {
            "inner_text": {
                "type": "string",
                "selector_type": "css",
                "selector": "body"
            },
            "images": {
                "type": "array",
                "selector_type": "css",
                "selector": "img",
                "items": {
                    "properties": {
                        "src": {
                            "type": "string",
                            "selector": "self",
                            "attribute": "src"
                        },
                        "alt": {
                            "type": "string",
                            "selector": "self",
                            "attribute": "alt"
                        }
                    }
                }
            },
            "links": {
                "type": "array",
                "selector_type": "css",
                "selector": "a[href]",
                "items": {
                    "properties": {
                        "href": {
                            "type": "string",
                            "selector": "self",
                            "attribute": "href"
                        },
                        "text": {
                            "type": "string",
                            "selector": "self"
                        }
                    }
                }
            },
            "scripts": {
                "type": "array",
                "selector_type": "css",
                "selector": "script",
                "items": {
                    "properties": {
                        "content": {
                            "type": "string",
                            "selector": "self"
                        }
                    }
                }
            }
        }
    }

def extract_playback_urls(scripts):
    playback_urls = []
    url_pattern = r'"(https?://[^"]+)"'
    playback_keywords = ["play", "stream", "video", "media", "embed", "iframe"]
    for script in scripts:
        urls = re.findall(url_pattern, script["content"])
        for url in urls:
            if any(keyword in url.lower() for keyword in playback_keywords):
                playback_urls.append(url)
    return list(set(playback_urls))

async def scrape_website(schema: ScrapingSchema):
    start_time = datetime.now(timezone.utc)
    url = schema.get("url", "https://www.google.com/")
    logger.info(f"Starting scraping job for {url} at {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    logger.info(f"Script version: {SCRIPT_VERSION}, User: {CURRENT_USER}")
    async with async_playwright() as p:
        context = await create_browser_context(p)
        page = await context.new_page()
        try:
            page.on("request", lambda request: logger.debug(f">> {request.method} {request.url}"))
            page.on("response", lambda response: logger.debug(f"<< {response.status} {response.url}"))
            page.set_default_timeout(30000)
            response = await page.goto(url, wait_until='networkidle')
            if not response or not response.ok:
                raise ScrapingError(f"Failed to load page: {response.status if response else 'No response'}")
            data = await extract_data(page, schema)
            data["playback_urls"] = extract_playback_urls(data["scripts"])
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            logger.info(f"Scraping completed in {duration:.2f} seconds")
            return data
        except Exception as e:
            logger.error(f"Error during scraping: {str(e)}", exc_info=True)
            return None
        finally:
            await context.close()

async def extract_data(page: Page, schema: ScrapingSchema):
    data = {key: None for key in schema["properties"]}
    if "properties" in schema:
        for key, value in schema["properties"].items():
            try:
                data[key] = await extract_property(page, key, value)
            except Exception as e:
                logger.error(f"Error extracting {key}: {str(e)}")
                data[key] = None
    if "actions" in schema:
        for action in schema["actions"]:
            await perform_action(page, action)
    return data

async def extract_property(page: Page, key: str, value: PropertyConfig):
    try:
        if value["type"] == "regex":
            pattern = value.get("pattern")
            if not pattern:
                raise ValueError(f"Regex pattern is required for key '{key}'")
            elements = await page.query_selector_all("a")
            results = []
            regex = re.compile(pattern, re.IGNORECASE)
            for element in elements:
                try:
                    href = await element.get_attribute("href")
                    text = await element.inner_text()
                    if href and regex.search(href):
                        results.append({
                            "url": href,
                            "text": text.strip(),
                            "matches": regex.findall(href)
                        })
                except Exception as e:
                    logger.debug(f"Skipping element due to: {str(e)}")
                    continue
            return results
        selector_type = value.get("selector_type", "css")
        selector = value["selector"]
        if selector_type == "css":
            await page.wait_for_selector(selector, state="attached", timeout=20000)
            elements = await page.query_selector_all(selector)
        elif selector_type == "xpath":
            elements = await page.locator(selector).all_inner_texts()
        else:
            raise ValueError(f"Invalid selector type: {selector_type}")
        if value["type"] == "string":
            if selector_type == "css":
                element = await page.query_selector(selector)
                if "attribute" in value and element:
                    return await element.get_attribute(value["attribute"])
                return await element.inner_text() if element else None
            return elements[0] if elements else None
        elif value["type"] == "array":
            result = []
            for element in elements:
                item = {}
                for sub_key, sub_value in value["items"]["properties"].items():
                    sub_selector_type = sub_value.get("selector_type", "css")
                    sub_selector = sub_value["selector"]
                    if sub_selector == "self":
                        if "attribute" in sub_value:
                            attr_value = await element.get_attribute(sub_value["attribute"])
                            if sub_value["attribute"] in ["href", "src"]:
                                attr_value = urljoin(page.url, attr_value)
                            item[sub_key] = attr_value
                        else:
                            item[sub_key] = (await element.inner_text()).strip()
                    else:
                        if sub_selector_type == "css":
                            sub_element = await element.query_selector(sub_selector)
                            item[sub_key] = (
                                await sub_element.get_attribute(sub_value["attribute"])
                                if sub_element and "attribute" in sub_value
                                else await sub_element.inner_text()
                                if sub_element
                                else None
                            )
                        elif sub_selector_type == "xpath":
                            sub_elements = await page.locator(sub_selector).all_inner_texts()
                            item[sub_key] = sub_elements[0] if sub_elements else None
                if key == "links" and (not item.get("text") or item["text"].strip() == ""):
                    continue
                result.append(item)
            return result
        elif value["type"] == "html":
            element = await page.query_selector(selector)
            return await element.evaluate('(element) => element.outerHTML') if element else None
    except Exception as e:
        logger.error(f"Error extracting {key}: {str(e)}")
        return None

async def perform_action(page: Page, action: ActionConfig):
    action_type = action.get("type")
    selector_type = action.get("selector_type", "css")
    selector = action.get("selector")
    value = action.get("value")
    duration = action.get("duration", 0)
    retries = action.get("retries", 3)
    for attempt in range(retries):
        try:
            if selector:
                if selector_type == "css":
                    await page.wait_for_selector(selector, state="attached", timeout=20000)
                    element = await page.query_selector(selector)
                elif selector_type == "xpath":
                    element = await page.locator(selector).first
                else:
                    raise ValueError(f"Invalid selector type: {selector_type}")
            if action_type == "click":
                if element:
                    await element.click()
            elif action_type == "write":
                if element and value:
                    await element.fill(value)
            elif action_type == "scroll":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            elif action_type == "wait":
                await asyncio.sleep(duration)
            elif action_type == "keyboard":
                if value:
                    await page.keyboard.press(value)
            elif action_type == "goto":
                if value:
                    await page.goto(value)
            break
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Action failed after {retries} attempts: {str(e)}")
                raise
            logger.warning(f"Attempt {attempt + 1} failed, retrying...")
            await asyncio.sleep(2 ** attempt)

async def main():
    logger.info(f"Script started at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    logger.info(f"User: {CURRENT_USER}")
    url = "https://french-stream.my/15119718-henry-danger-the-movie.html"
    schema = get_universal_schema(url)
    try:
        data = await scrape_website(schema)
        if data is None:
            data = {key: None for key in schema["properties"]}
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        output_file = f"output.json"
        with open(output_file, "w", encoding="utf-8") as outfile:
            json.dump({
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                    "version": SCRIPT_VERSION,
                    "user": CURRENT_USER,
                    "url": schema.get("url")
                },
                "data": data
            }, outfile, indent=2, ensure_ascii=False)
        logger.info(f"Data successfully saved to {output.json}")
    except Exception as e:
        logger.error(f"An error occurred during execution: {str(e)}", exc_info=True)

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())