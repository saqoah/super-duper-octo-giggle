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

SCRIPT_VERSION = "2.2.0"
CURRENT_USER = "user"
LAST_UPDATED = "2023-10-01 14:20:00"

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
    post_actions: Optional[Dict[str, Any]]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

async def scrape_website(schema: ScrapingSchema):
    start_time = datetime.now(timezone.utc)
    url = schema.get("url", "https://www.google.com/")
    logger.info(f"Starting scraping job for {url} at {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    async with async_playwright() as p:
        context = await create_browser_context(p)
        page = await context.new_page()
        try:
            page.set_default_timeout(60000)
            for attempt in range(3):
                try:
                    response = await page.goto(url, wait_until='domcontentloaded')
                    if not response or not response.ok:
                        raise ScrapingError(f"Failed to load page: {response.status if response else 'No response'}")
                    break
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"Failed after 3 attempts: {str(e)}")
                        raise
                    logger.warning(f"Attempt {attempt + 1} failed, retrying...")
                    await asyncio.sleep(2 ** attempt)
            data = await extract_data(page, schema)
            if "actions" in schema:
                for action in schema["actions"]:
                    await perform_action(page, action)
            if "post_actions" in schema:
                for key, config in schema["post_actions"].items():
                    data[key] = await extract_post_action(page, config)
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
    return data

async def extract_property(page: Page, key: str, value: PropertyConfig):
    try:
        if value["type"] == "regex":
            pattern = value.get("pattern")
            if not pattern:
                raise ValueError(f"Regex pattern is required for key '{key}'")
            elements = await page.query_selector_all(value["selector"])
            results = []
            regex = re.compile(pattern, re.IGNORECASE)
            for element in elements:
                text = await element.inner_text()
                matches = regex.findall(text)
                if matches:
                    results.extend(matches)
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
    except Exception as e:
        logger.error(f"Error extracting {key}: {str(e)}")
        return None

async def perform_action(page: Page, action: ActionConfig):
    action_type = action.get("type")
    selector_type = action.get("selector_type", "css")
    selector = action.get("selector")
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
            elif action_type == "wait":
                await asyncio.sleep(duration)
            break
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"Action failed after {retries} attempts: {str(e)}")
                raise
            logger.warning(f"Attempt {attempt + 1} failed, retrying...")
            await asyncio.sleep(2 ** attempt)

async def extract_post_action(page: Page, config: Dict[str, Any]):
    selector_type = config.get("selector_type", "css")
    selector = config.get("selector")
    attribute = config.get("attribute")
    if selector_type == "css":
        element = await page.query_selector(selector)
        if element and attribute:
            return await element.get_attribute(attribute)
        return await element.inner_text() if element else None
    elif selector_type == "xpath":
        elements = await page.locator(selector).all_inner_texts()
        return elements[0] if elements else None
    return None

async def main():
    logger.info(f"Script started at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    try:
        with open("schema.json", "r", encoding="utf-8") as f:
            schema = json.load(f)
    except FileNotFoundError:
        logger.error("Error: schema.json not found. Create this file with the correct JSON schema.")
        return
    except json.JSONDecodeError:
        logger.error("Error: Invalid JSON in schema.json. Check for syntax errors.")
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
                    "url": schema.get("url")
                },
                "data": data
            }, outfile, indent=2, ensure_ascii=False)
        logger.info(f"Data successfully saved to {output_file}")
    except Exception as e:
        logger.error(f"An error occurred during execution: {str(e)}", exc_info=True)

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())