import json
import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import random
import time 

from config import (
    EMAIL_REGEX, PHONE_REGEX, ADDRESS_REGEX, SOCIAL_REGEXES, 
    REACT_ATTRIBUTES, DEBUG_DIR, USER_AGENTS
)
from utils import custom_print, validate_social_link, save_contact_page_log, extract_structured_address

# Load country codes for phone validation
COUNTRY_CODES = {}
ALL_SCRAPED_LINKS = {}

# Lazy model loading with status tracking
import threading

pipeline = None
model_status = {
    "status": "not_started",  # not_started, loading, loaded, failed
    "message": "Model not yet initialized",
    "progress": 0
}
_model_lock = threading.Lock()
_model_loading = False

def get_model_status():
    """Get the current model loading status"""
    return model_status.copy()

def load_model_async():
    """Load the model in a background thread"""
    global pipeline, model_status, _model_loading

    with _model_lock:
        if _model_loading or model_status["status"] == "loaded":
            return
        _model_loading = True

    def _load():
        global pipeline, model_status, _model_loading
        try:
            model_status["status"] = "loading"
            model_status["message"] = "Importing libraries..."
            model_status["progress"] = 10

            import transformers
            import torch

            model_status["message"] = "Loading model weights..."
            model_status["progress"] = 30

            from config import MODEL_NAME
            print(f"🔄 Loading model: {MODEL_NAME}")

            # Use cached model from image build
            import os
            cache_dir = os.environ.get("HF_HUB_CACHE", "/root/.cache/huggingface")

            pipeline = transformers.pipeline(
                "text-generation",
                model=MODEL_NAME,
                model_kwargs={
                    "torch_dtype": torch.bfloat16,
                    "cache_dir": cache_dir,
                },
                device_map="auto",
            )

            model_status["status"] = "loaded"
            model_status["message"] = f"Model loaded: {MODEL_NAME}"
            model_status["progress"] = 100
            print(f"✅ Model loaded successfully: {MODEL_NAME}")

        except Exception as e:
            model_status["status"] = "failed"
            model_status["message"] = f"Failed to load model: {str(e)}"
            model_status["progress"] = 0
            print(f"❌ Failed to load Llama model: {e}")
            pipeline = None
        finally:
            _model_loading = False

    thread = threading.Thread(target=_load, daemon=True)
    thread.start()

def get_pipeline():
    """Get the model pipeline, starting load if not started"""
    global pipeline
    if model_status["status"] == "not_started":
        load_model_async()
    return pipeline

def extract_all_tel_attributes(html, url):
    """Extract ALL tel: attributes from HTML regardless of format"""
    tel_numbers = set()
    
    # Use BeautifulSoup to find all elements with tel: href
    soup = BeautifulSoup(html, "lxml")
    
    # Find all anchor tags with href starting with tel:
    tel_links = soup.find_all('a', href=lambda x: x and x.startswith('tel:'))
    
    for link in tel_links:
        tel_href = link['href']
        # Extract the phone number after tel:
        phone = tel_href[4:].strip()  # Remove "tel:" prefix
        
        # Clean common separators but preserve the number
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
        
        # Remove any parameters after ? or #
        phone = phone.split('?')[0].split('#')[0]
        
        # Remove any non-numeric characters except +
        phone = re.sub(r'[^\d+]', '', phone)
        
        if phone:  # Only add if we got something
            tel_numbers.add(phone)
            custom_print(f"📞 Found tel: attribute: {tel_href} -> {phone}")
    
    # Also check for tel: in onclick attributes and other places
    for tag in soup.find_all(attrs={'onclick': True}):
        onclick = tag['onclick']
        if 'tel:' in onclick.lower():
            # Extract tel: from onclick
            tel_match = re.search(r'tel:([^\'"\s]+)', onclick, re.IGNORECASE)
            if tel_match:
                phone = tel_match.group(1).strip()
                phone = re.sub(r'[^\d+]', '', phone)
                if phone:
                    tel_numbers.add(phone)
                    custom_print(f"📞 Found tel: in onclick: {phone}")
    
    # Check for data-tel, data-phone attributes
    for tag in soup.find_all(attrs={'data-tel': True}):
        phone = tag['data-tel'].strip()
        phone = re.sub(r'[^\d+]', '', phone)
        if phone:
            tel_numbers.add(phone)
            custom_print(f"📞 Found data-tel: {phone}")
    
    for tag in soup.find_all(attrs={'data-phone': True}):
        phone = tag['data-phone'].strip()
        phone = re.sub(r'[^\d+]', '', phone)
        if phone:
            tel_numbers.add(phone)
            custom_print(f"📞 Found data-phone: {phone}")
    
    # Check for tel: in meta tags
    for meta in soup.find_all('meta', attrs={'content': True}):
        if 'tel:' in meta['content'].lower():
            tel_match = re.search(r'tel:([^\'"\s]+)', meta['content'], re.IGNORECASE)
            if tel_match:
                phone = tel_match.group(1).strip()
                phone = re.sub(r'[^\d+]', '', phone)
                if phone:
                    tel_numbers.add(phone)
                    custom_print(f"📞 Found tel: in meta tag: {phone}")
    
    return list(tel_numbers)

def extract_location_info(text):
    current_pipeline = get_pipeline()
    if not current_pipeline:
        status = get_model_status()
        if status["status"] == "loading":
            custom_print("⏳ Model is still loading, returning N/A for location info")
            return {"country": "N/A (Model loading...)"}
        custom_print("❌ Llama model not loaded, returning N/A for location info")
        return {"country": "N/A"}

    prompt = f"""From this text, extract ONLY the country where the company is located. If no country is mentioned, return "N/A".
    
Text: {text[:2000]}  # Limit text length
    
Return a JSON object with a "country" key. Example: {{"country": "United States"}} or {{"country": "N/A"}}
Return ONLY the JSON object, no additional text."""

    messages = [
        {"role": "system", "content": "You are a helpful assistant that extracts country information from text. Return ONLY valid JSON."},
        {"role": "user", "content": prompt}
    ]

    try:
        outputs = current_pipeline(
            messages,
            max_new_tokens=100,
            temperature=0.1,
        )
        content = outputs[0]["generated_text"][-1]["content"]

        # Extract JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0))
            country = result.get("country", "N/A")
            custom_print(f"🌍 Extracted country: {country}")
            return {"country": country}

        return {"country": "N/A"}

    except Exception as e:
        custom_print(f"❌ Error extracting country info: {e}")
        return {"country": "N/A"}

def generate_business_nature(meta_desc, about_content):
    current_pipeline = get_pipeline()
    if not current_pipeline:
        status = get_model_status()
        if status["status"] == "loading":
            custom_print("⏳ Model is still loading, returning N/A for business nature")
            return "N/A (Model loading...)"
        custom_print("❌ Llama model not loaded, returning N/A for business nature")
        return "N/A (Llama model not loaded)"

    # Use footer text or combined text if about_content is empty
    input_text = ""
    if meta_desc:
        input_text += f"Meta Description: {meta_desc}\n"
    if about_content:
        input_text += f"About Us: {about_content[:1500]}"
    
    # If no proper inputs, return a generic message
    if len(input_text.strip()) < 50:
        custom_print("⚠️ Insufficient content for business nature generation")
        return "N/A (Insufficient company information)"
    
    prompt = f"""Based on the following company information, describe the business nature in one concise sentence. Focus on what the company does, its industry, and its main products/services.

{input_text}

Describe the business nature concisely. Example: "A technology company specializing in software development for e-commerce platforms."
Return only the description, no additional text or explanations."""

    messages = [
        {"role": "system", "content": "You are a business analyst that describes company business nature based on available information."},
        {"role": "user", "content": prompt}
    ]

    try:
        outputs = current_pipeline(
            messages,
            max_new_tokens=100,
            temperature=0.1,
        )
        content = outputs[0]["generated_text"][-1]["content"].strip()

        # Clean up the response
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        
        # Remove any prefix like "Business nature:" or similar
        content = re.sub(r'^(?:Business\s*Nature|Description|Company\s*Description):\s*', '', content, flags=re.IGNORECASE)

        with open(os.path.join(DEBUG_DIR, "llama_business_nature_output.txt"), "a", encoding="utf-8") as f:
            f.write(f"Input: {input_text}\nLlama output: {content}\n\n")

        custom_print(f"🏢 Generated business nature: {content}")
        return content
    except Exception as e:
        custom_print(f"❌ Error generating business nature with Llama: {e}")
        return "N/A (Error in Llama generation)"

def fallback_keyword_matching(links):
    """Fallback to simple keyword matching when Llama fails"""
    about_links = []
    contact_links = []
    
    about_patterns = [
        r'.*about.*', r'.*story.*', r'.*team.*', r'.*company.*', r'.*mission.*',
        r'.*vision.*', r'.*who.*we.*are.*', r'.*history.*', r'.*leadership.*',
        r'.*values.*', r'.*culture.*', r'.*philosophy.*'
    ]
    
    contact_patterns = [
        r'.*contact.*', r'.*get.*in.*touch.*', r'.*reach.*us.*', r'.*support.*',
        r'.*help.*', r'.*customer.*service.*', r'.*inquiries.*', r'.*feedback.*'
    ]
    
    for link in links:
        url = link.get('url', '').lower()
        text = link.get('text', '').lower()
        
        # Check for about pages
        for pattern in about_patterns:
            if re.search(pattern, url) or re.search(pattern, text):
                about_links.append(link)
                break
        
        # Check for contact pages
        for pattern in contact_patterns:
            if re.search(pattern, url) or re.search(pattern, text):
                contact_links.append(link)
                break
    
    return {
        "about": about_links,
        "contact": contact_links
    }

def load_country_codes():
    """Load country codes from countryCode.txt file"""
    global COUNTRY_CODES
    try:
        country_code_file = os.path.join(os.path.dirname(__file__), "countryCode.txt")
        with open(country_code_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '\t' in line:
                    parts = line.split('\t')
                    if len(parts) == 2:
                        country = parts[0].strip()
                        codes = parts[1].strip()
                        # Handle multiple codes (e.g., "1-787, 1-939")
                        for code in codes.split(','):
                            code = code.strip()
                            COUNTRY_CODES[code] = country
        custom_print(f"✅ Loaded {len(COUNTRY_CODES)} country codes")
    except Exception as e:
        print(f"❌ Failed to load country codes: {e}")

def validate_phone_with_country_code(phone):
    """Validate phone number against country codes and return phone with country info"""
    # Extract potential country code from phone number
    phone_clean = re.sub(r'[^\d+]', '', phone)

    # Check if phone starts with +
    if phone_clean.startswith('+'):
        phone_clean = phone_clean[1:]

    # Try to match country codes (from longest to shortest)
    matched_country = None
    matched_code = None

    # Sort codes by length (longest first) to match more specific codes first
    sorted_codes = sorted(COUNTRY_CODES.keys(), key=lambda x: len(x.replace('-', '')), reverse=True)

    for code in sorted_codes:
        code_clean = code.replace('-', '')
        if phone_clean.startswith(code_clean):
            matched_code = code
            matched_country = COUNTRY_CODES[code]
            break

    if matched_country:
        return {
            "phone": phone,
            "country_code": matched_code,
            "country": matched_country,
            "is_valid": True
        }
    else:
        return {
            "phone": phone,
            "country_code": "Unknown",
            "country": "Unknown",
            "is_valid": False
        }

def fetch_about_page_content(url, timeout=10):
    """
    Fetch an about page and extract meaningful text content

    Args:
        url: URL of the about page
        timeout: Request timeout in seconds

    Returns:
        Extracted text from the about page
    """
    try:
        custom_print(f"🔍 Fetching about page: {url}")

        # Create a session with retries
        session = requests.Session()
        retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        session.mount('http://', HTTPAdapter(max_retries=retries))

        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()

        # Extract paragraphs from the fetched HTML
        content = extract_paragraphs_from_html(response.text, max_paragraphs=5, min_length=50)

        if content:
            custom_print(f"✅ Successfully extracted {len(content)} chars from about page")
            return content
        else:
            custom_print(f"⚠️ No meaningful content found on about page")
            return ""

    except requests.Timeout:
        custom_print(f"⏱️ Timeout fetching about page: {url}")
        return ""
    except requests.RequestException as e:
        custom_print(f"❌ Error fetching about page {url}: {e}")
        return ""
    except Exception as e:
        custom_print(f"❌ Unexpected error fetching about page: {e}")
        return ""

def extract_paragraphs_from_html(html, max_paragraphs=5, min_length=50):
    """
    Extract first few meaningful paragraphs from HTML content

    Args:
        html: HTML content to extract from
        max_paragraphs: Maximum number of paragraphs to extract
        min_length: Minimum length of paragraph to consider

    Returns:
        String containing concatenated paragraphs
    """
    try:
        soup = BeautifulSoup(html, "lxml")

        # Remove script, style, and other non-content elements
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'meta', 'link']):
            tag.decompose()

        paragraphs = []

        # Try to find paragraphs in common content areas first
        content_areas = soup.find_all(['article', 'main', 'div'], class_=re.compile(r'content|about|main|article', re.I))

        # If content areas found, search within them, otherwise search whole page
        search_area = content_areas[0] if content_areas else soup

        # Extract paragraphs
        for p in search_area.find_all('p'):
            text = p.get_text(strip=True)
            # Filter out short paragraphs, navigation text, etc.
            if len(text) >= min_length and not text.lower().startswith(('cookie', 'privacy', 'terms')):
                paragraphs.append(text)
                if len(paragraphs) >= max_paragraphs:
                    break

        # If not enough paragraphs found, try divs and sections
        if len(paragraphs) < max_paragraphs:
            for tag in search_area.find_all(['div', 'section']):
                text = tag.get_text(strip=True)
                # Avoid duplicates and too large blocks
                if (len(text) >= min_length and len(text) < 1000 and
                    text not in paragraphs and
                    not text.lower().startswith(('cookie', 'privacy', 'terms'))):
                    paragraphs.append(text)
                    if len(paragraphs) >= max_paragraphs:
                        break

        result = " ".join(paragraphs[:max_paragraphs])
        custom_print(f"📝 Extracted {len(paragraphs)} paragraphs ({len(result)} chars)")
        return result

    except Exception as e:
        custom_print(f"❌ Error extracting paragraphs: {e}")
        return ""

def identify_about_contact_links(links):
    """
    Simple keyword matching for About Us and Contact Us pages
    Now also checks ALL scraped pages
    """
    custom_print(f"🔍 Keyword matching for About/Contact pages...")
    
    # First check the provided links
    about_links = []
    contact_links = []
    seen_urls = set()
    
    for link in links:
        if not isinstance(link, dict) or 'url' not in link:
            continue
            
        url = link.get('url', '')
        if not url or url in seen_urls:
            continue
            
        seen_urls.add(url)
        
        # Convert to lowercase for case-insensitive matching
        url_lower = url.lower()
        text = link.get('text', '').lower()
        
        # SIMPLE CHECK: If URL contains 'about' -> About page
        if 'about' in url_lower:
            about_links.append(link)
            custom_print(f"✅ About page (contains 'about'): {url}")
            continue
            
        # SIMPLE CHECK: If URL contains 'contact' -> Contact page  
        if 'contact' in url_lower:
            contact_links.append(link)
            custom_print(f"✅ Contact page (contains 'contact'): {url}")
            continue
    
    # Also check ALL scraped pages for more links
    custom_print("🔍 Also checking ALL scraped pages for About/Contact links...")
    all_pages_results = identify_about_contact_links_from_all_pages()
    
    # Combine results
    for link in all_pages_results.get('about', []):
        if link.get('url') not in seen_urls:
            about_links.append(link)
            seen_urls.add(link.get('url'))
    
    for link in all_pages_results.get('contact', []):
        if link.get('url') not in seen_urls:
            contact_links.append(link)
            seen_urls.add(link.get('url'))
    
    custom_print(f"✅ Total found {len(about_links)} About pages and {len(contact_links)} Contact pages (including all scraped pages)")
    
    # Show what we found
    if about_links:
        custom_print("About pages found:")
        for link in about_links[:5]:  # Show first 5
            custom_print(f"  - {link.get('url')}")
    
    if contact_links:
        custom_print("Contact pages found:")
        for link in contact_links[:5]:  # Show first 5
            custom_print(f"  - {link.get('url')}")
    
    return {"about": about_links, "contact": contact_links}

def get_all_scraped_links():
    """
    Get ALL links from ALL scraped pages
    Returns a deduplicated list of all links found
    """
    global ALL_SCRAPED_LINKS
    
    all_links = []
    seen_urls = set()
    
    custom_print(f"📊 Getting ALL links from {len(ALL_SCRAPED_LINKS)} scraped pages")
    
    for page_url, page_data in ALL_SCRAPED_LINKS.items():
        # Get all link types from this page
        link_sources = [
            page_data.get('all_hrefs', []),
            page_data.get('navbar_links', []),
            page_data.get('footer_nav_links', [])
        ]
        
        for link_source in link_sources:
            for link in link_source:
                if isinstance(link, dict) and 'url' in link:
                    url = link.get('url', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_links.append(link)
    
    custom_print(f"📊 Collected {len(all_links)} unique links from {len(ALL_SCRAPED_LINKS)} scraped pages")
    return all_links

def identify_about_contact_links_from_all_pages():
    """
    Identify About/Contact pages from ALL links found in ALL scraped pages
    """
    custom_print("🔍 Searching for About/Contact pages from ALL scraped pages...")
    
    all_links = get_all_scraped_links()
    
    about_links = []
    contact_links = []
    seen_urls = set()
    
    for link in all_links:
        if not isinstance(link, dict) or 'url' not in link:
            continue
            
        url = link.get('url', '')
        if not url or url in seen_urls:
            continue
            
        seen_urls.add(url)
        
        # Convert to lowercase for case-insensitive matching
        url_lower = url.lower()
        text = link.get('text', '').lower()
        
        # SIMPLE CHECK: If URL contains 'about' -> About page
        if 'about' in url_lower:
            about_links.append(link)
            custom_print(f"✅ About page found in scraped links: {url}")
            continue
            
        # SIMPLE CHECK: If URL contains 'contact' -> Contact page  
        if 'contact' in url_lower:
            contact_links.append(link)
            custom_print(f"✅ Contact page found in scraped links: {url}")
            continue
    
    custom_print(f"✅ Found {len(about_links)} About pages and {len(contact_links)} Contact pages from ALL scraped pages")
    
    # Show what we found
    if about_links:
        custom_print("About pages found in all scraped pages:")
        for link in about_links[:5]:  # Show first 5
            custom_print(f"  - {link.get('url')}")
    
    if contact_links:
        custom_print("Contact pages found in all scraped pages:")
        for link in contact_links[:5]:  # Show first 5
            custom_print(f"  - {link.get('url')}")
    
    return {"about": about_links, "contact": contact_links}

def scrape_info(html, is_about_page=False, is_contact_page=False, url="unknown", debug_data=None):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    
    # Initialize sets and lists for collecting data
    emails = set()
    phones = set()
    addresses = set()
    socials = {platform: [] for platform in SOCIAL_REGEXES.keys()}
    all_hrefs = []  # List to store all <a href> tags with metadata
    footer_tracking = []
    
    # Helper function to extract ALL tel attributes
    def extract_all_tel_from_html(html_content):
        """Extract ALL tel: attributes from HTML"""
        tel_numbers = set()
        soup_temp = BeautifulSoup(html_content, "lxml")
        
        # 1. Extract from href="tel:"
        for link in soup_temp.find_all('a', href=True):
            if link['href'].lower().startswith('tel:'):
                phone = link['href'][4:].strip()
                phone = re.sub(r'[^\d+]', '', phone)
                if phone:
                    tel_numbers.add(phone)
        
        # 2. Extract from onclick="tel:"
        for tag in soup_temp.find_all(attrs={'onclick': True}):
            onclick = tag['onclick']
            if 'tel:' in onclick.lower():
                matches = re.findall(r'tel:([^\'"\s;]+)', onclick, re.IGNORECASE)
                for match in matches:
                    phone = re.sub(r'[^\d+]', '', match)
                    if phone:
                        tel_numbers.add(phone)
        
        # 3. Extract from data-tel, data-phone attributes
        for tag in soup_temp.find_all(attrs={'data-tel': True}):
            phone = re.sub(r'[^\d+]', '', tag['data-tel'])
            if phone:
                tel_numbers.add(phone)
        
        for tag in soup_temp.find_all(attrs={'data-phone': True}):
            phone = re.sub(r'[^\d+]', '', tag['data-phone'])
            if phone:
                tel_numbers.add(phone)
        
        # 4. Extract from meta tags
        for meta in soup_temp.find_all('meta', attrs={'content': True}):
            if 'tel:' in meta['content'].lower():
                matches = re.findall(r'tel:([^\'"\s]+)', meta['content'], re.IGNORECASE)
                for match in matches:
                    phone = re.sub(r'[^\d+]', '', match)
                    if phone:
                        tel_numbers.add(phone)
        
        return tel_numbers
    
    # Helper function to verify and categorize hrefs - DO NOT strip #
    def verify_href(href, source):
        # DON'T strip # - keep everything as is for SPA URLs
        href_clean = href.strip('"\'').rstrip('/')  # Only strip quotes, NOT #
        
        # CAPTURE ALL tel: ATTRIBUTES - NO REGEX RESTRICTIONS
        if href.lower().startswith("tel:"):
            phone = href[4:].strip()  # Remove "tel:" prefix
            # Clean but preserve + and digits
            phone = re.sub(r'[^\d+]', '', phone)
            if phone:  # Only add if we got something
                phones.add(phone)
                footer_tracking.append(f"Phone found in {source} tel: attribute: {phone}")
                custom_print(f"✅ Captured {source} tel: attribute: {href} -> {phone}")
                return {"url": href, "source": source, "type": "Phone", "page_url": url}
        
        # Check for email (mailto: links)
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0]
            if EMAIL_REGEX.match(email) and not any(x in email for x in ["sentry", "wixpress"]):
                emails.add(email)
                footer_tracking.append(f"Email found in {source} <a href>: {email}")
                custom_print(f"✅ Verified {source} email: {email}")
                return {"url": href, "source": source, "type": "Email", "page_url": url}
        
        # Check for social media links with validation
        for platform, regex in SOCIAL_REGEXES.items():
            if regex.search(href_clean):
                validated_link = validate_social_link(href_clean, platform)
                if validated_link:
                    if validated_link not in socials[platform]:
                        socials[platform].append(validated_link)
                        footer_tracking.append(f"Social {platform} found in {source} <a href>: {validated_link}")
                        custom_print(f"✅ Verified {source} {platform} link: {validated_link}")
                    return {"url": validated_link, "source": source, "type": f"Social ({platform})", "page_url": url}
                else:
                    custom_print(f"🚫 Rejected {platform} media/invalid URL: {href_clean}")
        
        # Standard link - make absolute if relative, but KEEP the hash fragment
        # Check if it's already a full URL
        if href.startswith(('http://', 'https://', 'tel:', 'mailto:', '#', 'javascript:')):
            # Already a full URL or special protocol
            return {"url": href, "source": source, "type": "Link", "page_url": url}
        else:
            # Make it absolute relative to current page
            absolute_url = urljoin(url, href)
            return {"url": absolute_url, "source": source, "type": "Link", "page_url": url}

    # Collect ALL <a href> tags from the entire page - DO NOT split by #
    excluded_extensions = ['.pdf', '.jpg', '.png', '.jpeg', '.gif']
    
    # METHOD 1: Direct href extraction from <a> tags
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href and not any(href.lower().endswith(ext) for ext in excluded_extensions):
            href_info = verify_href(href, "Page")
            all_hrefs.append(href_info)
    
    # METHOD 2: Extract links from onclick handlers and other JavaScript
    for tag in soup.find_all(attrs={'onclick': True}):
        onclick = tag['onclick']
        # Look for URLs in onclick handlers
        url_patterns = [
            r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]",
            r"window\.open\s*\(\s*['\"]([^'\"]+)['\"]",
            r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
            r"\.navigate\s*\(\s*['\"]([^'\"]+)['\"]",
            r"\.push\s*\(\s*['\"]([^'\"]+)['\"]",
            r"\.go\s*\(\s*['\"]([^'\"]+)['\"]"
        ]
        
        for pattern in url_patterns:
            matches = re.findall(pattern, onclick, re.IGNORECASE)
            for match in matches:
                if match and not any(match.lower().endswith(ext) for ext in excluded_extensions):
                    href_info = verify_href(match, "JavaScript onclick")
                    all_hrefs.append(href_info)
    
    # METHOD 3: Extract from data attributes (common in SPAs)
    for tag in soup.find_all(attrs=True):
        for attr_name, attr_value in tag.attrs.items():
            if isinstance(attr_value, str) and any(keyword in attr_name.lower() for keyword in ['href', 'url', 'link', 'route', 'path']):
                if attr_value and not any(attr_value.lower().endswith(ext) for ext in excluded_extensions):
                    href_info = verify_href(attr_value, f"Data attribute {attr_name}")
                    all_hrefs.append(href_info)
    
    # METHOD 4: Extract from script tags (look for router configurations)
    for script in soup.find_all('script'):
        if script.string:
            script_content = script.string
            # Look for router configurations common in SPAs
            router_patterns = [
                r'path:\s*["\']([^"\']+)["\']',
                r'url:\s*["\']([^"\']+)["\']',
                r'route:\s*["\']([^"\']+)["\']',
                r'\.when\s*\(\s*["\']([^"\']+)["\']',
                r'path:\s*["\']([^"\']+)["\']',
                r'\.route\s*\(\s*["\']([^"\']+)["\']'
            ]
            
            for pattern in router_patterns:
                matches = re.findall(pattern, script_content, re.IGNORECASE)
                for match in matches:
                    if match and '/contact' in match.lower():
                        # This looks like a contact route
                        contact_route = match
                        if not contact_route.startswith(('http://', 'https://')):
                            contact_route = f"#{contact_route}" if contact_route.startswith('/') else f"#/{contact_route}"
                        href_info = verify_href(contact_route, "JavaScript router")
                        all_hrefs.append(href_info)
    
    # STEP 1: Extract ALL tel attributes from main HTML (MOST IMPORTANT)
    custom_print(f"📞 Step 1: Extracting ALL tel attributes from HTML...")
    all_tel_numbers = extract_all_tel_from_html(html)
    for phone in all_tel_numbers:
        if phone not in phones:
            phones.add(phone)
            footer_tracking.append(f"Phone found in HTML tel attribute: {phone}")
            custom_print(f"✅ Captured tel attribute: {phone}")

    # Integrate shadow DOM data - THIS IS CRITICAL FOR SPA LINKS
    if debug_data:
        custom_print("🔄 Integrating shadow DOM data...")
        
        # Integrate shadow DOM emails
        if debug_data.get("raw_emails"):
            for email in debug_data["raw_emails"]:
                clean_email = email.replace("(at)", "@").replace("(dot)", ".").replace("%40", "@").replace(" ", "")
                clean_email = clean_email.replace('"email":"', "").replace('"', "")
                if "@" in clean_email and "." in clean_email and not any(x in clean_email for x in ["sentry", "wixpress"]):
                    emails.add(clean_email)
                    footer_tracking.append(f"Email found in shadow DOM: {clean_email}")
                    custom_print(f"✅ Added shadow DOM email: {clean_email}")
        
        # Integrate shadow DOM phones
        if debug_data.get("raw_phones"):
            for phone in debug_data["raw_phones"]:
                clean_phone = re.sub(r'^(Tel|Phone|Call):\s*', '', phone).strip()
                clean_phone = clean_phone.replace("tel:", "").strip()
                clean_phone = re.sub(r'[^\d+]', '', clean_phone)
                if clean_phone and clean_phone not in phones:
                    phones.add(clean_phone)
                    footer_tracking.append(f"Phone found in shadow DOM: {clean_phone}")
                    custom_print(f"✅ Added shadow DOM phone: {clean_phone}")
        
        # Integrate shadow DOM addresses
        if debug_data.get("raw_addresses"):
            for addr in debug_data["raw_addresses"]:
                clean_addr = addr.strip()
                if len(clean_addr.split()) > 3:
                    addresses.add(clean_addr)
                    footer_tracking.append(f"Address found in shadow DOM: {clean_addr}")
                    custom_print(f"✅ Added shadow DOM address: {clean_addr}")
        
        # Integrate shadow DOM social media links with validation
        if debug_data.get("shadow_content"):
            for shadow_item in debug_data["shadow_content"]:
                shadow_soup = BeautifulSoup(shadow_item["html"], "lxml")
                
                # Process shadow DOM HTML for ALL links (not just social)
                for a in shadow_soup.find_all("a", href=True):
                    href = a["href"]
                    if href and not any(href.lower().endswith(ext) for ext in excluded_extensions):
                        href_info = verify_href(href, "Shadow DOM")
                        all_hrefs.append(href_info)
                        custom_print(f"✅ Found link in shadow DOM: {href}")
                
                # Also extract from shadow DOM text for potential contact info
                shadow_text = shadow_soup.get_text(" ", strip=True)
                if 'contact' in shadow_text.lower() and 'href' not in shadow_text.lower():
                    # Check if there are any contact-related elements
                    contact_elements = shadow_soup.find_all(text=re.compile(r'contact', re.IGNORECASE))
                    for element in contact_elements:
                        parent = element.parent
                        if parent.name == 'a' and parent.get('href'):
                            # Already captured above
                            pass
                        elif parent.name in ['div', 'span', 'button', 'li']:
                            # Look for onclick or data attributes
                            if parent.get('onclick'):
                                onclick = parent.get('onclick')
                                if 'contact' in onclick.lower():
                                    custom_print(f"⚠️ Potential contact link in shadow DOM onclick: {onclick[:50]}...")
                
                # Process shadow DOM HTML for social links
                for platform, regex in SOCIAL_REGEXES.items():
                    matches = regex.findall(shadow_item["html"])
                    for match in matches:
                        cleaned_match = match.strip('"\' #').rstrip('/')
                        if not cleaned_match.startswith(('http://', 'https://')):
                            cleaned_match = f"https://{cleaned_match}"
                        
                        validated_link = validate_social_link(cleaned_match, platform)
                        if validated_link and validated_link not in socials[platform]:
                            socials[platform].append(validated_link)
                            footer_tracking.append(f"Social {platform} found in shadow DOM: {validated_link}")
                            custom_print(f"✅ Added shadow DOM {platform} link: {validated_link}")
                        elif not validated_link:
                            custom_print(f"🚫 Rejected shadow DOM {platform} media/invalid URL: {cleaned_match}")
                
                # Extract tel from shadow DOM
                shadow_tel_numbers = extract_all_tel_from_html(shadow_item["html"])
                for phone in shadow_tel_numbers:
                    if phone not in phones:
                        phones.add(phone)
                        custom_print(f"✅ Found tel in shadow DOM: {phone}")

    # Enhanced footer detection
    footer_selector = (
        'footer, *[tagName*="footer" i], [class*="footer" i], [id*="footer" i], '
        '[role="contentinfo"], explorug-footer, explorug-contact, #root explorug-footer, '
        '#root explorug-contact, #root [class*="footer" i], #root [class*="Footer" i], '
        '[class*="bottom" i], [class*="foot" i], #root > *:last-child, '
        '[class*="social" i], [class*="list-social" i]'
    )
    footer = soup.select_one(footer_selector)
    
    footer_html = str(footer) if footer else "No footer found"
    footer_text = footer.get_text(" ", strip=True) if footer else ""
    
    # Extract footerURL attribute if present
    footer_urls = []
    if footer and footer.get("footerURL"):
        try:
            footer_url_data = json.loads(footer.get("footerURL").replace("'", "\""))
            footer_urls = [
                {"key": key, "url": url} for key, url in footer_url_data.items()
                if url.startswith(('http://', 'https://'))
            ]
            for item in footer_urls:
                all_hrefs.append(verify_href(item["url"], "Footer footerURL"))
        except json.JSONDecodeError:
            custom_print(f"❌ Failed to parse footerURL attribute: {footer.get('footerURL')}")
    
    # Collect <a href> tags from footer - KEEP hash fragments
    footer_nav_links = []  # Store footer navigation links for Llama processing
    if footer:
        for href_elem in footer.select('a[href]'):
            if href_elem["href"] and not any(href_elem["href"].lower().endswith(ext) for ext in excluded_extensions):
                # Pass raw href to verify_href
                href_info = verify_href(href_elem["href"], "Footer")
                all_hrefs.append(href_info)
                # For navigation tracking
                absolute_url = href_info["url"]
                link_text = href_elem.get_text().strip()
                if absolute_url.startswith(('http://', 'https://')):
                    footer_nav_links.append({"url": absolute_url, "text": link_text})

    # Collect navbar/header links - KEEP hash fragments
    navbar_links = []
    navbar_selector = (
        'nav, header, [role="navigation"], [class*="nav" i], [class*="menu" i], '
        '[class*="header" i], [id*="nav" i], [id*="menu" i], [id*="header" i]'
    )
    navbar = soup.select(navbar_selector)
    for nav_elem in navbar:
        for href_elem in nav_elem.select('a[href]'):
            if href_elem["href"] and not any(href_elem["href"].lower().endswith(ext) for ext in excluded_extensions):
                href_info = verify_href(href_elem["href"], "Navbar")
                all_hrefs.append(href_info)
                absolute_url = href_info["url"]
                link_text = href_elem.get_text().strip()
                if absolute_url.startswith(('http://', 'https://')):
                    navbar_links.append({"url": absolute_url, "text": link_text})

    custom_print(f"📋 Collected {len(navbar_links)} navbar links and {len(footer_nav_links)} footer links")
    custom_print(f"📊 Total unique hrefs collected: {len(all_hrefs)}")

    # Store ALL links from this page in the global tracker
    global ALL_SCRAPED_LINKS
    ALL_SCRAPED_LINKS[url] = {
        'all_hrefs': all_hrefs.copy(),  # Store all hrefs
        'navbar_links': navbar_links.copy(),  # Store navbar links
        'footer_nav_links': footer_nav_links.copy(),  # Store footer nav links
        'timestamp': time.time()
    }
    custom_print(f"💾 Stored {len(all_hrefs)} links from {url} in global tracker")

    # Process page sections
    about_sections = []
    contact_sections = []
    contact_text = ""
    
    if is_about_page:
        about_sections = soup.select('div[class*="about" i], section[class*="about" i], article[class*="about" i], '
                                   'div[id*="about" i], section[id*="about" i], article[id*="about" i], '
                                   'div[class*="company" i], div[class*="mission" i], div[class*="vision" i], '
                                   'div[id*="company" i], div[id*="mission" i], div[id*="vision" i]')
        if not about_sections:
            about_sections = [soup.select_one('main, [role="main"], body')]

    if is_contact_page:
        contact_sections = soup.select('div[class*="contact" i], section[class*="contact" i], article[class*="contact" i], '
                                     'div[id*="contact" i], section[id*="contact" i], article[id*="contact" i], '
                                     'div[class*="reach-us" i], div[class*="get-in-touch" i], '
                                     'div[id*="reach-us" i], div[id*="get-in-touch" i], '
                                     'explorug-contact')
        if not contact_sections:
            contact_sections = [soup.select_one('main, [role="main"], body')]

        for section in contact_sections:
            if section:
                section_text = section.get_text(" ", strip=True)
                if section_text:
                    contact_text += section_text + " "

        # Save contact section text for debugging
        if contact_text:
            contact_text_path = os.path.join(DEBUG_DIR, f"{urlparse(url).netloc.replace('.', '_')}_contact_text.txt")
            with open(contact_text_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {url}\n\n")
                f.write(f"Contact Section Text ({len(contact_text)} characters):\n")
                f.write("=" * 80 + "\n")
                f.write(contact_text)
            custom_print(f"📝 Contact section text saved to {contact_text_path}")

    # STEP 2: Extract phone numbers from text content
    custom_print(f"📞 Step 2: Extracting phones from text content...")
    text_for_phone_extraction = contact_text if is_contact_page else (footer_text + " " + text)
    
    # Pattern 1: (+91) 9984508591 format
    pattern1 = re.compile(r'\(\+?\d{1,4}\)[\s\-]?\d{6,14}')
    for match in pattern1.findall(text_for_phone_extraction):
        phone = match.replace('(', '').replace(')', '').replace(' ', '').replace('-', '')
        if not phone.startswith('+'):
            phone = '+' + phone
        if phone not in phones:
            phones.add(phone)
            custom_print(f"✅ Found phone (parentheses format): {match} -> {phone}")
    
    # Pattern 2: CALL : (+91) 9984508591 format
    pattern2 = re.compile(r'(?:call|phone|tel|mobile|contact)[\s:]*[\(\+\d].*?\d{6,14}', re.IGNORECASE)
    for match in pattern2.findall(text_for_phone_extraction):
        # Extract just the phone number part
        phone_match = re.search(r'\(?\+?\d{1,4}\)?[\s\-\.]?\d[\s\-\.]?\d[\s\-\.]?\d[\s\-\.]?\d[\s\-\.]?\d[\s\-\.]?\d[\s\-\.]?\d{1,}', match)
        if phone_match:
            phone = phone_match.group(0)
            phone = re.sub(r'[^\d+]', '', phone)
            if phone and phone not in phones:
                phones.add(phone)
                custom_print(f"✅ Found phone (call prefix format): {match} -> {phone}")
    
    # Pattern 3: +91 9984508591 format
    pattern3 = re.compile(r'\+\d{1,4}[\s\-\.]?\d{6,14}')
    for match in pattern3.findall(text_for_phone_extraction):
        phone = re.sub(r'[\s\-\.]', '', match)
        if phone not in phones:
            phones.add(phone)
            custom_print(f"✅ Found phone (direct + format): {match} -> {phone}")
    
    # Pattern 4: Numbers that look like phone numbers
    pattern4 = re.compile(r'\b\d{10,15}\b')
    for match in pattern4.findall(text_for_phone_extraction):
        if len(match) == 10 and match[0] in ['6', '7', '8', '9']:  # Indian mobile numbers
            phone = '+91' + match
            if phone not in phones:
                phones.add(phone)
                custom_print(f"✅ Found Indian phone (10-digit): {match} -> {phone}")
        elif len(match) >= 10:
            if match not in phones:
                phones.add(match)
                custom_print(f"✅ Found potential phone (raw digits): {match}")
    
    # Pattern 5: 91-9984508591 or 91.9984508591 format
    pattern5 = re.compile(r'\d{1,4}[\-\.,]\d{6,14}')
    for match in pattern5.findall(text_for_phone_extraction):
        phone = '+' + re.sub(r'[\-\.,]', '', match)
        if phone not in phones:
            phones.add(phone)
            custom_print(f"✅ Found phone (dash format): {match} -> {phone}")

    # Regular DOM email extraction from text (non-href sources)
    for match in EMAIL_REGEX.findall(text):
        email = match.replace("(at)", "@").replace("(dot)", ".").replace("%40", "@").replace(" ", "")
        email = email.replace('"email":"', "").replace('"', "")
        if "@" in email and "." in email and not any(x in email for x in ["sentry", "wixpress"]):
            if email not in emails:
                emails.add(email)
                footer_tracking.append(f"Email found in page text: {email}")

    if footer_text:
        for match in EMAIL_REGEX.findall(footer_text):
            email = match.replace("(at)", "@").replace("(dot)", ".").replace("%40", "@").replace(" ", "")
            email = email.replace('"email":"', "").replace('"', "")
            if "@" in email and "." in email and not any(x in email for x in ["sentry", "wixpress"]):
                if email not in emails:
                    emails.add(email)
                    footer_tracking.append(f"Email found in footer text: {email}")

    # Regular DOM social media extraction from HTML (non-href sources) with validation
    for platform, regex in SOCIAL_REGEXES.items():
        matches = regex.findall(html)
        for match in matches:
            cleaned_match = match.strip('"\' ?#').rstrip('/')
            if not cleaned_match.startswith(('http://', 'https://')):
                cleaned_match = f"https://{cleaned_match}"
            
            validated_link = validate_social_link(cleaned_match, platform)
            if validated_link and validated_link not in socials[platform]:
                socials[platform].append(validated_link)
                footer_tracking.append(f"Social {platform} found in page HTML (non-href): {validated_link}")
                custom_print(f"✅ Added page {platform} link (non-href): {validated_link}")
            elif not validated_link:
                custom_print(f"🚫 Rejected page {platform} media/invalid URL: {cleaned_match}")

    # Regular DOM address extraction
    if footer_text:
        # Use both regex and structured extraction
        for match in ADDRESS_REGEX.findall(footer_text):
            addr = match.strip()
            if len(addr.split()) > 3 and addr not in addresses:
                addresses.add(addr)
        
        # Use structured extraction
        structured_addresses = extract_structured_address(footer_text)
        for addr in structured_addresses:
            addresses.add(addr)

    # Extract from main text too
    for match in ADDRESS_REGEX.findall(text):
        addr = match.strip()
        if len(addr.split()) > 3 and addr not in addresses:
            addresses.add(addr)
    
    structured_addresses = extract_structured_address(text)
    for addr in structured_addresses:
        addresses.add(addr)

    # Clean up social media lists and remove duplicates
    for platform in socials:
        socials[platform] = sorted(list(set(socials[platform])))
        custom_print(f"Final {platform} links: {socials[platform]}")

    # Extract meta description
    desc = ""
    desc_tags = soup.select('meta[name*="description" i], meta[property*="og:description" i], meta[name*="twitter:description" i]')
    for tag in desc_tags:
        if tag and "content" in tag.attrs:
            tag_desc = tag["content"].strip()
            if tag.attrs.get("name", "").lower() == "description" and tag_desc:
                desc = tag_desc
                break
            elif len(tag_desc) > len(desc):
                desc = tag_desc

    # Extract about content
    about_content = ""
    if is_about_page:
        for section in about_sections:
            if section:
                section_text = section.get_text(" ", strip=True)
                if section_text and len(section_text) > len(about_content):
                    about_content = section_text[:2000]

    combined_text = (footer_text + " " + contact_text).strip()

    # Save tracking information
    footer_tracking_path = os.path.join(DEBUG_DIR, f"{urlparse(url).path.replace('/', '_') or 'index'}_footer_tracking.txt")
    with open(footer_tracking_path, "w", encoding="utf-8") as f:
        f.write(f"URL: {url}\n")
        f.write(f"Total phones found: {len(phones)}\n")
        for phone in phones:
            f.write(f"  - {phone}\n")
        if footer_tracking:
            f.write("\n".join(footer_tracking) + "\n")
        else:
            f.write("No data extraction tracking available.\n")

    # Save all hrefs to debug file
    hrefs_log_path = os.path.join(DEBUG_DIR, f"{urlparse(url).path.replace('/', '_') or 'index'}_hrefs.json")
    with open(hrefs_log_path, "w", encoding="utf-8") as f:
        json.dump(all_hrefs, f, indent=2, ensure_ascii=False)
    custom_print(f"📝 All hrefs saved to {hrefs_log_path}")

    # Validate phone numbers with country codes
    validated_phones = []
    for phone in phones:
        validation_result = validate_phone_with_country_code(phone)
        if validation_result["is_valid"]:
            validated_phones.append(validation_result)
            custom_print(f"✅ Validated phone: {phone} -> {validation_result['country']} ({validation_result['country_code']})")
        else:
            # Keep unvalidated phones too, but mark them
            validated_phones.append(validation_result)
            custom_print(f"⚠️ Phone without valid country code: {phone}")

    custom_print(f"📧 Final emails: {list(emails)}")
    custom_print(f"📞 Final phones ({len(validated_phones)} total):")
    for i, phone_data in enumerate(validated_phones, 1):
        phone = phone_data.get("phone", "N/A")
        country = phone_data.get("country", "Unknown")
        custom_print(f"  {i}. {phone} ({country})")
    custom_print(f"🏠 Final addresses: {list(addresses)}")
    custom_print(f"🔗 Total hrefs collected: {len(all_hrefs)}")

    # Save contact page log if this is a contact page
    if is_contact_page:
        custom_print(f"💾 Saving contact page log for {url}")
        save_contact_page_log(
            url=url,
            emails=emails,
            phones=validated_phones,
            addresses=addresses,
            socials=socials,
            contact_text=contact_text,
            footer_text=footer_text,
            page_hrefs=all_hrefs,
            domain=urlparse(url).netloc
        )

    return (
        emails,
        socials,
        desc,
        about_content,
        validated_phones,  # Return validated phones instead of raw phones
        combined_text,
        footer_html,
        footer_text,
        addresses,
        all_hrefs,
        navbar_links,
        footer_nav_links
    )