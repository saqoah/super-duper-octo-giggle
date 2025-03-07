import nest_asyncio
import json
import random
import asyncio
import logging
import re
import base64
import binascii
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union, Any, TypedDict, Set
from playwright.async_api import async_playwright, Page, BrowserContext, Request, Response
from urllib.parse import urljoin, urlparse, unquote
import concurrent.futures

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s UTC - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SCRIPT_VERSION = "3.0.0"  # Major version bump for enhanced features
CURRENT_USER = "saqoah"
LAST_UPDATED = "2025-03-07 12:00:00"

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
    process_base64: Optional[bool]

class PostActionConfig(TypedDict, total=False):
    type: Optional[str]
    selector_type: Optional[str]
    selector: Optional[str]
    attribute: Optional[str]
    methods: Optional[List[str]]
    pattern: Optional[str]
    media_only: Optional[bool]

class ScrapingSchema(TypedDict):
    url: Optional[str]
    url_template: Optional[str]
    url_range: Optional[Dict[str, int]]
    properties: Dict[str, PropertyConfig]
    actions: Optional[List[ActionConfig]]
    post_actions: Optional[Dict[str, PostActionConfig]]
    enable_media_capture: Optional[bool]
    enable_hidden_links: Optional[bool]
    enable_base64_decode: Optional[bool]
    scan_javascript: Optional[bool]
    max_page_scroll: Optional[int]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
]

BROWSER_ARGS = [
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-software-rasterizer',
    '--disable-extensions',
    '--disable-web-security',  # Added to capture more cross-origin requests
    '--autoplay-policy=no-user-gesture-required'  # Help with media detection
]

# Media patterns for detecting relevant URLs
MEDIA_PATTERNS = {
    'm3u8': re.compile(r'\.m3u8(\?.*)?$', re.IGNORECASE),
    'mp4': re.compile(r'\.mp4(\?.*)?$', re.IGNORECASE),
    'mpd': re.compile(r'\.mpd(\?.*)?$', re.IGNORECASE),  # DASH manifests
    'ts': re.compile(r'\.ts(\?.*)?$', re.IGNORECASE),    # TS segments
    'key': re.compile(r'key\.php|\.key(\?.*)?$', re.IGNORECASE),  # Encryption keys
}

# JavaScript URL patterns (commonly used for hidden URLs)
JS_URL_PATTERNS = [
    # Standard URLs in single or double quotes
    r'(?:"|\'|\()(?:https?:)?\/\/[a-zA-Z0-9_\-\.\/\?\=\&\%\+\~\#\;\:\@\[\]\(\)]+(?:"|\'|\))',
    # m3u8 URLs in various formats
    r'["\'`](?:https?:)?\/\/[^"\'`\s]+\.m3u8(?:\?[^"\'`\s]*)?["\'`]',
    # mp4 URLs in various formats
    r'["\'`](?:https?:)?\/\/[^"\'`\s]+\.mp4(?:\?[^"\'`\s]*)?["\'`]',
    # Base path + variable construction
    r'const\s+(?:url|src|path|baseUrl)\s*=\s*["\'](?:https?:)?\/\/[^"\']+["\']',
    # Common video player configs
    r'(?:file|source|src|url):\s*["\'`](?:https?:)?\/\/[^"\'`\s]+\.(?:m3u8|mp4)(?:\?[^"\'`\s]*)?["\'`]'
]

# Combine patterns into one regex
JS_URL_REGEX = re.compile('|'.join(JS_URL_PATTERNS), re.IGNORECASE)

async def create_browser_context(playwright):
    browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={'width': 1920, 'height': 1080},
        ignore_https_errors=True
    )
    return context, browser

def generate_urls_from_template(template: str, start: int, end: int) -> List[str]:
    """Generate a list of URLs from a template and a range of IDs."""
    return [template.format(id=id) for id in range(start, end + 1)]

def is_valid_media_url(url: str) -> bool:
    """Check if URL matches known media patterns."""
    for pattern_name, pattern in MEDIA_PATTERNS.items():
        if pattern.search(url):
            return True
    return False

def extract_urls_from_text(text: str, base_url: str = None) -> List[str]:
    """Extract URLs from text including JS strings."""
    # Generic URL pattern
    url_pattern = re.compile(r'https?://[^\s\'",<>()]+', re.IGNORECASE)
    urls = url_pattern.findall(text)
    
    # Extract JS URLs
    js_urls = JS_URL_REGEX.findall(text)
    for match in js_urls:
        # Clean up the extracted URLs (remove quotes and parentheses)
        cleaned = re.sub(r'^[\'"\(]+|[\'"\)]+$', '', match)
        if cleaned.startswith('//'):
            cleaned = 'https:' + cleaned
        if cleaned and cleaned.startswith(('http://', 'https://')):
            urls.append(cleaned)
    
    # Process relative URLs if base_url is provided
    if base_url:
        relative_url_pattern = re.compile(r'[\'"](/[^\s\'",<>()]+\.(?:m3u8|mp4|ts|mpd)(?:\?[^\s\'",<>()]*)?)[\'"]')
        relative_matches = relative_url_pattern.findall(text)
        for rel_url in relative_matches:
            urls.append(urljoin(base_url, rel_url))
    
    # Remove duplicates and clean
    unique_urls = []
    for url in urls:
        cleaned_url = url.strip('\'"()[]{}')
        if cleaned_url and cleaned_url.startswith(('http://', 'https://')):
            unique_urls.append(cleaned_url)
    
    return list(set(unique_urls))

def try_decode_base64(text: str) -> Optional[str]:
    """Attempt to decode a potential base64 string and check if result is a URL."""
    # Remove potential base64 prefixes
    if "base64," in text:
        text = text.split("base64,")[1]
    
    # Remove whitespace that might be in the string
    text = re.sub(r'\s+', '', text)
    
    # Check if it's a valid base64 string (correct length and characters)
    if not re.match(r'^[A-Za-z0-9+/=]+$', text):
        return None
    
    # Try decoding
    try:
        decoded = base64.b64decode(text).decode('utf-8', errors='ignore')
        # Check if the decoded content is a URL
        if re.match(r'https?://', decoded):
            return decoded
        # Check if it might be JSON containing URLs
        if '{' in decoded and '}' in decoded:
            # Try to extract URLs from the JSON-like string
            urls = extract_urls_from_text(decoded)
            if urls:
                return '\n'.join(urls)
        return None
    except (binascii.Error, UnicodeDecodeError):
        return None

async def scrape_website(schema: ScrapingSchema):
    start_time = datetime.now(timezone.utc)
    urls = []
    if "url_template" in schema and "url_range" in schema:
        # Generate URLs from template
        urls = generate_urls_from_template(
            schema["url_template"],
            schema["url_range"]["start"],
            schema["url_range"]["end"]
        )
    elif "url" in schema:
        # Use single URL
        urls = [schema["url"]]
    else:
        raise ScrapingError("No URL or URL template provided in schema.")

    all_data = []
    async with async_playwright() as p:
        context, browser = await create_browser_context(p)
        
        # Enable media capture if specified
        media_urls: Set[str] = set()
        hidden_links: Set[str] = set()
        decoded_urls: Set[str] = set()
        
        for url in urls:
            logger.info(f"Starting scraping job for {url} at {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            page = await context.new_page()
            
            # Add request/response handlers for media detection
            if schema.get("enable_media_capture", False):
                async def handle_request(request: Request):
                    if is_valid_media_url(request.url):
                        media_urls.add(request.url)
                
                async def handle_response(response: Response):
                    if is_valid_media_url(response.url):
                        media_urls.add(response.url)
                    # Check content-type for media types
                    content_type = response.headers.get("content-type", "")
                    if any(media_type in content_type for media_type in ["video/", "application/x-mpegURL", "application/dash+xml"]):
                        media_urls.add(response.url)
                
                page.on("request", handle_request)
                page.on("response", handle_response)
            
            try:
                page.set_default_timeout(60000)
                for attempt in range(3):
                    try:
                        response = await page.goto(url, wait_until='networkidle')
                        if not response or not response.ok:
                            logger.warning(f"Page loaded with status: {response.status}")
                            if attempt == 2:  # Still try to extract what we can
                                break
                        else:
                            break
                    except Exception as e:
                        if attempt == 2:
                            logger.error(f"Failed after 3 attempts: {str(e)}")
                            break  # Continue with extraction despite failure
                        logger.warning(f"Attempt {attempt + 1} failed, retrying...")
                        await asyncio.sleep(2 ** attempt)
                
                # Perform auto-scrolling to trigger lazy-loaded content
                max_scroll = schema.get("max_page_scroll", 3)
                for scroll in range(max_scroll):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(1)  # Wait for content to load
                
                # Extract data based on the schema
                data = await extract_data(page, schema)
                
                # Handle actions (clicks, waits, etc.)
                if "actions" in schema:
                    for action in schema["actions"]:
                        await perform_action(page, action)
                        # Wait for any new media to load after action
                        await asyncio.sleep(2)
                
                # Extract hidden links from JavaScript if enabled
                if schema.get("enable_hidden_links", False):
                    js_content = await page.evaluate("""
                        () => {
                            const scripts = Array.from(document.querySelectorAll('script:not([src])'));
                            return scripts.map(script => script.innerText).join('\\n');
                        }
                    """)
                    extracted_urls = extract_urls_from_text(js_content, url)
                    for extracted_url in extracted_urls:
                        hidden_links.add(extracted_url)
                
                # Scan all JavaScript sources for media URLs
                if schema.get("scan_javascript", False):
                    script_srcs = await page.evaluate("""
                        () => {
                            const scriptTags = Array.from(document.querySelectorAll('script[src]'));
                            return scriptTags.map(script => script.src);
                        }
                    """)
                    for script_src in script_srcs:
                        try:
                            script_content_response = await page.request.get(script_src)
                            if script_content_response.ok:
                                script_content = await script_content_response.text()
                                extracted_urls = extract_urls_from_text(script_content, url)
                                for extracted_url in extracted_urls:
                                    if is_valid_media_url(extracted_url):
                                        media_urls.add(extracted_url)
                                    hidden_links.add(extracted_url)
                        except Exception as script_err:
                            logger.warning(f"Error fetching script {script_src}: {script_err}")
                
                # Process base64 encoded content if enabled
                if schema.get("enable_base64_decode", False):
                    # Look for base64 encoded strings in HTML and JS
                    base64_pattern = re.compile(r'(?:base64,)([A-Za-z0-9+/=]{30,})')
                    page_content = await page.content()
                    
                    # Find potential base64 strings
                    base64_matches = base64_pattern.findall(page_content)
                    for b64_str in base64_matches:
                        decoded = try_decode_base64(b64_str)
                        if decoded:
                            decoded_urls.add(decoded)
                            # Check if decoded content contains media URLs
                            media_in_decoded = [u for u in extract_urls_from_text(decoded) if is_valid_media_url(u)]
                            for media_url in media_in_decoded:
                                media_urls.add(media_url)
                    
                    # Also check script tags for potential encoded URLs
                    script_contents = await page.evaluate("""
                        () => {
                            const scripts = Array.from(document.querySelectorAll('script:not([src])'));
                            return scripts.map(script => script.innerText).join('\\n');
                        }
                    """)
                    
                    # Look for patterns that might indicate base64 encoded URLs
                    potential_b64_vars = re.finditer(r'(?:var|let|const)\s+(\w+)\s*=\s*[\'"]([A-Za-z0-9+/=]{30,})[\'"]', script_contents)
                    for match in potential_b64_vars:
                        var_name, b64_str = match.groups()
                        decoded = try_decode_base64(b64_str)
                        if decoded:
                            decoded_urls.add(decoded)
                            # Check if decoded content contains media URLs
                            media_in_decoded = [u for u in extract_urls_from_text(decoded) if is_valid_media_url(u)]
                            for media_url in media_in_decoded:
                                media_urls.add(media_url)
                
                # Process post-actions
                if "post_actions" in schema:
                    for key, config in schema["post_actions"].items():
                        if config.get("type") == "network":
                            # Only include media URLs if media_only is True
                            if config.get("media_only", False):
                                data[key] = [url for url in media_urls if re.search(config.get("pattern", ""), url)]
                            else:
                                # Use original network request extraction
                                pass  # This would use the original network request extraction
                        else:
                            data[key] = await extract_post_action(page, config)
                
                # Add collected media URLs to data
                if schema.get("enable_media_capture", False):
                    data["media_urls"] = list(media_urls)
                
                # Add collected hidden links to data
                if schema.get("enable_hidden_links", False):
                    data["hidden_links"] = list(hidden_links)
                
                # Add decoded base64 URLs to data
                if schema.get("enable_base64_decode", False):
                    data["decoded_urls"] = list(decoded_urls)
                
                all_data.append(data)
                
            except Exception as e:
                logger.error(f"Error during scraping for {url}: {str(e)}", exc_info=True)
                # Add empty data structure with error information
                error_data = {key: None for key in schema["properties"]}
                error_data["error"] = str(e)
                all_data.append(error_data)
            finally:
                await page.close()
        
        await context.close()
        await browser.close()
    
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    logger.info(f"Scraping completed in {duration:.2f} seconds")
    return all_data

async def extract_data(page: Page, schema: ScrapingSchema):
    data = {key: None for key in schema["properties"]}
    for key, value in schema["properties"].items():
        try:
            data[key] = await extract_property(page, key, value, schema)
        except Exception as e:
            logger.error(f"Error extracting {key}: {str(e)}")
            data[key] = None
    return data

async def extract_property(page: Page, key: str, value: PropertyConfig, schema: ScrapingSchema):
    try:
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
            
            # Process base64 if enabled
            if value.get("process_base64", False) and schema.get("enable_base64_decode", False):
                decoded_results = []
                for result in results:
                    decoded = try_decode_base64(result)
                    if decoded:
                        decoded_results.append(decoded)
                if decoded_results:
                    results.extend(decoded_results)
            
            return list(set(results))
        
        selector_type = value.get("selector_type", "css")
        selector = value["selector"]
        
        if selector_type == "css":
            await page.wait_for_selector(selector, state="attached", timeout=20000).catch(lambda _: None)
            elements = await page.query_selector_all(selector)
        elif selector_type == "xpath":
            elements = await page.locator(selector).all_inner_texts()
        else:
            raise ValueError(f"Invalid selector type: {selector_type}")
        
        if value["type"] == "string":
            element = await page.query_selector(selector)
            if not element:
                return None
                
            if "attribute" in value and element:
                if value["attribute"] == "innerHTML":
                    text = await element.inner_html()
                elif value["attribute"] == "outerHTML":
                    text = await page.evaluate("(element) => element.outerHTML", element)
                else:
                    text = await element.get_attribute(value["attribute"])
                
                # For href or src attributes, convert to absolute URL
                if value["attribute"] in ["href", "src", "data-src", "data-url"]:
                    if text:
                        text = urljoin(page.url, text)
                
                # Process base64 content if enabled and requested
                if value.get("process_base64", False) and schema.get("enable_base64_decode", False) and text:
                    decoded = try_decode_base64(text)
                    if decoded:
                        return decoded
                
                return text
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
                            elif sub_value["attribute"] == "outerHTML":
                                attr_value = await page.evaluate("(element) => element.outerHTML", element)
                            else:
                                attr_value = await element.get_attribute(sub_value["attribute"])
                            
                            # Convert relative URLs to absolute
                            if sub_value["attribute"] in ["href", "src", "data-src", "data-url"] and attr_value:
                                attr_value = urljoin(page.url, attr_value)
                            
                            # Process base64 content if enabled and requested
                            if sub_value.get("process_base64", False) and schema.get("enable_base64_decode", False) and attr_value:
                                decoded = try_decode_base64(attr_value)
                                if decoded:
                                    attr_value = decoded
                            
                            item[sub_key] = attr_value
                        else:
                            item[sub_key] = (await element.inner_text()).strip()
                    else:
                        sub_element = await element.query_selector(sub_selector)
                        if sub_element:
                            if "attribute" in sub_value:
                                if sub_value["attribute"] == "innerHTML":
                                    attr_value = await sub_element.inner_html()
                                elif sub_value["attribute"] == "outerHTML":
                                    attr_value = await page.evaluate("(element) => element.outerHTML", sub_element)
                                else:
                                    attr_value = await sub_element.get_attribute(sub_value["attribute"])
                                
                                # Convert relative URLs to absolute
                                if sub_value["attribute"] in ["href", "src", "data-src", "data-url"] and attr_value:
                                    attr_value = urljoin(page.url, attr_value)
                                
                                # Process base64 content if enabled and requested
                                if sub_value.get("process_base64", False) and schema.get("enable_base64_decode", False) and attr_value:
                                    decoded = try_decode_base64(attr_value)
                                    if decoded:
                                        attr_value = decoded
                                
                                item[sub_key] = attr_value
                            else:
                                item[sub_key] = (await sub_element.inner_text()).strip()
                
                # Apply filter if specified
                if filter_pattern:
                    filter_attr = item.get(value["filter"]["attribute"], "")
                    if not filter_pattern.search(filter_attr):
                        continue
                
                # Skip empty links
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
    match_text = action.get("match_text")
    duration = action.get("duration", 0)
    retries = action.get("retries", 3)
    
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
                        # Scroll element into view before clicking
                        await page.evaluate("(element) => element.scrollIntoView({behavior: 'smooth', block: 'center'})", target_element)
                        await asyncio.sleep(0.5)  # Small delay for scroll to complete
                        await target_element.click()
                    elif action_type == "hover":
                        await target_element.hover()
            
            if action_type == "wait":
                await asyncio.sleep(duration)
            elif action_type == "scroll":
                # Scroll to bottom of page to trigger lazy loading
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            
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
                elif attribute == "outerHTML":
                    value = await page.evaluate("(element) => element.outerHTML", element)
                else:
                    value = await element.get_attribute(attribute)
                
                if value and attribute in ["href", "src", "data-src", "data-url"]:
                    value = urljoin(page.url, value)
                    results.append(value)
            else:
                # Extract inner text if no attribute specified
                text = await element.inner_text()
                if text and text.strip():
                    results.append(text.strip())
        
        return list(set(results)) if results else None
    
    return None

async def main():
    logger.info(f"Enhanced scraper v{SCRIPT_VERSION} started at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    
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
            data = [{key: None for key in schema["properties"]}]
        
        output_file = "output.json"
        with open(output_file, "w", encoding="utf-8") as outfile:
            json.dump({
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                    "version": SCRIPT_VERSION,
                    "url": schema.get("url") or schema.get("url_template")
                },
                "data": data
            }, outfile, indent=2, ensure_ascii=False)
        
        # Also create a media-specific output file if media capture is enabled
        if schema.get("enable_media_capture", False):
            media_urls = set()
            for item in data:
                if "media_urls" in item and item["media_urls"]:
                    media_urls.update(item["media_urls"])
            
            if media_urls:
                media_output = "media_urls.json"
                with open(media_output, "w", encoding="utf-8") as media_outfile:
                    json.dump({
                        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                        "media_count": len(media_urls),
                        "media_urls": list(media_urls)
                    }, media_outfile, indent=2, ensure_ascii=False)
                logger.info(f"Media URLs saved to {media_output}")
        
        logger.info(f"Data successfully saved to {output_file}")
    
    except Exception as e:
        logger.error(f"An error occurred during execution: {str(e)}", exc_info=True)

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())