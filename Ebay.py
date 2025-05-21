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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from collections import OrderedDict
import sanitize_filename

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure logging to console only
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class eBayScraper:
    def _init_(self, search_keyword, max_pages=10, output_file=None):
        self.search_keyword = search_keyword
        self.max_pages = max_pages
        self.output_file = output_file
        self.retries = 3
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ]
        self.browser = self._setup_browser()
        self.scraped_data = []

    def _setup_browser(self):
        """Configure and return a Selenium WebDriver instance"""
        options = webdriver.ChromeOptions()
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--log-level=3")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--headless=new")
        options.add_argument(f"user-agent={random.choice(self.user_agents)}")
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.maximize_window()
            logger.info("Chrome WebDriver initialized successfully.")
            return driver
        except WebDriverException as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def rotate_user_agent(self):
        """Change the user agent to avoid detection"""
        try:
            user_agent = random.choice(self.user_agents)
            self.browser.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": user_agent
            })
            logger.info(f"Rotated user agent to: {user_agent}")
        except Exception as e:
            logger.warning(f"Failed to rotate user agent: {e}")

    def retry_extraction(self, func, attempts=3, delay=2, default="N/A"):
        """Retries an extraction function up to 'attempts' times."""
        for i in range(attempts):
            try:
                result = func()
                if result:
                    return result
            except Exception as e:
                if i < attempts - 1:
                    time.sleep(delay)
        return default

    def create_product_data(self):
        """Create an ordered dictionary with field order preserved"""
        return OrderedDict([
            ("url", "N/A"),
            ("title", "N/A"),
            ("currency", "N/A"),
            ("exact_price", "N/A"),
            ("description", "N/A"),
            ("min_order", "1 unit"),
            ("supplier", "N/A"),
            ("feedback", {"rating": "N/A", "review": "N/A"}),
            ("image_url", "N/A"),
            ("images", []),
            ("videos", []),
            ("specifications", {}),
            ("website_name", "eBay"),
            ("discount_information", "N/A"),
            ("brand_name", "N/A"),
            ("origin", "N/A")
        ])

    def scroll_to_element(self, css_selector):
        """Scroll to element to ensure visibility"""
        try:
            element = self.browser.find_element(By.CSS_SELECTOR, css_selector)
            self.browser.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
            time.sleep(random.uniform(0.5, 1))
            return element
        except Exception as e:
            logger.warning(f"Error scrolling to element {css_selector}: {e}")
            return None

    def scrape_products(self):
        """Main scraping function"""
        scraped_products = {}
        try:
            for page in range(1, self.max_pages + 1):
                for attempt in range(self.retries):
                    try:
                        self.rotate_user_agent()
                        search_url = f"https://www.ebay.com/sch/i.html?_nkw={self.search_keyword.replace(' ', '+')}&_sacat=0&_from=R40&_pgn={page}"
                        logger.info(f"Scraping page {page}/{self.max_pages}: {search_url}")
                        self.browser.get(search_url)
                        WebDriverWait(self.browser, 15).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "ul.srp-results li.s-item"))
                        )
                        time.sleep(random.uniform(1, 2))

                        # Scroll to load dynamic content
                        last_height = self.browser.execute_script("return document.body.scrollHeight")
                        for _ in range(3):
                            self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                            time.sleep(random.uniform(1, 2))
                            new_height = self.browser.execute_script("return document.body.scrollHeight")
                            if new_height == last_height:
                                break
                            last_height = new_height

                        product_cards = self.browser.find_elements(By.CSS_SELECTOR, "ul.srp-results li.s-item.s-item__pl-on-bottom")
                        if not product_cards:
                            logger.warning(f"No products found on page {page}")
                            break
                        logger.info(f"Found {len(product_cards)} products on page {page}")

                        for product in product_cards:
                            product_data = self.create_product_data()
                            try:
                                # Extract URL and title
                                product_title_url = self.retry_extraction(
                                    lambda: product.find_element(By.CSS_SELECTOR, "a.s-item__link"),
                                    default=None
                                )
                                if product_title_url:
                                    product_data["title"] = self.retry_extraction(
                                        lambda: product_title_url.find_element(By.CSS_SELECTOR, "div.s-item__title").text.strip()
                                    )
                                    product_data["url"] = product_title_url.get_attribute("href").split('?')[0]
                                if not product_data["url"] or not product_data["url"].startswith("https://www.ebay.com/itm/"):
                                    logger.warning(f"Invalid URL: {product_data['url']}")
                                    continue
                            #     if product_data["url"] in scraped_products:
                            #         logger.info(f"Skipping duplicate product: {product_data['url']}")
                            #         continue
                            #     if self.search_keyword.lower() not in product_data["title"].lower():
                            #         logger.info(f"Skipping non-matching product: {product_data['title']}")
                            #         continue

                                # Extract price
                                price_element = self.retry_extraction(
                                    lambda: product.find_element(By.CSS_SELECTOR, "div[data-testid='x-price-primary'] span.ux-textspans").text.strip(),
                                    default=""
                                )
                                if price_element:
                                    currency_match = re.match(r"([A-Z]{2,})\s?\$", price_element)
                                    price_match = re.search(r"[\d,.]+", price_element)
                                    product_data["currency"] = currency_match.group(1).strip() if currency_match else "N/A"
                                    product_data["exact_price"] = price_match.group(0).replace(",", "") if price_match else "N/A"

                                # Extract origin
                                product_data["origin"] = self.retry_extraction(
                                    lambda: product.find_element(By.CSS_SELECTOR, "span.s-item__location").text.replace("from ", "").strip(),
                                    default="N/A"
                                )

                                # Open product page
                                self.browser.execute_script("window.open('');")
                                self.browser.switch_to.window(self.browser.window_handles[-1])
                                self.browser.get(product_data["url"])
                                WebDriverWait(self.browser, 15).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.ux-layout-section-evo"))
                                )
                                time.sleep(random.uniform(1, 2))
                                product_page_html = BeautifulSoup(self.browser.page_source, "html.parser")

                                # Re-extract price
                                price_element = self.retry_extraction(
                                    lambda: product_page_html.find("div", {"class": "x-price-primary"}).find("span", {"class": "ux-textspans"}).get_text(strip=True)
                                    if product_page_html.find("div", {"class": "x-price-primary"}) else "",
                                    default=""
                                )
                                if price_element:
                                    currency_match = re.match(r"([A-Z]{2,})\s?\$", price_element)
                                    price_match = re.search(r"[\d,.]+", price_element)
                                    product_data["currency"] = currency_match.group(1).strip() if currency_match else product_data["currency"]
                                    product_data["exact_price"] = price_match.group(0).replace(",", "") if price_match else product_data["exact_price"]

                                # Extract description
                                product_data["description"] = self.retry_extraction(
                                    lambda: product_page_html.find("div", {"id": "viTabs_0_is"}).get_text(strip=True) if product_page_html.find("div", {"id": "viTabs_0_is"}) else "",
                                    default="N/A"
                                )

                                # Extract supplier
                                product_data["supplier"] = self.retry_extraction(
                                    lambda: product_page_html.find("div", class_=re.compile(r"x-sellercard-atf_info_about-seller"))
                                        .find("a", href=re.compile(r'https://www.ebay.com/str/'))
                                        .find("span", class_="ux-textspans--BOLD").get_text(strip=True)
                                        if product_page_html.find("div", class_=re.compile(r"x-sellercard-atf_info_about-seller")) else "",
                                    default="N/A"
                                )
                                if not product_data["supplier"]:
                                    product_data["supplier"] = self.retry_extraction(
                                        lambda: next(
                                            (json.loads(a.get("data-clientpresentationmetadata")).get("_ssn", "")
                                             for a in product_page_html.find_all("a", href=re.compile(r'https://www.ebay.com/str/'))
                                             if a.get("data-clientpresentationmetadata") and json.loads(a.get("data-clientpresentationmetadata")).get("_ssn")),
                                            "N/A"
                                        ),
                                        default="N/A"
                                    )

                                # Extract feedback
                                feedback_container = product_page_html.find("div", class_="x-sellercard-atf_info_about-seller")
                                if feedback_container:
                                    product_data["feedback"]["rating"] = self.retry_extraction(
                                        lambda: feedback_container.find("span", class_="ux-textspans ux-textspans--BOLD").get_text(strip=True),
                                        default="N/A"
                                    )
                                    review_text = self.retry_extraction(
                                        lambda: feedback_container.find("span", class_="ux-textspans ux-textspans--SECONDARY").get_text(strip=True),
                                        default=""
                                    )
                                    review_match = re.search(r'\(?(\d[\d,]*)\)?', review_text)
                                    product_data["feedback"]["review"] = review_match.group(1).replace(",", "") if review_match else "N/A"

                                # Extract images
                                image_urls = set()
                                carousel_items = product_page_html.find_all("div", {"class": "ux-image-carousel-item"})
                                for item in carousel_items:
                                    img_tag = item.find("img")
                                    if img_tag:
                                        for attr in ["src", "data-zoom-src", "srcset"]:
                                            src = self.retry_extraction(lambda: img_tag.get(attr), default="")
                                            if src:
                                                if attr == "srcset":
                                                    image_urls.update(url.split(" ")[0] for url in src.split(",") if url.strip())
                                                else:
                                                    image_urls.add(src)
                                product_data["images"] = sorted(list(image_urls), key=lambda x: int(re.search(r's-l(\d+)', x).group(1)) if re.search(r's-l(\d+)', x) else 0, reverse=True)
                                product_data["image_url"] = product_data["images"][0] if product_data["images"] else "N/A"

                                # Extract dimensions
                                dim_regex = r'\b(?:About\s*)?\d+(\.\d+)?\s*(cm|in|inches|centimeters|mm)\b|' + \
                                            r'\b\d+(\.\d+)?\s*x\s*\d+(\.\d+)?\s*(inch|in)\b'
                                dimensions = []
                                spec_table = product_page_html.find("div", {"class": "ux-layout-section-evo"})
                                if spec_table:
                                    labels = spec_table.find_all("div", {"class": "ux-labels-values__labels"})
                                    for label in labels:
                                        label_text = label.get_text(strip=True).lower()
                                        if any(key in label_text for key in ["size", "dimensions"]):
                                            value_container = label.find_parent().find_next_sibling("div", {"class": "ux-labels-values__values"})
                                            if value_container:
                                                span = value_container.find("span", {"class": "ux-textspans"})
                                                if span:
                                                    dim_text = self.retry_extraction(lambda: span.get_text(strip=True), default="")
                                                    if dim_text:
                                                        matches = re.finditer(dim_regex, dim_text, re.IGNORECASE)
                                                        for match in matches:
                                                            dim_value = match.group(0)
                                                            dimensions.append({"context": f"{label_text}: {dim_text}", "dimension": dim_value})
                                product_data["dimensions"] = "; ".join([f"{dim['context']} ({dim['dimension']})" for dim in dimensions]) if dimensions else "N/A"

                                # Extract specifications
                                item_specifics_xpath = "//div[@id='viTabs_0_is']//dl[@data-testid='ux-labels-values']"
                                specs = self.browser.find_elements(By.XPATH, item_specifics_xpath)
                                specifications = {}
                                for spec in specs:
                                    try:
                                        key = self.retry_extraction(lambda: spec.find_element(By.XPATH, ".//dt").text.strip(), default="")
                                        value = self.retry_extraction(lambda: spec.find_element(By.XPATH, ".//dd").text.strip(), default="")
                                        if key and value:
                                            specifications[key] = value
                                    except Exception:
                                        continue
                                product_data["specifications"] = specifications

                                # Extract discount information
                                original_price_elem = product_page_html.find("span", {"class": "ux-textspans--STRIKETHROUGH"})
                                if original_price_elem:
                                    original_price = original_price_elem.get_text(strip=True)
                                    try:
                                        original_val = float(original_price.replace(product_data["currency"], "").replace(",", "").strip())
                                        current_val = float(product_data["exact_price"])
                                        if original_val > current_val:
                                            discount_percentage = ((original_val - current_val) / original_val) * 100
                                            product_data["discount_information"] = f"{discount_percentage:.2f}% off"
                                    except ValueError:
                                        pass
                                else:
                                    discount_elem = product_page_html.find("span", {"class": "ux-textspans ux-textspans--EMPHASIS"})
                                    product_data["discount_information"] = discount_elem.get_text(strip=True).strip('()') if discount_elem else "N/A"

                                # Extract brand name
                                product_data["brand_name"] = specifications.get("Brand", "N/A")
                                if not product_data["brand_name"] or product_data["brand_name"] == "N/A":
                                    brand_parts = self.search_keyword.split()
                                    if len(brand_parts) > 0 and brand_parts[0].lower() in product_data["title"].lower():
                                        product_data["brand_name"] = brand_parts[0]

                                scraped_products[product_data["url"]] = product_data
                                logger.info(f"Successfully scraped product: {product_data['title']}")

                            except Exception as e:
                                logger.error(f"Error processing product page {product_data['url']}: {e}")
                            finally:
                                if len(self.browser.window_handles) > 1:
                                    try:
                                        self.browser.close()
                                        self.browser.switch_to.window(self.browser.window_handles[0])
                                    except Exception as e:
                                        logger.warning(f"Error switching windows: {e}")
                        break
                    except TimeoutException:
                        logger.error(f"Timeout on page {page}, attempt {attempt + 1}")
                        time.sleep(5 * (attempt + 1))
                    except Exception as e:
                        logger.error(f"Attempt {attempt + 1} failed for page {page}: {e}")
                        time.sleep(5 * (attempt + 1))
                time.sleep(random.uniform(2, 5))

                # Check if next page exists
                try:
                    WebDriverWait(self.browser, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.pagination__next"))
                    )
                except Exception:
                    logger.warning(f"No next page button found after page {page}")
                    break
        except KeyboardInterrupt:
            logger.info("Scraping interrupted by user")
        except Exception as e:
            logger.error(f"Unexpected error during scraping: {e}")
        finally:
            try:
                self.browser.quit()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
        self.scraped_data = list(scraped_products.values())
        return self.scraped_data

    def save_results(self):
        """Save scraped data to JSON file and return results"""
        try:
            if not self.scraped_data:
                logger.warning("No products scraped.")
                return OrderedDict([
                    ("success", False),
                    ("error", "No products scraped"),
                    ("data", [])
                ])

            if not self.output_file:
                default_dir = os.path.expanduser("~/Desktop")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_keyword = sanitize_filename.sanitize(self.search_keyword.replace(" ", "_"))
                self.output_file = os.path.join(default_dir, f"output_ebay_{safe_keyword}_{timestamp}.json")
                logger.info(f"No output file specified. Using default: {self.output_file}")

            output_dir = os.path.dirname(self.output_file)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(self.scraped_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Data saved to {self.output_file}")

            if os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 0:
                logger.info("JSON file verified.")
            else:
                logger.warning("Warning: JSON file is empty or was not created.")

            return OrderedDict([
                ("success", True),
                ("keyword", self.search_keyword),
                ("pages_scraped", self.max_pages),
                ("total_products", len(self.scraped_data)),
                ("output_file", self.output_file),
                ("data", self.scraped_data)
            ])
        except Exception as e:
            logger.error(f"Error saving JSON file: {str(e)}")
            return OrderedDict([
                ("success", False),
                ("error", f"Error saving data: {str(e)}"),
                ("data", [])
            ])

@app.route('/api/scrape', methods=['POST'])
def scrape():
    keyword = None
    pages = 10

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
            pages = data.get('pages', 10)
        except Exception as e:
            logger.error(f"Error parsing JSON: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Invalid JSON format"
            }), 400
    elif 'application/x-www-form-urlencoded' in content_type or 'multipart/form-data' in content_type:
        try:
            keyword = request.form.get('keyword', '').strip()
            pages_str = request.form.get('pages', '10').strip()
            try:
                pages = int(pages_str)
            except ValueError:
                return jsonify({
                    "success": False,
                    "error": "Pages must be a valid integer"
                }), 400
        except Exception as e:
            logger.error(f"Error parsing form data: {str(e)}")
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

    logger.info(f"Scraping for keyword: '{keyword}', pages: {pages}")

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = sanitize_filename.sanitize(keyword.replace(" ", "_"))
        output_file = os.path.join(os.path.expanduser("~/Desktop"), f"output_ebay_{safe_keyword}_{timestamp}.json")

        scraper = eBayScraper(keyword, pages, output_file)
        scraper.scraped_data = scraper.scrape_products()
        result = scraper.save_results()

        response = app.response_class(
            response=json.dumps(result, ensure_ascii=False),
            status=200 if result["success"] else 500,
            mimetype='application/json'
        )
        return response
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

        required_packages = ['flask', 'flask-cors', 'selenium', 'beautifulsoup4', 'webdriver-manager', 'sanitize-filename']
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
    app.start_time = time.time()
    if not check_dependencies():
        logger.error("Server started but dependencies are missing. Some features may not work.")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))