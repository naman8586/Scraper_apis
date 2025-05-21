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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, WebDriverException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime
import sanitize_filename
from collections import OrderedDict  # Import OrderedDict for maintaining key order

app = Flask(__name__)
CORS(app)

# Configure logging to console only (no log files)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class IndiaMartScraper:
    def __init__(self, search_keyword, max_pages=10, output_file=None):
        self.search_keyword = search_keyword
        self.max_pages = max_pages
        self.output_file = output_file
        self.retries = 3
        self.max_scroll_attempts = 5
        self.scraped_data = []
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
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--headless")
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

    def clean_title(self, title):
        """Clean up malformed titles by removing duplicates, redundant brands, and normalizing."""
        if not title:
            return None
        title = re.sub(r'<[^>]+>', '', title)
        title = re.sub(r'[^\w\s,()&-]', '', title)
        parts = re.split(r'[,|/]', title)
        parts = [part.strip() for part in parts if part.strip()]
        seen = set()
        cleaned_parts = []
        for part in parts:
            part_lower = part.lower()
            if part_lower not in seen:
                seen.add(part_lower)
                cleaned_parts.append(part)
        title = " ".join(cleaned_parts)
        words = title.split()
        common_brands = ["rolex", "omega", "tag heuer", "cartier", "patek philippe", "audemars piguet", "tissot", "seiko", "citizen"]
        brand_count = {brand: 0 for brand in common_brands}
        cleaned_words = []
        for word in words:
            word_lower = word.lower()
            skip = False
            for brand in common_brands:
                if brand in word_lower:
                    if brand_count[brand] > 0:
                        skip = True
                        break
                    brand_count[brand] += 1
            if not skip and word_lower not in ["watch", "timepiece", "used"]:
                cleaned_words.append(word)
        cleaned_title = " ".join(cleaned_words).strip()
        if self.search_keyword.lower() not in cleaned_title.lower():
            cleaned_title += f" {self.search_keyword.capitalize()}"
        if len(cleaned_title) > 100:
            cleaned_title = cleaned_title[:97] + "..."
        return cleaned_title

    def extract_price(self, card_soup, title):
        """Extract currency and exact price from card."""
        try:
            price_selectors = [
                "p.price", "div.price", "span.price", "p[class*='price']",
                "div[class*='price']", "span[class*='price']", "[class*='price']",
                "div.mprice2", "span.mprice2"
            ]
            for selector in price_selectors:
                if price_el := card_soup.select_one(selector):
                    raw_price = price_el.get_text(strip=True)
                    if "Ask Price" in raw_price or "Call" in raw_price:
                        return {"currency": None, "exact_price": "Ask Price"}
                    currency = None
                    currency_symbols = ["₹", "$", "€", "¥", "£", "Rs"]
                    for symbol in currency_symbols:
                        if symbol in raw_price:
                            currency = symbol
                            break
                    if not currency and "rs" in raw_price.lower():
                        currency = "₹"
                    price_pattern = r'[\d,]+(?:\.\d+)?'
                    price_matches = re.findall(price_pattern, raw_price)
                    price_values = [re.sub(r'[^\d.]', '', p) for p in price_matches]
                    if len(price_values) >= 1:
                        return {"currency": currency or "₹", "exact_price": price_values[0]}
                    break
            logger.warning(f"No price element found for {title}")
            return {"currency": None, "exact_price": None}
        except Exception as e:
            logger.error(f"Error extracting price for {title}: {e}")
            return {"currency": None, "exact_price": None}

    def extract_images(self, card_soup, card_elem, title):
        """Extract image_url, images, and dimensions from card."""
        try:
            img_selectors = [
                "img[class*='product-img']", "img[class*='image']",
                "img[src*='product']", "img[src]", "img",
                "img.limg", "img.prdimg"
            ]
            images = []
            image_url = None
            dimensions = None
            for selector in img_selectors:
                img_elements = card_soup.select(selector)
                if not img_elements:
                    continue
                for idx, img in enumerate(img_elements):
                    src = img.get("src", "")
                    data_src = img.get("data-src", "")
                    if not src or src.endswith(('placeholder.png', 'default.jpg', 'noimage.jpg')):
                        if data_src and not data_src.endswith(('placeholder.png', 'default.jpg', 'noimage.jpg')):
                            src = data_src
                        else:
                            continue
                    if src and not src.startswith('data:'):
                        if idx == 0:
                            image_url = src
                            width = height = "Unknown"
                            try:
                                img_elem = card_elem.find_element(By.CSS_SELECTOR, selector)
                                width = self.browser.execute_script("return arguments[0].naturalWidth", img_elem) or img.get("width", "Unknown")
                                height = self.browser.execute_script("return arguments[0].naturalHeight", img_elem) or img.get("height", "Unknown")
                                dimensions = f"{width}x{height}"
                            except Exception as e:
                                logger.debug(f"Error getting image dimensions for {title}: {e}")
                                dimensions = f"{img.get('width', 'Unknown')}x{img.get('height', 'Unknown')}"
                        images.append(src)
                if images:
                    break
            if images:
                logger.info(f"Found {len(images)} images for {title}")
                return {"image_url": image_url, "images": images, "dimensions": dimensions}
            else:
                logger.warning(f"No images found for {title}")
                return {"image_url": None, "images": [], "dimensions": None}
        except Exception as e:
            logger.error(f"Error extracting images for {title}: {e}")
            return {"image_url": None, "images": [], "dimensions": None}

    def extract_description(self, card_soup, title):
        """Extract product description."""
        try:
            desc_selectors = [
                "div.description", "p.description", "div.prod-desc",
                "div.dtls", "p.dtl", "div.detail"
            ]
            for selector in desc_selectors:
                if desc := card_soup.select_one(selector):
                    return desc.get_text(strip=True)
            return None
        except Exception as e:
            logger.error(f"Error extracting description for {title}: {e}")
            return None

    def extract_min_order(self, card_soup, title):
        """Extract minimum order quantity and unit."""
        try:
            min_order_selectors = [
                "span.unit", "div.moq", "[class*='moq']", "[class*='min-order']",
                "span.min", "div.min-order"
            ]
            for selector in min_order_selectors:
                if min_order_el := card_soup.select_one(selector):
                    text = min_order_el.get_text(strip=True)
                    qty_pattern = r'(\d+)'
                    qty_match = re.search(qty_pattern, text)
                    qty = qty_match.group(1) if qty_match else None
                    unit_pattern = r'([A-Za-z]+)'
                    unit_match = re.search(unit_pattern, text)
                    unit = unit_match.group(1) if unit_match else None
                    if qty and unit:
                        return f"{qty} {unit}"
                    return None
            return None
        except Exception as e:
            logger.error(f"Error extracting min order for {title}: {e}")
            return None

    def extract_supplier(self, card_soup, title):
        """Extract supplier name."""
        try:
            name_selectors = [
                "div.companyname a", "div.companyname", "p.company-name",
                "[class*='company']", "div.cname", "span.cname"
            ]
            for selector in name_selectors:
                if elem := card_soup.select_one(selector):
                    return elem.get_text(strip=True)
            return None
        except Exception as e:
            logger.error(f"Error extracting supplier for {title}: {e}")
            return None

    def extract_origin(self, card_soup, title):
        """Extract product origin."""
        try:
            origin_selectors = [
                "span.origin", "div.origin", "[class*='origin']",
                "span.location", "div.location"
            ]
            for selector in origin_selectors:
                if origin_el := card_soup.select_one(selector):
                    return origin_el.get_text(strip=True)
            return None
        except Exception as e:
            logger.error(f"Error extracting origin for {title}: {e}")
            return None

    def extract_feedback(self, card_soup, title):
        """Extract rating and review count."""
        feedback = {"rating": None, "review": None}
        try:
            rating_selectors = [
                "span.bo.color", "span.rating", "div.rating",
                "span.score", "div.score"
            ]
            review_selectors = [
                "span:contains('(')", "span.reviews", "[class*='review']",
                "span.review-count", "div.review-count"
            ]
            for selector in rating_selectors:
                if rating_el := card_soup.select_one(selector):
                    rating_text = rating_el.get_text(strip=True)
                    rating_match = re.search(r'([\d.]+)', rating_text)
                    if rating_match:
                        feedback["rating"] = rating_match.group(1)
                        break
            for selector in review_selectors:
                if review_el := card_soup.select_one(selector):
                    review_text = review_el.get_text(strip=True)
                    review_match = re.search(r'\((\d+)\)', review_text)
                    if review_match:
                        feedback["review"] = review_match.group(1)
                        break
            return feedback
        except Exception as e:
            logger.error(f"Error extracting feedback for {title}: {e}")
            return {"rating": None, "review": None}

    def extract_brand(self, title):
        """Extract brand from title."""
        try:
            title_text = title.lower()
            common_brands = [
                "rolex", "omega", "tag heuer", "cartier", "patek philippe",
                "audemars piguet", "tissot", "seiko", "citizen"
            ]
            for brand in common_brands:
                if re.search(r'\b' + brand + r'\b', title_text):
                    return brand.capitalize()
            return None
        except Exception as e:
            logger.error(f"Error extracting brand: {e}")
            return None

    def extract_discount(self, card_soup, title):
        """Extract discount information."""
        try:
            discount_selectors = [
                "span.discount", "div.discount", "[class*='discount']",
                "span.offer", "div.offer"
            ]
            for selector in discount_selectors:
                if discount_el := card_soup.select_one(selector):
                    return discount_el.get_text(strip=True)
            return None
        except Exception as e:
            logger.error(f"Error extracting discount for {title}: {e}")
            return None

    def extract_videos(self, card_soup, title):
        """Extract video URLs."""
        try:
            videos = []
            video_selectors = ["video", "video[src]", "[class*='video']"]
            for selector in video_selectors:
                if video_el := card_soup.select_one(selector):
                    if src := video_el.get("src"):
                        videos.append(src)
            return videos if videos else []
        except Exception as e:
            logger.error(f"Error extracting videos for {title}: {e}")
            return []

    def create_product_data(self):
        """Create an ordered dictionary with field order preserved"""
        return OrderedDict([
            ("url", None),
            ("title", None),
            ("currency", None),
            ("exact_price", None),
            ("description", None),
            ("min_order", None),
            ("supplier", None),
            ("origin", None),
            ("feedback", {"rating": None, "review": None}),
            ("image_url", None),
            ("images", []),
            ("videos", []),
            ("dimensions", None),
            ("website_name", "IndiaMart"),
            ("discount_information", None),
            ("brand_name", None)
        ])

    def scrape_products(self):
        """Main scraping function"""
        try:
            for page in range(1, self.max_pages + 1):
                url = f"https://dir.indiamart.com/search.mp?ss={self.search_keyword.replace(' ', '+')}&page={page}"
                logger.info(f"Scraping page {page}/{self.max_pages}: {url}")
                for attempt in range(self.retries):
                    try:
                        self.rotate_user_agent()
                        self.browser.get(url)
                        WebDriverWait(self.browser, 20).until(
                            lambda d: d.execute_script("return document.readyState") == "complete"
                        )
                        WebDriverWait(self.browser, 10).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.card"))
                        )
                        previous_product_count = 0
                        scroll_attempts = 0
                        while scroll_attempts < self.max_scroll_attempts:
                            cards = self.browser.find_elements(By.CSS_SELECTOR, "div.card")
                            current_count = len(cards)
                            logger.info(f"Scroll attempt {scroll_attempts + 1}: Found {current_count} products")
                            if current_count == previous_product_count:
                                break
                            previous_product_count = current_count
                            self.browser.execute_script(
                                "window.scrollTo(0, Math.min(document.body.scrollHeight, window.scrollY + 800));"
                            )
                            time.sleep(random.uniform(1, 2))
                            scroll_attempts += 1
                        cards = self.browser.find_elements(By.CSS_SELECTOR, "div.card")
                        if not cards:
                            logger.warning(f"No products found on page {page}")
                            break
                        logger.info(f"Found {len(cards)} product cards on page {page}")
                        for card_idx, card_elem in enumerate(cards):
                            # Use OrderedDict to maintain field order
                            product_data = self.create_product_data()
                            try:
                                card_html = card_elem.get_attribute("outerHTML")
                                card_soup = BeautifulSoup(card_html, "html.parser")
                            except StaleElementReferenceException:
                                logger.warning(f"Stale element for card {card_idx}. Skipping.")
                                continue
                            except Exception as e:
                                logger.error(f"Error retrieving card HTML for card {card_idx}: {e}")
                                continue
                            if prod_name := card_soup.select_one("div.producttitle, div.listing-title, div.prdname"):
                                raw_title = prod_name.get_text(strip=True)
                                product_data["title"] = self.clean_title(raw_title)
                                if self.search_keyword.lower() not in product_data["title"].lower():
                                    logger.info(f"Skipping non-matching product: {product_data['title']}")
                                    continue
                            else:
                                logger.warning(f"No title found for card {card_idx}")
                                continue
                            if prod_url_el := card_soup.select_one("div.titleAskPriceImageNavigation, div.listing-title"):
                                if a_tag := prod_url_el.find("a"):
                                    product_data["url"] = a_tag.get("href", None)
                            if not product_data["url"]:
                                for url_selector in ["a.product-title", "a.cardlinks", "a[href]", "a.listing-link"]:
                                    if a_tag := card_soup.select_one(url_selector):
                                        href = a_tag.get("href", None)
                                        if href and ("indiamart.com" in href or href.startswith("/")):
                                            product_data["url"] = href
                                            break
                            if not product_data["url"]:
                                logger.warning(f"No URL found for {product_data['title']}")
                                continue
                            if product_data["url"].startswith("/"):
                                product_data["url"] = f"https://www.indiamart.com{product_data['url']}"
                            if product_data["url"] and "?" in product_data["url"]:
                                product_data["url"] = product_data["url"].split("?")[0]
                            price_data = self.extract_price(card_soup, product_data["title"])
                            product_data["currency"] = price_data["currency"]
                            product_data["exact_price"] = price_data["exact_price"]
                            product_data["description"] = self.extract_description(card_soup, product_data["title"])
                            product_data["min_order"] = self.extract_min_order(card_soup, product_data["title"])
                            product_data["supplier"] = self.extract_supplier(card_soup, product_data["title"])
                            product_data["origin"] = self.extract_origin(card_soup, product_data["title"])
                            product_data["feedback"] = self.extract_feedback(card_soup, product_data["title"])
                            image_data = self.extract_images(card_soup, card_elem, product_data["title"])
                            product_data["image_url"] = image_data["image_url"]
                            product_data["images"] = image_data["images"]
                            product_data["dimensions"] = image_data["dimensions"]
                            product_data["videos"] = self.extract_videos(card_soup, product_data["title"])
                            product_data["discount_information"] = self.extract_discount(card_soup, product_data["title"])
                            product_data["brand_name"] = self.extract_brand(product_data["title"])
                            
                            if product_data["title"] and product_data["url"]:
                                self.scraped_data.append(product_data)
                                logger.info(f"Successfully scraped product: {product_data['title']}")
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
        return self.scraped_data

    def save_results(self):
        """Save scraped data to JSON file and return results"""
        try:
            if not self.scraped_data:
                logger.warning("No products scraped.")
                return {
                    "success": False,
                    "error": "No products scraped",
                    "data": []
                }
            if self.output_file:
                # Use json.dumps with a custom encoder to maintain order
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
            logger.error(f"Error saving JSON file: {e}")
            return OrderedDict([
                ("success", False),
                ("error", f"Error saving data: {str(e)}"),
                ("data", [])
            ])

@app.route('/api/scrape', methods=['POST'])
def scrape():
    # Initialize variables for keyword and pages
    keyword = None
    pages = 10  # Default value

    # Check Content-Type to determine how to parse the request
    content_type = request.headers.get('Content-Type', '')

    if 'application/json' in content_type:
        # Handle JSON data
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
        # Handle form data
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

    # Validate keyword and pages
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

        scraper = IndiaMartScraper(keyword, pages, output_file)
        products = scraper.scrape_products()
        result = scraper.save_results()

        # Use Flask's jsonify, but ensure it preserves the order
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