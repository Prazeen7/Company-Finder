import os
import uuid
import time
from flask import Flask, render_template, request, jsonify, Response
from utils import custom_print, process_log
from scraper import crawl_website
from export import save_results, generate_excel
from google_api import find_domain_google, validate_domain_for_scraping, fallback_google_search, validate_company_match
from config import SOCIAL_REGEXES, DEBUG_DIR

# Import from data_processor - model loading is now lazy/async
from data_processor import load_country_codes, extract_location_info, generate_business_nature, get_model_status, load_model_async

# Ensure debug directory exists
if not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = str(uuid.uuid4())

# Load country codes at startup
load_country_codes()

# Start model loading in background (UI loads instantly)
print("🚀 Starting model loading in background...")
load_model_async()

# Store results globally for export
current_results = []

def scrape_company(company, location=None, manual_domain=None):
    global process_log
    process_log = []
    query = f"{company} {location}" if location else company
    custom_print(f"\n🔍 Searching Google for '{query}'...")
    
    domain = manual_domain or find_domain_google(query, company)
    
    if not domain:
        custom_print("❌ No valid domain found matching the company name.")
        return {
            "company_name": company,
            "domain": "N/A",
            "emails": [],
            "social_media": {platform: [] for platform in SOCIAL_REGEXES.keys()},
            "meta_description": "N/A",
            "about_content": "N/A",
            "phone_numbers": [],
            "addresses": [],
            "links": [],
            "country": "N/A",
            "business_nature": "N/A",
            "company_links": [],
            "process_log": process_log,
            "status": "failed",
            "error_message": f"No website domain found for '{company}'. Try adding location or providing domain manually."
        }
    
    # Validate the domain before proceeding
    if not validate_domain_for_scraping(domain):
        custom_print(f"⚠️ Domain {domain} appears to be a service portal. This may not be the main website.")
        # You could add logic here to try alternative domains
    
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    if not domain.endswith("/"):
        domain += "/"
    
    custom_print(f"✅ Using verified domain: {domain}")
    custom_print(f"📡 Starting full-site scraping from: {domain}\n")

    emails, socials, meta_desc, about_content, phones, addresses, links, location_info, business_nature, footer_contents, debug_info = crawl_website(domain)

    fallback_debug_logs = []
    company_links = []
    # Only perform fallback if emails are missing or some social platforms have no links
    missing_socials = [platform for platform in SOCIAL_REGEXES.keys() if not socials.get(platform)]
    if not emails or missing_socials:
        custom_print(f"\n⚠️ Missing some data (Emails: {not emails}, Missing socials: {missing_socials}). Performing fallback Google searches...")
        
        if not emails:
            email_query = f"{company} {location} email" if location else f"{company} email"
            custom_print(f"🔍 Searching Google for '{email_query}'...")
            fallback_emails, email_company_links, email_debug_log = fallback_google_search(email_query, company, location or "")
            emails.update(fallback_emails)
            company_links.extend(email_company_links)
            custom_print(f"📧 Found {len(fallback_emails)} emails in fallback search: {fallback_emails}")
            custom_print(f"🔗 Found {len(email_company_links)} company links in email search: {email_company_links}")
            fallback_debug_logs.append({"query": email_query, "log": email_debug_log})

        if missing_socials:
            custom_print(f"🔍 Searching Google for social media: {missing_socials}")
            for platform in missing_socials:
                social_query = f"{company} {location} {platform}" if location else f"{company} {platform}"
                custom_print(f"🔍 Searching Google for '{social_query}'...")
                fallback_socials, social_company_links, social_debug_log = fallback_google_search(social_query, company, location or "", [platform])
                if fallback_socials[platform]:
                    socials[platform] = list(set(socials.get(platform, []) + fallback_socials[platform]))
                    custom_print(f"🔗 Found {len(fallback_socials[platform])} {platform} links: {fallback_socials[platform]}")
                else:
                    custom_print(f"🔗 No {platform} links found in fallback search.")
                company_links.extend(social_company_links)
                custom_print(f"🔗 Found {len(social_company_links)} company links in {platform} search: {social_company_links}")
                fallback_debug_logs.append({"query": social_query, "log": social_debug_log})

    company_links = sorted(list(set(company_links)))
    debug_info.append({
        "url": "fallback_searches",
        "status": "fallback",
        "fallback_logs": fallback_debug_logs
    })

    # Convert phone set to list for the results
    phone_list = list(phones)

    results = save_results(
        domain, 
        emails, 
        socials, 
        meta_desc, 
        about_content, 
        phone_list,  # Pass list of phone strings
        addresses, 
        links, 
        location_info,
        business_nature, 
        footer_contents, 
        debug_info, 
        process_log, 
        company_links, 
        company
    )
    
    # Add success status
    results["status"] = "success"
    results["error_message"] = ""
    
    return results

# Flask Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/model-status')
def model_status():
    """Get the current model loading status"""
    status = get_model_status()
    return jsonify(status)

@app.route('/search', methods=['POST'])
def search():
    global current_results
    try:
        search_type = request.form.get('search_type')
        results = []
        
        if search_type == 'single':
            company = request.form.get('company', '').strip()
            location = request.form.get('location', '').strip()
            manual_domain = request.form.get('manual_domain', '').strip()
            
            if not company:
                return jsonify({
                    'error': 'Please provide company name.',
                    'results': [],
                    'errors': []
                }), 400
            
            custom_print(f"Processing single search for {company} in {location or 'N/A'}")
            result = scrape_company(company, location or None, manual_domain or None)
            
            # Check if domain was found
            if result.get('status') == 'failed':
                return jsonify({
                    'error': result.get('error_message', 'Domain not found'),
                    'results': [result],  # Include the result with error info
                    'errors': []
                }), 200  # Still return 200 to show the error in UI
            
            results.append(result)
            
        elif search_type in ['batch_5', 'batch_10']:
            batch_size = 5 if search_type == 'batch_5' else 10
            batch_errors = []
            failed_companies = []
            
            for i in range(batch_size):
                company = request.form.get(f'company_{i}', '').strip()
                location = request.form.get(f'location_{i}', '').strip()
                
                if company:
                    custom_print(f"Processing batch search {i+1}/{batch_size} for {company} in {location or 'N/A'}")
                    result = scrape_company(company, location or None)
                    result['serial_no'] = i + 1
                    
                    # Check if domain was found
                    if result.get('status') == 'failed':
                        failed_companies.append({
                            'company': company,
                            'error': result.get('error_message', 'Domain not found')
                        })
                        custom_print(f"⚠️ Failed to find domain for: {company}")
                    
                    results.append(result)
                else:
                    custom_print(f"Skipping batch entry {i+1} due to missing company name")
                    batch_errors.append({
                        'batch_index': i + 1,
                        'error': 'Missing company name'
                    })
            
            # If all companies failed, return error
            if len(results) == len(failed_companies) and len(failed_companies) > 0:
                error_messages = [f"{fc['company']}: {fc['error']}" for fc in failed_companies]
                return jsonify({
                    'error': 'Failed to find domains for all companies:\n' + '\n'.join(error_messages),
                    'results': results,  # Include all results with their error status
                    'errors': batch_errors + failed_companies
                }), 200
            
            # If some companies failed, still return results but with warning
            if failed_companies and len(failed_companies) < len(results):
                error_messages = [f"{fc['company']}: {fc['error']}" for fc in failed_companies]
                custom_print(f"⚠️ Some companies failed: {error_messages}")

        # Aggregate error details from debug_info
        aggregated_errors = []
        for result in results:
            debug_info = result.get('process_log', [])
            for debug_entry in debug_info:
                if 'error_details' in debug_entry and debug_entry['error_details']:
                    for error in debug_entry['error_details']:
                        aggregated_errors.append({
                            'url': debug_entry['url'],
                            'method': error.get('method', 'unknown'),
                            'attempt': error.get('attempt', 'unknown'),
                            'error_type': error.get('error_type', 'unknown'),
                            'message': error.get('message', 'No error message provided'),
                            'http_status': error.get('http_status', 'Unknown'),
                            'current_url': error.get('current_url', 'Unknown')
                        })

        current_results = results  # Store for export
        
        return jsonify({
            'success': True,
            'results': results,
            'batch': search_type.startswith('batch'),
            'errors': aggregated_errors,
            'domain_not_found_count': len([r for r in results if r.get('status') == 'failed'])
        })
        
    except Exception as e:
        custom_print(f"Error in search route: {str(e)}")
        return jsonify({
            'error': f'An error occurred during the search: {str(e)}',
            'results': [],
            'errors': [{'error_type': type(e).__name__, 'message': str(e)}]
        }), 500

@app.route('/export/excel', methods=['POST'])
def export_excel():
    try:
        data = request.get_json()
        results_data = data.get('results', []) if data else current_results
        
        if not results_data:
            return jsonify({'error': 'No results to export'}), 400
        
        excel_content = generate_excel(results_data)
        
        response = Response(
            excel_content,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename=company_scraper_results_{time.strftime("%Y%m%d_%H%M%S")}.xlsx'
            }
        )
        
        return response
        
    except Exception as e:
        custom_print(f"Error in Excel export: {str(e)}")
        return jsonify({'error': f'Failed to export Excel: {str(e)}'}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)