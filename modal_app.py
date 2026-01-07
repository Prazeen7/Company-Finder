"""
Modal Labs deployment wrapper for Company Finder application.

This script deploys the Flask-based company finder application to Modal Labs
with all necessary dependencies including Chrome/Selenium for web scraping.
"""

import modal
import os

# Create a Modal app for the application
app = modal.App(name="company-finder")

# Define the container image with all dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        # Chrome and dependencies for Selenium
        "wget",
        "gnupg",
        "unzip",
        "curl",
        "ca-certificates",
        "fonts-liberation",
        "libasound2",
        "libatk-bridge2.0-0",
        "libatk1.0-0",
        "libatspi2.0-0",
        "libcups2",
        "libdbus-1-3",
        "libdrm2",
        "libgbm1",
        "libgtk-3-0",
        "libnspr4",
        "libnss3",
        "libwayland-client0",
        "libxcomposite1",
        "libxdamage1",
        "libxfixes3",
        "libxkbcommon0",
        "libxrandr2",
        "xdg-utils",
        "libu2f-udev",
        "libvulkan1",
    )
    .run_commands(
        # Install Chrome for Testing (stable version)
        "wget -q https://storage.googleapis.com/chrome-for-testing-public/142.0.7444.61/linux64/chrome-linux64.zip -O /tmp/chrome-linux64.zip",
        "unzip -q /tmp/chrome-linux64.zip -d /opt/",
        "ln -s /opt/chrome-linux64/chrome /usr/local/bin/google-chrome",
        "ln -s /opt/chrome-linux64/chrome /usr/local/bin/chrome",
        "rm /tmp/chrome-linux64.zip",
        # Install ChromeDriver (matching version)
        "wget -q https://storage.googleapis.com/chrome-for-testing-public/142.0.7444.61/linux64/chromedriver-linux64.zip -O /tmp/chromedriver-linux64.zip",
        "unzip -q /tmp/chromedriver-linux64.zip -d /tmp/",
        "mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver",
        "chmod +x /usr/local/bin/chromedriver",
        "rm -rf /tmp/chromedriver*",
    )
    .pip_install_from_requirements("requirements-modal.txt")
    .add_local_dir(
        ".",
        "/root/app",
        ignore=["debug_html", "myenv", "__pycache__", "*.pyc"]
    )
)

# Create persistent volume for model cache and debug data
volume = modal.Volume.from_name("company-finder-data", create_if_missing=True)

# To create secrets, run:
# modal secret create google-api-keys \
#   GOOGLE_API_KEY_1="AIzaSyDASUJ9-Q1kw0uYoUYuIpNZmBBvG-0PlCE" \
#   GOOGLE_API_KEY_2="AIzaSyAeHrqRKZ1nYn_nNN8KXrgDrhX8_hy-bKo" \
#   GOOGLE_API_KEY_3="AIzaSyCTWb-yJEKMc6ff9CXiW-jEWol05w7VldU" \
#   GOOGLE_API_KEY_4="AIzaSyB8FHdHOcHygkkFxOitFdBxuT9MMwLwqoQ" \
#   SEARCH_ENGINE_ID="a6cea8f5219ce4ccb"


@app.function(
    image=image,
    gpu=None,
    cpu=4.0,
    memory=16384,  # 16GB RAM
    timeout=3600,  # 1 hour timeout
    volumes={"/data": volume},
    secrets=[modal.Secret.from_name("google-api-keys")],
)
@modal.wsgi_app()
def flask_app():
    """
    Main Flask application served via WSGI.
    """
    import sys
    sys.path.insert(0, "/root/app")

    # Set environment variables for the app
    os.environ.setdefault("DEBUG_DIR", "/data/debug_html")

    # Create debug directory if it doesn't exist
    os.makedirs("/data/debug_html", exist_ok=True)

    # Import the Flask app
    from app import app as flask_application

    # Return WSGI app
    return flask_application


@app.function(
    image=image,
    gpu="A100",  # GPU for model inference
    cpu=2.0,
    memory=81920,
    timeout=86400,  # 24 hours
    volumes={"/data": volume},
)
def warm_up_model():
    """
    Pre-load the transformer model to warm up GPU.
    Call this function to cache the model before serving requests.
    """
    from transformers import AutoModelForCausalLM, AutoTokenizer

    MODEL_NAME = "Qwen/Qwen2-0.5B-Instruct"
    print(f"Loading model: {MODEL_NAME}")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        torch_dtype="auto",
        device_map="auto"
    )

    print(f"Model loaded successfully!")
    return {"status": "Model warmed up", "model": MODEL_NAME}


@app.local_entrypoint()
def main():
    """
    Local entrypoint for testing or running batch jobs.
    Use `modal run modal_app.py` to execute this.
    """
    print("Company Finder - Modal Labs Deployment")
    print("=" * 50)
    print("\nDeploying Flask application to Modal...")
    print("\nTo serve the web app, use:")
    print("  modal serve modal_app.py")
    print("\nTo deploy permanently:")
    print("  modal deploy modal_app.py")
    print("\n" + "=" * 50)

    # Optionally warm up the model
    result = warm_up_model.remote()
    print(f"\nModel warmup result: {result}")


# CLI function for batch processing
@app.function(
    image=image,
    gpu="T4",
    cpu=4.0,
    memory=16384,
    timeout=3600,
    volumes={"/data": volume},
    secrets=[modal.Secret.from_name("google-api-keys")],
)
def batch_search(query: str, num_results: int = 10):
    """
    Run a batch company search from CLI.

    Usage:
        modal run modal_app.py::batch_search --query "tech startups in SF" --num-results 20
    """
    import sys
    sys.path.insert(0, "/root/app")

    from app import enhanced_google_search, extract_data

    print(f"Searching for: {query}")
    results = enhanced_google_search(query, num_results)

    print(f"\nFound {len(results)} results")
    for idx, result in enumerate(results, 1):
        print(f"\n{idx}. {result.get('title', 'N/A')}")
        print(f"   URL: {result.get('link', 'N/A')}")

    return results


if __name__ == "__main__":
    print("Use 'modal run modal_app.py' to deploy to Modal")
