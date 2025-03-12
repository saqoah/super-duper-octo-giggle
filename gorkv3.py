import nest_asyncio
import json
import random
import asyncio
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union, Any, TypedDict
from playwright.async_api import async_playwright, Page, BrowserContext
from urllib.parse import urljoin

SCRIPT_VERSION = "2.4.0"
CURRENT_USER = "saqoah"
LAST_UPDATED = "2025-03-12 12:30:00"

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

class PropertyConfig(TypedDict, total=False):
    type: str
    selector_type: str
    selector: str
    attribute: Optional[str]
    items: Optional[Dict[str, Any]]
    pattern: Optional[str]
    filter: Optional[Dict[str, str]]

class PostActionConfig(TypedDict, total=False):
    type: Optional[str]
    selector_type: Optional[str]
    selector: Optional[str]
    attribute: Optional[str]
    methods: Optional[List[str]]
    pattern: Optional[str]

class ScrapingSchema(TypedDict):
    url: str
    properties: Dict[str, PropertyConfig]
    actions: Optional[List[ActionConfig]]
    post_actions: Optional[Dict[str, PostActionConfig]]

# Expanded modern user agent list
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.88",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.88"
]

# Enhanced browser arguments to avoid detection
BROWSER_ARGS = [
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-software-rasterizer',
    '--disable-extensions',
    '--disable-blink-features=AutomationControlled',
    '--disable-web-security',
    '--disable-features=IsolateOrigins,site-per-process',
    '--disable-setuid-sandbox',
    '--ignore-certificate-errors',
    '--ignore-certificate-errors-spki-list',
    '--disable-infobars',
    '--disable-notifications',
    '--disable-popup-blocking',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-breakpad',
    '--disable-component-extensions-with-background-pages',
    '--disable-hang-monitor',
    '--disable-ipc-flooding-protection',
    '--disable-renderer-backgrounding',
    '--metrics-recording-only',
    '--mute-audio',
    '--no-first-run'
]

async def create_browser_context(playwright):
    browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
    
    # Create context with extended options
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={'width': random.randint(1300, 1920), 'height': random.randint(800, 1080)},
        ignore_https_errors=True,
        locale='en-US,en;q=0.9',
        timezone_id='America/New_York',
        geolocation={'longitude': -73.58, 'latitude': 40.65, 'accuracy': 100},
        permissions=['geolocation', 'notifications'],
        screen={'width': 1920, 'height': 1080},
        device_scale_factor=1.0
    )
    
    # Add JavaScript to mask automated browser indicators
    await context.add_init_script("""
    () => {
        // Overwrite the 'webdriver' property
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false
        });
        
        // Overwrite chrome properties
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        
        // Overwrite permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' || 
            parameters.name === 'geolocation' ||
            parameters.name === 'midi' || 
            parameters.name === 'microphone' || 
            parameters.name === 'camera' || 
            parameters.name === 'clipboard-read' ||
            parameters.name === 'clipboard-write'
        ) ?
            Promise.resolve({state: 'granted', onchange: null}) :
            originalQuery(parameters);
            
        // Add language plugins to mimic regular browser
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en', 'es'],
        });
        
        // Spoof plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                return [
                    {
                        0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Portable Document Format",
                        filename: "internal-pdf-viewer",
                        length: 1,
                        name: "Chrome PDF Plugin"
                    },
                    {
                        0: {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Portable Document Format",
                        filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                        length: 1,
                        name: "Chrome PDF Viewer"
                    },
                    {
                        0: {type: "application/x-nacl", suffixes: "", description: "Native Client Executable"},
                        1: {type: "application/x-pnacl", suffixes: "", description: "Portable Native Client Executable"},
                        description: "Native Client",
                        filename: "internal-nacl-plugin",
                        length: 2,
                        name: "Native Client"
                    }
                ];
            }
        });
    }
    """)
    
    return context

async def scrape_website(schema: ScrapingSchema):
    async with async_playwright() as p:
        context = await create_browser_context(p)
        page = await context.new_page()
        network_requests = []
        def handle_request(request):
            network_requests.append({"method": request.method, "url": request.url})
        page.on("request", handle_request)
        
        try:
            # Add random timing between actions to appear more human-like
            page.set_default_timeout(90000)  # More forgiving timeout
            url = schema.get("url", "https://www.google.com/")
            
            # Add randomization to page loading
            for attempt in range(3):
                try:
                    # Simulate human typing the URL
                    await page.goto('about:blank')
                    
                    # Random pre-navigation delay
                    await asyncio.sleep(random.uniform(1, 3))
                    
                    response = await page.goto(url, wait_until='domcontentloaded')
                    if not response:
                        continue
                    
                    # Simulate human-like behavior (random scrolling)
                    await simulate_human_behavior(page)
                    
                    # Additional wait for dynamic content
                    await asyncio.sleep(random.uniform(2, 4))
                    break
                except Exception:
                    if attempt == 2:
                        return {key: None for key in schema["properties"]}
                    await asyncio.sleep(2 ** attempt + random.uniform(0.5, 1.5))
            
            data = await extract_data(page, schema)
            
            if "actions" in schema:
                for action in schema["actions"]:
                    await perform_action(page, action)
                    # Add random delay between actions
                    await asyncio.sleep(random.uniform(1, 3))
            
            if "post_actions" in schema:
                for key, config in schema["post_actions"].items():
                    if config.get("type") == "network":
                        data[key] = extract_network_requests(network_requests, config)
                    else:
                        data[key] = await extract_post_action(page, config)
            
            return data
        except Exception:
            return {key: None for key in schema["properties"]} if "properties" in schema else {}
        finally:
            await context.close()

async def simulate_human_behavior(page: Page):
    # Simulate random mouse movements
    for _ in range(random.randint(3, 8)):
        await page.mouse.move(
            random.randint(100, 800),
            random.randint(100, 600)
        )
        await asyncio.sleep(random.uniform(0.1, 0.5))
    
    # Simulate random scrolling
    for _ in range(random.randint(2, 5)):
        await page.evaluate(f"window.scrollBy(0, {random.randint(100, 500)})")
        await asyncio.sleep(random.uniform(0.5, 1.5))
    
    # Sometimes scroll back up
    if random.random() > 0.7:
        await page.evaluate(f"window.scrollBy(0, {random.randint(-400, -100)})")
        await asyncio.sleep(random.uniform(0.5, 1))

def extract_network_requests(requests: List[Dict[str, str]], config: PostActionConfig):
    pattern = re.compile(config["pattern"], re.IGNORECASE)
    methods = config.get("methods", ["GET", "POST"])
    results = []
    for req in requests:
        if req["method"] in methods and pattern.search(req["url"]):
            results.append(req["url"])
    return list(set(results)) if results else None

async def extract_data(page: Page, schema: ScrapingSchema):
    data = {key: None for key in schema["properties"]}
    for key, value in schema["properties"].items():
        try:
            data[key] = await extract_property(page, key, value)
            # Add small random delays between property extractions
            await asyncio.sleep(random.uniform(0.2, 0.7))
        except Exception:
            data[key] = None
    return data

async def extract_property(page: Page, key: str, value: PropertyConfig):
    try:
        if value["type"] == "regex":
            pattern = value.get("pattern")
            if not pattern:
                return None
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
        
        # More fault-tolerant selector handling
        if selector_type == "css":
            try:
                await page.wait_for_selector(selector, state="attached", timeout=10000)
            except Exception:
                pass  # Continue anyway, might be available without waiting
            elements = await page.query_selector_all(selector)
        elif selector_type == "xpath":
            elements = await page.locator(selector).all_inner_texts()
        else:
            return None
            
        if value["type"] == "string":
            element = await page.query_selector(selector)
            if not element:
                return None
                
            if "attribute" in value and element:
                if value["attribute"] == "innerHTML":
                    return await element.inner_html()
                return await element.get_attribute(value["attribute"])
            return await element.inner_text()
            
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
                
                if filter_pattern:
                    filter_attr = item.get(value["filter"]["attribute"], "")
                    if not filter_pattern.search(filter_attr):
                        continue
                
                if key == "links" and (not item.get("text") or item["text"].strip() == ""):
                    continue
                    
                result.append(item)
            return result
    except Exception:
        return None

async def perform_action(page: Page, action: ActionConfig):
    action_type = action.get("type")
    selector_type = action.get("selector_type", "css")
    selector = action.get("selector")
    match_text = action.get("match_text")
    duration = action.get("duration", 0)
    retries = action.get("retries", 3)
    
    for attempt in range(retries):
        try:
            if selector:
                if selector_type == "css":
                    # Add a small random delay before performing action
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    
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
                        raise ValueError(f"No matching element")
                        
                    if action_type == "click":
                        # Move mouse to element before clicking (more human-like)
                        await target_element.scroll_into_view_if_needed()
                        
                        # Get bounding box and click at random position within element
                        box = await target_element.bounding_box()
                        if box:
                            x = box['x'] + random.uniform(5, box['width'] - 5)
                            y = box['y'] + random.uniform(5, box['height'] - 5)
                            
                            # Move slowly to the element
                            await page.mouse.move(x, y, steps=random.randint(5, 10))
                            await asyncio.sleep(random.uniform(0.1, 0.3))
                            
                            # Click with random delay between mousedown and mouseup (human-like)
                            await page.mouse.down()
                            await asyncio.sleep(random.uniform(0.05, 0.15))
                            await page.mouse.up()
                        else:
                            await target_element.click()
            
            if action_type == "wait":
                # Add randomness to wait duration
                await asyncio.sleep(duration + random.uniform(-0.5, 0.5))
                
            break
        except Exception:
            if attempt == retries - 1:
                return
            await asyncio.sleep(2 ** attempt + random.uniform(0.5, 1.0))

async def extract_post_action(page: Page, config: PostActionConfig):
    selector_type = config.get("selector_type", "css")
    selector = config.get("selector")
    attribute = config.get("attribute")
    
    if not selector:
        return None
        
    try:
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
    except Exception:
        return None
        
    return None

async def main():
    try:
        with open("schema.json", "r", encoding="utf-8") as f:
            schema = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return
        
    try:
        data = await scrape_website(schema)
        
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
    except Exception:
        pass

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())