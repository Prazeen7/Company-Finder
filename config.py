import re
import tldextract

extract = tldextract.TLDExtract()

# === Configuration ===
GOOGLE_API_KEYS = [
    "AIzaSyDASUJ9-Q1kw0uYoUYuIpNZmBBvG-0PlCE",
    "AIzaSyAeHrqRKZ1nYn_nNN8KXrgDrhX8_hy-bKo",
    "AIzaSyCTWb-yJEKMc6ff9CXiW-jEWol05w7VldU",
    "AIzaSyB8FHdHOcHygkkFxOitFdBxuT9MMwLwqoQ"
]
SEARCH_ENGINE_ID = "a6cea8f5219ce4ccb"
MAX_DEPTH = 3
CRAWL_DELAY = 0.5
RETRY_ATTEMPTS = 2
TIMEOUT = 30
DEBUG_DIR = "debug_html"
MODEL_NAME = "meta-llama/Meta-Llama-3.1-8B-Instruct"
MAX_SELENIUM_WORKERS = 4
BATCH_SIZE = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1"
]

# Compiled regex patterns
EMAIL_REGEX = re.compile(
    r'(?:'  # Start non-capturing group
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}\b|'  # Standard email
    r'[a-zA-Z0-9._%+-]+\s*$$at$$\s*[a-zA-Z0-9.-]+\s*$$dot$$\s*[a-zA-Z]{2,6}\b|'  # (at)/(dot)
    r'[a-zA-Z0-9._%+-]+%40[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}\b|'  # Encoded @ (%40)
    r'"email":"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}"'  # JSON email
    r')',  # Close non-capturing group
    re.IGNORECASE)

PHONE_REGEX = re.compile(
    r'(?<![\w\-/])'  # Negative lookbehind to avoid matching within words or paths
    r'(?:\+(?:1|44|61|91|977|86|81|49|33|39|34|7|55|52|54|27|20|234|254|256|233|63|92|94|95|880|60|65|66|84|852|853|886|82|357|30|31|32|41|43|45|46|47|48|351|420|421|36|695|386|383|389|355|377|376|378|379|380|381|382|385|387|359|40|373))'  # Country codes
    r'[-.\s]?\d{1,4}[-.\s]?\d{2,4}[-.\s]?\d{2,4}(?:[-.\s]?\d{2,4})?(?:\s*(?:ext\.?|extension|x)\s*\d{1,5})?'  # Phone number pattern
    r'|tel:\+\d{1,15}'  # tel: links with country code
    r'|"(?:phone|tel)":\s*"\+[^"]*"'  # JSON phone with country code
    r'(?!\w)',  # Negative lookahead to avoid trailing characters
    re.IGNORECASE
)

ADDRESS_REGEX = re.compile(
    r'\b(?:\d{1,5}\s+)?[\w\s.-]+(?:\s+(?:St|Ave|Rd|Blvd|Ln|Dr|Ct|Pl|Way|Ter|Cir|Pkwy|Sq)(?:\.|(?:reet|venue|oad|oulevard|ane|rive|ourt|lace|errace|ircle|arkway|quare))?)?(?:[\s,]+[\w\s.-]+){1,4}(?:\s+\d{5}(?:-\d{4})?)?\b',
    re.IGNORECASE)

SOCIAL_REGEXES = {
    "facebook": re.compile(
        r'(?:https?://)?(?:[a-z]{2,3}\.)?facebook\.com/(?:profile\.php\?id=\d+|(?:pages/)?[A-Za-z0-9._-]+/?)(?:\?[^"\']*)?$',
        re.IGNORECASE
    ),
    "instagram": re.compile(
        r'(?:https?://)?(?:[a-z]{2,3}\.)?instagram\.com/[A-Za-z0-9._-]+/?(?:\?.*)?$',
        re.IGNORECASE
    ),
    "linkedin": re.compile(
        r'(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9._-]+/?(?:\?.*)?$',
        re.IGNORECASE
    ),
    "youtube": re.compile(
        r'(?:https?://)?(?:[a-z]{2,3}\.)?youtube\.com/(?:channel|user|c)/[A-Za-z0-9._-]+/?(?:\?.*)?$',
        re.IGNORECASE
    ),
    "pinterest": re.compile(
        r'(?:https?://)?(?:[a-z]{2,3}\.)?pinterest\.com/[A-Za-z0-9._-]+/?(?:\?.*)?$',
        re.IGNORECASE
    ),
}
LINK_REGEX = re.compile(r'(?:href|url|Link|to)\s*[:=]\s*[\'"]([^"\']+)[\'"]', re.IGNORECASE)

REACT_ATTRIBUTES = [
    "id", "class", "href", "src", "for", "type", "name", "value", "placeholder",
    "role", "aria-label", "aria-hidden", "aria-describedby", "data-testid", "data-component",
    "product", "backgroundColor", "themeColor", "titleColor", "textColor", "footerURL"
]