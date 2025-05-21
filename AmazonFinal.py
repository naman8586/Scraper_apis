from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import re
import time
import json
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from pathlib import Path
import tempfile
import logging
from datetime import datetime
import sanitize_filename
import os

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure logging to support UTF-8
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("amazon_scraper.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Sanitize log messages for console output on Windows
def sanitize_log_message(message):
    """Replace non-ASCII characters to avoid encoding errors in console."""
    if os.name == 'nt':
        return message.encode('ascii', 'replace').decode('ascii')
    return message

class AmazonScraper:
    def __init__(self, search_keyword: str, max_pages: int = 1, output_file: str = None):
        """Initialize the Amazon scraper."""
        if not search_keyword or not isinstance(search_keyword, str) or not search_keyword.strip():
            raise ValueError("Search keyword must be a non-empty string")
        self.search_keyword = search_keyword.strip()
        self.max_pages = max(1, min(max_pages, 20))  # Cap at 20 pages
        self.output_file = output_file
        self.retries = 3
        self.scraped_products = {}
        self.browser = self._setup_browser()

    def _setup_browser(self):
        """Configure and return a Selenium WebDriver instance."""
        options = webdriver.ChromeOptions()
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--log-level=3")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--headless=new")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.maximize_window()
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            logger.info("Chrome WebDriver initialized successfully.")
            return driver
        except WebDriverException as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def retry_extraction(self, func, attempts=3, delay=1, default=""):
        """Retries an extraction function up to 'attempts' times."""
        for i in range(attempts):
            try:
                result = func()
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Retry {i+1}/{attempts} failed: {e}")
                time.sleep(delay)
        return default

    def clean_text(self, text):
        """Clean text by removing extra whitespace, newlines, control characters, and special Unicode characters."""
        if not text:
            return ""
        cleaned = re.sub(r'[\u2000-\u200F\u2028-\u202F]+', '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = re.sub(r'\[U\+[0-9A-Fa-f]+\]', '', cleaned)
        return cleaned.strip()

    def detect_captcha(self):
        """Check for CAPTCHA presence."""
        try:
            captcha_elements = self.browser.find_elements(By.CSS_SELECTOR, "form[action*='captcha'], div.a-box.a-alert.a-alert-warning, div#captchacharacters")
            if captcha_elements:
                logger.warning("CAPTCHA detected!")
                return True
            return False
        except Exception as e:
            logger.warning(f"Error checking CAPTCHA: {e}")
            return False

    def extract_product_description(self, product_page_html):
        """Extract product description from the product page, including overview, features, and technical specs."""
        description = {
            "features": [],
            "technical_specs": {}
        }

        # Extract features from module-9 sections
        try:
            module_9_sections = product_page_html.find_all("div", {"class": re.compile(r"aplus-module module-9")})
            for section in module_9_sections:
                flex_items = section.find_all("div", {"class": "apm-flex-item-third-width"})
                for item in flex_items:
                    try:
                        heading = self.clean_text(item.find("h4").get_text(strip=True))
                        paragraphs = item.find_all("p")
                        lists = item.find_all("ul", {"class": "a-unordered-list"})
                        feature_text = f"{heading}\n"
                        for p in paragraphs:
                            p_text = self.clean_text(p.get_text(strip=True))
                            if p_text:
                                feature_text += f"{p_text}\n"
                        for ul in lists:
                            for li in ul.find_all("li"):
                                li_text = self.clean_text(li.get_text(strip=True))
                                feature_text += f"- {li_text}\n"
                        description["features"].append(feature_text.strip())
                    except Exception as e:
                        logger.warning(f"Error extracting feature: {e}")
        except Exception as e:
            logger.warning(f"Error extracting module-9 sections: {e}")

        # Extract technical specifications from module-16-tech-specs
        try:
            tech_specs_table = product_page_html.find("table", {"class": "aplus-tech-spec-table"})
            if tech_specs_table:
                rows = tech_specs_table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) == 2:
                        key = self.clean_text(cells[0].get_text(strip=True))
                        value = self.clean_text(cells[1].get_text(strip=True))
                        description["technical_specs"][key] = value
        except Exception as e:
            logger.warning(f"Error extracting technical specs: {e}")

        # Extract description from feature-bullets (updated selector)
        try:
            description_elements = WebDriverWait(self.browser, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "#feature-bullets ul li"))
            )
            if description_elements:
                feature_text = ""
                for element in description_elements:
                    self.browser.execute_script("arguments[0].scrollIntoView(true);", element)
                    time.sleep(0.5)
                    element_text = self.clean_text(element.text.strip())
                    if element_text:
                        feature_text += f"- {element_text}\n"
                description["features"].append(feature_text.strip())
        except TimeoutException:
            logger.warning("Primary description selector not found, trying alternative selector...")
            try:
                description_elements = product_page_html.select("div#productDescription, ul.a-unordered-list.a-vertical.a-spacing-mini li")
                if description_elements:
                    feature_text = ""
                    for element in description_elements:
                        element_text = self.clean_text(element.get_text(strip=True))
                        if element_text:
                            feature_text += f"- {element_text}\n"
                    description["features"].append(feature_text.strip())
            except Exception as e:
                logger.warning(f"Alternative description selector not found: {e}")

        return description if description["features"] or description["technical_specs"] else "N/A"

    def scrape_products(self):
        """Main scraping function."""
        try:
            for page in range(1, self.max_pages + 1):
                for attempt in range(self.retries):
                    try:
                        search_url = f"https://www.amazon.in/s?k={self.search_keyword.replace(' ', '+')}&page={page}"
                        logger.info(f"Scraping page {page}/{self.max_pages}: {search_url}")
                        self.browser.get(search_url)
                        WebDriverWait(self.browser, 15).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.s-main-slot"))
                        )
                        if self.detect_captcha():
                            logger.error(f"CAPTCHA detected on page {page}, stopping.")
                            break
                        time.sleep(2)
                        html_data = BeautifulSoup(self.browser.page_source, "html.parser")
                        product_cards = html_data.select("div.s-result-item.s-asin:not(.AdHolder)")
                        logger.info(f"Found {len(product_cards)} organic product cards on page {page}")

                        for product in product_cards:
                            product_json_data = {
                                "url": "N/A",
                                "title": "N/A",
                                "currency": "N/A",
                                "exact_price": "N/A",
                                "mrp": "N/A",
                                "description": "N/A",
                                "min_order": "1 unit",
                                "supplier": "N/A",
                                "origin": "N/A",
                                "feedback": {"rating": "N/A", "review": "N/A"},
                                "image_url": "N/A",
                                "images": [],
                                "videos": [],
                                "Specifications": {},
                                "website_name": "Amazon",
                                "discount_information": "N/A",
                                "brand_name": "N/A"
                            }

                            # Extract product URL
                            try:
                                product_link = self.retry_extraction(
                                    lambda: product.select_one("a.a-link-normal.s-no-outline")["href"]
                                )
                                if product_link:
                                    product_url = product_link if product_link.startswith("https://www.amazon.in") else f"https://www.amazon.in{product_link}"
                                    product_json_data["url"] = product_url.split("?")[0].split("/ref=")[0]
                                    logger.info(f"Product URL: {product_url}")
                                else:
                                    logger.warning("No product link found, skipping product.")
                                    continue
                            except Exception as e:
                                logger.warning(f"Error extracting product URL: {e}")
                                continue

                            # Avoid duplicate products by URL
                            if product_json_data["url"] in self.scraped_products:
                                logger.info(f"Skipping duplicate product: {product_json_data['url']}")
                                continue

                            # Extract product title
                            try:
                                title_elem = self.retry_extraction(
                                    lambda: product.select_one("span.a-size-medium.a-color-base.a-text-normal, h2.a-size-mini.a-spacing-none.a-color-base")
                                )
                                if title_elem:
                                    product_json_data["title"] = self.clean_text(title_elem.get_text(strip=True))
                                    logger.info(f"Product title: {product_json_data['title']}")
                            except Exception as e:
                                logger.warning(f"Error extracting product title: {e}")

                            # Extract product currency
                            try:
                                product_currency_element = self.retry_extraction(
                                    lambda: product.select_one("span.a-price-symbol")
                                )
                                if product_currency_element:
                                    currency = self.clean_text(product_currency_element.get_text(strip=True))
                                    product_json_data["currency"] = currency
                                    logger.info(f"Product currency: {sanitize_log_message(currency)}")
                            except Exception as e:
                                logger.warning(f"Error extracting product currency: {e}")

                            # Extract product price
                            try:
                                product_price_whole = self.retry_extraction(
                                    lambda: product.select_one("span.a-price-whole")
                                )
                                product_price_fraction = self.retry_extraction(
                                    lambda: product.select_one("span.a-price-fraction")
                                )
                                if product_price_whole:
                                    price_whole = self.clean_text(product_price_whole.get_text(strip=True)).replace(",", "")
                                    price_text = price_whole
                                    if product_price_fraction:
                                        price_fraction = self.clean_text(product_price_fraction.get_text(strip=True))
                                        price_text = f"{price_whole}.{price_fraction}"
                                    product_json_data["exact_price"] = price_text
                                    logger.info(f"Product price: {product_json_data['exact_price']}")
                            except Exception as e:
                                logger.warning(f"Error extracting product price: {e}")

                            # Open product page to extract additional details
                            if product_json_data["url"] != "N/A":
                                try:
                                    self.browser.execute_script("window.open('');")
                                    self.browser.switch_to.window(self.browser.window_handles[-1])
                                    self.browser.get(product_json_data["url"])
                                    WebDriverWait(self.browser, 15).until(
                                        EC.presence_of_element_located((By.CSS_SELECTOR, "div#ppd, div#dp-container"))
                                    )
                                    time.sleep(1)
                                    product_page_html = BeautifulSoup(self.browser.page_source, "html.parser")

                                    # Extract product description
                                    product_json_data["description"] = self.extract_product_description(product_page_html)

                                    # Extract MRP
                                    try:
                                        mrp_element = self.retry_extraction(
                                            lambda: product_page_html.select_one("span.a-price.a-text-price span.a-offscreen")
                                        )
                                        if mrp_element:
                                            mrp_text = self.clean_text(mrp_element.get_text(strip=True))
                                            if mrp_text:
                                                mrp_value = re.sub(r'[^\d.]', '', mrp_text)
                                                product_json_data["mrp"] = float(mrp_value) if mrp_value else "N/A"
                                                logger.info(f"MRP extracted: {product_json_data['mrp']}")
                                            else:
                                                logger.warning(f"No MRP text found")
                                        else:
                                            logger.warning(f"No MRP element found")
                                    except Exception as e:
                                        logger.warning(f"Error extracting MRP: {e}")

                                    # Extract discount information
                                    try:
                                        discount_elem = self.retry_extraction(
                                            lambda: product_page_html.select_one("span.savingsPercentage")
                                        )
                                        if discount_elem:
                                            product_json_data["discount_information"] = self.clean_text(discount_elem.get_text(strip=True))
                                            logger.info(f"Discount extracted: {product_json_data['discount_information']}")
                                        else:
                                            # Fallback: Calculate discount from MRP and price
                                            if product_json_data["mrp"] != "N/A" and product_json_data["exact_price"] != "N/A":
                                                try:
                                                    current_price = float(re.sub(r'[^\d.]', '', product_json_data["exact_price"]))
                                                    mrp_value = float(product_json_data["mrp"])
                                                    if mrp_value > current_price:
                                                        discount_percentage = ((mrp_value - current_price) / mrp_value) * 100
                                                        product_json_data["discount_information"] = f"{discount_percentage:.2f}% off"
                                                        logger.info(f"Calculated discount: {product_json_data['discount_information']}")
                                                    else:
                                                        logger.info(f"No discount applicable (MRP <= Price)")
                                                except ValueError as e:
                                                    logger.warning(f"Error calculating discount: {e}")
                                            else:
                                                logger.warning(f"No discount found (missing MRP or price)")
                                    except Exception as e:
                                        logger.warning(f"Error extracting discount: {e}")

                                    # Extract product details
                                    product_details = {}
                                    try:
                                        detail_lists = product_page_html.select("ul.detail-bullet-list > li")
                                        for li in detail_lists:
                                            try:
                                                label_tag = li.select_one("span.a-text-bold")
                                                value_tag = label_tag.find_next_sibling("span") if label_tag else None
                                                if label_tag and value_tag:
                                                    label = self.clean_text(label_tag.get_text(strip=True).replace(":", ""))
                                                    value = self.clean_text(value_tag.get_text(" ", strip=True))
                                                    if label and value:
                                                        product_details[label] = value
                                            except Exception as e:
                                                logger.warning(f"Error parsing product detail item: {e}")
                                        if not product_details:
                                            details_table = product_page_html.select_one("table#productDetails_detailBullets_sections1")
                                            if details_table:
                                                rows = details_table.find_all("tr")
                                                for row in rows:
                                                    try:
                                                        label = row.find("th", {"class": "a-color-secondary a-size-base prodDetSectionEntry"})
                                                        value = row.find("td", {"class": "a-size-base prodDetAttrValue"})
                                                        if label and value:
                                                            label_text = self.clean_text(label.get_text(strip=True).replace(":", ""))
                                                            value_text = self.clean_text(value.get_text(" ", strip=True))
                                                            if label_text and value_text:
                                                                product_details[label_text] = value_text
                                                    except Exception as e:
                                                        logger.warning(f"Error parsing table detail row: {e}")
                                        product_json_data["Specifications"] = product_details
                                        logger.info(f"Product details extracted: {product_details}")
                                    except Exception as e:
                                        logger.warning(f"Error extracting product details: {e}")

                                    # Extract product reviews
                                    try:
                                        product_review_element = self.retry_extraction(
                                            lambda: product_page_html.find("span", {"id": "acrCustomerReviewText"})
                                        )
                                        if product_review_element:
                                            product_review_text = self.clean_text(product_review_element.get_text(strip=True))
                                            numeric_match = re.search(r"(\d+)", product_review_text)
                                            if numeric_match:
                                                product_json_data["feedback"]["review"] = numeric_match.group(1)
                                                logger.info(f"Product reviews: {product_json_data['feedback']['review']}")
                                    except Exception as e:
                                        logger.warning(f"Error extracting product reviews: {e}")

                                    # Extract product rating
                                    try:
                                        product_rating_element = self.retry_extraction(
                                            lambda: product_page_html.find(
                                                lambda tag: tag.name == "span" and tag.get("id") == "acrPopover" and "reviewCountTextLinkedHistogram" in tag.get("class", []) and tag.has_attr("title")
                                            )
                                        )
                                        if product_rating_element:
                                            rating_span = product_rating_element.find("span", {"class": "a-size-base a-color-base"})
                                            if rating_span:
                                                product_json_data["feedback"]["rating"] = self.clean_text(rating_span.get_text(strip=True))
                                                logger.info(f"Product rating: {product_json_data['feedback']['rating']}")
                                    except Exception as e:
                                        logger.warning(f"Error extracting product rating: {e}")

                                    # Extract product supplier
                                    try:
                                        product_supplier_element = product_page_html.find("a", {"id": "sellerProfileTriggerId"})
                                        if not product_supplier_element:
                                            product_supplier_element = product_page_html.find("span", {"class": "tabular-buybox-text"})
                                        if product_supplier_element:
                                            product_json_data["supplier"] = self.clean_text(product_supplier_element.get_text(strip=True))
                                            logger.info(f"Product supplier: {product_json_data['supplier']}")
                                    except Exception as e:
                                        logger.warning(f"Error extracting product supplier: {e}")

                                    # Extract product images (static extraction)
                                    try:
                                        main_image = product_page_html.select_one("#landingImage, img#imgTagWrapperId")
                                        if main_image and main_image.get("src"):
                                            product_json_data["image_url"] = main_image["src"]
                                            product_json_data["images"].append(main_image["src"])
                                        scripts = product_page_html.find_all("script", string=re.compile("colorImages"))
                                        for script in scripts:
                                            matches = re.findall(r'"large":"(https://[^"]+)"', script.string)
                                            product_json_data["images"].extend(matches)
                                        thumbs = product_page_html.select("#altImages .a-button-thumbnail img")
                                        for thumb in thumbs:
                                            if thumb.get("src"):
                                                hi_res_url = re.sub(r'\._(AC_SR\d+,\d+|SX\d+_SY\d+)_', '._AC_SL1500_', thumb["src"])
                                                if hi_res_url not in product_json_data["images"]:
                                                    product_json_data["images"].append(hi_res_url)
                                        product_json_data["images"] = list(set(product_json_data["images"]))[:5]
                                        logger.info(f"Product images: {product_json_data['images']}")
                                    except Exception as e:
                                        logger.warning(f"Error extracting product images: {e}")

                                    # Extract brand name
                                    try:
                                        if "Brand" in product_json_data["Specifications"]:
                                            product_json_data["brand_name"] = product_json_data["Specifications"]["Brand"]
                                        elif "brand" in product_json_data["Specifications"]:
                                            product_json_data["brand_name"] = product_json_data["Specifications"]["brand"]
                                        else:
                                            brand_elem = product_page_html.select_one("#bylineInfo")
                                            if brand_elem:
                                                brand_text = self.clean_text(brand_elem.get_text(strip=True))
                                                brand_match = re.search(r"(?:Visit|Brand:|by|from)\s+the\s+(.+?)\s+(?:Store|Brand|$)", brand_text, re.IGNORECASE)
                                                if brand_match:
                                                    product_json_data["brand_name"] = brand_match.group(1)
                                                else:
                                                    product_json_data["brand_name"] = brand_text
                                        logger.info(f"Brand name: {product_json_data['brand_name']}")
                                    except Exception as e:
                                        logger.warning(f"Error extracting brand name: {e}")

                                    # Extract origin
                                    try:
                                        if "Country of Origin" in product_json_data["Specifications"]:
                                            product_json_data["origin"] = product_json_data["Specifications"]["Country of Origin"]
                                        elif "country of origin" in product_json_data["Specifications"]:
                                            product_json_data["origin"] = product_json_data["Specifications"]["country of origin"]
                                        else:
                                            detail_bullets = product_page_html.select("ul.detail-bullet-list > li")
                                            for bullet in detail_bullets:
                                                text = self.clean_text(bullet.get_text(strip=True))
                                                match = re.search(r"Country of Origin:?\s*([^:]+?)(?:\.|\s|$)", text, re.IGNORECASE)
                                                if match:
                                                    product_json_data["origin"] = match.group(1).strip()
                                                    break
                                        logger.info(f"Origin: {product_json_data['origin']}")
                                    except Exception as e:
                                        logger.warning(f"Error extracting origin: {e}")

                                except Exception as e:
                                    logger.error(f"Error processing product page {product_json_data['url']}: {e}")
                                finally:
                                    self.browser.close()
                                    self.browser.switch_to.window(self.browser.window_handles[0])

                            # Save product if URL is valid
                            if product_json_data["url"] != "N/A":
                                self.scraped_products[product_json_data["url"]] = product_json_data
                                logger.info(f"Saved product: {product_json_data['url']}")

                        # Break out of the retry loop for the page if successful
                        break
                    except Exception as e:
                        logger.error(f"Attempt {attempt+1}/{self.retries}: Error scraping products from page {page}: {e}")
                        time.sleep(1)
                else:
                    logger.error(f"Failed to scrape products from page {page} after {self.retries} attempts.")
                    break
        except Exception as e:
            logger.error(f"Scraping error: {e}")
        return list(self.scraped_products.values())

    def save_results(self):
        """Save scraped data to JSON file and return results."""
        json_data = list(self.scraped_products.values())
        logger.info(f"Number of products scraped: {len(json_data)}")

        if not json_data:
            logger.warning("No products scraped.")
            return {
                "success": False,
                "error": "No products scraped",
                "data": [],
                "keyword": self.search_keyword,
                "pages_scraped": 0,
                "total_products": 0,
                "output_file": None
            }

        # Ensure data is JSON-serializable
        try:
            json.dumps(json_data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.error(f"Data is not JSON-serializable: {e}")
            return {
                "success": False,
                "error": f"Data is not JSON-serializable: {e}",
                "data": [],
                "keyword": self.search_keyword,
                "pages_scraped": 0,
                "total_products": len(json_data),
                "output_file": None
            }

        # Define primary and fallback output paths
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = sanitize_filename.sanitize(self.search_keyword.replace(" ", "_"))
        primary_path = Path(self.output_file) if self.output_file else Path.home() / "Desktop" / f"output_amazon_{safe_keyword}_{timestamp}.json"
        fallback_path = Path(tempfile.gettempdir()) / f"output_amazon_{safe_keyword}_{timestamp}.json"
        output_path = None

        # Try primary path
        try:
            output_dir = primary_path.parent
            logger.info(f"Attempting to save to primary path: {primary_path}")
            output_dir.mkdir(parents=True, exist_ok=True)
            if not os.access(output_dir, os.W_OK):
                raise PermissionError(f"No write permission for directory: {output_dir}")
            with open(primary_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=4)
            output_path = str(primary_path)
            logger.info(f"Successfully saved to primary path: {output_path}")
        except (PermissionError, OSError, IOError) as e:
            logger.warning(f"Failed to save to primary path {primary_path}: {e}")
            # Try fallback path
            try:
                output_dir = fallback_path.parent
                logger.info(f"Attempting to save to fallback path: {fallback_path}")
                output_dir.mkdir(parents=True, exist_ok=True)
                if not os.access(output_dir, os.W_OK):
                    raise PermissionError(f"No write permission for directory: {output_dir}")
                with open(fallback_path, "w", encoding="utf-8") as f:
                    json.dump(json_data, f, ensure_ascii=False, indent=4)
                output_path = str(fallback_path)
                logger.info(f"Successfully saved to fallback path: {output_path}")
            except (PermissionError, OSError, IOError) as e:
                logger.error(f"Failed to save to fallback path {fallback_path}: {e}")
                return {
                    "success": False,
                    "error": f"Failed to save file: {e}",
                    "data": [],
                    "keyword": self.search_keyword,
                    "pages_scraped": min(self.max_pages, len(json_data) // 20 + 1),
                    "total_products": len(json_data),
                    "output_file": None
                }

        # Verify saved file
        try:
            if output_path and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"JSON file verified: {output_path} (Size: {os.path.getsize(output_path)} bytes)")
                with open(output_path, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)
                if len(saved_data) != len(json_data):
                    logger.warning(f"Verification warning: Saved data has {len(saved_data)} items, expected {len(json_data)}")
            else:
                logger.warning(f"JSON file is empty or not created: {output_path}")
                output_path = None
        except Exception as e:
            logger.error(f"Error verifying JSON file: {e}")
            output_path = None

        return {
            "success": bool(output_path),
            "keyword": self.search_keyword,
            "pages_scraped": min(self.max_pages, len(json_data) // 20 + 1),
            "total_products": len(json_data),
            "output_file": output_path,
            "data": json_data
        }

    def close(self):
        """Close the WebDriver."""
        if self.browser:
            try:
                self.browser.quit()
                logger.info("Browser closed")
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            self.browser = None

@app.route('/api/scrape', methods=['POST'])
def scrape():
    content_type = request.headers.get('Content-Type', '')
    logger.info(f"Received request with Content-Type: {content_type}")

    keyword = None
    pages = 1

    if 'application/json' in content_type:
        try:
            data = request.get_json()
            if not data:
                logger.error("No JSON data provided")
                return jsonify({
                    "success": False,
                    "error": "No JSON data provided",
                    "data": [],
                    "keyword": None,
                    "pages_scraped": 0,
                    "total_products": 0,
                    "output_file": None
                }), 400
            keyword = data.get('keyword', '').strip()
            pages = data.get('pages', 1)
            logger.info(f"Parsed JSON input: keyword='{keyword}', pages={pages}")
        except Exception as e:
            logger.error(f"Error parsing JSON: {str(e)}")
            return jsonify({
                "success": False,
                "error": f"Invalid JSON format: {str(e)}",
                "data": [],
                "keyword": None,
                "pages_scraped": 0,
                "total_products": 0,
                "output_file": None
            }), 400
    elif 'application/x-www-form-urlencoded' in content_type or 'multipart/form-data' in content_type:
        try:
            keyword = request.form.get('keyword', '').strip()
            pages_str = request.form.get('pages', '1').strip()
            try:
                pages = int(pages_str)
            except ValueError:
                logger.error(f"Invalid pages value: {pages_str}")
                return jsonify({
                    "success": False,
                    "error": "Pages must be a valid integer",
                    "data": [],
                    "keyword": keyword,
                    "pages_scraped": 0,
                    "total_products": 0,
                    "output_file": None
                }), 400
            logger.info(f"Parsed form input: keyword='{keyword}', pages={pages}")
        except Exception as e:
            logger.error(f"Error parsing form data: {str(e)}")
            return jsonify({
                "success": False,
                "error": f"Invalid form data: {str(e)}",
                "data": [],
                "keyword": None,
                "pages_scraped": 0,
                "total_products": 0,
                "output_file": None
            }), 400
    else:
        logger.error(f"Unsupported Content-Type: {content_type}")
        return jsonify({
            "success": False,
            "error": "Unsupported Content-Type. Use application/json, application/x-www-form-urlencoded, or multipart/form-data",
            "data": [],
            "keyword": None,
            "pages_scraped": 0,
            "total_products": 0,
            "output_file": None
        }), 415

    if not keyword:
        logger.error("Keyword is required")
        return jsonify({
            "success": False,
            "error": "Keyword is required",
            "data": [],
            "keyword": None,
            "pages_scraped": 0,
            "total_products": 0,
            "output_file": None
        }), 400

    try:
        pages = int(pages)
        if pages < 1 or pages > 20:
            logger.error(f"Invalid pages value: {pages}")
            return jsonify({
                "success": False,
                "error": "Pages must be a number between 1 and 20",
                "data": [],
                "keyword": keyword,
                "pages_scraped": 0,
                "total_products": 0,
                "output_file": None
            }), 400
    except (ValueError, TypeError):
        logger.error(f"Invalid pages type: {pages}")
        return jsonify({
            "success": False,
            "error": "Pages must be a valid integer",
            "data": [],
            "keyword": keyword,
            "pages_scraped": 0,
            "total_products": 0,
            "output_file": None
        }), 400

    logger.info(f"Starting scrape for keyword: '{keyword}', pages: {pages}")

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = sanitize_filename.sanitize(keyword.replace(" ", "_"))
        output_file = str(Path.home() / "Desktop" / f"output_amazon_{safe_keyword}_{timestamp}.json")

        scraper = AmazonScraper(keyword, pages, output_file)
        try:
            products = scraper.scrape_products()
            result = scraper.save_results()
        finally:
            scraper.close()

        response = app.response_class(
            response=json.dumps(result, ensure_ascii=False),
            status=200 if result["success"] else 500,
            mimetype='application/json'
        )
        logger.info(f"Scrape completed: {result['total_products']} products saved to {result.get('output_file', 'None')}")
        return response
    except Exception as e:
        logger.error(f"Scraping failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Scraping failed: {str(e)}",
            "data": [],
            "keyword": keyword,
            "pages_scraped": 0,
            "total_products": 0,
            "output_file": None
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
        import flask
        import flask_cors
        import selenium
        import bs4
        import webdriver_manager
        import sanitize_filename
        logger.info("All required Python packages are installed.")
        return True
    except ImportError as e:
        logger.error(f"Missing required packages: {e}. Install them using: pip install flask flask-cors selenium beautifulsoup4 webdriver-manager sanitize-filename")
        return False

if __name__ == "__main__":
    app.start_time = time.time()
    if not check_dependencies():
        logger.error("Server started but dependencies are missing. Some features may not work.")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)