#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import boto3
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

PRESIGNED_POST_DATA = json.loads(os.environ.get("PRESIGNED_POST_DATA", "{}"))
S3_PREFIX = os.environ.get("S3_PREFIX", "")
TASK_DESCRIPTION = os.environ.get("TASK_DESCRIPTION", "python selenium test")
TENANT_ID = os.environ.get("TENANT_ID", "")
SECRETS_PREFIX = os.environ.get("SECRETS_PREFIX", "")
OUTPUT_DIR = "/output"

secrets_client = boto3.client("secretsmanager")


def log_step(step: int, action: str, **extra):
    entry = {
        "step": step,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    print(json.dumps(entry), flush=True)


def upload_file(local_path: str, s3_key: str):
    if not PRESIGNED_POST_DATA:
        return
    url = PRESIGNED_POST_DATA["url"]
    fields = PRESIGNED_POST_DATA["fields"].copy()
    fields["key"] = f"{S3_PREFIX}{s3_key}"
    boundary = "----PresignedBoundary"
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    with open(local_path, "rb") as f:
        file_data = f.read()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{s3_key}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
    body += file_data
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    urllib.request.urlopen(req)
    log_step(0, "upload", key=s3_key)


def take_screenshot(driver, step: int, name: str) -> str:
    os.makedirs(f"{OUTPUT_DIR}/screenshots", exist_ok=True)
    local_path = f"{OUTPUT_DIR}/screenshots/{name}"
    driver.save_screenshot(local_path)
    s3_key = f"screenshots/{name}"
    upload_file(local_path, s3_key)
    log_step(step, "screenshot", key=s3_key)
    return s3_key


def load_credentials():
    if not SECRETS_PREFIX or not TENANT_ID:
        log_step(0, "skip_credentials", reason="SECRETS_PREFIX or TENANT_ID not set")
        return False
    secret_name = f"{SECRETS_PREFIX}/{TENANT_ID}/website-credentials"
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        secret = json.loads(response["SecretString"])
        log_step(0, "credentials_loaded", key_count=len(secret))
        return True
    except Exception as e:
        log_step(0, "credentials_error", error=str(e))
        return False


def run():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    credentials_loaded = load_credentials()

    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1024,768")

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=chrome_options)
    screenshots = []

    try:
        log_step(1, "navigate", url="https://books.toscrape.com")
        driver.get("https://books.toscrape.com")
        time.sleep(2)
        screenshots.append(take_screenshot(driver, 2, "step1_homepage.png"))

        log_step(3, "extract_books", page="homepage")
        books = []
        for article in driver.find_elements(By.CSS_SELECTOR, "article.product_pod"):
            title = article.find_element(By.CSS_SELECTOR, "h3 a").get_attribute("title")
            price = article.find_element(By.CSS_SELECTOR, ".price_color").text
            rating = article.find_element(By.CSS_SELECTOR, "p.star-rating").get_attribute("class").split()[-1]
            books.append({"title": title, "price": price, "rating": rating})

        screenshots.append(take_screenshot(driver, 4, "step2_books_extracted.png"))

        log_step(5, "navigate_detail", book=books[0]["title"] if books else "none")
        first_link = driver.find_element(By.CSS_SELECTOR, "article.product_pod h3 a")
        first_link.click()
        time.sleep(2)

        description = ""
        desc_elements = driver.find_elements(By.CSS_SELECTOR, "#product_description ~ p")
        if desc_elements:
            description = desc_elements[0].text

        screenshots.append(take_screenshot(driver, 6, "step3_book_detail.png"))

        result = {
            "status": "success",
            "task": TASK_DESCRIPTION,
            "credentials_loaded": credentials_loaded,
            "books_found": len(books),
            "books": books[:10],
            "featured_book_description": description,
            "screenshots": screenshots,
            "completedAt": datetime.now(timezone.utc).isoformat(),
        }

        result_path = f"{OUTPUT_DIR}/result.json"
        with open(result_path, "w") as f:
            json.dump(result, f, indent=2)
        upload_file(result_path, "result.json")

        log_step(7, "complete", status="success", result_count=len(books))
        return 0

    except Exception as e:
        log_step(99, "error", error=str(e))
        error_result = {
            "status": "error",
            "error": str(e),
            "completedAt": datetime.now(timezone.utc).isoformat(),
        }
        result_path = f"{OUTPUT_DIR}/result.json"
        with open(result_path, "w") as f:
            json.dump(error_result, f, indent=2)
        upload_file(result_path, "result.json")
        return 1

    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(run())
