import os
import time
import json
import re
from urllib.parse import urljoin, urlparse
from config import DEBUG_DIR, EMAIL_REGEX, PHONE_REGEX, ADDRESS_REGEX, SOCIAL_REGEXES

# Store process log globally
process_log = []

def custom_print(*args, **kwargs):
    """Custom print function to capture console output"""
    message = " ".join(str(arg) for arg in args)
    process_log.append({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "message": message})
    print(*args, **kwargs)

def save_contact_page_log(url, emails, phones, addresses, socials, contact_text, footer_text, page_hrefs, domain="unknown"):
    """Save all scraped content from contact pages to a structured log file"""
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        domain_clean = domain.replace('https://', '').replace('http://', '').replace('/', '_').replace('.', '_')

        # Create a contact_logs directory inside debug_html
        contact_logs_dir = os.path.join(DEBUG_DIR, "contact_logs")
        if not os.path.exists(contact_logs_dir):
            os.makedirs(contact_logs_dir)

        # Create log filename based on domain and timestamp
        log_filename = f"contact_page_{domain_clean}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        log_path = os.path.join(contact_logs_dir, log_filename)

        # Prepare contact page data
        contact_data = {
            "timestamp": timestamp,
            "url": url,
            "domain": domain,
            "scraping_summary": {
                "emails_found": len(emails),
                "phones_found": len(phones),
                "addresses_found": len(addresses),
                "social_media_found": sum(len(v) for v in socials.values()),
                "total_hrefs": len(page_hrefs)
            },
            "extracted_data": {
                "emails": list(emails),
                "phones": [
                    {
                        "phone": p.get("phone", p) if isinstance(p, dict) else str(p),
                        "country_code": p.get("country_code", "Unknown") if isinstance(p, dict) else "Unknown",
                        "country": p.get("country", "Unknown") if isinstance(p, dict) else "Unknown",
                        "is_valid": p.get("is_valid", False) if isinstance(p, dict) else False
                    }
                    for p in phones
                ],
                "addresses": list(addresses),
                "social_media": {
                    platform: links for platform, links in socials.items() if links
                },
                "all_hrefs": page_hrefs[:100]  # Limit to first 100 hrefs to avoid huge files
            },
            "page_content": {
                "contact_text_length": len(contact_text) if contact_text else 0,
                "contact_text_preview": contact_text[:500] if contact_text else "",
                "footer_text_length": len(footer_text) if footer_text else 0,
                "footer_text_preview": footer_text[:500] if footer_text else ""
            }
        }

        # Save to JSON file
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(contact_data, f, indent=2, ensure_ascii=False)

        custom_print(f"📝 Contact page log saved to {log_path}")

        # Also append to a consolidated log file for easy reference
        consolidated_log_path = os.path.join(contact_logs_dir, "all_contact_pages.jsonl")
        with open(consolidated_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(contact_data, ensure_ascii=False) + "\n")

        return log_path

    except Exception as e:
        custom_print(f"❌ Failed to save contact page log: {str(e)}")
        return None

def extract_structured_address(text):
    """Extract structured address information from text"""
    # Common address patterns
    address_patterns = [
        r'(\d+\s+[\w\s]+(?:\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane))?[\s,]+[\w\s,]+(?:\s+\d{5}(?:-\d{4})?)?)',
        r'([\w\s]+(?:\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard))?[\s,]+(?:Suite|Ste\.?|Unit|Apt\.?|#)?\s*\d*[\s,]+[\w\s,]+(?:\s+\d{5}(?:-\d{4})?)?)',
        r'([A-Za-z\s]+,\s*[A-Za-z\s]+,\s*[A-Za-z\s]+(?:\s+\d{5})?)',
    ]
    
    addresses = []
    for pattern in address_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            addr = match.strip()
            # Check if it looks like a real address (not just random text)
            if (len(addr.split()) >= 4 and 
                any(keyword in addr.lower() for keyword in ['st', 'street', 'ave', 'avenue', 'rd', 'road', 'blvd', 'boulevard']) or
                re.search(r'\d{5}(?:-\d{4})?', addr)):
                addresses.append(addr)
    
    return addresses

def validate_social_link(url, platform):
    """Validate and clean social media links to ensure they're profile/page URLs, not media URLs"""
    
    # Skip if URL contains media file extensions or CDN patterns
    media_patterns = [
        r'\.(?:jpg|jpeg|png|gif|mp4|webp|svg)(?:\?|$)',  # Media files
        r'scontent\.cdninstagram\.com',  # Instagram CDN
        r'scontent\..*\.fna\.fbcdn\.net',  # Facebook CDN
        r'fbcdn\.net',  # Facebook CDN
        r'/v/t\d+\.',  # Instagram media paths
        r'/o1/v/t\d+/',  # Instagram media paths
        r'/reel[s]?/',  # Instagram reels
        r'/p/',  # Instagram posts
        r'/stories/',  # Instagram stories
        r'/explore/',  # Instagram explore
        r'/direct/',  # Instagram direct messages
    ]
    
    for pattern in media_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            custom_print(f"🚫 Rejected {platform} URL due to media pattern match: {url} (pattern: {pattern})")
            return None
    
    # Platform-specific validation
    if platform == "instagram":
        instagram_profile_pattern = r'^(?:https?://)?(?:[a-z]{2,3}\.)?instagram\.com/([A-Za-z0-9._-]+)/?(?:\?.*)?$'
        match = re.match(instagram_profile_pattern, url, re.IGNORECASE)
        if match:
            username = match.group(1)
            # Skip generic/system usernames and paths
            skip_usernames = ['tv', 'reel', 'reels', 'stories', 'explore', 'accounts', 'direct', 'developer']
            if username.lower() not in skip_usernames and len(username) > 1:
                custom_print(f"✅ Validated Instagram URL: {url} -> Username: {username}")
                return f"https://www.instagram.com/{username}/"
            else:
                custom_print(f"🚫 Rejected Instagram URL due to invalid username: {url} (username: {username})")
                return None
        custom_print(f"🚫 Rejected Instagram URL due to pattern mismatch: {url}")
        return None
    
    elif platform == "facebook":
        facebook_pattern = r'^(?:https?://)?(?:[a-z]{2,3}\.)?facebook\.com/(?:profile\.php\?id=(\d+)(?:&.*)?$|(?:pages/)?([A-Za-z0-9._-]+)/?(?:\?.*)?$)'
        match = re.match(facebook_pattern, url, re.IGNORECASE)
        if match:
            if match.group(1):  # ID-based profile
                custom_print(f"✅ Validated Facebook URL (ID-based): {url}")
                return f"https://www.facebook.com/profile.php?id={match.group(1)}"
            elif match.group(2):  # Username-based profile or page
                custom_print(f"✅ Validated Facebook URL (username-based): {url}")
                return f"https://www.facebook.com/{match.group(2)}/"
        custom_print(f"🚫 Rejected Facebook URL due to pattern mismatch: {url}")
        return None
    
    elif platform == "pinterest":
        pinterest_pattern = r'^(?:https?://)?(?:[a-z]{2,3}\.)?pinterest\.com/([A-Za-z0-9._-]+)/?(?:\?.*)?$'
        match = re.match(pinterest_pattern, url, re.IGNORECASE)
        if match:
            username = match.group(1)
            custom_print(f"✅ Validated Pinterest URL: {url} -> Username: {username}")
            return f"https://www.pinterest.com/{username}/"
        custom_print(f"🚫 Rejected Pinterest URL due to pattern mismatch: {url}")
        return None
    
    elif platform == "linkedin":
        # Use the existing SOCIAL_REGEXES pattern for LinkedIn
        linkedin_regex = SOCIAL_REGEXES.get("linkedin")
        
        if linkedin_regex and linkedin_regex.match(url):
            # Just clean up the URL a bit but keep the exact structure
            parsed_url = urlparse(url)
            
            # Ensure https scheme
            if not parsed_url.scheme:
                url = 'https://' + url
            
            # Ensure proper LinkedIn domain
            if 'linkedin.com' not in url.lower():
                custom_print(f"🚫 Rejected LinkedIn URL (invalid domain): {url}")
                return None
            
            # Remove query parameters and fragments, keep trailing slash
            clean_url = url.split('?')[0].split('#')[0]
            if not clean_url.endswith('/'):
                clean_url += '/'
            
            custom_print(f"✅ Accepted LinkedIn URL (exact): {clean_url}")
            return clean_url
        
        custom_print(f"🚫 Rejected LinkedIn URL (doesn't match regex): {url}")
        return None
    
    elif platform == "youtube":
        youtube_pattern = r'^(?:https?://)?(?:[a-z]{2,3}\.)?youtube\.com/(?:channel|user|c)/([A-Za-z0-9._-]+)/?(?:\?.*)?$'
        match = re.match(youtube_pattern, url, re.IGNORECASE)
        if match:
            path = match.group(1)
            custom_print(f"✅ Validated YouTube URL: {url} -> Path: {path}")
            return f"https://www.youtube.com/{path}/"
        custom_print(f"🚫 Rejected YouTube URL due to pattern mismatch: {url}")
        return None
    
    # For other platforms, return as-is if it matches the platform's regex
    if SOCIAL_REGEXES.get(platform, re.compile(r'')).match(url):
        custom_print(f"✅ Validated {platform} URL (fallback): {url}")
        return url
    custom_print(f"🚫 Rejected {platform} URL due to regex mismatch: {url}")
    return None