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

class FlipkartScraper:
    def __init__(self, search_keyword, max_pages=10, output_file=None):
        self.search_keyword = search_keyword
        self.max_pages = max_pages
        self.output_file = output_file
        self.retries = 3
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36"
        ]
        self.browser = self._setup_browser()
        self.scraped_data = []  # Initialize scraped_data

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
            ("website_name", "Flipkart"),
            ("discount_information", "N/A")
        ])

    def scrape_products(self):
        """Main scraping function"""
        scraped_products = {}
        try:
            for page in range(1, self.max_pages + 1):
                for attempt in range(self.retries):
                    try:
                        self.rotate_user_agent()
                        search_url = f"https://www.flipkart.com/search?q={self.search_keyword.replace(' ', '+')}&page={page}"
                        logger.info(f"Scraping page {page}/{self.max_pages}: {search_url}")
                        self.browser.get(search_url)
                        WebDriverWait(self.browser, 15).until(
                            lambda d: d.execute_script("return document.readyState") == "complete"
                        )
                        time.sleep(3)  # Allow dynamic content to load

                        # Check for CAPTCHA
                        if "captcha" in self.browser.current_url.lower() or "verify" in self.browser.page_source.lower():
                            logger.warning("CAPTCHA detected. Skipping page.")
                            break

                        # Try multiple product card selectors
                        product_cards_selectors = ["div.slAVV4"]
                        product_cards = None
                        for selector in product_cards_selectors:
                            try:
                                product_cards = WebDriverWait(self.browser, 10).until(
                                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector))
                                )
                                if product_cards:
                                    break
                            except TimeoutException:
                                continue

                        if not product_cards:
                            logger.warning(f"No products found on page {page}")
                            break

                        logger.info(f"Found {len(product_cards)} products on page {page}")
                        for index, product_card in enumerate(product_cards):
                            product_data = self.create_product_data()
                            try:
                                # Extract product URL
                                product_url_tag = product_card.find_element(By.TAG_NAME, "a")
                                product_data["url"] = product_url_tag.get_attribute("href")
                                if not product_data["url"] or product_data["url"] in scraped_products:
                                    continue

                                # Open product page
                                self.browser.execute_script("window.open('');")
                                self.browser.switch_to.window(self.browser.window_handles[-1])
                                self.browser.get(product_data["url"])
                                WebDriverWait(self.browser, 15).until(
                                    lambda d: d.execute_script("return document.readyState") == "complete"
                                )
                                time.sleep(2)

                                # Product title
                                product_data["title"] = self.retry_extraction(
                                    lambda: self.browser.find_element(By.CSS_SELECTOR, "span.VU-ZEz").text.strip()
                                )
                                if self.search_keyword.lower() not in product_data["title"].lower():
                                    logger.info(f"Skipping non-matching product: {product_data['title']}")
                                    self.browser.close()
                                    self.browser.switch_to.window(self.browser.window_handles[0])
                                    continue

                                # Product price and currency
                                product_data["exact_price"] = self.retry_extraction(
                                    lambda: self.browser.find_element(By.CSS_SELECTOR, "div.Nx9bqj.CxhGGd").text.strip()
                                )
                                match = re.match(r'([^0-9]+)([0-9,]+)', product_data["exact_price"])
                                if match:
                                    product_data["currency"] = match.group(1)
                                    product_data["exact_price"] = match.group(2).replace(",", "")

                                # Product description
                                product_data["description"] = self.retry_extraction(
                                    lambda: " ".join([e.text.strip() for e in self.browser.find_elements(By.CSS_SELECTOR, "span.VU-ZEz") if e.text.strip()])
                                )

                                # Supplier (seller info)
                                product_data["supplier"] = self.retry_extraction(
                                    lambda: self.browser.find_element(By.CSS_SELECTOR, "div.cvCpHS").text.strip()
                                )

                                # Feedback (rating and reviews)
                                product_data["feedback"]["rating"] = self.retry_extraction(
                                    lambda: self.browser.find_element(By.CSS_SELECTOR, "div.XQDdHH._1Quie7").text.split()[0]
                                )
                                product_data["feedback"]["review"] = self.retry_extraction(
                                    lambda: self.browser.find_element(By.CSS_SELECTOR, "span.Wphh3N span").text.strip()
                                )

                                # Discount information
                                product_data["discount_information"] = self.retry_extraction(
                                    lambda: self.browser.find_element(By.CSS_SELECTOR, "div.UkUFwK.WW8yVX").text.strip()
                                )

                                # Product images
                                try:
                                    images_elem = WebDriverWait(self.browser, 10).until(
                                        EC.presence_of_element_located((By.CSS_SELECTOR, "div.qOPjUY"))
                                    )
                                    img_buttons = images_elem.find_elements(By.CSS_SELECTOR, "li.YGoYIP")
                                    for i, img_button in enumerate(img_buttons):
                                        try:
                                            self.browser.execute_script("arguments[0].scrollIntoView(true);", img_button)
                                            img_button.click()
                                            time.sleep(1)
                                            wrapper = images_elem.find_element(By.CSS_SELECTOR, "div.vU5WPQ")
                                            img_tag = wrapper.find_element(By.TAG_NAME, "img")
                                            image_url = img_tag.get_attribute("src")
                                            if i == 0:
                                                product_data["image_url"] = image_url
                                            if image_url not in product_data["images"]:
                                                product_data["images"].append(image_url)
                                        except Exception:
                                            continue
                                except Exception as e:
                                    logger.warning(f"Error extracting images for {product_data['title']}: {e}")

                                # Specifications
                                try:
                                    WebDriverWait(self.browser, 10).until(
                                        EC.presence_of_element_located((By.CSS_SELECTOR, "div.GNDEQ-"))
                                    )
                                    table_html = self.browser.find_element(By.CSS_SELECTOR, "div.GNDEQ-").get_attribute("innerHTML")
                                    soup = BeautifulSoup(table_html, "html.parser")
                                    rows = soup.select("tr.WJdYP6")
                                    product_data["specifications"] = {}
                                    for row in rows:
                                        try:
                                            label = row.select_one("td.col-3-12").get_text(strip=True)
                                            value = ", ".join(li.get_text(strip=True) for li in row.select("td.col-9-12 li"))
                                            if label:
                                                product_data["specifications"][label] = value
                                        except Exception:
                                            continue
                                except Exception as e:
                                    logger.warning(f"Error extracting specifications for {product_data['title']}: {e}")

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

            # Ensure output_file is valid
            if not self.output_file:
                default_dir = os.path.expanduser("~/Desktop")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_keyword = sanitize_filename.sanitize(self.search_keyword.replace(" ", "_"))
                self.output_file = os.path.join(default_dir, f"output_flipkart_{safe_keyword}_{timestamp}.json")
                logger.info(f"No output file specified. Using default: {self.output_file}")

            # Create directory if it doesn't exist
            output_dir = os.path.dirname(self.output_file)
            if output_dir:  # Only create directory if path is non-empty
                os.makedirs(output_dir, exist_ok=True)

            # Save data to JSON
            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(self.scraped_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Data saved to {self.output_file}")

            # Verify file
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
    # Initialize variables
    keyword = None
    pages = 10

    # Handle different Content-Types
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

    # Validate inputs
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
        output_file = os.path.join(os.path.expanduser("~/Desktop"), f"output_flipkart_{safe_keyword}_{timestamp}.json")

        scraper = FlipkartScraper(keyword, pages, output_file)
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

if __name__ == '__main__':
    app.start_time = time.time()
    if not check_dependencies():
        logger.error("Server started but dependencies are missing. Some features may not work.")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))