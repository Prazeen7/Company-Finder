import re
import os  # Add this import
import requests
import subprocess
import time
import random
import json  # Add this import
from urllib.parse import urlparse
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import GOOGLE_API_KEYS, SEARCH_ENGINE_ID, USER_AGENTS, SOCIAL_REGEXES, DEBUG_DIR
from utils import custom_print
from data_processor import validate_social_link


def get_all_google_domains(query, company_name=None):
    """Get all domains from Google search results and validate them"""
    social_media_domains = [
        'facebook.com', 'instagram.com', 'linkedin.com', 'youtube.com', 'pinterest.com',
        'twitter.com', 'tiktok.com', 'snapchat.com', 'reddit.com'
    ]
    
    company_clean = ""
    company_words = []
    if company_name:
        company_clean = re.sub(r'\s*(pvt|ltd|limited|private|inc|incorporated|co|company|corp|corporation|llc|gmbh|plc)\s*\.?$', 
                              '', company_name, flags=re.IGNORECASE)
        company_clean = company_clean.lower().strip()
        company_words = re.findall(r'\b\w+\b', company_clean)
    
    custom_print(f"🔍 Searching Google for all domains matching: '{company_clean}' (words: {company_words})")
    
    all_domains = []
    
    for api_key in GOOGLE_API_KEYS:
        try:
            service = build("customsearch", "v1", developerKey=api_key)
            res = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=10).execute()
            items = res.get("items", [])
            
            for item in items:
                link = item.get("link", "")
                title = item.get("title", "").lower()
                snippet = item.get("snippet", "").lower()
                parsed = urlparse(link)
                domain_name = parsed.netloc.lower()
                domain = f"{parsed.scheme}://{parsed.netloc}/"
                
                # Skip social media domains
                if any(social_domain in domain_name for social_domain in social_media_domains):
                    continue
                
                # Extract domain without TLD and www for matching
                domain_base = re.sub(r'^www\.', '', domain_name)
                domain_base = re.sub(r'\.(com|org|net|io|co|in|us|uk|au|ca|de|fr|jp|biz|info|mobi|name|tv|cc|ws)$', '', domain_base)
                
                # Check if this domain matches the company
                matches_company = False
                if company_words:
                    matches = []
                    for word in company_words:
                        if word in domain_base:
                            matches.append(word)
                    
                    # For multi-word companies, require ALL words to match
                    if len(company_words) >= 2:
                        matches_company = (len(matches) == len(company_words))
                    else:
                        matches_company = (len(matches) == 1)
                
                domain_info = {
                    'domain': domain,
                    'domain_name': domain_name,
                    'domain_base': domain_base,
                    'title': title[:100],
                    'matches_company': matches_company,
                    'full_match': False
                }
                
                # Mark as full match if ALL company words are in domain
                if company_words and matches_company:
                    domain_info['full_match'] = True
                    custom_print(f"  ✅ FULL MATCH: {domain_name}")
                else:
                    custom_print(f"  ⚠️ Partial/No match: {domain_name}")
                
                all_domains.append(domain_info)
            
            break  # Successfully got results, stop trying API keys
            
        except HttpError as e:
            if e.resp.status == 429:
                custom_print(f"❌ Google API error with key {api_key}: Quota exceeded. Trying next API key...")
                continue
            else:
                custom_print(f"❌ Google API error with key {api_key}: {e}")
                return []
        except Exception as e:
            custom_print(f"❌ Google API error with key {api_key}: {e}")
            return []
    
    return all_domains

def validate_domain_for_scraping(domain):
    """Check if a domain is suitable for scraping (not a returns/order portal)"""
    parsed = urlparse(domain)
    domain_name = parsed.netloc.lower()
    
    # Patterns that indicate unsuitable domains for scraping
    unsuitable_patterns = [
        r'returns\.', r'order\.', r'track\.', r'shipping\.', r'delivery\.',
        r'support\.', r'help\.', r'portal\.', r'app\.', r'secure\.',
        r'account\.', r'login\.', r'admin\.'
    ]
    
    # Check if domain matches any unsuitable patterns
    for pattern in unsuitable_patterns:
        if re.search(pattern, domain_name):
            custom_print(f"⚠️ Domain appears to be a service portal: {domain_name}")
            return False
    
    # Check path for returns/order patterns
    path = parsed.path.lower()
    if any(keyword in path for keyword in ['/returns', '/order', '/track', '/ship', '/delivery']):
        custom_print(f"⚠️ Domain path indicates service page: {path}")
        return False
    
    return True

def find_domain_google(query, company_name=None):
    """Find domain using Google Search API with multiple search approaches"""
    
    # Clean company name for matching
    company_clean = ""
    company_words = []
    if company_name:
        company_clean = re.sub(r'\s*(pvt|ltd|limited|private|inc|incorporated|co|company|corp|corporation|llc|gmbh|plc)\s*\.?$', 
                              '', company_name, flags=re.IGNORECASE)
        company_clean = company_clean.lower().strip()
        company_words = re.findall(r'\b\w+\b', company_clean)
    
    custom_print(f"🔍 Searching for domain matching: '{company_clean}' (words: {company_words})")
    
    # Try multiple search queries
    search_queries = [
        query,  # Original query
        f"{company_name} website",
        f"{company_name} official website",
        f"{company_clean.replace(' ', '')}.com",
        f"{company_clean} com",
    ]
    
    all_domains = []
    
    for search_query in search_queries:
        custom_print(f"  Trying search query: '{search_query}'")
        
        for api_key in GOOGLE_API_KEYS:
            try:
                service = build("customsearch", "v1", developerKey=api_key)
                res = service.cse().list(q=search_query, cx=SEARCH_ENGINE_ID, num=10).execute()
                items = res.get("items", [])
                
                for item in items:
                    link = item.get("link", "")
                    title = item.get("title", "").lower()
                    snippet = item.get("snippet", "").lower()
                    
                    # Skip social media and common unrelated sites
                    social_media = ['facebook', 'instagram', 'linkedin', 'youtube', 'pinterest', 
                                   'twitter', 'tiktok', 'snapchat', 'reddit', 'wikipedia']
                    if any(social in link.lower() for social in social_media):
                        continue
                    
                    # Parse domain using tldextract
                    from config import extract
                    parsed_domain = extract(link)
                    domain_name = parsed_domain.fqdn
                    
                    # Get domain parts properly
                    subdomain = parsed_domain.subdomain
                    domain_without_tld = parsed_domain.domain
                    suffix = parsed_domain.suffix
                    
                    # Get the scheme from the original link
                    scheme = 'https'  # default
                    if link.startswith('http://'):
                        scheme = 'http'
                    elif link.startswith('https://'):
                        scheme = 'https'
                    
                    domain = f"{scheme}://{domain_name}/"
                    
                    # Determine if it's a subdomain (has a non-www subdomain part)
                    # 'www' is not considered a meaningful subdomain for our purposes
                    is_subdomain = bool(subdomain and subdomain not in ['www', ''])
                    
                    # Check if it looks like a main business domain
                    # Main domains usually don't have extra prefixes before the actual domain name
                    is_main_domain = not is_subdomain or subdomain in ['www', '']
                    
                    # Check for service subdomains
                    service_subdomains = ['returns', 'shop', 'store', 'blog', 'support', 'help', 
                                         'app', 'portal', 'account', 'login', 'admin', 'secure',
                                         'order', 'tracking', 'shipping', 'delivery', 'exchange',
                                         'api', 'cdn', 'static', 'assets', 'media', 'images']
                    
                    # IMPROVED MATCHING LOGIC
                    matches_company = False
                    match_score = 0
                    exact_match = False
                    
                    if company_words:
                        # Get the actual domain name without subdomain and TLD for matching
                        domain_for_matching = domain_without_tld.lower()
                        
                        # PRIORITY 1: Check for exact combined match (no spaces)
                        company_combined = company_clean.replace(" ", "")
                        company_combined_dash = company_clean.replace(" ", "-")
                        
                        if domain_for_matching == company_combined or domain_for_matching == company_combined_dash:
                            match_score += 100  # HIGHEST priority for EXACT match
                            matches_company = True
                            exact_match = True
                            custom_print(f"    ✅✅✅ EXACT MATCH: {domain_for_matching} == {company_combined}")
                        
                        # PRIORITY 2: Check if domain contains exact combined company name
                        elif company_combined in domain_for_matching or company_combined_dash in domain_for_matching:
                            # Check if it's the full domain or just a prefix/suffix
                            if domain_for_matching.startswith(company_combined) or domain_for_matching.startswith(company_combined_dash):
                                match_score += 70  # High score but penalize for extra suffix
                                matches_company = True
                                custom_print(f"    ✅✅ PREFIX MATCH: {domain_for_matching} starts with {company_combined}")
                            else:
                                match_score += 50  # Lower if company name is in middle
                                matches_company = True
                                custom_print(f"    ✅ CONTAINS MATCH: {domain_for_matching} contains {company_combined}")
                        
                        # PRIORITY 3: Check individual words (ONLY if no exact match)
                        else:
                            matches = []
                            for word in company_words:
                                if len(word) > 3 and word in domain_for_matching:
                                    matches.append(word)
                                    match_score += 8  # Lower score for individual words
                            
                            if len(matches) >= len(company_words):
                                match_score += 20  # All words present but not combined
                                matches_company = True
                                custom_print(f"    ⚠️ PARTIAL MATCH: {domain_for_matching} contains words: {matches}")
                            elif len(matches) > 0:
                                match_score += 10  # Some words match
                                custom_print(f"    ⚠️ WEAK MATCH: {domain_for_matching} contains some words: {matches}")
                    
                    # Check title and snippet for company mention
                    for word in company_words:
                        if word in title:
                            match_score += 3
                        if word in snippet:
                            match_score += 2
                    
                    # Check if title contains the exact company name
                    if company_clean in title:
                        match_score += 15
                        custom_print(f"    📄 Title contains exact company name")
                    
                    # Better subdomain penalty logic
                    if is_subdomain:
                        # Check if it's a service subdomain
                        subdomain_parts = subdomain.split('.')
                        for subdomain_part in subdomain_parts:
                            if subdomain_part in service_subdomains:
                                match_score -= 50
                                custom_print(f"    ⚠️ Service subdomain penalty: {subdomain}")
                                break
                        else:
                            # Penalize non-service subdomains less
                            match_score -= 10
                            custom_print(f"    ⚠️ Non-service subdomain penalty: {subdomain}")
                    
                    # Bonus for main domain
                    if is_main_domain:
                        match_score += 25
                        custom_print(f"    ✅ Main domain bonus")
                    
                    # Bonus for homepage or root path
                    path = urlparse(link).path
                    if path in ['/', '/index.html', '/index.php', '']:
                        match_score += 10
                    
                    # Penalty for returns/order pages
                    if any(keyword in link.lower() or keyword in title for keyword in 
                          ['returns', 'order', 'tracking', 'shipping', 'delivery', 'exchange']):
                        match_score -= 25
                    
                    # Skip very low scoring domains
                    if match_score < 5:
                        continue
                    
                    domain_info = {
                        'domain': domain,
                        'domain_name': domain_name,
                        'domain_base': domain_without_tld,
                        'subdomain': subdomain,
                        'suffix': suffix,
                        'title': title[:100],
                        'match_score': match_score,
                        'matches_company': matches_company,
                        'exact_match': exact_match,
                        'search_query': search_query,
                        'is_subdomain': is_subdomain,
                        'is_main_domain': is_main_domain
                    }
                    
                    # Check if we already have this domain
                    if not any(d['domain'] == domain for d in all_domains):
                        all_domains.append(domain_info)
                        match_type = "EXACT MATCH" if exact_match else ("MAIN DOMAIN" if is_main_domain else "SUBDOMAIN")
                        custom_print(f"    Found: {domain_name} (score: {match_score}, type: {match_type})")
                
                break  # Successfully got results with this API key
                
            except HttpError as e:
                if e.resp.status == 429:
                    custom_print(f"❌ Google API error with key {api_key}: Quota exceeded. Trying next API key...")
                    continue
                else:
                    custom_print(f"❌ Google API error with key {api_key}: {e}")
                    continue
            except Exception as e:
                custom_print(f"❌ Google API error with key {api_key}: {e}")
                continue
    
    # Sort domains: EXACT MATCHES FIRST, then by score, then main domains over subdomains
    all_domains.sort(key=lambda x: (
        x['exact_match'],  # Exact matches first
        x['match_score'],  # Then by score
        x['is_main_domain'],  # Then main domains
        not x['is_subdomain']  # Then non-subdomains
    ), reverse=True)
    
    custom_print(f"\n📊 Search Results Analysis:")
    custom_print(f"  Total domains found: {len(all_domains)}")
    
    if all_domains:
        custom_print(f"\n✅ Top matching domains:")
        for i, domain_info in enumerate(all_domains[:5], 1):
            if domain_info['exact_match']:
                domain_type = "⭐ EXACT MATCH"
            elif domain_info['is_main_domain'] and not domain_info['is_subdomain']:
                domain_type = "MAIN DOMAIN"
            else:
                domain_type = "SUBDOMAIN"
            custom_print(f"  {i}. {domain_info['domain']} (score: {domain_info['match_score']}, type: {domain_type})")
            custom_print(f"     Domain: {domain_info['domain_base']}.{domain_info['suffix']}")
            custom_print(f"     Subdomain: {domain_info['subdomain'] or '(none)'}")
            custom_print(f"     Title: {domain_info['title']}")
        
        # Return the best matching domain
        best = all_domains[0]
        
        # If we have an exact match, always use it
        if best['exact_match']:
            custom_print(f"\n⭐⭐⭐ Using EXACT MATCH domain: {best['domain']} (score: {best['match_score']})")
            return best['domain']
        
        # Better subdomain filtering
        skip_subdomains = ['returns', 'order', 'tracking', 'shipping', 'shop', 'store']
        if best['is_subdomain']:
            subdomain_parts = best['subdomain'].split('.')
            if any(part in skip_subdomains for part in subdomain_parts):
                custom_print(f"⚠️ Best domain is a service subdomain: {best['domain_name']}")
                # Try to find a main domain alternative or exact match
                for domain_info in all_domains[1:]:
                    if domain_info['exact_match'] or (domain_info['is_main_domain'] and not domain_info['is_subdomain'] and domain_info['match_score'] >= 30):
                        custom_print(f"✅ Using alternative domain: {domain_info['domain']}")
                        return domain_info['domain']
        
        if best['match_score'] >= 20:
            custom_print(f"\n✅ Using best matching domain: {best['domain']} (score: {best['match_score']})")
            return best['domain']
        else:
            custom_print(f"\n⚠️ Best domain score too low: {best['match_score']} (threshold: 20)")
            custom_print(f"   Best available: {best['domain']}")
            return None
    
    custom_print("❌ No suitable domains found in any search")
    
    # REMOVED DIRECT DOMAIN CONSTRUCTION FALLBACK
    # Return None if no domains found
    return None

def fallback_google_search(query, company, location, social_platforms=None):
    debug_log = f"Query: {query}\nResults:\n"
    socials = {platform: [] for platform in social_platforms} if social_platforms else set()
    company_links = []
    company_clean = re.sub(r'\s*(pvt|ltd|limited|private|inc|incorporated|co|company)\s*$', '', company, flags=re.IGNORECASE).lower()
    location_lower = location.lower() if location else ""
    company_variations = [company_clean, company_clean.replace(" ", ""), company_clean.replace(" ", "-")]
    location_variations = [location_lower, location_lower.replace(" ", ""), location_lower.replace(" ", "-")] if location else [""]
    excluded_terms = ["login", "security", "plugins", "help", "signup", "terms", "privacy", "ads", "careers", "policies", "support"]

    for api_key in GOOGLE_API_KEYS:
        try:
            service = build("customsearch", "v1", developerKey=api_key)
            res = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=10).execute()
            results = res.get("items", [])
            
            for item in results:
                title = item.get("title", "").lower()
                snippet = item.get("snippet", "").lower()
                link = item.get("link", "")
                link_lower = link.lower()
                path = urlparse(link).path.lower().strip('/')
                debug_log += f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n"
                
                is_company_relevant = False
                for comp_var in company_variations:
                    if (comp_var in title or
                         comp_var in snippet or
                         comp_var in link_lower or
                        (location and f"{comp_var}{location_variations[0]}" in title) or
                        (location and f"{comp_var}{location_variations[0]}" in snippet) or
                        (location and f"{comp_var}{location_variations[0]}" in link_lower)):
                        is_company_relevant = True
                        break
                
                is_location_relevant = any(loc_var in title or loc_var in snippet or loc_var in link_lower for loc_var in location_variations) if location else True
                context_indicators = ["official", "profile", "page", "account", company_clean] + ([location_lower] if location else [])
                is_context_relevant = any(indicator in title or indicator in snippet for indicator in context_indicators)
                
                if social_platforms and is_company_relevant and is_context_relevant:
                    for platform in social_platforms:
                        regex = SOCIAL_REGEXES.get(platform)
                        if regex and regex.search(link):
                            # Apply validation to fallback results too
                            validated_link = validate_social_link(link, platform)
                            if validated_link and not any(x in validated_link.lower() for x in excluded_terms):
                                if validated_link not in socials[platform]:
                                    socials[platform].append(validated_link)
                                    debug_log += (
                                        f"Accepted {platform} link: {validated_link}\n"
                                        f"Path: {path}\n"
                                        f"Company relevance: {any(comp_var in path for comp_var in company_variations)}\n"
                                        f"Location relevance: {any(loc_var in path for loc_var in location_variations) if location else 'N/A'}\n"
                                        f"Context relevance: {is_context_relevant}\n---\n"
                                    )
                            else:
                                debug_log += f"Rejected {platform} link: {link} (failed validation or contains excluded terms)\n---\n"
                        else:
                            debug_log += f"Rejected link for {platform}: {link} (no regex match)\n---\n"
                
                if is_company_relevant:
                    formatted_link = link if link.startswith(('http://', 'https://')) else f"https://{link}"
                    if (not any(x in formatted_link.lower() for x in excluded_terms) and
                        not any(formatted_link in socials[platform] for platform in socials)):
                        parsed_link = urlparse(formatted_link)
                        if parsed_link.netloc:
                            company_links.append(formatted_link)
                            debug_log += (
                                f"Accepted company link: {formatted_link}\n"
                                f"Path: {path}\n"
                                f"Company relevance: {is_company_relevant}\n"
                                f"Location relevance: {is_location_relevant}\n"
                                f"Context relevance: {is_context_relevant}\n---\n"
                            )
                        else:
                            debug_log += f"Rejected company link: {formatted_link} (invalid domain)\n---\n"
                    else:
                        debug_log += f"Rejected company link: {formatted_link} (contains excluded terms or already in socials)\n---\n"
                else:
                    debug_log += f"Rejected company link: {link} (not company relevant)\n---\n"
            
            for platform in socials:
                socials[platform] = sorted(list(set(socials[platform])))
            company_links = sorted(list(set(company_links)))
            
            # Make sure DEBUG_DIR exists before writing
            if not os.path.exists(DEBUG_DIR):
                os.makedirs(DEBUG_DIR)
                
            with open(os.path.join(DEBUG_DIR, f"fallback_search_{query.replace(' ', '_')}.txt"), "a", encoding="utf-8") as f:
                f.write(debug_log + "\n")
            custom_print(f"🔍 Fallback search for {query}: Found {sum(len(v) for v in socials.values()) if social_platforms else len(socials)} {'links' if social_platforms else 'emails'}, {len(company_links)} company links")
            return socials, company_links, debug_log
            
        except HttpError as e:
            if e.resp.status == 429:
                custom_print(f"❌ Google API error with key {api_key}: Quota exceeded. Trying next API key...")
                debug_log += f"Error with key {api_key}: Quota exceeded\n"
                continue
            else:
                custom_print(f"❌ Fallback Google API error with key {api_key} for query '{query}': {e}")
                debug_log += f"Error with key {api_key}: {e}\n"
                
                # Make sure DEBUG_DIR exists before writing
                if not os.path.exists(DEBUG_DIR):
                    os.makedirs(DEBUG_DIR)
                    
                with open(os.path.join(DEBUG_DIR, f"fallback_search_{query.replace(' ', '_')}.txt"), "a", encoding="utf-8") as f:
                    f.write(debug_log + "\n")
                return ({} if social_platforms else set()), [], debug_log
        except Exception as e:
            custom_print(f"❌ Fallback Google API error with key {api_key} for query '{query}': {e}")
            debug_log += f"Error with key {api_key}: {e}\n"
            
            # Make sure DEBUG_DIR exists before writing
            if not os.path.exists(DEBUG_DIR):
                os.makedirs(DEBUG_DIR)
                
            with open(os.path.join(DEBUG_DIR, f"fallback_search_{query.replace(' ', '_')}.txt"), "a", encoding="utf-8") as f:
                f.write(debug_log + "\n")
            return ({} if social_platforms else set()), [], debug_log
    
    custom_print(f"❌ All API keys failed or quota exceeded for query '{query}'.")
    debug_log += "All API keys failed or quota exceeded\n"
    
    # Make sure DEBUG_DIR exists before writing
    if not os.path.exists(DEBUG_DIR):
        os.makedirs(DEBUG_DIR)
        
    with open(os.path.join(DEBUG_DIR, f"fallback_search_{query.replace(' ', '_')}.txt"), "a", encoding="utf-8") as f:
        f.write(debug_log + "\n")
    return ({} if social_platforms else set()), [], debug_log

def check_with_requests_session(url):
    """Check accessibility using requests with session and headers"""
    session = requests.Session()
    
    # Set up retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
    }
    
    try:
        response = session.get(url, headers=headers, timeout=30, allow_redirects=True)
        return response.status_code in [200, 301, 302]
    except:
        return False

def check_with_selenium_quick(url):
    """Quick check using Selenium with enhanced options"""
    driver = None
    try:
        from selenium_handler import init_selenium_driver
        driver = init_selenium_driver()
        driver.get(url)
        # Wait a bit for potential redirects or dynamic loading
        time.sleep(3)
        
        # Check if page loaded successfully
        page_source = driver.page_source
        return len(page_source) > 1000 and "error" not in page_source.lower()[:500]
    
    except Exception:
        return False
    finally:
        if driver:
            driver.quit()

def check_with_curl(url):
    """Fallback using curl command"""
    try:
        cmd = [
            'curl', '-s', '-I', '--max-time', '30',
            '-H', f'User-Agent: {random.choice(USER_AGENTS)}',
            '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        return result.returncode == 0 and ('200 OK' in result.stdout or '301' in result.stdout or '302' in result.stdout)
    except:
        return False

def is_link_accessible(url, max_retries=3):
    """Enhanced accessibility check with multiple methods and retry logic"""
    
    methods = [
        ('requests_session', check_with_requests_session),
        ('selenium_quick', check_with_selenium_quick),
        ('curl_fallback', check_with_curl)
    ]
    
    for method_name, method_func in methods:
        for attempt in range(max_retries):
            try:
                custom_print(f"🔄 Checking {url} with {method_name} (attempt {attempt + 1})")
                result = method_func(url)
                if result:
                    custom_print(f"✅ Link accessible via {method_name}: {url}")
                    return True
                time.sleep(random.uniform(1, 3))  # Random delay between attempts
            except Exception as e:
                custom_print(f"❌ {method_name} failed for {url}: {str(e)}")
                continue
    
    custom_print(f"⏭️ All methods failed for: {url}")
    return False

def validate_company_match(domain, company_name):
    """Validate if a domain belongs to the given company with strict matching"""
    if not domain or not company_name:
        return False
    
    parsed = urlparse(domain)
    domain_name = parsed.netloc.lower()
    
    # Clean company name
    company_clean = re.sub(r'\s*(pvt|ltd|limited|private|inc|incorporated|co|company|corp|corporation|llc|gmbh|plc)\s*\.?$', 
                          '', company_name, flags=re.IGNORECASE)
    company_clean = company_clean.lower().strip()
    
    # Get company words (ignore common short words)
    company_words = [word for word in re.findall(r'\b\w+\b', company_clean) if len(word) > 3]
    
    if not company_words:
        company_words = re.findall(r'\b\w+\b', company_clean)
    
    # Extract domain without TLD and www
    domain_base = re.sub(r'^www\.', '', domain_name)
    domain_base = re.sub(r'\.(com|org|net|io|co|in|us|uk|au|ca|de|fr|jp|biz|info|mobi|name|tv|cc|ws)$', '', domain_base)
    
    # Check if domain contains company words
    matches = []
    for word in company_words:
        if word in domain_base:
            matches.append(word)
    
    # Calculate match percentage
    if company_words:
        match_percentage = (len(matches) / len(company_words)) * 100
        
        # Strict criteria: At least 50% match OR first word must match
        if match_percentage >= 50 or (company_words and company_words[0] in domain_base):
            custom_print(f"✅ Company match: {match_percentage:.0f}% - Words matched: {matches}")
            return True
        else:
            custom_print(f"❌ Weak company match: {match_percentage:.0f}% - Only matched: {matches}")
            return False
    
    return False