import time
import json
import os
import logging
import sys
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from datetime import datetime
from collections import OrderedDict
import sanitize_filename
import random

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Logging setup (console only)
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    return time.strftime("%Y%m%d_%H%M%S")

# Scraper class
class MadeInChinaScraper:
    def __init__(self, search_keyword, max_pages=1, output_file=None):
        self.search_keyword = search_keyword
        self.max_pages = max_pages
        self.output_file = output_file
        self.retries = 2
        self.scraped_products = {}
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36"
        ]
        self.browser = self._setup_browser()

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
        options.add_argument("--disable-extensions")
        options.add_argument(f"user-agent={random.choice(self.user_agents)}")
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.maximize_window()
            return driver
        except WebDriverException as e:
            logging.error(f"Failed to initialize WebDriver: {e}")
            raise

    def rotate_user_agent(self):
        """Change the user agent to avoid detection"""
        try:
            user_agent = random.choice(self.user_agents)
            self.browser.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": user_agent
            })
            logging.info(f"Rotated user agent to: {user_agent}")
        except Exception as e:
            logging.warning(f"Failed to rotate user agent: {e}")

    def retry_extraction(self, func, attempts=2, delay=0.5, default=""):
        """Retry a function with specified attempts and delay"""
        for i in range(attempts):
            try:
                result = func()
                if result:
                    return result
            except Exception as e:
                logging.debug(f"Retry {i+1}/{attempts} failed: {e}")
                time.sleep(delay)
        return default

    def create_product_data(self):
        """Create an ordered dictionary with field order preserved"""
        return OrderedDict([
            ("url", ""),
            ("title", ""),
            ("currency", ""),
            ("exact_price", ""),
            ("min_order", ""),
            ("supplier", ""),
            ("origin", ""),
            ("feedback", OrderedDict([("rating", ""), ("star count", "0")])),
            ("specifications", {}),
            ("images", []),
            ("videos", []),
            ("website_name", "MadeinChina"),
            ("discount_information", "N/A"),
            ("brand_name", "N/A")
        ])

    def scrape_products(self):
        """Main scraping function"""
        for page in range(1, self.max_pages + 1):
            for attempt in range(self.retries):
                try:
                    self.rotate_user_agent()
                    search_url = f'https://www.made-in-china.com/multi-search/{self.search_keyword.replace(" ", "+")}/F1/{page}.html?pv_id=1ik76htapa40&faw_id=null'
                    logging.info(f"Scraping page {page}/{self.max_pages}: {search_url}")
                    self.browser.get(search_url)
                    WebDriverWait(self.browser, 8).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )
                    try:
                        captcha = self.browser.find_element(By.XPATH, '//form[contains(@action, "captcha")]')
                        logging.warning(f"CAPTCHA detected on page {page}!")
                        break
                    except NoSuchElementException:
                        logging.info(f"No CAPTCHA detected on page {page}, proceeding...")
                    product_cards_container = WebDriverWait(self.browser, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, '.prod-list'))
                    )
                    if not product_cards_container:
                        logging.warning(f"No products found on page {page}")
                        break

                    product_cards_html = BeautifulSoup(product_cards_container.get_attribute("outerHTML"), "html.parser")
                    product_cards = product_cards_html.select("div.prod-info")

                    for product in product_cards:
                        product_json_data = self.create_product_data()
                        try:
                            product_link = product.select_one('.product-name a[href]')
                            if product_link:
                                product_url = product_link['href']
                                if product_url.startswith('//'):
                                    product_url = 'https:' + product_url
                                product_json_data["url"] = product_url

                            product_title_elem = product.select_one('.product-name[title]')
                            if product_title_elem:
                                product_json_data["title"] = product_title_elem['title'].strip()

                            if product_json_data["url"] in self.scraped_products:
                                continue

                            currency_price_elem = product.select_one('.product-property .price-info .price')
                            if currency_price_elem:
                                currency_price_text = currency_price_elem.get_text(strip=True)
                                currency = ''.join(c for c in currency_price_text if not c.isdigit() and c not in ['.', '-', ' ']).strip()
                                product_json_data["currency"] = currency
                                product_json_data["exact_price"] = currency_price_text.replace(currency, '').strip()

                            for info_elem in product.select('div.info'):
                                if '(MOQ)' in info_elem.text:
                                    min_order_text = info_elem.text.strip()
                                    product_json_data["min_order"] = min_order_text.replace('(MOQ)', '').strip()
                                    break

                            supplier_elem = product.select_one('.company-name-wrapper .compnay-name span')
                            if supplier_elem:
                                product_json_data["supplier"] = supplier_elem.get_text(strip=True)

                            if product_json_data["url"]:
                                try:
                                    self.browser.execute_script(f"window.open('{product_json_data['url']}');")
                                    self.browser.switch_to.window(self.browser.window_handles[-1])
                                    WebDriverWait(self.browser, 8).until(
                                        lambda d: d.execute_script("return document.readyState") == "complete"
                                    )
                                    self.browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                                    time.sleep(1)
                                    product_page_html = BeautifulSoup(self.browser.page_source, "html.parser")

                                    product_origin_info = product_page_html.select_one('.basic-info-list')
                                    if product_origin_info:
                                        for item in product_origin_info.select('div.bsc-item.cf'):
                                            label = item.select_one('div.bac-item-label.fl')
                                            if label and 'Origin' in label.text:
                                                value = item.select_one('div.bac-item-value.fl')
                                                if value:
                                                    product_json_data["origin"] = value.get_text(strip=True)

                                    try:
                                        rating_elem = WebDriverWait(self.browser, 5).until(
                                            EC.presence_of_element_located((By.CSS_SELECTOR, "a.J-company-review .review-score"))
                                        )
                                        rating_text = rating_elem.text
                                        star_elems = self.browser.find_elements(By.CSS_SELECTOR, "a.J-company-review .review-rate i")
                                        product_json_data["feedback"]["rating"] = rating_text
                                        product_json_data["feedback"]["star count"] = str(len(star_elems))
                                    except (NoSuchElementException, TimeoutException):
                                        product_json_data["feedback"]["rating"] = "No rating available"
                                        product_json_data["feedback"]["star count"] = "0"

                                    specifications = {}
                                    try:
                                        rows = self.browser.find_elements(By.XPATH, "//div[@class='basic-info-list']/div[@class='bsc-item cf']")
                                        for row in rows:
                                            label_div = row.find_element(By.XPATH, ".//div[contains(@class,'bac-item-label')]")
                                            value_div = row.find_element(By.XPATH, ".//div[contains(@class,'bac-item-value')]")
                                            label = label_div.text.strip()
                                            value = value_div.text.strip()
                                            if label and value:
                                                specifications[label] = value
                                        product_json_data["specifications"] = specifications
                                    except Exception as e:
                                        logging.error(f"Error extracting specifications: {e}")

                                    swiper = product_page_html.select_one("div.sr-proMainInfo-slide-container")
                                    if swiper:
                                        wrapper = swiper.select_one("div.swiper-wrapper")
                                        if wrapper:
                                            for media in wrapper.select("div.sr-prMainInfo-slide-inner"):
                                                for vid in media.select("script[type='text/data-video']"):
                                                    try:
                                                        video_data = json.loads(vid.get_text(strip=True))
                                                        if video_data.get("videoUrl"):
                                                            product_json_data["videos"].append(video_data["videoUrl"])
                                                    except Exception:
                                                        pass
                                                for img in media.select("img[src]"):
                                                    src = img["src"]
                                                    if src.startswith("//"):
                                                        src = "https:" + src
                                                    product_json_data["images"].append(src)

                                except Exception as e:
                                    logging.error(f"Error processing product page: {e}")
                                finally:
                                    if len(self.browser.window_handles) > 1:
                                        self.browser.close()
                                        self.browser.switch_to.window(self.browser.window_handles[0])

                            self.scraped_products[product_json_data["url"]] = product_json_data

                        except Exception as e:
                            logging.error(f"Error processing product: {e}")

                    break
                except Exception as e:
                    logging.error(f"Attempt {attempt + 1}/{self.retries}: Error scraping page {page}: {e}")
                    time.sleep(2)
            else:
                logging.error(f"Failed to scrape page {page} after {self.retries} attempts.")

        return list(self.scraped_products.values())

    def save_results(self):
        """Save scraped data and return results"""
        try:
            json_data = list(self.scraped_products.values())
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
                self.output_file = os.path.join(default_dir, f"output_madeinchina_{safe_keyword}_{timestamp}.json")
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
                ("keyword", self.search_keyword),
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
            try:
                self.browser.quit()
            except Exception as e:
                logging.warning(f"Error closing browser: {e}")

# API endpoint to scrape Made-in-China
@app.route('/api/scrape', methods=['POST'])
def scrape():
    keyword = None
    pages = 1

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
            pages = data.get('pages', 1)
        except Exception as e:
            logging.error(f"Error parsing JSON: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Invalid JSON format"
            }), 400
    elif 'application/x-www-form-urlencoded' in content_type or 'multipart/form-data' in content_type:
        try:
            keyword = request.form.get('keyword', '').strip()
            pages_str = request.form.get('pages', '1').strip()
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
        output_file = os.path.join(os.path.expanduser("~/Desktop"), f"output_madeinchina_{safe_keyword}_{timestamp}.json")

        scraper = MadeInChinaScraper(keyword, pages, output_file)
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