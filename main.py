import nest_asyncio
import json
import random
import asyncio
import logging
from playwright.async_api import async_playwright
from urllib.parse import urljoin
from IPython import get_ipython

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    # Add more user agents as needed
]

async def extract_image_data(element):
    src = await element.get_attribute('src')
    if not src or 'placeholder' in src:
        src = await element.get_attribute('data-src')
    
    srcset = await element.get_attribute('srcset')
    if srcset:
        srcset_urls = [url.split(' ')[0] for urlset in srcset.split(',')]
        src = srcset_urls[-1]  # Use the largest image by default
    
    alt = await element.get_attribute('alt')
    
    return {
        "src": src,
        "alt": alt
    }

async def scrape_website(schema):
    url = schema.get("url", "https://www.google.com/")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-gpu'
            ]
        )

        context = await browser.new_context(user_agent=random.choice(USER_AGENTS))  # Set user agent here
        page = await context.new_page()

        try:
            await page.goto(url, wait_until='networkidle')

            data = {}
            if "properties" in schema:
                for key, value in schema["properties"].items():
                    try:
                        selector_type = value.get("selector_type", "css")
                        selector = value["selector"]

                        if selector_type == "css":
                            await page.wait_for_selector(selector, state="attached", timeout=10000)
                            elements = await page.query_selector_all(selector)
                        elif selector_type == "xpath":
                            elements = await page.locator(selector).all_inner_texts()
                        else:
                            raise ValueError(f"Invalid selector type: {selector_type}")

                        if value["type"] == "string":
                            if selector_type == "css":
                                element = await page.query_selector(selector)
                                if value.get("is_image", False):
                                    image_data = await extract_image_data(element)
                                    data[key] = image_data
                                else:
                                    data[key] = await element.get_attribute(value["attribute"]) if element and "attribute" in value else await element.inner_text() if element else None
                            elif selector_type == "xpath":
                                data[key] = elements[0] if elements else None
                        elif value["type"] == "array":
                            data[key] = []
                            for element in elements:
                                item = {}
                                for sub_key, sub_value in value["items"]["properties"].items():
                                    sub_selector_type = sub_value.get("selector_type", "css")
                                    sub_selector = sub_value["selector"]
                                    if sub_selector_type == "css":
                                        sub_element = await element.query_selector(sub_selector)
                                        if sub_value.get("is_image", False):
                                            image_data = await extract_image_data(sub_element)
                                            item[sub_key] = image_data
                                        else:
                                            item[sub_key] = await sub_element.get_attribute(sub_value["attribute"]) if sub_element and "attribute" in sub_value else await sub_element.inner_text() [...]
                                    elif sub_selector_type == "xpath":
                                        sub_elements = await page.locator(sub_selector).all_inner_texts()
                                        item[sub_key] = sub_elements[0] if sub_elements else None
                                data[key].append(item)

                    except Exception as e:
                        logging.error(f"Error extracting data for {key}: {e}")
                        data[key] = None

            if "actions" in schema:
                for action in schema["actions"]:
                    await perform_action(page, action)

            return data

        except Exception as e:
            logging.error(f"Error during scraping: {e}")
            return None
        finally:
            await context.close()
            await browser.close()

async def perform_action(page, action):
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
                    await page.wait_for_selector(selector, state="attached", timeout=10000)
                    element = await page.query_selector(selector)
                elif selector_type == "xpath":
                    element = await page.locator(selector).first
                else:
                    raise ValueError(f"Invalid selector type: {selector_type}")

            if action_type == "click":
                await element.click() if element else None
            elif action_type == "write":
                await element.fill(value) if element else None
            elif action_type == "scroll":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            elif action_type == "wait":
                await asyncio.sleep(duration)
            elif action_type == "keyboard":
                await page.keyboard.press(value)
            elif action_type == "goto":
                await page.goto(value)
            break
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt == retries - 1:
                raise

async def main():
    try:
        with open("schema.json", "r", encoding="utf-8") as f:
            schema = json.load(f)
    except FileNotFoundError:
        logging.error("Error: schema.json not found. Create this file with the correct JSON schema.")
        return
    except json.JSONDecodeError:
        logging.error("Error: Invalid JSON in schema.json. Check for syntax errors.")
        return

    try:
        data = await scrape_website(schema)
        if data:
            with open("output.json", "w", encoding="utf-8") as outfile:
                json.dump(data, outfile, indent=2, ensure_ascii=False)
        else:
            logging.info("No data was retrieved from the website.")
    except Exception as e:
        logging.error(f"An error occurred during execution: {e}")

if __name__ == "__main__":
    if 'google.colab' in str(get_ipython()):
        nest_asyncio.apply()
        asyncio.run(main())
    else:
        asyncio.run(main())