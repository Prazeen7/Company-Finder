import time
import os
import json
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests

from config import MAX_DEPTH, CRAWL_DELAY, DEBUG_DIR, SOCIAL_REGEXES
from utils import custom_print
from selenium_handler import init_selenium_driver, get_rendered_html
from data_processor import (
    scrape_info, identify_about_contact_links, fetch_about_page_content, 
    load_country_codes, extract_location_info, generate_business_nature,
    fallback_keyword_matching
)
from google_api import is_link_accessible

def crawl_website(domain, max_depth=MAX_DEPTH):
    visited = set()
    to_visit = [(domain, 0)]
    all_emails = set()
    all_socials = {}
    all_phones = set()
    all_addresses = set()
    all_links = set()
    all_footer_contact_texts = []
    meta_description = ""
    about_content = ""
    footer_contents = []
    debug_info = []
    all_hrefs_collection = []

    parsed_domain = urlparse(domain).netloc

    while to_visit:
        url, depth = to_visit.pop(0)
        
        custom_print(f"\n🌐 Processing URL ({len(to_visit)+1} remaining): {url} (depth: {depth})")
        
        result, page_debug_data, discovered_links = process_page(url, depth, parsed_domain, visited)
        
        # Add discovered links to to_visit if they're not already queued or visited
        for new_url, new_depth in discovered_links:
            if new_url not in visited and not any(new_url == u for u, _ in to_visit):
                to_visit.append((new_url, new_depth))
                custom_print(f"📝 Added to crawl queue: {new_url} (depth: {new_depth})")
        
        if result:
            emails, socials, desc, page_about_content, phones, combined_text, footer_html, footer_text, addresses, page_hrefs = result
            
            # Only update if we don't have this data yet
            if not all_emails:
                all_emails.update(emails)
            
            for platform, links in socials.items():
                if platform not in all_socials:
                    all_socials[platform] = []
                all_socials[platform].extend([link for link in links if link not in all_socials.get(platform, [])])
            
            # Only update phones if we don't have any yet
            if not all_phones:
                for phone_data in phones:
                    if isinstance(phone_data, dict):
                        all_phones.add(phone_data.get("phone", ""))
                    else:
                        all_phones.add(str(phone_data))
            
            # Only update addresses if we don't have any yet
            if not all_addresses:
                all_addresses.update(addresses)
            
            # Collect meta description and about content from deeper pages
            if desc and not meta_description:
                meta_description = desc
                custom_print(f"📝 Found meta description on {url}")

            # If this is an about page, fetch it separately and extract paragraphs
            about_keywords = ['about', 'story', 'mission', 'vision', 'who-we-are', 'our-company', 'our-team', 'company-info']
            if any(keyword in url.lower() for keyword in about_keywords):
                custom_print(f"🔍 Detected about page URL: {url}")
                fetched_about_content = fetch_about_page_content(url)
                if fetched_about_content and len(fetched_about_content) > len(about_content):
                    about_content = fetched_about_content
                    custom_print(f"📝 Found about content from dedicated fetch on {url} ({len(fetched_about_content)} chars)")
            elif page_about_content and len(page_about_content) > len(about_content):
                about_content = page_about_content
                custom_print(f"📝 Found about content on {url} ({len(page_about_content)} chars)")
            
            if footer_html != "No footer found":
                footer_contents.append({
                    "url": page_debug_data["url"],
                    "html": footer_html,
                    "text": footer_text
                })
            
            all_hrefs_collection.extend(page_hrefs)
            debug_info.append(page_debug_data)
        
        else:
            debug_info.append(page_debug_data)
        
        time.sleep(CRAWL_DELAY)
    
    # Save all hrefs
    all_hrefs_log_path = os.path.join(DEBUG_DIR, f"all_hrefs_{parsed_domain.replace('.', '_')}.json")
    try:
        with open(all_hrefs_log_path, "w", encoding="utf-8") as f:
            json.dump(all_hrefs_collection, f, indent=2, ensure_ascii=False)
        custom_print(f"📝 All collected <a href> links saved to {all_hrefs_log_path}")
    except Exception as e:
        custom_print(f"❌ Error saving all hrefs log file: {str(e)}")

    combined_text = " ".join(all_footer_contact_texts)
    
    # Extract location info
    location_info = {"country": "N/A"}
    if addresses:
        for address in addresses:
            if any(country in address.lower() for country in ['nepal', 'india', 'usa', 'united states', 'uk', 'united kingdom']):
                if 'nepal' in address.lower():
                    location_info["country"] = "Nepal"
                elif 'india' in address.lower():
                    location_info["country"] = "India"
                elif 'usa' in address.lower() or 'united states' in address.lower():
                    location_info["country"] = "United States"
                elif 'uk' in address.lower() or 'united kingdom' in address.lower():
                    location_info["country"] = "United Kingdom"
                custom_print(f"🌍 Extracted country from address: {location_info['country']}")
                break
    
    # Use Llama if no country found
    if location_info["country"] == "N/A" and combined_text:
        location_info = extract_location_info(combined_text)
    
    # Generate business nature
    custom_print(f"📝 Business nature generation inputs:")
    custom_print(f"  Meta description: {'Yes' if meta_description else 'No'} ({len(meta_description)} chars)")
    custom_print(f"  About content: {'Yes' if about_content else 'No'} ({len(about_content)} chars)")
    
    if not about_content and footer_contents:
        for fc in footer_contents:
            if "About" in fc["text"] or "about" in fc["text"]:
                about_content = fc["text"][:2000]
                custom_print(f"📝 Using footer text for about content")
                break
    
    business_nature = generate_business_nature(meta_description, about_content)
    
    custom_print(f"\n✅ Crawl completed:")
    custom_print(f"   Pages visited: {len(visited)}")
    custom_print(f"   Emails found: {len(all_emails)}")
    custom_print(f"   Phone numbers found: {len(all_phones)}")
    custom_print(f"   Social media links found: {sum(len(v) for v in all_socials.values())}")
    
    return all_emails, all_socials, meta_description, about_content, all_phones, all_addresses, all_links, location_info, business_nature, footer_contents, debug_info

def process_page(url, depth, parsed_domain, visited):
    if depth > MAX_DEPTH or url in visited or not urlparse(url).netloc.endswith(parsed_domain):
        custom_print(f"⏭️ Skipping {url} (depth: {depth}, visited: {url in visited}, domain: {urlparse(url).netloc})")
        return None, {
            "url": url,
            "status": "skipped",
            "dynamic_html": "",
            "links": [],
            "scripts": [],
            "raw_socials": [],
            "raw_emails": [],
            "raw_phones": [],
            "raw_addresses": [],
            "shadow_links": [],
            "shadow_content": [],
            "alerts": [],
            "consolidated_links": [],
            "all_links": [],
            "iframes": [],
            "error_details": []
        }, []

    if not is_link_accessible(url):
        custom_print(f"⏭️ Skipping inaccessible link: {url}")
        return None, {
            "url": url,
            "status": "skipped_inaccessible",
            "dynamic_html": "",
            "links": [],
            "scripts": [],
            "raw_socials": [],
            "raw_emails": [],
            "raw_phones": [],
            "raw_addresses": [],
            "shadow_links": [],
            "shadow_content": [],
            "alerts": [],
            "consolidated_links": [],
            "all_links": [],
            "iframes": [],
            "error_details": [{"error_type": "Inaccessible", "message": "Link failed accessibility checks"}]
        }, []

    visited.add(url)
    custom_print(f"🔎 Crawling: {url}")
    driver = init_selenium_driver()
    try:
        html, page_debug_data = get_rendered_html(url, driver)
        
        if not html or "blocked" in html.lower() or len(html) < 500:
            custom_print(f"❌ Selenium failed or returned blocked/minimal content for {url}. Falling back to primary requests.")
            page_debug_data["status"] = "fallback_requests"
            page_debug_data["alerts"].append("Selenium failed, using primary requests fallback")
            
            # Primary requests fallback
            session = requests.Session()
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
            }
            try:
                response = session.get(url, headers=headers, timeout=30, allow_redirects=True)
                if response.status_code == 200 and len(response.text) > 500 and "blocked" not in response.text.lower():
                    html = response.text
                    page_debug_data["dynamic_html"] = html
                    page_debug_data["alerts"].append("Successfully fetched HTML with primary requests fallback")
                    custom_print(f"✅ Primary requests fallback successful for {url}")
                else:
                    custom_print(f"❌ Primary requests fallback failed for {url}: Status {response.status_code} or blocked/minimal content")
                    page_debug_data["error_details"].append({
                        "method": "requests_fallback",
                        "attempt": 1,
                        "error_type": "FailedFallback",
                        "message": f"Primary requests returned status {response.status_code} or blocked/minimal content",
                        "http_status": response.status_code,
                        "current_url": url
                    })
                    # Second fallback: lightweight requests-based link collection
                    custom_print(f"❌ Primary requests fallback failed. Attempting lightweight requests fallback for link collection.")
                    page_debug_data["status"] = "fallback_lightweight_requests"
                    page_debug_data["alerts"].append("Primary requests failed, using lightweight requests fallback")
                    try:
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                        }
                        resp = requests.get(url, headers=headers, timeout=10)
                        resp.raise_for_status()
                        html = resp.text
                        page_debug_data["dynamic_html"] = html
                        page_debug_data["alerts"].append("Successfully fetched HTML with lightweight requests fallback")
                        custom_print(f"✅ Lightweight requests fallback successful for {url}")
                    except Exception as e:
                        custom_print(f"❌ Lightweight requests fallback failed for {url}: {str(e)}")
                        page_debug_data["error_details"].append({
                            "method": "lightweight_requests_fallback",
                            "attempt": 1,
                            "error_type": type(e).__name__,
                            "message": str(e),
                            "http_status": getattr(e.response, 'status_code', None),
                            "current_url": url
                        })
                        return None, page_debug_data, []
            except Exception as e:
                custom_print(f"❌ Primary requests fallback failed for {url}: {str(e)}")
                page_debug_data["error_details"].append({
                    "method": "requests_fallback",
                    "attempt": 1,
                    "error_type": type(e).__name__,
                    "message": str(e),
                    "http_status": getattr(e.response, 'status_code', None),
                    "current_url": url
                })
                # Second fallback: lightweight requests-based link collection
                custom_print(f"❌ Primary requests fallback failed. Attempting lightweight requests fallback for link collection.")
                page_debug_data["status"] = "fallback_lightweight_requests"
                page_debug_data["alerts"].append("Primary requests failed, using lightweight requests fallback")
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                    }
                    resp = requests.get(url, headers=headers, timeout=10)
                    resp.raise_for_status()
                    html = resp.text
                    page_debug_data["dynamic_html"] = html
                    page_debug_data["alerts"].append("Successfully fetched HTML with lightweight requests fallback")
                    custom_print(f"✅ Lightweight requests fallback successful for {url}")
                except Exception as e:
                    custom_print(f"❌ Lightweight requests fallback failed for {url}: {str(e)}")
                    page_debug_data["error_details"].append({
                        "method": "lightweight_requests_fallback",
                        "attempt": 1,
                        "error_type": type(e).__name__,
                        "message": str(e),
                        "http_status": getattr(e.response, 'status_code', None),
                        "current_url": url
                    })
                    return None, page_debug_data, []

        is_about_page = "about" in url.lower()
        is_contact_page = "contact" in url.lower()

        emails, socials, desc, page_about_content, phones, combined_text, footer_html, footer_text, addresses, page_hrefs, navbar_links, footer_nav_links = scrape_info(
            html, is_about_page, is_contact_page, url, page_debug_data
        )

        custom_print(f"page_debug_data keys before update: {list(page_debug_data.keys())}")

        page_debug_data.update({
            "status": "crawled" if html == page_debug_data["dynamic_html"] and page_debug_data.get("status", "initial") not in ["fallback_requests", "fallback_lightweight_requests"] else page_debug_data.get("status", "initial"),
            "all_links": []
        })

        soup = BeautifulSoup(html, "lxml")
        discovered_links = []
        excluded_extensions = ['.pdf', '.jpg', '.png', '.jpeg', '.gif']
        for a in soup.select('a[href]:not([href$=".pdf"], [href$=".jpg"], [href$=".png"], [href$=".jpeg"], [href$=".gif"])'):
            next_url = urljoin(url, a["href"]).split("#")[0]
            parsed_next = urlparse(next_url)
            link_text = a.get_text().strip().lower()
            page_debug_data["all_links"].append({"href": next_url, "text": link_text})

        # Use Llama to intelligently identify About/Contact pages from navbar and footer links
        all_nav_links = navbar_links + footer_nav_links
        if depth == 0:  # Only on the homepage to discover pages
            custom_print(f"🤖 Identifying About/Contact pages from {len(all_nav_links)} navigation links...")
            # Pass base domain for URL variation fallback if no links found
            base_domain = f"{urlparse(url).scheme}://{parsed_domain}"
            identified_links = identify_about_contact_links(all_nav_links, base_domain=base_domain)

            # Add identified About pages to crawl queue
            for link_info in identified_links.get("about", []):
                link_url = link_info["url"]
                parsed_link = urlparse(link_url)
                if (parsed_link.netloc.endswith(parsed_domain) and
                    link_url not in visited):
                    custom_print(f"📖 Llama identified About page: {link_url}")
                    discovered_links.append((link_url, depth + 1))

            # Add identified Contact pages to crawl queue
            for link_info in identified_links.get("contact", []):
                link_url = link_info["url"]
                parsed_link = urlparse(link_url)
                if (parsed_link.netloc.endswith(parsed_domain) and
                    link_url not in visited):
                    custom_print(f"📞 Llama identified Contact page: {link_url}")
                    discovered_links.append((link_url, depth + 1))
        else:
            # Fallback to keyword matching for non-homepage pages or when no nav links
            for a in soup.select('a[href]:not([href$=".pdf"], [href$=".jpg"], [href$=".png"], [href$=".jpeg"], [href$=".gif"])'):
                next_url = urljoin(url, a["href"]).split("#")[0]
                parsed_next = urlparse(next_url)
                link_text = a.get_text().strip().lower()

                if (parsed_next.netloc.endswith(parsed_domain) and
                    next_url not in visited and
                    ("about" in next_url.lower() or "contact" in next_url.lower() or "about" in link_text or "contact" in link_text)):
                    discovered_links.append((next_url, depth + 1))

        # Save extracted links to all_links.txt (similar to provided code)
        all_links_file = os.path.join(DEBUG_DIR, "all_links.txt")
        try:
            with open(all_links_file, "a", encoding="utf-8") as f:
                for link in page_hrefs:
                    if link["url"].startswith(('http://', 'https://', 'mailto:', 'tel:')):
                        f.write(f"{link['url']} (from {url})\n")
            custom_print(f"📝 Appended links to {all_links_file}")
        except Exception as e:
            custom_print(f"❌ Error saving links to {all_links_file}: {str(e)}")
            page_debug_data["error_details"].append({
                "method": "save_links",
                "attempt": 1,
                "error_type": type(e).__name__,
                "message": str(e),
                "http_status": None,
                "current_url": url
            })

        return (emails, socials, desc, page_about_content, phones, combined_text, footer_html, footer_text, addresses, page_hrefs), page_debug_data, discovered_links
    finally:
        driver.quit()