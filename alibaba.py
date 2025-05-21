import time
import json
import re
import logging
import os
import random
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from pathlib import Path
from datetime import datetime
from urllib.parse import quote, urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, WebDriverException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from collections import OrderedDict
import sanitize_filename

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Logging setup (console only)
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()]
    )
    return datetime.now().strftime("%Y%m%d_%H%M%S")

# Scraper class
class AlibabaScraper:
    def __init__(self, search_keyword: str, max_pages: int = 5, output_file: str = None):
        """Initialize the Alibaba scraper."""
        if not search_keyword or not search_keyword.strip():
            raise ValueError("Search keyword cannot be empty")
        self.original_keyword = search_keyword.strip()
        self.search_keyword = self.original_keyword.lower()
        self.max_pages = max(1, max_pages)
        self.output_file = output_file
        self.scraped_data = []
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ]
        self.driver = None
        self.wait = None
        self.base_url = "https://www.alibaba.com"
        self.selectors = {
            "product_card": ".j-offer-wrapper, .offer-wrapper, .organic-list-offer-outter, .search-card-item, .organic-gallery-offer-outter, .m-gallery-product-item-v2, div[class*='offer'], div[class*='card']",
            "product_link": "a.elements-title-normal__link, a.organic-gallery-title__link, a[href*='product-detail'], a[class*='title'], a",
            "next_page": ".organic-gallery-offer-pagination a.next, a.next-pagination-item, a[class*='next'], [aria-label*='next'], a[rel='next']",
            "title": "h1.product-title, h2.organic-gallery-title, .elements-title-normal__content, a[class*='title'], div[class*='title']",
            "price": ".elements-offer-price-normal__price, .organic-gallery-offer-section__offer-price, div[class*='price'], span[class*='price']",
            "description": "div[class*='desc'], div.product-detail-description, div[class*='text']",
            "detail_description": "div.product-main-description, div.detail-desc-decorate-richtext, div[class*='description'], div[class*='detail-content']",
            "supplier": ".company-name-wrapper, .company-name, div[class*='company'], div[class*='supplier']",
            "origin": "span.origin, div.product-shipping-location, div[class*='location'], div[class*='place']",
            "feedback": ".rating-info, .rating, div[class*='rating'], span[class*='rating']",
            "discount": "span.discount, div[class*='discount'], div[class*='promo']",
            "image": "img.main-image, img[src*='product'], img[class*='image'], img[src], img[data-src]",
            "detail_images": ".detail-gallery img, .thumb-list img, img[src*='product'], .main-image img",
            "detail_specs": ".product-props-list, .spec-table, div[class*='specification'], .attribute-list",
            "video": "video, video[src], div[class*='video']",
            "captcha": "div[class*='captcha'], iframe[src*='captcha'], div[class*='verify'], div[class*='slider'], .nc_wrapper"
        }
        self._setup_driver()

    def _setup_driver(self):
        """Set up Selenium WebDriver with Chrome."""
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument(f"--window-size={random.randint(1600, 1920)},{random.randint(900, 1080)}")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument(f"user-agent={random.choice(self.user_agents)}")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--start-maximized")
        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.navigator.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                """
            })
            self.wait = WebDriverWait(self.driver, 15)
            logging.info("WebDriver initialized")
        except WebDriverException as e:
            logging.error(f"Failed to initialize WebDriver: {e}")
            raise

    def rotate_user_agent(self):
        """Rotate user agent to avoid detection."""
        try:
            user_agent = random.choice(self.user_agents)
            self.driver.execute_cdp_cmd('Network.setUserAgentOverride', {"userAgent": user_agent})
            logging.info(f"Rotated user agent to: {user_agent}")
        except Exception as e:
            logging.warning(f"Failed to rotate user agent: {e}")

    def handle_anti_bot_checks(self) -> bool:
        """Detect CAPTCHA presence."""
        try:
            time.sleep(random.uniform(1, 2))
            captcha_elements = self.driver.find_elements(By.CSS_SELECTOR, self.selectors["captcha"])
            if captcha_elements:
                logging.warning("CAPTCHA detected!")
                return False
            logging.info("No CAPTCHA detected, proceeding...")
            return True
        except Exception as e:
            logging.error(f"Error checking for CAPTCHA: {e}")
            return True

    def human_like_scroll(self):
        """Perform simple scrolling to load content."""
        try:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            logging.warning(f"Error during scrolling: {e}")

    def create_product_data(self):
        """Create an ordered dictionary with field order preserved."""
        return OrderedDict([
            ("url", None),
            ("title", None),
            ("currency", None),
            ("exact_price", None),
            ("description", None),
            ("min_order", None),
            ("supplier", None),
            ("origin", None),
            ("feedback", OrderedDict([("rating", None), ("review", None)])),
            ("image_url", None),
            ("images", None),
            ("videos", None),
            ("dimensions", None),
            ("website_name", "Alibaba"),
            ("discount_information", None),
            ("brand_name", None),
            ("specifications", {})
        ])

    def scrape_products(self) -> list:
        """Main scraping logic."""
        try:
            for page in range(1, self.max_pages + 1):
                query_params = {
                    "SearchText": self.original_keyword,
                    "page": page,
                    "IndexArea": "product_en",
                    "viewtype": "G"
                }
                params_string = "&".join([f"{k}={quote(str(v))}" for k, v in query_params.items()])
                url = f"{self.base_url}/trade/search?{params_string}"
                logging.info(f"Scraping page {page}/{self.max_pages}: {url}")
                self.rotate_user_agent()
                self.driver.get(url)
                self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                if not self.handle_anti_bot_checks():
                    logging.error(f"Failed anti-bot checks on page {page}")
                    continue
                self.human_like_scroll()
                working_selector = None
                for selector in self.selectors["product_card"].split(", "):
                    try:
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if elements:
                            working_selector = selector
                            logging.info(f"Found {len(elements)} elements with selector: {selector}")
                            break
                    except TimeoutException:
                        logging.debug(f"Selector {selector} failed")
                        continue
                if not working_selector:
                    logging.error(f"No products found on page {page}")
                    continue
                cards = self.driver.find_elements(By.CSS_SELECTOR, working_selector)
                logging.info(f"Total cards found on page {page}: {len(cards)}")
                for idx, card_elem in enumerate(cards):
                    product_data = self.create_product_data()
                    try:
                        card_html = card_elem.get_attribute("outerHTML")
                        card_soup = BeautifulSoup(card_html, "html.parser")
                    except StaleElementReferenceException:
                        logging.warning(f"Stale element for card {idx}. Skipping.")
                        continue
                    title = None
                    for selector in self.selectors["title"].split(", "):
                        if title_el := card_soup.select_one(selector):
                            title = title_el.get_text(strip=True)
                            break
                    if not title:
                        logging.warning(f"No title found for card {idx}")
                        continue
                    product_data["title"] = self.clean_title(title)
                    product_url = None
                    for selector in self.selectors["product_link"].split(", "):
                        if a_tag := card_soup.select_one(selector):
                            product_url = a_tag.get("href", None)
                            break
                    if not product_url:
                        logging.warning(f"No URL found for {product_data['title']}")
                        continue
                    if product_url.startswith('//'):
                        product_url = f"https:{product_url}"
                    elif not product_url.startswith(('http://', 'https://')):
                        product_url = urljoin(self.base_url, product_url)
                    if "?" in product_url:
                        product_url = product_url.split("?")[0]
                    product_data["url"] = product_url
                    product_data.update(self.extract_price(card_soup, product_data["title"]))
                    product_data["min_order"] = self.extract_min_order(card_soup, product_data["title"])
                    product_data["supplier"] = self.extract_supplier(card_soup, product_data["title"])
                    product_data["feedback"] = self.extract_feedback(card_soup, product_data["title"])
                    product_data["discount_information"] = self.extract_discount(card_soup, product_data["title"])
                    product_data["brand_name"] = self.extract_brand(product_data["title"])
                    image_data = self.extract_images(card_soup, card_elem, product_data["title"])
                    product_data.update(image_data)
                    try:
                        detail_data = self.extract_detail_page(product_data["url"], product_data["title"])
                        product_data["description"] = detail_data["description"]
                        product_data["videos"] = detail_data["videos"]
                        product_data["specifications"] = detail_data["specifications"]
                        product_data["origin"] = detail_data["origin"]
                        if detail_data["images"]:
                            product_data["images"] = list(set((product_data["images"] or []) + detail_data["images"]))[:5]
                            if not product_data["image_url"] and product_data["images"]:
                                product_data["image_url"] = product_data["images"][0]
                    except Exception as e:
                        logging.error(f"Failed to extract detail page for {product_data['title']}: {e}")
                    if product_data["title"] and product_data["url"]:
                        self.scraped_data.append(product_data)
                        logging.info(f"Scraped product on page {page}: {product_data['title']}")
                    self.driver.get(url)
                    self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                    time.sleep(random.uniform(1, 2))
                if page < self.max_pages:
                    try:
                        next_button = None
                        for selector in self.selectors["next_page"].split(", "):
                            try:
                                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                                for elem in elements:
                                    if elem.is_displayed() and elem.is_enabled():
                                        next_button = elem
                                        break
                                if next_button:
                                    break
                            except NoSuchElementException:
                                continue
                        if not next_button:
                            logging.info("Next page button not found, stopping pagination")
                            break
                        self.driver.execute_script("arguments[0].click();", next_button)
                        time.sleep(random.uniform(2, 3))
                    except Exception as e:
                        logging.info(f"Error finding next page button: {e}")
                        break
        except Exception as e:
            logging.error(f"Scraping error: {e}")
        return self.scraped_data

    def clean_title(self, title: str) -> str:
        """Clean and normalize product title."""
        if not title:
            return ""
        title = re.sub(r'<[^>]+>', '', title)
        title = re.sub(r'[^\w\s,()&-]', ' ', title)
        title = " ".join(title.split())
        if self.search_keyword.lower() not in title.lower():
            title += f" {self.original_keyword.capitalize()}"
        if len(title) > 100:
            title = title[:97] + "..."
        return title

    def extract_price(self, soup: BeautifulSoup, title: str) -> dict:
        """Extract currency and exact price."""
        try:
            for selector in self.selectors["price"].split(", "):
                if price_el := soup.select_one(selector):
                    raw_price = price_el.get_text(strip=True)
                    if "Contact Supplier" in raw_price or "Negotiable" in raw_price:
                        return {"currency": None, "exact_price": "Ask Price"}
                    currency = None
                    currency_symbols = ["$", "€", "¥", "£", "US$", "CNY", "₹"]
                    for symbol in currency_symbols:
                        if symbol in raw_price:
                            currency = symbol
                            break
                    price_pattern = r'[\d,]+(?:\.\d+)?'
                    price_matches = re.findall(price_pattern, raw_price)
                    price_values = [re.sub(r'[^\d.]', '', p) for p in price_matches]
                    if price_values:
                        return {"currency": currency, "exact_price": price_values[0]}
                    break
            logging.warning(f"No price found for {title}")
            return {"currency": None, "exact_price": None}
        except Exception as e:
            logging.error(f"Error extracting price for {title}: {e}")
            return {"currency": None, "exact_price": None}

    def extract_min_order(self, soup: BeautifulSoup, title: str) -> str:
        """Extract minimum order quantity and unit."""
        try:
            for selector in self.selectors["discount"].split(", "):
                if moq_el := soup.select_one(selector):
                    text = moq_el.get_text(strip=True)
                    qty_pattern = r'(\d+)'
                    qty_match = re.search(qty_pattern, text)
                    qty = qty_match.group(1) if qty_match else None
                    unit_pattern = r'([A-Za-z]+)'
                    unit_match = re.search(unit_pattern, text)
                    unit = unit_match.group(1) if unit_match else None
                    if qty and unit:
                        return f"{qty} {unit}"
                    return text or None
            return None
        except Exception as e:
            logging.error(f"Error extracting min order for {title}: {e}")
            return None

    def extract_supplier(self, soup: BeautifulSoup, title: str) -> str:
        """Extract supplier name."""
        try:
            for selector in self.selectors["supplier"].split(", "):
                if elem := soup.select_one(selector):
                    return elem.get_text(strip=True)
            return None
        except Exception as e:
            logging.error(f"Error extracting supplier for {title}: {e}")
            return None

    def extract_origin(self, soup: BeautifulSoup, title: str) -> str:
        """Extract product origin."""
        try:
            for selector in self.selectors["origin"].split(", "):
                if origin_el := soup.select_one(selector):
                    return origin_el.get_text(strip=True)
            return None
        except Exception as e:
            logging.error(f"Error extracting origin for {title}: {e}")
            return None

    def extract_feedback(self, soup: BeautifulSoup, title: str) -> dict:
        """Extract rating and review count."""
        feedback = OrderedDict([("rating", None), ("review", None)])
        try:
            for selector in self.selectors["feedback"].split(", "):
                if rating_el := soup.select_one(selector):
                    rating_text = rating_el.get_text(strip=True)
                    rating_match = re.search(r'([\d.]+)', rating_text)
                    if rating_match:
                        feedback["rating"] = rating_match.group(1)
                        break
            for selector in self.selectors["feedback"].split(", "):
                if review_el := soup.select_one(selector):
                    review_text = review_el.get_text(strip=True)
                    review_match = re.search(r'\((\d+)\)', review_text)
                    if review_match:
                        feedback["review"] = review_match.group(1)
                        break
            return feedback
        except Exception as e:
            logging.error(f"Error extracting feedback for {title}: {e}")
            return feedback

    def extract_discount(self, soup: BeautifulSoup, title: str) -> str:
        """Extract discount information."""
        try:
            for selector in self.selectors["discount"].split(", "):
                if discount_el := soup.select_one(selector):
                    return discount_el.get_text(strip=True)
            return None
        except Exception as e:
            logging.error(f"Error extracting discount for {title}: {e}")
            return None

    def extract_brand(self, title: str) -> str:
        """Extract brand from title."""
        try:
            title_lower = title.lower()
            common_brands = ["dior", "sauvage", "creed", "ysl", "chanel", "gucci", "armani", "versace"]
            for brand in common_brands:
                if re.search(r'\b' + brand + r'\b', title_lower):
                    return brand.capitalize()
            return None
        except Exception as e:
            logging.error(f"Error extracting brand: {e}")
            return None

    def extract_images(self, soup: BeautifulSoup, card_elem, title: str) -> dict:
        """Extract image_url, images, and dimensions."""
        try:
            images = []
            image_url = None
            dimensions = None
            for selector in self.selectors["image"].split(", "):
                img_elements = soup.select(selector)
                if not img_elements:
                    continue
                for idx, img in enumerate(img_elements):
                    src = img.get("src", "") or img.get("data-src", "") or img.get("data-lazy-src", "")
                    if not src or any(x in src.lower() for x in ['placeholder', 'default', '.svg', 'noimage']):
                        continue
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif not src.startswith(('http://', 'https://')):
                        src = urljoin(self.base_url, src)
                    if idx == 0:
                        image_url = src
                        width = height = "Unknown"
                        try:
                            img_elem = card_elem.find_element(By.CSS_SELECTOR, selector)
                            width = self.driver.execute_script("return arguments[0].naturalWidth", img_elem) or img.get("width", "Unknown")
                            height = self.driver.execute_script("return arguments[0].naturalHeight", img_elem) or img.get("height", "Unknown")
                            dimensions = f"{width}x{height}"
                        except Exception as e:
                            logging.debug(f"Error getting image dimensions for {title}: {e}")
                            dimensions = f"{img.get('width', 'Unknown')}x{img.get('height', 'Unknown')}"
                    images.append(src)
                if images:
                    break
            if images:
                logging.info(f"Found {len(images)} images for {title}")
                return {"image_url": image_url, "images": images[:5], "dimensions": dimensions}
            logging.warning(f"No images found for {title}")
            return {"image_url": None, "images": None, "dimensions": None}
        except Exception as e:
            logging.error(f"Error extracting images for {title}: {e}")
            return {"image_url": None, "images": None, "dimensions": None}

    def extract_videos(self, soup: BeautifulSoup, title: str) -> list:
        """Extract video URLs."""
        try:
            videos = []
            for selector in self.selectors["video"].split(", "):
                for video_el in soup.find_all("video"):
                    if src := video_el.get("src"):
                        videos.append(src)
            return videos if videos else None
        except Exception as e:
            logging.error(f"Error extracting videos for {title}: {e}")
            return None

    def extract_specifications(self, soup: BeautifulSoup, title: str) -> dict:
        """Extract product specifications."""
        specs = {}
        try:
            for selector in self.selectors["detail_specs"].split(", "):
                for spec_elem in soup.select(selector):
                    if selector == ".attribute-list":
                        for item in spec_elem.select(".attribute-item"):
                            key_elem = item.select_one(".left")
                            value_elem = item.select_one(".right span")
                            if key_elem and value_elem:
                                key = key_elem.get_text(strip=True)
                                value = value_elem.get_text(strip=True)
                                if key and value and len(key) < 100 and len(value) < 500:
                                    specs[key] = value
                    else:
                        for row in spec_elem.select("tr, li, div.do-entry-item"):
                            cells = row.select("th, td, span.attr-name, span.attr-value")
                            if len(cells) >= 2:
                                key = cells[0].get_text(strip=True)
                                value = cells[1].get_text(strip=True)
                                if key and value and len(key) < 100 and len(value) < 500:
                                    specs[key] = value
                    if specs:
                        break
                if specs:
                    break
            return specs
        except Exception as e:
            logging.error(f"Error extracting specifications for {title}: {e}")
            return {}

    def extract_description(self, soup: BeautifulSoup, title: str) -> str:
        """Extract product description."""
        try:
            for selector in self.selectors["detail_description"].split(", "):
                if desc := soup.select_one(selector):
                    return desc.get_text(strip=True)
            return None
        except Exception as e:
            logging.error(f"Error extracting description for {title}: {e}")
            return None

    def extract_detail_page(self, url: str, title: str) -> dict:
        """Extract data from product detail page."""
        detail_data = {
            "description": None,
            "videos": None,
            "specifications": {},
            "images": [],
            "origin": None
        }
        try:
            self.driver.execute_script(f"window.open('{url}');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            if not self.handle_anti_bot_checks():
                logging.error(f"Failed anti-bot checks on detail page: {url}")
                return detail_data
            self.human_like_scroll()
            detail_html = self.driver.page_source
            detail_soup = BeautifulSoup(detail_html, "html.parser")
            detail_data["description"] = self.extract_description(detail_soup, title)
            detail_data["videos"] = self.extract_videos(detail_soup, title)
            detail_data["specifications"] = self.extract_specifications(detail_soup, title)
            detail_data["origin"] = self.extract_origin(detail_soup, title)
            valid_extensions = ('.jpg', '.jpeg', '.png', '.webp')
            for selector in self.selectors["detail_images"].split(", "):
                for img in detail_soup.select(selector):
                    src = img.get("src", "") or img.get("data-src", "") or img.get("data-lazy-src", "")
                    if not src or any(x in src.lower() for x in ['placeholder', 'default', '.svg', 'noimage']):
                        continue
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif not src.startswith(('http://', 'https://')):
                        src = urljoin(self.base_url, src)
                    if src.lower().endswith(valid_extensions):
                        src = src.replace("_.webp", "")
                        if src not in detail_data["images"]:
                            detail_data["images"].append(src)
                if detail_data["images"]:
                    break
            detail_data["images"] = detail_data["images"][:5]
            logging.info(f"Extracted detail page data for: {title}")
        except Exception as e:
            logging.error(f"Error extracting detail page {url}: {e}")
        finally:
            if len(self.driver.window_handles) > 1:
                self.driver.close()
                self.driver.switch_to.window(self.driver.window_handles[0])
        return detail_data

    def save_results(self) -> dict:
        """Save scraped data to JSON file and return results."""
        try:
            json_data = self.scraped_data
            logging.info(f"Number of products scraped: {len(json_data)}")
            if not json_data:
                logging.warning("No products scraped.")
                return OrderedDict([
                    ("success", False),
                    ("error", "No products scraped"),
                    ("data", [])
                ])

            if not self.output_file:
                default_dir = os.path.expanduser("~/Desktop")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_keyword = sanitize_filename.sanitize(self.search_keyword.replace(" ", "_"))
                self.output_file = os.path.join(default_dir, f"output_alibaba_{safe_keyword}_{timestamp}.json")
                logging.info(f"No output file specified. Using default: {self.output_file}")

            output_dir = os.path.dirname(self.output_file)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=4)
            logging.info(f"Scraping completed and saved to {self.output_file}")

            if os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 0:
                logging.info("JSON file verified.")
            else:
                logging.warning("Warning: JSON file is empty or was not created.")

            return OrderedDict([
                ("success", True),
                ("keyword", self.original_keyword),
                ("pages_scraped", self.max_pages),
                ("total_products", len(json_data)),
                ("output_file", self.output_file),
                ("data", json_data)
            ])
        except Exception as e:
            logging.error(f"Error saving JSON file: {str(e)}")
            return OrderedDict([
                ("success", False),
                ("error", f"Error saving data: {str(e)}"),
                ("data", [])
            ])
        finally:
            self.close()

    def close(self):
        """Close the WebDriver."""
        if self.driver:
            try:
                self.driver.quit()
                logging.info("Browser closed")
            except Exception as e:
                logging.warning(f"Error closing browser: {e}")
            self.driver = None

# API endpoint to scrape Alibaba
@app.route('/api/scrape', methods=['POST'])
def scrape():
    keyword = None
    pages = 5

    content_type = request.headers.get('Content-Type', '')

    if 'application/json' in content_type:
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    "success": False,
                    "error": "Invalid or missing JSON data"
                }), 400
            keyword = data.get('keyword', '').strip()
            pages = data.get('pages', 5)
        except Exception as e:
            logging.error(f"Error parsing JSON: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Invalid JSON format"
            }), 400
    elif 'application/x-www-form-urlencoded' in content_type or 'multipart/form-data' in content_type:
        try:
            keyword = request.form.get('keyword', '').strip()
            pages_str = request.form.get('pages', '5').strip()
            try:
                pages = int(pages_str)
            except ValueError:
                return jsonify({
                    "success": False,
                    "error": "Pages must be a valid integer"
                }), 400
        except Exception as e:
            logging.error(f"Error parsing form data: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Invalid form data"
            }), 400
    else:
        return jsonify({
            "success": False,
            "error": "Unsupported Content-Type. Use application/json or application/x-www-form-urlencoded"
        }), 415

    if not keyword:
        return jsonify({
            "success": False,
            "error": "Keyword is required"
        }), 400

    if not isinstance(pages, int) or pages < 1 or pages > 20:
        return jsonify({
            "success": False,
            "error": "Pages must be a number between 1 and 20"
        }), 400

    logging.info(f"Scraping for keyword: '{keyword}', pages: {pages}")

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = sanitize_filename.sanitize(keyword.replace(" ", "_"))
        output_file = os.path.join(os.path.expanduser("~/Desktop"), f"output_alibaba_{safe_keyword}_{timestamp}.json")

        scraper = AlibabaScraper(keyword, pages, output_file)
        products = scraper.scrape_products()
        result = scraper.save_results()

        response = app.response_class(
            response=json.dumps(result, ensure_ascii=False),
            status=200 if result["success"] else 500,
            mimetype='application/json'
        )
        return response
    except Exception as e:
        logging.error(f"Scraping failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Scraping failed: {str(e)}",
            "data": []
        }), 500

# Health check endpoint
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "uptime": time.time() - app.start_time
    })

# Check dependencies
def check_dependencies():
    try:
        import flask
        import flask_cors
        import selenium
        import bs4
        import webdriver_manager
        import sanitize_filename
        logging.info("All required Python packages are installed.")
        return True
    except ImportError as e:
        logging.error(f"Missing required packages: {e}. Install them using: pip install flask flask-cors selenium beautifulsoup4 webdriver-manager sanitize-filename")
        return False

# Initialize app
if __name__ == "__main__":
    setup_logging()
    app.start_time = time.time()
    if not check_dependencies():
        logging.error("Server started but dependencies are missing. Some features may not work.")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)