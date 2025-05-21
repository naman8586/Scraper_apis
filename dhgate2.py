from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import re
import time
import random
import logging
import subprocess
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime
import sanitize_filename

app = Flask(__name__)
CORS(app)

# Configure logging to console only (no log files)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class DHgateScraper:
    def __init__(self, search_keyword, max_pages=1, output_file=None):
        self.search_keyword = search_keyword
        self.max_pages = max_pages
        self.output_file = output_file
        self.retries = 3
        self.scraped_products = {}
        self.browser = self._setup_browser()

    def _setup_browser(self):
        """Configure and return a Selenium WebDriver instance"""
        options = webdriver.ChromeOptions()
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--log-level=3")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--headless")
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.maximize_window()
            return driver
        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def retry_extraction(self, func, attempts=3, delay=1, default=""):
        """Retry a function with specified attempts and delay"""
        for i in range(attempts):
            try:
                result = func()
                if result:
                    return result
            except Exception as e:
                logger.debug(f"Retry {i+1}/{attempts} failed: {e}")
                time.sleep(delay)
        return default

    def clean_text(self, text):
        """Clean text by removing extra whitespace"""
        return ' '.join(text.strip().split()) if text else ''

    def scroll_to_element(self, css_selector):
        """Scroll to element with retry"""
        try:
            element = self.browser.find_element(By.CSS_SELECTOR, css_selector)
            self.browser.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
            time.sleep(random.uniform(0.5, 1))
            return element
        except Exception as e:
            logger.error(f"Error scrolling to {css_selector}: {e}")
            return None

    def extract_specifications(self, product_page_html, product_json_data):
        """Extract product specifications"""
        try:
            if 'specifications' not in product_json_data:
                product_json_data['specifications'] = {}
            spec_regex = r'\b\d+(\.\d+)?\s*(?:x|X)\s*\d+(\.\d+)?\s*(?:x|X)\s*\d+(\.\d+)?\s*(cm|in|inches|centimeters|mm|kg|g)\b|' + \
                         r'\b\d+\s*-\s*\d+\s*(millimeters|mm|cm|in|inches|centimeters|kg|g)\b|' + \
                         r'\b\d+(\.\d+)?\s*(cm|in|inches|centimeters|mm|kg|g)\b|' + \
                         r'\b\d+\s*(UK|US|EU|CM)\b'
            specs_container = self.retry_extraction(
                lambda: product_page_html.find("div", {"class": "prodSpecifications_showLayer__15RQA"}),
                attempts=3, delay=1, default=None
            )
            if specs_container:
                specs_list = specs_container.find("ul", {"class": "prodSpecifications_showUl__fmY8y"})
                if specs_list:
                    for li in specs_list.find_all("li"):
                        key_span = li.find("span")
                        value_div = li.find("div", {"class": "prodSpecifications_deswrap___Z092"})
                        if key_span and value_div:
                            key = self.clean_text(key_span.get_text(strip=True).replace(":", ""))
                            value = self.clean_text(value_div.get_text(strip=True))
                            if key and value:
                                if re.match(spec_regex, value, re.IGNORECASE) or key.lower() in ["dial diameter", "waterproof deepness", "band width", "band length"]:
                                    product_json_data['specifications'][key] = value
                    logger.info(f"Specifications (showLayer): {product_json_data['specifications']}")
            if not product_json_data['specifications']:
                self.scroll_to_element("table.product-spec")
                specs_table = self.retry_extraction(
                    lambda: product_page_html.find("table", {"class": "product-spec"}),
                    attempts=3, delay=1, default=None
                )
                if specs_table:
                    for row in specs_table.find_all("tr"):
                        th = row.find("th")
                        td = row.find("td")
                        if th and td:
                            key = self.clean_text(th.get_text(strip=True))
                            value = self.clean_text(td.get_text(strip=True))
                            if key and value:
                                if re.match(spec_regex, value, re.IGNORECASE):
                                    product_json_data['specifications'][key] = value
                    logger.info(f"Specifications (table): {product_json_data['specifications']}")
            if not product_json_data['specifications']:
                description = product_json_data.get("description", "")
                spec_matches = re.findall(spec_regex, description, re.IGNORECASE)
                if spec_matches:
                    for match in spec_matches:
                        spec_value = self.clean_text(match[0])
                        if spec_value:
                            product_json_data['specifications']['Dimensions'] = spec_value
                    logger.info(f"Specifications (description): {product_json_data['specifications']}")
            if not product_json_data['specifications']:
                logger.info(f"No specifications found for product: {product_json_data['url']}")
        except Exception as e:
            logger.error(f"Error extracting specifications: {e}")
            product_json_data['specifications'] = {}
        return product_json_data

    def scrape_products(self):
        """Main scraping function"""
        for page in range(1, self.max_pages + 1):
            for attempt in range(self.retries):
                try:
                    search_url = f'https://www.dhgate.com/wholesale/search.do?act=search&searchkey={self.search_keyword}&pageNum={page}'
                    logger.info(f"Scraping page {page}/{self.max_pages}: {search_url}")
                    self.browser.get(search_url)
                    WebDriverWait(self.browser, 10).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )
                    time.sleep(random.uniform(2, 3))
                    try:
                        captcha = self.browser.find_element(By.XPATH, '//form[contains(@action, "captcha")]')
                        logger.warning(f"CAPTCHA detected on page {page}! Retrying...")
                        time.sleep(random.uniform(2, 3))
                        continue
                    except NoSuchElementException:
                        logger.info(f"No CAPTCHA detected on page {page}, proceeding...")
                    self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(random.uniform(1, 2))
                    product_cards = WebDriverWait(self.browser, 10).until(
                        EC.presence_of_all_elements_located((By.CLASS_NAME, "gallery-main"))
                    )
                    if not product_cards:
                        logger.warning(f"No products found on page {page}")
                        break
                    for product in product_cards:
                        product_json_data = {
                            "url": "",
                            "title": "",
                            "currency": "",
                            "min_price": "",
                            "max_price": "",
                            "description": "",
                            "supplier": "",
                            "feedback": {"rating": "", "review": ""},
                            "image_url": "",
                            "images": [],
                            "videos": [],
                            "dimensions": "",
                            "specifications": {},
                            "website_name": "DHgate.com",
                            "discount_information": "",
                            "brand_name": ""
                        }
                        try:
                            product_html = BeautifulSoup(product.get_attribute('outerHTML'), "html.parser")
                            title_div = self.retry_extraction(
                                lambda: product_html.find('div', {"class": "gallery-pro-name"}),
                                attempts=3, delay=1, default=None
                            )
                            if title_div:
                                a_tag = self.retry_extraction(
                                    lambda: title_div.find("a"),
                                    attempts=3, delay=1, default=None  # ✅ Fixed missing comma here
                                )
                                if a_tag:
                                    product_json_data["title"] = a_tag.get("title", "").strip()
                                    product_url = a_tag.get("href", "").strip()
                                    if product_url and not product_url.startswith("http"):
                                        product_url = f"https://www.dhgate.com{product_url}"
                                    product_json_data["url"] = product_url
                                    logger.info(f"Product URL: {product_url}")
                        except Exception as e:
                            logger.error(f"Error extracting product URL and title: {e}")
                            continue
                        
                        if product_json_data.get("url") in self.scraped_products:
                            continue

                        try:
                            price_element = self.retry_extraction(
                                lambda: product.find_element(By.CSS_SELECTOR, "[class*='price'], .gallery-pro-price"),
                                attempts=3, delay=1, default=None
                            )
                            if price_element:
                                price_text = self.clean_text(price_element.get_attribute('textContent'))
                                price_match = re.match(r'([A-Z]+)?\s*(\d+\.\d+\s*-\s*\d+\.\d+|\d+\.\d+)', price_text)
                                if price_match:
                                    product_json_data["currency"] = price_match.group(1) or "USD"
                                    prices = price_match.group(2).split('-') if '-' in price_match.group(2) else [price_match.group(2), price_match.group(2)]
                                    product_json_data["min_price"] = prices[0].strip()
                                    product_json_data["max_price"] = prices[1].strip() if len(prices) > 1 else prices[0].strip()
                                    logger.info(f"Price: {product_json_data['currency']} {product_json_data['min_price']} - {product_json_data['max_price']}")
                            else:
                                logger.warning(f"No price found for product: {product_json_data['url']}")
                        except Exception as e:
                            logger.error(f"Error extracting product price: {e}")
                        if product_json_data["url"]:
                            try:
                                self.browser.execute_script("window.open('');")
                                self.browser.switch_to.window(self.browser.window_handles[-1])
                                self.browser.get(product_json_data["url"])
                                WebDriverWait(self.browser, 10).until(
                                    lambda d: d.execute_script("return document.readyState") == "complete"
                                )
                                time.sleep(random.uniform(1, 2))
                                self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                                time.sleep(random.uniform(1, 2))
                                product_page_html = BeautifulSoup(self.browser.page_source, "html.parser")
                                try:
                                    description_elements = self.retry_extraction(
                                        lambda: product_page_html.find("div", {"class": "product-description-detail"}).find_all("p"),
                                        attempts=3, delay=1, default=[]
                                    )
                                    if description_elements:
                                        description = " ".join([elem.get_text(strip=True) for elem in description_elements])
                                        product_json_data["description"] = description
                                        logger.info(f"Description (product-description-detail): {description[:100]}...")
                                    else:
                                        info_section = self.retry_extraction(
                                            lambda: product_page_html.find("div", {"class": "product-info"}),
                                            attempts=3, delay=1, default=None
                                        )
                                        if info_section:
                                            description = info_section.get_text(strip=True)
                                            product_json_data["description"] = description
                                            logger.info(f"Description (product-info): {description[:100]}...")
                                        else:
                                            h1_title = self.retry_extraction(
                                                lambda: product_page_html.find("h1").get_text(strip=True),
                                                attempts=3, delay=1, default=""
                                            )
                                            if h1_title:
                                                product_json_data["description"] = h1_title
                                                logger.info(f"Description (h1 title): {h1_title[:100]}...")
                                except Exception as e:
                                    logger.error(f"Error extracting description: {e}")
                                try:
                                    review_text = self.retry_extraction(
                                        lambda: product_page_html.find("span", {"class": "productSellerMsg_reviewsCount__HJ3MJ"}).get_text(strip=True),
                                        attempts=3, delay=1, default=""
                                    )
                                    if review_text:
                                        review_match = re.search(r'\d+', review_text)
                                        if review_match:
                                            product_json_data["feedback"]["review"] = review_match.group(0)
                                            logger.info(f"Review count: {product_json_data['feedback']['review']}")
                                    else:
                                        alt_reviews = self.retry_extraction(
                                            lambda: product_page_html.find("span", {"class": "review-count"}).get_text(strip=True),
                                            attempts=3, delay=1, default=""
                                        )
                                        if alt_reviews:
                                            review_match = re.search(r'\d+', alt_reviews)
                                            if review_match:
                                                product_json_data["feedback"]["review"] = review_match.group(0)
                                                logger.info(f"Review count (fallback): {product_json_data['feedback']['review']}")
                                except Exception as e:
                                    logger.error(f"Error extracting product reviews: {e}")
                                try:
                                    rating = self.retry_extraction(
                                        lambda: product_page_html.find("div", {"class": "productSellerMsg_starWarp__WeIw2"}).find("span", string=re.compile(r'^\d+\.\d+$')),
                                        attempts=3, delay=1, default=""
                                    )
                                    if rating:
                                        product_json_data["feedback"]["rating"] = rating.get_text(strip=True)
                                        logger.info(f"Rating: {product_json_data['feedback']['rating']}")
                                    else:
                                        alt_rating = self.retry_extraction(
                                            lambda: product_page_html.find("span", {"class": "star-rating"}).get_text(strip=True),
                                            attempts=3, delay=1, default=""
                                        )
                                        if alt_rating and re.match(r'^\d+\.\d+$', alt_rating):
                                            product_json_data["feedback"]["rating"] = alt_rating
                                            logger.info(f"Rating (fallback): {product_json_data['feedback']['rating']}")
                                except Exception as e:
                                    logger.error(f"Error extracting product rating: {e}")
                                try:
                                    supplier_name = self.retry_extraction(
                                        lambda: product_page_html.find("a", {"class": "store-name"}).get_text(strip=True),
                                        attempts=3, delay=1, default=""
                                    )
                                    if supplier_name:
                                        product_json_data["supplier"] = supplier_name
                                        logger.info(f"Supplier: {supplier_name}")
                                    else:
                                        store_link = self.retry_extraction(
                                            lambda: product_page_html.find("a", href=re.compile(r'https://www\.dhgate\.com/store/')).get_text(strip=True),
                                            attempts=3, delay=1, default=""
                                        )
                                        if store_link:
                                            product_json_data["supplier"] = store_link
                                            logger.info(f"Supplier (fallback from store link): {store_link}")
                                except Exception as e:
                                    logger.error(f"Error extracting product supplier: {e}")
                                try:
                                    main_image_elem = self.retry_extraction(
                                        lambda: product_page_html.find("div", {"class": "masterMap_bigMapWarp__2Jzw2"}).find("img"),
                                        attempts=3, delay=1, default=None
                                    )
                                    if main_image_elem:
                                        main_image = main_image_elem.get("data-zoom-image") or main_image_elem.get("src", "")
                                        if main_image and not main_image.startswith("http"):
                                            main_image = f"https:{main_image}"
                                        if "100x100" not in main_image and main_image:
                                            product_json_data["image_url"] = main_image
                                            logger.info(f"Primary image URL: {main_image}")
                                    if not product_json_data["image_url"]:
                                        alt_image_elem = self.retry_extraction(
                                            lambda: product_page_html.find("img", {"class": "main-image"}),
                                            attempts=3, delay=1, default=None
                                        )
                                        if alt_image_elem:
                                            alt_image = alt_image_elem.get("data-zoom-image") or alt_image_elem.get("src", "")
                                            if alt_image and not alt_image.startswith("http"):
                                                alt_image = f"https:{alt_image}"
                                            if "100x100" not in alt_image and alt_image:
                                                product_json_data["image_url"] = alt_image
                                                logger.info(f"Primary image URL (fallback main-image): {alt_image}")
                                    if not product_json_data["image_url"]:
                                        thumb_image = self.retry_extraction(
                                            lambda: product_page_html.find("ul", {"class": "masterMap_smallMapList__JTkBX"}).find("img").get("data-zoom-image") or 
                                                    product_page_html.find("ul", {"class": "masterMap_smallMapList__JTkBX"}).find("img").get("src"),
                                            attempts=3, delay=1, default=""
                                        )
                                        if thumb_image and not thumb_image.startswith("http"):
                                            thumb_image = f"https:{thumb_image}"
                                        if "100x100" not in thumb_image and thumb_image:
                                            product_json_data["image_url"] = thumb_image
                                            logger.info(f"Primary image URL (thumbnail fallback): {thumb_image}")
                                except Exception as e:
                                    logger.error(f"Error extracting primary image URL: {e}")
                                try:
                                    self.scroll_to_element("ul.masterMap_smallMapList__JTkBX")
                                    thumbnails = self.retry_extraction(
                                        lambda: self.browser.find_elements(By.CSS_SELECTOR, "ul.masterMap_smallMapList__JTkBX li"),
                                        attempts=3, delay=1, default=[]
                                    )
                                    media_images = set([product_json_data["image_url"]]) if product_json_data["image_url"] else set()
                                    media_videos = set()
                                    for thumb in thumbnails:
                                        try:
                                            ActionChains(self.browser).move_to_element(thumb).click().perform()
                                            time.sleep(random.uniform(0.5, 1))
                                            media_soup = BeautifulSoup(self.browser.page_source, "html.parser")
                                            big_map_div = media_soup.find("div", {"class": "masterMap_bigMapWarp__2Jzw2"})
                                            if big_map_div:
                                                video_tag = big_map_div.find("video")
                                                if video_tag and video_tag.get("src"):
                                                    video_src = video_tag.get("src")
                                                    if not video_src.startswith("http"):
                                                        video_src = f"https:{video_src}"
                                                    media_videos.add(video_src)
                                                else:
                                                    image_tag = big_map_div.find("img")
                                                    if image_tag:
                                                        img_src = image_tag.get("data-zoom-image") or image_tag.get("src", "")
                                                        if img_src and not img_src.startswith("http"):
                                                            img_src = f"https:{img_src}"
                                                        if "100x100" not in img_src and img_src:
                                                            media_images.add(img_src)
                                        except Exception as e:
                                            logger.error(f"Error extracting media for a thumbnail: {e}")
                                    product_json_data["images"] = list(media_images)
                                    product_json_data["videos"] = list(media_videos)
                                    logger.info(f"Images: {product_json_data['images']}")
                                    logger.info(f"Videos: {product_json_data['videos']}")
                                except Exception as e:
                                    logger.error(f"Error extracting additional images and videos: {e}")
                                try:
                                    dim_regex = r'\b\d+(\.\d+)?\s*(?:x|X)\s*\d+(\.\d+)?\s*(?:x|X)\s*\d+(\.\d+)?\s*(cm|in|inches|centimeters|mm)\b|' + \
                                                r'\b\d+\s*-\s*\d+\s*(millimeters|mm|cm|in|inches|centimeters)\b|' + \
                                                r'\b\d+(\.\d+)?\s*(cm|in|inches|centimeters|mm)\b'
                                    dimension_keys = ["Band length", "Dial Diameter", "Band Width", "Waterproof Deepness", "Case Size", "Dimensions"]
                                    specs_list = self.retry_extraction(
                                        lambda: product_page_html.find("ul", {"class": "prodSpecifications_showUl__fmY8y"}),
                                        attempts=3, delay=1, default=None
                                    )
                                    dimensions = []
                                    if specs_list:
                                        for li in specs_list.find_all("li"):
                                            key_elem = li.find("span")
                                            value_elem = li.find("div", {"class": "prodSpecifications_deswrap___Z092"})
                                            if key_elem and value_elem:
                                                key = key_elem.get_text(strip=True).replace(":", "").strip()
                                                value = value_elem.get_text(strip=True)
                                                if key in dimension_keys and re.match(dim_regex, value, re.IGNORECASE):
                                                    dimensions.append(f"{key}: {value}")
                                        if dimensions:
                                            product_json_data["dimensions"] = "; ".join(dimensions)
                                            logger.info(f"Dimensions (specifications): {product_json_data['dimensions']}")
                                    if not product_json_data["dimensions"]:
                                        description = product_json_data.get("description", "")
                                        dimension_matches = re.findall(dim_regex, description, re.IGNORECASE)
                                        if dimension_matches:
                                            dimensions = "; ".join([match[0] for match in dimension_matches if match[0]])
                                            product_json_data["dimensions"] = dimensions
                                            logger.info(f"Dimensions (description): {dimensions}")
                                except Exception as e:
                                    logger.error(f"Error extracting dimensions: {e}")
                                product_json_data = self.extract_specifications(product_page_html, product_json_data)
                                try:
                                    discount_element = self.retry_extraction(
                                        lambda: product_page_html.find("span", {"class": "productPrice_discount__dMPyI"}),
                                        attempts=3, delay=1, default=None
                                    )
                                    if discount_element:
                                        discount_text = discount_element.get_text(strip=True)
                                        if re.match(r'\d+%\s*(off)?', discount_text, re.IGNORECASE):
                                            product_json_data["discount_information"] = discount_text
                                            logger.info(f"Discount information: {discount_text}")
                                    else:
                                        discount_element = self.retry_extraction(
                                            lambda: product_page_html.find("span", {"class": "discount-label"}),
                                            attempts=3, delay=1, default=None
                                        )
                                        if discount_element:
                                            discount_text = discount_element.get_text(strip=True)
                                            if re.match(r'\d+%\s*(off)?', discount_text, re.IGNORECASE):
                                                product_json_data["discount_information"] = discount_text
                                                logger.info(f"Discount information (discount-label): {discount_text}")
                                        else:
                                            promo_tag = self.retry_extraction(
                                                lambda: product_page_html.find("span", {"class": "promo-label"}).get_text(strip=True),
                                                attempts=3, delay=1, default=""
                                            )
                                            if promo_tag and re.match(r'\d+%\s*off', promo_tag, re.IGNORECASE):
                                                product_json_data["discount_information"] = promo_tag
                                                logger.info(f"Discount (promo tag): {promo_tag}")
                                except Exception as e:
                                    logger.error(f"Error extracting discount information: {e}")
                                try:
                                    brand_name = None
                                    if 'specifications' in product_json_data and product_json_data['specifications']:
                                        for key, value in product_json_data['specifications'].items():
                                            if key.lower() in ["brand", "product brand"]:
                                                brand_name = value
                                                product_json_data["brand_name"] = brand_name
                                                logger.info(f"Brand name (specifications): {brand_name}")
                                                break
                                    if not brand_name:
                                        brand_element = self.retry_extraction(
                                            lambda: product_page_html.find("span", {"class": "brand-name"}),
                                            attempts=3, delay=1, default=None
                                        )
                                        if brand_element:
                                            brand_name = brand_element.get_text(strip=True)
                                            brand_name = re.sub(r'^Brand:\s*', '', brand_name, flags=re.IGNORECASE)
                                            product_json_data["brand_name"] = brand_name
                                            logger.info(f"Brand name (page): {brand_name}")
                                        else:
                                            title = product_json_data.get("title", "").lower()
                                            if self.search_keyword.lower() in title:
                                                product_json_data["brand_name"] = self.search_keyword
                                                logger.info(f"Brand name (title): {self.search_keyword}")
                                except Exception as e:
                                    logger.error(f"Error extracting brand name: {e}")
                            except Exception as e:
                                logger.error(f"Error processing product page: {e}")
                            finally:
                                self.browser.close()
                                self.browser.switch_to.window(self.browser.window_handles[0])
                        self.scraped_products[product_json_data["url"]] = product_json_data
                    break
                except Exception as e:
                    logger.error(f"Attempt {attempt + 1}/{self.retries}: Error scraping page {page}: {e}")
                    time.sleep(random.uniform(1, 2))
            else:
                logger.error(f"Failed to scrape page {page} after {self.retries} attempts.")
        return list(self.scraped_products.values())

    def save_results(self):
        """Save scraped data and return results"""
        try:
            json_data = list(self.scraped_products.values())
            logger.info(f"Number of products scraped: {len(json_data)}")
            if not json_data:
                logger.warning("No products scraped.")
                return {
                    "success": False,
                    "error": "No products scraped",
                    "data": []
                }
            if self.output_file:
                with open(self.output_file, "w", encoding="utf-8") as f:
                    json.dump(json_data, f, ensure_ascii=False, indent=4)
                logger.info(f"Scraping completed and saved to {self.output_file}")
                if os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 0:
                    logger.info("JSON file verified.")
                else:
                    logger.warning("Warning: JSON file is empty or was not created.")
            return {
                "success": True,
                "keyword": self.search_keyword,
                "pages_scraped": self.max_pages,
                "total_products": len(json_data),
                "data": json_data,
                "output_file": self.output_file
            }
        except Exception as e:
            logger.error(f"Error saving final JSON file: {e}")
            return {
                "success": False,
                "error": f"Error saving data: {str(e)}",
                "data": []
            }
        finally:
            try:
                self.browser.quit()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")

@app.route('/api/scrape', methods=['POST'])
def scrape():
    data = request.get_json()
    keyword = data.get('keyword', '').strip()
    pages = int(data.get('pages', 1))
    
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
    
    logger.info(f"Scraping for keyword: '{keyword}', pages: {pages}")
    
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = sanitize_filename.sanitize(keyword.replace(" ", "_"))
        output_file = f"output_{safe_keyword}_{timestamp}.json"
        
        scraper = DHgateScraper(keyword, pages, output_file)
        products = scraper.scrape_products()
        result = scraper.save_results()
        return jsonify(result), 200 if result["success"] else 500
    except Exception as e:
        logger.error(f"Scraping failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Scraping failed: {str(e)}",
            "data": []
        }), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "uptime": time.time() - app.start_time
    })

def check_dependencies():
    try:
        result = subprocess.run(['python', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Python is not installed")
        
        required_packages = ['selenium', 'beautifulsoup4', 'webdriver-manager', 'flask-cors', 'sanitize-filename']
        for pkg in required_packages:
            result = subprocess.run(['pip3', 'show', pkg], capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"Required Python package {pkg} is missing. Install it using: pip3 install {pkg}")
        
        logger.info("Dependencies verified successfully")
        return True
    except Exception as e:
        logger.error(f"Dependency check failed: {e}")
        return False

if __name__ == '__main__':
    app.start_time = time.time()
    if not check_dependencies():
        logger.error("Server started but dependencies are missing. Some features may not work.")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
