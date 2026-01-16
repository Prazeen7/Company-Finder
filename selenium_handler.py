import time
import random
import json
import os
import re  # Add this import
from urllib.parse import urlparse, urljoin
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

from config import USER_AGENTS, TIMEOUT, DEBUG_DIR, EMAIL_REGEX, PHONE_REGEX, ADDRESS_REGEX, SOCIAL_REGEXES  # Add regex imports
from utils import custom_print

# Initialize Selenium driver with optimized configuration
def init_selenium_driver():
    options = Options()
    
    # Basic headless options
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")  # More realistic window size
    options.add_argument("--disable-dev-shm-usage")
    
    # Anti-detection measures
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions-except")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins-discovery")
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-client-side-phishing-detection")
    options.add_argument("--disable-hang-monitor")
    options.add_argument("--disable-prompt-on-repost")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    
    # Random User-Agent
    user_agent = random.choice(USER_AGENTS)
    options.add_argument(f"--user-agent={user_agent}")
    
    # Additional headers to appear more legitimate
    options.add_argument("--accept-language=en-US,en;q=0.9")
    options.add_argument("--accept-encoding=gzip, deflate, br")
    
    # Disable automation indicators
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # Enable images and JavaScript for more realistic browsing (removed disable arguments)
    options.add_argument("--enable-javascript")
    
    # Performance optimizations while keeping functionality
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--log-level=3")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    # Execute script to hide webdriver property
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    # Set realistic timeouts
    driver.set_page_load_timeout(45)  # Increased timeout
    driver.implicitly_wait(10)
    
    return driver

def extract_tel_from_shadow_html(shadow_html, url):
    """Extract tel: attributes from shadow DOM HTML"""
    tel_numbers = set()
    try:
        shadow_soup = BeautifulSoup(shadow_html, "lxml")
        
        # Extract from href="tel:"
        for link in shadow_soup.find_all('a', href=lambda x: x and x.startswith('tel:')):
            phone = link['href'][4:].strip()
            phone = re.sub(r'[^\d+]', '', phone)
            if phone:
                tel_numbers.add(phone)
        
        # Extract from onclick="tel:"
        for tag in shadow_soup.find_all(attrs={'onclick': True}):
            onclick = tag['onclick']
            if 'tel:' in onclick.lower():
                matches = re.findall(r'tel:([^\'"\s;]+)', onclick, re.IGNORECASE)
                for match in matches:
                    phone = re.sub(r'[^\d+]', '', match)
                    if phone:
                        tel_numbers.add(phone)
        
        # Extract from data attributes
        for tag in shadow_soup.find_all(attrs={'data-tel': True}):
            phone = re.sub(r'[^\d+]', '', tag['data-tel'])
            if phone:
                tel_numbers.add(phone)
        
        for tag in shadow_soup.find_all(attrs={'data-phone': True}):
            phone = re.sub(r'[^\d+]', '', tag['data-phone'])
            if phone:
                tel_numbers.add(phone)
                
    except Exception as e:
        custom_print(f"❌ Error extracting tel from shadow HTML: {e}")
    
    return tel_numbers

def access_with_referer(driver, url):
    """Access URL with a fake referer"""
    parsed_url = urlparse(url)
    base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    try:
        # Set referer header via CDP
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": random.choice(USER_AGENTS)
        })
    except:
        pass
    
    # First visit the base domain
    try:
        driver.get(base_domain)
        time.sleep(2)
    except:
        pass
    
    # Then visit the target URL
    driver.get(url)

def staged_access(driver, url):
    """Access URL in stages, mimicking human behavior"""
    parsed_url = urlparse(url)
    base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    stages = [
        base_domain,
        f"{base_domain}/",
        url
    ]
    
    for stage in stages:
        try:
            driver.get(stage)
            time.sleep(random.uniform(1, 3))
            
            # Simulate some human-like actions
            driver.execute_script("window.scrollTo(0, Math.floor(Math.random() * 200));")
            time.sleep(random.uniform(0.5, 1.5))
        except:
            continue

def access_with_mobile_ua(driver, url):
    """Access URL with mobile user agent"""
    mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1"
    
    try:
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": mobile_ua
        })
    except:
        pass
    
    driver.get(url)

def get_rendered_html(url, driver, retries=2):
    debug_data = {
        "url": url,
        "dynamic_html": "",
        "links": [],
        "scripts": [],
        "raw_socials": [],
        "raw_emails": [],
        "raw_phones": [],
        "raw_addresses": [],
        "shadow_content": [],
        "alerts": [],
        "consolidated_links": [],
        "iframes": [],
        "access_methods_tried": [],
        "error_details": []  # New key to store detailed error information
    }
    
    consolidated_links = set()
    
    # Try different approaches for 403 errors
    access_methods = [
        ('direct', lambda: driver.get(url)),
        ('with_referer', lambda: access_with_referer(driver, url)),
        ('staged_access', lambda: staged_access(driver, url)),
        ('mobile_user_agent', lambda: access_with_mobile_ua(driver, url))
    ]
    
    for method_name, access_method in access_methods:
        for attempt in range(retries):
            try:
                debug_data["access_methods_tried"].append(f"{method_name}_attempt_{attempt + 1}")
                custom_print(f"🔄 Trying {method_name} method for {url} (attempt {attempt + 1})")
                
                # Execute the access method
                access_method()
                
                # Add random human-like delay
                time.sleep(random.uniform(2, 5))
                
                # Check if we got content
                current_url = driver.current_url
                page_source = driver.page_source
                
                # Check for common error indicators
                error_indicators = ['403 forbidden', '403 error', 'access denied', 'unauthorized', 'blocked']
                if any(indicator in page_source.lower() for indicator in error_indicators):
                    # Capture HTTP status if possible
                    status_code = None
                    try:
                        import requests
                        response = requests.head(url, timeout=10, allow_redirects=True)
                        status_code = response.status_code
                    except Exception as e:
                        custom_print(f"❌ Failed to get HTTP status for {url}: {e}")
                    
                    error_detail = {
                        "method": method_name,
                        "attempt": attempt + 1,
                        "error_type": "Error Page",
                        "message": f"Received error page with indicators: {', '.join(indicator for indicator in error_indicators if indicator in page_source.lower())}",
                        "http_status": status_code if status_code else "Unknown",
                        "current_url": current_url
                    }
                    debug_data["error_details"].append(error_detail)
                    custom_print(f"❌ {method_name} method got error page for {url}: {error_detail['message']} (HTTP Status: {error_detail['http_status']})")
                    continue
                
                if len(page_source) < 500:
                    error_detail = {
                        "method": method_name,
                        "attempt": attempt + 1,
                        "error_type": "Minimal Content",
                        "message": f"Received minimal content (length: {len(page_source)} bytes)",
                        "http_status": None,
                        "current_url": current_url
                    }
                    debug_data["error_details"].append(error_detail)
                    custom_print(f"❌ {method_name} method got minimal content for {url}: {error_detail['message']}")
                    continue
                
                custom_print(f"✅ {method_name} method successful for {url}")
                
                # Wait for page elements (unchanged)
                try:
                    WebDriverWait(driver, TIMEOUT).until(
                        EC.any_of(
                            EC.presence_of_element_located((By.TAG_NAME, "body")),
                            EC.presence_of_element_located((By.CSS_SELECTOR, (
                                "footer, [class*='footer' i], [id*='footer' i], "
                                "[class*='Footer' i], [id*='Footer' i], "
                                "explorug-footer, explorug-contact, #root explorug-footer, "
                                "#root explorug-contact, #root [class*='footer' i], #root [class*='Footer' i], "
                                "[class*='bottom' i], [class*='foot' i], #root > *:last-child"
                            )))
                        )
                    )
                except Exception as e:
                    debug_data["alerts"].append(f"Timeout waiting for page elements: {str(e)}")
                
                # Wait for page to be completely loaded (document ready + network idle)
                custom_print(f"⏳ Waiting for page to fully load...")
                try:
                    # Wait for document.readyState to be 'complete'
                    WebDriverWait(driver, 15).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )
                    custom_print(f"✅ Document ready state: complete")
                    
                    # Wait for any pending network requests to settle (check for stable anchor count)
                    initial_anchor_count = driver.execute_script("return document.querySelectorAll('a[href]').length")
                    time.sleep(1)
                    
                    # Check if anchor count stabilizes (no new dynamic content being added)
                    for _ in range(5):  # Max 5 checks
                        time.sleep(0.5)
                        current_anchor_count = driver.execute_script("return document.querySelectorAll('a[href]').length")
                        if current_anchor_count == initial_anchor_count:
                            break
                        initial_anchor_count = current_anchor_count
                        custom_print(f"📊 Anchor count changed: {current_anchor_count}, waiting for stability...")
                    
                    custom_print(f"✅ Page content stabilized with {initial_anchor_count} anchor tags")
                except Exception as e:
                    custom_print(f"⚠️ Page load wait issue: {e}, continuing anyway...")
                
                # Handle alerts (unchanged)
                try:
                    alert = Alert(driver)
                    alert_text = alert.text
                    debug_data["alerts"].append(f"Alert on {method_name}: {alert_text}")
                    custom_print(f"⚠️ Alert found on {url}: {alert_text}")
                    alert.dismiss()
                except:
                    pass
                
                # Check if this is a contact page
                is_contact_page = 'contact' in url.lower()

                # Progressive scrolling to trigger lazy-loaded content
                custom_print(f"🔄 Starting progressive scroll to load all content...")

                # First, scroll to top
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.5)

                # Get initial page height
                initial_height = driver.execute_script("return document.body.scrollHeight")
                page_height = initial_height

                # Scroll in steps from top to bottom
                scroll_steps = 10  # Number of scroll steps
                scroll_pause = 0.5  # Pause between scrolls

                for i in range(scroll_steps + 1):
                    scroll_position = (page_height * i) / scroll_steps
                    driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                    custom_print(f"  Scrolling... {int((i / scroll_steps) * 100)}%")
                    time.sleep(scroll_pause)

                # Ensure we're at the bottom
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)

                # Check if page height changed (indicates lazy loading happened)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height > initial_height:
                    custom_print(f"📏 Page height increased: {initial_height} → {new_height} (lazy content loaded)")
                    page_height = new_height

                # Trigger dynamic content loading and interactions
                driver.execute_script("""
                    document.querySelectorAll('button, [role="button"], [class*="load-more"], [class*="show-more"], [class*="footer" i], [class*="Footer" i], explorug-footer, explorug-contact').forEach(btn => {
                        try { btn.click(); } catch(e) {}
                    });
                    // Trigger custom events for frameworks like React
                    ['click', 'mouseover'].forEach(eventType => {
                        document.querySelectorAll('#root, footer, explorug-footer, explorug-contact, [class*="footer" i], [class*="Footer" i]').forEach(elem => {
                            try {
                                let event = new Event(eventType, { bubbles: true });
                                elem.dispatchEvent(event);
                            } catch(e) {}
                        });
                    });
                """)

                # Wait longer for contact pages to ensure all dynamic content loads
                if is_contact_page:
                    custom_print(f"📞 Contact page detected, applying aggressive loading strategy...")

                    # Try to wait for specific contact elements to load
                    try:
                        custom_print(f"⏳ Waiting for contact content to render...")
                        WebDriverWait(driver, 10).until(
                            lambda d: d.execute_script("""
                                // Check if page has typical contact content indicators
                                const text = document.body.innerText.toLowerCase();
                                return text.includes('call') ||
                                       text.includes('phone') ||
                                       text.includes('email us') ||
                                       text.includes('@') ||
                                       text.includes('showroom') ||
                                       document.querySelectorAll('a[href^="tel:"]').length > 0 ||
                                       document.querySelectorAll('a[href^="mailto:"]').length > 0;
                            """)
                        )
                        custom_print(f"✅ Contact content indicators found")
                    except:
                        custom_print(f"⚠️ Contact content indicators not found after 10s, continuing anyway...")

                    # Try to trigger Shopify section loading
                    driver.execute_script("""
                        // Force Shopify sections to load if present
                        if (window.Shopify && window.Shopify.designMode) {
                            console.log('Shopify design mode detected');
                        }
                        // Trigger load events on all sections
                        document.querySelectorAll('[class*="section"], [data-section], [id*="shopify-section"]').forEach(section => {
                            try {
                                section.dispatchEvent(new Event('load', { bubbles: true }));
                                section.dispatchEvent(new Event('shopify:section:load', { bubbles: true }));
                            } catch(e) {}
                        });
                        // Scroll to trigger intersection observers
                        window.dispatchEvent(new Event('scroll'));
                        window.dispatchEvent(new Event('resize'));
                    """)

                    # Wait for any AJAX/API calls to complete
                    time.sleep(5)

                    # Scroll back through the page one more time to catch any newly loaded content
                    custom_print(f"🔄 Second pass scroll for contact page...")
                    for i in range(scroll_steps + 1):
                        scroll_position = (page_height * i) / scroll_steps
                        driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                        time.sleep(0.5)  # Longer pause on second pass

                    # Check for height changes again
                    final_height = driver.execute_script("return document.body.scrollHeight")
                    if final_height > new_height:
                        custom_print(f"📏 Page height increased again: {new_height} → {final_height}")

                        # Content is still loading, do one more pass
                        custom_print(f"🔄 Additional content detected, third pass...")
                        for i in range(scroll_steps + 1):
                            scroll_position = (final_height * i) / scroll_steps
                            driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                            time.sleep(0.3)

                    # Final wait at bottom of page
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(3)

                    custom_print(f"✅ Contact page loading complete after extended wait")
                else:
                    time.sleep(2)
                
                # Shadow DOM and iframe processing (unchanged)
                shadow_js_script = """
                    function getShadowHTML(host) {
                        if (!host.shadowRoot) return null;
                        const result = [];
                        try {
                            [...host.shadowRoot.children].forEach(child => {
                                result.push(child.outerHTML);
                                child.querySelectorAll('*').forEach(nested => {
                                    if (nested.shadowRoot) {
                                        const nestedShadow = getShadowHTML(nested);
                                        if (nestedShadow) result.push(...nestedShadow);
                                    }
                                });
                            });
                        } catch(e) {
                            console.error('Shadow DOM traversal error:', e);
                        }
                        return result;
                    }

                    const shadowHosts = [...document.querySelectorAll('*')].filter(el => el.shadowRoot);
                    const allShadowHTML = {};
                    shadowHosts.forEach((host, index) => {
                        const tagKey = host.tagName.toLowerCase() + '_' + index;
                        allShadowHTML[tagKey] = getShadowHTML(host);
                    });
                    return allShadowHTML;
                """
                shadow_html = driver.execute_script(shadow_js_script)
                
                shadow_log_path = os.path.join(DEBUG_DIR, f"{urlparse(url).path.replace('/', '_') or 'index'}_shadow_dom.json")
                with open(shadow_log_path, "w", encoding="utf-8") as f:
                    json.dump(shadow_html, f, indent=2, ensure_ascii=False)
                custom_print(f"📝 Shadow DOM content saved to {shadow_log_path}")
                
                try:
                    for host_tag, shadow_elements in shadow_html.items():
                        if not shadow_elements:
                            continue
                        for elem_html in shadow_elements:
                            shadow_soup = BeautifulSoup(elem_html, "lxml")
                            shadow_text = shadow_soup.get_text(" ", strip=True)
                            
                            debug_data["shadow_content"].append({
                                "host_tag": host_tag,
                                "html": elem_html,
                                "text": shadow_text[:1000]
                            })
                            
                            # Extract ALL tel attributes from shadow DOM
                            shadow_tel_numbers = extract_tel_from_shadow_html(elem_html, url)
                            for phone in shadow_tel_numbers:
                                debug_data["raw_phones"].append(phone)
                                consolidated_links.add((phone, "Shadow DOM Phone"))
                                custom_print(f"✅ Found tel: in shadow DOM: {phone}")
                            
                            # Also extract phone numbers from plain text (not just tel: links)
                            # Pattern for phone numbers in text
                            phone_patterns = [
                                r'(?:\+?977[\s\-]?)?[9][0-9]{9}',  # Nepal mobile
                                r'\+?[0-9]{1,4}[\s\-]?[0-9]{6,14}',  # International
                                r'(?:\(\+?[0-9]{1,4}\)[\s\-]?)?[0-9]{6,14}',  # With country code in parens
                            ]
                            for pattern in phone_patterns:
                                for match in re.findall(pattern, shadow_text):
                                    phone = re.sub(r'[\s\-]', '', match)
                                    if phone and len(phone) >= 10 and phone not in debug_data["raw_phones"]:
                                        debug_data["raw_phones"].append(phone)
                                        consolidated_links.add((phone, "Shadow DOM Phone Text"))
                                        custom_print(f"✅ Found phone in shadow DOM text: {phone}")
                            
                            # Original extraction logic (keep for email, addresses, socials)
                            for match in EMAIL_REGEX.findall(shadow_text + " " + elem_html):
                                email = match.replace("(at)", "@").replace("(dot)", ".").replace("%40", "@").replace(" ", "")
                                email = email.replace('"email":"', "").replace('"', "")
                                if "@" in email and "." in email and not any(x in email for x in ["sentry", "wixpress"]):
                                    debug_data["raw_emails"].append(email)
                                    consolidated_links.add((email, "Shadow DOM Email"))
                            
                            for match in ADDRESS_REGEX.findall(shadow_text):
                                addr = match.strip()
                                if len(addr.split()) > 3:
                                    debug_data["raw_addresses"].append(addr)
                            
                            for platform, regex in SOCIAL_REGEXES.items():
                                matches = regex.findall(elem_html)
                                for m in matches:
                                    debug_data["raw_socials"].append((platform, m))
                                    formatted_match = m if m.startswith(('http://', 'https://')) else f"https://{m}"
                                    consolidated_links.add((formatted_match, "Shadow DOM Social"))
                            
                            for a in shadow_soup.find_all("a", href=True):
                                href = urljoin(url, a["href"])
                                consolidated_links.add((href, "Shadow DOM Link"))
                
                except Exception as e:
                    custom_print(f"❌ Error processing shadow DOM: {e}")
                    debug_data["alerts"].append(f"Error processing shadow DOM: {str(e)}")
                
                # Process iframes
                try:
                    iframes = driver.find_elements(By.TAG_NAME, "iframe")
                    for iframe in iframes:
                        try:
                            driver.switch_to.frame(iframe)
                            WebDriverWait(driver, 3).until(
                                EC.presence_of_element_located((By.TAG_NAME, "body"))
                            )
                            iframe_html = driver.page_source
                            debug_data["iframes"].append(iframe_html)
                            iframe_soup = BeautifulSoup(iframe_html, "lxml")
                            iframe_text = iframe_soup.get_text(" ", strip=True)

                            # Extract data from iframe
                            for match in EMAIL_REGEX.findall(iframe_text):
                                email = match.replace("(at)", "@").replace("(dot)", ".").replace("%40", "@").replace(" ", "")
                                email = email.replace('"email":"', "").replace('"', "")
                                if "@" in email and "." in email and not any(x in email for x in ["sentry", "wixpress"]):
                                    debug_data["raw_emails"].append(email)
                                    consolidated_links.add((email, "Iframe Email"))

                            for match in PHONE_REGEX.findall(iframe_text):
                                phone = re.sub(r'^(Tel|Phone|Call):\s*', '', match).strip()
                                if len(phone) >= 8 and re.match(r'[\d\s\-\+\(\)]{8,}', phone):
                                    debug_data["raw_phones"].append(phone)

                            for match in ADDRESS_REGEX.findall(iframe_text):
                                addr = match.strip()
                                if len(addr.split()) > 3:
                                    debug_data["raw_addresses"].append(addr)

                            for platform, regex in SOCIAL_REGEXES.items():
                                matches = regex.findall(iframe_html)
                                debug_data["raw_socials"].extend([(platform, m) for m in matches])
                                for m in matches:
                                    formatted_match = m if m.startswith(('http://', 'https://')) else f"https://{m}"
                                    consolidated_links.add((formatted_match, "Iframe Social"))

                            for a in iframe_soup.find_all("a", href=True):
                                href = urljoin(url, a["href"])
                                consolidated_links.add((href, "Iframe"))

                            driver.switch_to.default_content()
                        except Exception as e:
                            custom_print(f"Error processing iframe: {e}")
                            driver.switch_to.default_content()
                except:
                    pass
                
                # Get final page source
                html = driver.page_source
                debug_data["dynamic_html"] = html

                # Save raw HTML for contact pages for debugging
                if is_contact_page:
                    contact_html_path = os.path.join(DEBUG_DIR, f"{urlparse(url).netloc.replace('.', '_')}_contact_raw.html")
                    with open(contact_html_path, "w", encoding="utf-8") as f:
                        f.write(html)
                    custom_print(f"📝 Contact page HTML saved to {contact_html_path}")

                    # Debug: Check what contact-related content is in the page
                    page_text = driver.execute_script("return document.body.innerText.toLowerCase();")
                    has_phone_keyword = 'phone' in page_text or 'call' in page_text
                    has_email_keyword = 'email' in page_text or '@' in page_text
                    has_tel_links = driver.execute_script("return document.querySelectorAll('a[href^=\"tel:\"]').length;")
                    has_mailto_links = driver.execute_script("return document.querySelectorAll('a[href^=\"mailto:\"]').length;")

                    custom_print(f"🔍 Contact page content check:")
                    custom_print(f"  - Phone/call keywords: {'✅' if has_phone_keyword else '❌'}")
                    custom_print(f"  - Email/@ keywords: {'✅' if has_email_keyword else '❌'}")
                    custom_print(f"  - tel: links: {has_tel_links}")
                    custom_print(f"  - mailto: links: {has_mailto_links}")

                custom_print(f"📧 Shadow emails found: {list(set(debug_data['raw_emails']))}")
                custom_print(f"📞 Shadow phones found: {list(set(debug_data['raw_phones']))}")
                custom_print(f"🏠 Shadow addresses found: {list(set(debug_data['raw_addresses']))}")
                custom_print(f"🌐 Shadow socials found: {debug_data['raw_socials']}")

                return html, debug_data
                
            except Exception as e:
                error_detail = {
                    "method": method_name,
                    "attempt": attempt + 1,
                    "error_type": type(e).__name__,
                    "message": str(e),
                    "http_status": None,
                    "current_url": driver.current_url if hasattr(driver, 'current_url') else "Unknown"
                }
                try:
                    import requests
                    response = requests.head(url, timeout=10, allow_redirects=True)
                    error_detail["http_status"] = response.status_code
                except Exception as req_e:
                    custom_print(f"❌ Failed to get HTTP status for {url}: {req_e}")
                
                debug_data["error_details"].append(error_detail)
                debug_data["alerts"].append(f"{method_name}_attempt_{attempt + 1}_error: {str(e)}")
                custom_print(f"❌ {method_name} method failed for {url} (attempt {attempt + 1}): {error_detail['error_type']}: {error_detail['message']} (HTTP Status: {error_detail['http_status']})")
                continue
        
        time.sleep(random.uniform(3, 7))
    
    custom_print(f"❌ All access methods failed for {url}")
    debug_data["dynamic_html"] = "All access methods failed"
    debug_data["status"] = "failed"
    return "", debug_data