from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import re
import time
import json
import os
import random
import logging
import subprocess
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from collections import OrderedDict

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Track app start time for uptime
APP_START_TIME = time.time()

# Configure logging to console only (no log files)
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for detailed diagnostics
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class AmazonScraper:
    def _init_(self, search_keyword, max_pages=1):
        self.search_keyword = search_keyword.strip()[:100]
        # Auto-correct typo
        if "sauvaeg" in self.search_keyword.lower():
            logger.warning(f"Correcting typo: '{self.search_keyword}' to 'Dior Sauvage'")
            self.search_keyword = "Dior Sauvage"
        self.max_pages = min(max_pages, 10)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = self.search_keyword.replace(" ", "_").lower()
        self.output_file = f"amazon_{safe_keyword}_{timestamp}.json"
        self.retries = 3
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36"
        ]
        self.browser = self._setup_browser()
        self.scraped_data = []
        self._cached_selectors = {
            'product': ["div[data-component-type='s-search-result']", "div.s-result-item.s-asin"],
            'title': ["h2 a.a-link-normal.s-title", "span.a-size-medium.a-color-base.a-text-normal"],
            'url': ["a.a-link-normal.s-no-outline", "a.a-link-normal.s-title"],
            'price': ["span.a-price-whole", "span.a-price span.a-offscreen"],
            'currency': ["span.a-price-symbol"],
            'mrp': ["span.a-price.a-text-price span.a-offscreen", "span.a-color-base.a-text-strike"],
            'discount': ["span.savingsPercentage", "span.a-color-price"],
            'image': ["img.s-image"],
            'rating': ["span.a-icon-alt", "i.a-icon-star"],
            'review': ["span.a-size-base.s-review-count", "a.a-link-normal span.a-size-base"],
            'supplier': ["a#sellerProfileTriggerId", "span.tabular-buybox-text"],
            'pagination': ["span.s-pagination-strip", "div.s-pagination-container"],
            'next_page': ["a.s-pagination-next", "a.s-pagination-item.s-pagination-button"]
        }

    def _setup_browser(self):
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--ignore-certificate-errors")
            options.add_argument("--log-level=3")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--headless=new")
            options.add_argument(f"user-agent={random.choice(self.user_agents)}")
            options.add_argument("--window-size=1920,1080")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(30)
            logger.info("Browser initialized successfully")
            return driver
        except WebDriverException as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def _del_(self):
        self.close_browser()

    def close_browser(self):
        try:
            if self.browser:
                for handle in self.browser.window_handles[1:]:
                    try:
                        self.browser.switch_to.window(handle)
                        self.browser.close()
                    except:
                        pass
                self.browser.switch_to.window(self.browser.window_handles[0])
                self.browser.quit()
                self.browser = None
                logger.info("Browser closed successfully")
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")

    def rotate_user_agent(self):
        try:
            user_agent = random.choice(self.user_agents)
            self.browser.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": user_agent
            })
            logger.debug(f"Rotated user agent to: {user_agent}")
        except Exception as e:
            logger.warning(f"Failed to rotate user agent: {e}")

    def clean_text(self, text):
        if not text:
            return "N/A"
        cleaned = re.sub(r'[\u2000-\u200F\u2028-\u202F]+', '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = re.sub(r'\[U\+[0-9A-Fa-f]+\]', '', cleaned)
        return cleaned.strip()[:500]

    def create_product_data(self):
        return OrderedDict([
            ("url", "N/A"),
            ("title", "N/A"),
            ("currency", "N/A"),
            ("exact_price", "N/A"),
            ("mrp", "N/A"),
            ("description", {"features": [], "technical_specs": {}}),
            ("min_order", "1 unit"),
            ("supplier", "N/A"),
            ("origin", "N/A"),
            ("feedback", {"rating": "N/A", "review": "N/A"}),
            ("image_url", "N/A"),
            ("images", []),
            ("videos", []),
            ("specifications", {}),
            ("website_name", "Amazon"),
            ("discount_information", "N/A"),
            ("brand_name", "N/A"),
            ("scraped_at", datetime.now().isoformat())
        ])

    def extract_product_description(self, product_page_html):
        description = {"features": [], "technical_specs": {}}
        try:
            # Feature-bullets
            feature_bullets = product_page_html.select_one("#feature-bullets ul")
            if feature_bullets:
                features = [self.clean_text(li.get_text(strip=True)) for li in feature_bullets.select("li")]
                if features:
                    description["features"].extend([f"- {f}" for f in features if f != "N/A"])
            
            # Module-9 sections
            module_9_sections = product_page_html.find_all("div", {"class": "aplus-module module-9"})
            for section in module_9_sections:
                flex_items = section.find_all("div", {"class": "apm-flex-item-third-width"})
                for item in flex_items:
                    try:
                        heading = self.clean_text(item.find("h4").get_text(strip=True)) if item.find("h4") else "N/A"
                        feature_text = f"{heading}\n" if heading != "N/A" else ""
                        for p in item.find_all("p"):
                            p_text = self.clean_text(p.get_text(strip=True))
                            if p_text != "N/A":
                                feature_text += f"{p_text}\n"
                        for ul in item.find_all("ul", {"class": "a-unordered-list"}):
                            for li in ul.find_all("li"):
                                li_text = self.clean_text(li.get_text(strip=True))
                                if li_text != "N/A":
                                    feature_text += f"- {li_text}\n"
                        if feature_text.strip():
                            description["features"].append(feature_text.strip())
                    except Exception as e:
                        logger.debug(f"Error extracting module-9 feature: {e}")

            # Technical specs
            tech_specs_table = product_page_html.find("table", {"class": "aplus-tech-spec-table"})
            if tech_specs_table:
                for row in tech_specs_table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) == 2:
                        key = self.clean_text(cells[0].get_text(strip=True))
                        value = self.clean_text(cells[1].get_text(strip=True))
                        if key != "N/A" and value != "N/A":
                            description["technical_specs"][key] = value
        except Exception as e:
            logger.debug(f"Error extracting description: {e}")
        return description

    def get_total_pages(self, soup):
        try:
            for selector in self._cached_selectors['pagination']:
                pagination = soup.select_one(selector)
                if pagination:
                    text = pagination.get_text(strip=True)
                    page_match = re.search(r'Page \d+ of (\d+)', text) or re.search(r'of (\d+)', text)
                    if page_match:
                        return int(page_match.group(1))
            logger.debug("No pagination info found, assuming 1 page")
            return 1
        except Exception as e:
            logger.debug(f"Error extracting total pages: {e}")
            return 1

    def scrape_products(self):
        scraped_products = {}
        pages_scraped = 0
        try:
            for page in range(1, self.max_pages + 1):
                for attempt in range(self.retries):
                    try:
                        self.rotate_user_agent()
                        search_url = f"https://www.amazon.in/s?k={self.search_keyword.replace(' ', '+')}&page={page}"
                        logger.info(f"Scraping page {page}/{self.max_pages}: {search_url}")
                        self.browser.get(search_url)
                        WebDriverWait(self.browser, 15).until(
                            lambda d: d.execute_script("return document.readyState") == "complete"
                        )
                        time.sleep(random.uniform(1, 3))

                        # Check for CAPTCHA
                        if any(x in self.browser.current_url.lower() or x in self.browser.page_source.lower() for x in ["captcha", "verify", "robot"]):
                            logger.warning(f"CAPTCHA detected on page {page}, attempt {attempt + 1}")
                            if attempt == self.retries - 1:
                                logger.error(f"Max CAPTCHA retries reached for page {page}")
                                break
                            time.sleep(random.uniform(5, 10))
                            continue

                        # Cache page source
                        page_source = self.browser.page_source
                        soup = BeautifulSoup(page_source, "html.parser")

                        # Get total pages
                        total_pages = self.get_total_pages(soup)
                        logger.info(f"Detected {total_pages} total pages for query")
                        if page > total_pages:
                            logger.info(f"Page {page} exceeds total pages ({total_pages}). Stopping pagination.")
                            break

                        # Find product cards
                        products = []
                        for selector in self._cached_selectors['product']:
                            try:
                                WebDriverWait(self.browser, 10).until(
                                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector))
                                )
                                products.extend(self.browser.find_elements(By.CSS_SELECTOR, selector))
                            except TimeoutException:
                                logger.debug(f"No elements found for selector {selector} on page {page}")
                                continue

                        if not products:
                            logger.warning(f"No products found on page {page}")
                            break

                        logger.info(f"Found {len(products)} product cards on page {page}")

                        for product_elem in products:
                            for retry in range(self.retries):
                                try:
                                    # Cache product HTML
                                    product_html = product_elem.get_attribute("outerHTML")
                                    product_soup = BeautifulSoup(product_html, "html.parser")

                                    product_data = self.create_product_data()

                                    # Extract URL
                                    url = "N/A"
                                    for selector in self._cached_selectors['url']:
                                        if url_elem := product_soup.select_one(selector):
                                            href = url_elem.get("href", None)
                                            if href:
                                                url = f"https://www.amazon.in{href}" if href.startswith("/") else href
                                                product_data["url"] = url
                                                break

                                    if url == "N/A" or url in scraped_products:
                                        logger.debug(f"Invalid or duplicate URL on page {page}: {url}")
                                        break

                                    # Extract title
                                    title = "N/A"
                                    for selector in self._cached_selectors['title']:
                                        if title_elem := product_soup.select_one(selector):
                                            title = self.clean_text(title_elem.get_text(strip=True))
                                            if title != "N/A":
                                                product_data["title"] = title
                                                break

                                    if title == "N/A":
                                        logger.warning(f"No title found for product on page {page}")
                                        break

                                    scraped_products[url] = product_data

                                    # Open product page
                                    try:
                                        self.browser.execute_script("window.open('');")
                                        self.browser.switch_to.window(self.browser.window_handles[-1])
                                        self.browser.get(url)
                                        WebDriverWait(self.browser, 15).until(
                                            lambda d: d.execute_script("return document.readyState") == "complete"
                                        )
                                        time.sleep(random.uniform(1, 2))
                                        product_page_html = BeautifulSoup(self.browser.page_source, "html.parser")

                                        # Title (more reliable from product page)
                                        try:
                                            title_elem = WebDriverWait(self.browser, 5).until(
                                                EC.presence_of_element_located((By.ID, "productTitle"))
                                            )
                                            product_data["title"] = self.clean_text(title_elem.text)
                                        except Exception as e:
                                            logger.debug(f"Error extracting product title: {e}")

                                        # Price and currency
                                        try:
                                            price_whole = self.browser.find_element(By.CSS_SELECTOR, "span.a-price-whole")
                                            price_fraction = self.browser.find_element(By.CSS_SELECTOR, "span.a-price-fraction")
                                            currency_symbol = self.browser.find_element(By.CSS_SELECTOR, "span.a-price-symbol")
                                            product_data["exact_price"] = self.clean_text(price_whole.text + price_fraction.text).replace(",", "")
                                            product_data["currency"] = self.clean_text(currency_symbol.text)
                                        except Exception as e:
                                            logger.debug(f"Error extracting price: {e}")

                                        # MRP
                                        try:
                                            mrp_elem = product_page_html.select_one(self._cached_selectors['mrp'][0])
                                            if mrp_elem:
                                                mrp_text = self.clean_text(mrp_elem.get_text(strip=True))
                                                product_data["mrp"] = re.sub(r'[^\d.]', '', mrp_text) if mrp_text != "N/A" else "N/A"
                                        except Exception as e:
                                            logger.debug(f"Error extracting MRP: {e}")

                                        # Discount
                                        try:
                                            discount_elem = product_page_html.select_one(self._cached_selectors['discount'][0])
                                            if discount_elem:
                                                product_data["discount_information"] = self.clean_text(discount_elem.get_text(strip=True))
                                            elif product_data["mrp"] != "N/A" and product_data["exact_price"] != "N/A":
                                                try:
                                                    current_price = float(product_data["exact_price"])
                                                    mrp_value = float(product_data["mrp"])
                                                    if mrp_value > current_price:
                                                        discount_percentage = ((mrp_value - current_price) / mrp_value) * 100
                                                        product_data["discount_information"] = f"{discount_percentage:.2f}% off"
                                                except ValueError:
                                                    pass
                                        except Exception as e:
                                            logger.debug(f"Error extracting discount: {e}")

                                        # Description
                                        product_data["description"] = self.extract_product_description(product_page_html)

                                        # Specifications
                                        specifications = {}
                                        try:
                                            detail_lists = product_page_html.select("ul.detail-bullet-list > li")
                                            for li in detail_lists:
                                                label_tag = li.select_one("span.a-text-bold")
                                                value_tag = label_tag.find_next_sibling("span") if label_tag else None
                                                if label_tag and value_tag:
                                                    label = self.clean_text(label_tag.get_text(strip=True).replace(":", ""))
                                                    value = self.clean_text(value_tag.get_text(" ", strip=True))
                                                    if label != "N/A" and value != "N/A":
                                                        specifications[label] = value
                                            specs_tables = product_page_html.select("table#productDetails_techSpec_section_1, table#productDetails_detailBullets_sections1")
                                            for table in specs_tables:
                                                for row in table.find_all("tr"):
                                                    label = row.find("th")
                                                    value = row.find("td")
                                                    if label and value:
                                                        label_text = self.clean_text(label.get_text(strip=True))
                                                        value_text = self.clean_text(value.get_text(strip=True))
                                                        if label_text != "N/A" and value_text != "N/A":
                                                            specifications[label_text] = value_text
                                            product_data["specifications"] = specifications
                                        except Exception as e:
                                            logger.debug(f"Error extracting specifications: {e}")

                                        # Rating and reviews
                                        try:
                                            rating_elem = self.browser.find_element(By.ID, "acrPopover")
                                            rating_text = rating_elem.get_attribute("title")
                                            if rating_text:
                                                rating_match = re.search(r"(\d+(?:\.\d+)?)", rating_text)
                                                if rating_match:
                                                    product_data["feedback"]["rating"] = rating_match.group(1)
                                            reviews_elem = self.browser.find_element(By.ID, "acrCustomerReviewText")
                                            if reviews_elem:
                                                reviews_text = reviews_elem.text
                                                reviews_match = re.search(r"(\d+(?:,\d+)*)", reviews_text)
                                                if reviews_match:
                                                    product_data["feedback"]["review"] = reviews_match.group(1).replace(",", "")
                                        except Exception as e:
                                            logger.debug(f"Error extracting rating/reviews: {e}")

                                        # Supplier
                                        try:
                                            supplier_elem = product_page_html.select_one(self._cached_selectors['supplier'][0])
                                            if supplier_elem:
                                                product_data["supplier"] = self.clean_text(supplier_elem.get_text(strip=True))
                                        except Exception as e:
                                            logger.debug(f"Error extracting supplier: {e}")

                                        # Images
                                        try:
                                            main_image = self.browser.find_element(By.ID, "landingImage")
                                            if main_image:
                                                product_data["image_url"] = main_image.get_attribute("src") or "N/A"
                                                product_data["images"].append(product_data["image_url"])
                                            alt_images = WebDriverWait(self.browser, 5).until(
                                                EC.presence_of_element_located((By.ID, "altImages"))
                                            )
                                            img_buttons = alt_images.find_elements(By.CSS_SELECTOR, "li.imageThumbnail")
                                            for idx, img_button in enumerate(img_buttons[1:], 1):
                                                try:
                                                    img_button.click()
                                                    time.sleep(0.5)
                                                    current_image = self.browser.find_element(By.ID, "landingImage")
                                                    img_url = current_image.get_attribute("src")
                                                    if img_url and img_url not in product_data["images"]:
                                                        product_data["images"].append(img_url)
                                                except Exception as e:
                                                    logger.debug(f"Error getting image {idx}: {e}")
                                        except Exception as e:
                                            logger.debug(f"Error extracting images: {e}")

                                        # Brand
                                        try:
                                            product_data["brand_name"] = specifications.get("Brand", specifications.get("brand_name", "N/A"))
                                        except Exception as e:
                                            logger.debug(f"Error extracting brand: {e}")

                                        logger.info(f"Successfully scraped product: {product_data['title']}")
                                    except Exception as e:
                                        logger.error(f"Error processing product page {url}: {e}")
                                    finally:
                                        if len(self.browser.window_handles) > 1:
                                            try:
                                                self.browser.close()
                                                self.browser.switch_to.window(self.browser.window_handles[0])
                                            except Exception as e:
                                                logger.debug(f"Error switching windows: {e}")

                                    time.sleep(random.uniform(0.3, 0.8))
                                    break
                                except StaleElementReferenceException:
                                    logger.warning(f"Stale element for product on page {page}, retry {retry + 1}")
                                    if retry == self.retries - 1:
                                        logger.error(f"Max retries for stale element on page {page}")
                                        break
                                    time.sleep(1)
                                    continue
                                except Exception as e:
                                    logger.error(f"Error processing product on page {page}: {e}")
                                    break

                        pages_scraped += 1

                        # Pagination
                        next_page_found = False
                        for selector in self._cached_selectors['next_page']:
                            try:
                                next_button = WebDriverWait(self.browser, 5).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                                )
                                next_page_found = True
                                self.browser.execute_script("arguments[0].click();", next_button)
                                time.sleep(random.uniform(1, 3))
                                break
                            except (TimeoutException, NoSuchElementException):
                                logger.debug(f"Next page button not found for selector {selector} on page {page}")
                                continue

                        if not next_page_found:
                            logger.info(f"No next page button found on page {page}. Stopping pagination.")
                            break

                        if not products or len(products) < 5:
                            logger.info(f"Few or no products on page {page}. Stopping pagination.")
                            break

                        break
                    except TimeoutException:
                        logger.error(f"Timeout on page {page}, attempt {attempt + 1}")
                        if attempt == self.retries - 1:
                            break
                        time.sleep(5 * (attempt + 1))
                    except Exception as e:
                        logger.error(f"Attempt {attempt + 1} failed for page {page}: {e}")
                        if attempt == self.retries - 1:
                            break
                        time.sleep(5 * (attempt + 1))

                if not products and page > 1:
                    logger.info(f"No products found after page {page}. Stopping pagination.")
                    break

        except Exception as e:
            logger.error(f"Fatal error in scrape_products: {e}")
        finally:
            self.close_browser()

        # Convert scraped_products dict to list before returning
        self.scraped_data = list(scraped_products.values())
        return self.scraped_data

    def save_results(self):
        try:
            # Ensure we have data to save
            if not self.scraped_data:
                logger.warning("No data to save")
                return OrderedDict([
                    ("success", False),
                    ("error", "No products were scraped"),
                    ("data", [])
                ])
                
            # Prepare data for saving
            data_to_save = OrderedDict([
                ("keyword", self.search_keyword),
                ("scraped_at", datetime.now().isoformat()),
                ("products", self.scraped_data),
                ("count", len(self.scraped_data))
            ])
            
            # Write data to file with proper encoding
            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
                
            logger.info(f"Data saved successfully to {self.output_file} ({len(self.scraped_data)} products)")
            
            # Return success response with summarized data
            return OrderedDict([
                ("success", True),
                ("keyword", self.search_keyword),
                ("pages_scraped", len(set(p.get("url", "N/A") for p in self.scraped_data if p.get("url", "N/A") != "N/A"))),
                ("total_products", len(self.scraped_data)),
                ("output_file", self.output_file),
                ("data", self.scraped_data)
            ])
        except Exception as e:
            logger.error(f"Error saving results: {str(e)}")
            return OrderedDict([
                ("success", False),
                ("error", f"Error saving data: {str(e)}"),
                ("data", self.scraped_data if self.scraped_data else [])
            ])

@app.route('/api/scrape', methods=['POST'])
def scrape():
    content_type = request.headers.get('Content-Type', '')
    if 'application/json' in content_type:
        try:
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "Invalid or missing JSON data"}), 400
            keyword = data.get('keyword', '').strip()
            pages = data.get('pages', 1)
        except Exception as e:
            logger.error(f"Error parsing JSON: {str(e)}")
            return jsonify({"success": False, "error": "Invalid JSON format"}), 400
    elif 'application/x-www-form-urlencoded' in content_type or 'multipart/form-data' in content_type:
        try:
            keyword = request.form.get('keyword', '').strip()
            pages_str = request.form.get('pages', '1').strip()
            try:
                pages = int(pages_str)
            except ValueError:
                return jsonify({"success": False, "error": "Pages must be a valid integer"}), 400
        except Exception as e:
            logger.error(f"Error parsing form data: {str(e)}")
            return jsonify({"success": False, "error": "Invalid form data"}), 400
    else:
        return jsonify({"success": False, "error": "Unsupported Content-Type"}), 415

    if not keyword:
        return jsonify({"success": False, "error": "Keyword is required"}), 400

    if not isinstance(pages, int) or pages < 1 or pages > 10:
        return jsonify({"success": False, "error": "Pages must be an integer between 1 and 10"}), 400

    logger.info(f"Scraping for keyword: '{keyword}', pages: {pages}")

    try:
        scraper = AmazonScraper(keyword, pages)
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
    return jsonify(OrderedDict([
        ("status", "ok"),
        ("timestamp", datetime.now().isoformat()),
        ("uptime", time.time() - APP_START_TIME)
    ])), 200

def check_dependencies():
    try:
        result = subprocess.run(['python', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception("Python is not installed")
        
        required_packages = ['flask', 'flask-cors', 'selenium', 'beautifulsoup4', 'webdriver-manager']
        for pkg in required_packages:
            result = subprocess.run(['pip3', 'show', pkg], capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"Required Python package {pkg} is missing. Install it using: pip3 install {pkg}")
        
        logger.info("Dependencies verified successfully")
        return True
    except Exception as e:
        logger.error(f"Dependency check failed: {e}")
        return False

if __name__ == '_main_':
    if not check_dependencies():
        logger.error("Server started but dependencies are missing. Some features may not work.")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))