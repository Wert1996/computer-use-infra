#!/usr/bin/env python3
import json
import os
import sys
import time
from datetime import datetime, timezone

import boto3
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "")
TASK_DESCRIPTION = os.environ.get("TASK_DESCRIPTION", "python selenium test")
OUTPUT_DIR = "/output"

s3 = boto3.client("s3") if S3_BUCKET else None


def log_step(step: int, action: str, **extra):
    entry = {
        "step": step,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    print(json.dumps(entry), flush=True)


def upload_file(local_path: str, s3_key: str):
    if s3 and S3_BUCKET:
        s3.upload_file(local_path, S3_BUCKET, f"{S3_PREFIX}{s3_key}")
        log_step(0, "upload", key=s3_key)


def take_screenshot(driver, step: int, name: str) -> str:
    os.makedirs(f"{OUTPUT_DIR}/screenshots", exist_ok=True)
    local_path = f"{OUTPUT_DIR}/screenshots/{name}"
    driver.save_screenshot(local_path)
    s3_key = f"screenshots/{name}"
    upload_file(local_path, s3_key)
    log_step(step, "screenshot", key=s3_key)
    return s3_key


def run():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1024,768")

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=chrome_options)
    screenshots = []

    try:
        log_step(1, "navigate", url="https://www.google.com")
        driver.get("https://www.google.com")
        time.sleep(2)
        screenshots.append(take_screenshot(driver, 2, "step1_google_home.png"))

        log_step(3, "search", query=TASK_DESCRIPTION)
        search_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        search_box.send_keys(TASK_DESCRIPTION)
        search_box.send_keys(Keys.RETURN)
        time.sleep(3)
        screenshots.append(take_screenshot(driver, 4, "step2_search_results.png"))

        log_step(5, "extract_results")
        results = []
        for elem in driver.find_elements(By.CSS_SELECTOR, "h3")[:5]:
            results.append(elem.text)

        screenshots.append(take_screenshot(driver, 6, "step3_final.png"))

        result = {
            "status": "success",
            "query": TASK_DESCRIPTION,
            "results": results,
            "screenshots": screenshots,
            "completedAt": datetime.now(timezone.utc).isoformat(),
        }

        result_path = f"{OUTPUT_DIR}/result.json"
        with open(result_path, "w") as f:
            json.dump(result, f, indent=2)
        upload_file(result_path, "result.json")

        log_step(7, "complete", status="success", result_count=len(results))
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
