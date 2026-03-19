# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google search engine using a browser backend (Camoufox) to bypass
JavaScript requirements and anti-bot detection.

This engine proxies Google search requests through a Camoufox HTTP service
that renders the page in a real anti-detect browser, then parses the
rendered HTML using the same logic as the standard Google engine.

Configuration in settings.yml::

    - name: google (browser)
      engine: google_browser
      shortcut: gb
      camoufox_url: http://camoufox-svc.searxng:8080
      timeout: 30.0
"""

import re
import typing as t
from urllib.parse import urlencode

from lxml import html

from searx.result_types import EngineResults
from searx.utils import (
    eval_xpath,
    eval_xpath_getindex,
    eval_xpath_list,
    extract_text,
)

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

about = {
    "website": "https://www.google.com",
    "wikidata_id": "Q9366",
    "official_api_documentation": "https://developers.google.com/custom-search/",
    "use_official_api": False,
    "require_api_key": False,
    "results": "HTML",
}

categories = ["general", "web"]
paging = True
max_page = 10
time_range_support = True
safesearch = True

# Camoufox service URL — configurable via settings.yml
camoufox_url = "http://camoufox-svc.searxng:8080"

time_range_dict = {"day": "d", "week": "w", "month": "m", "year": "y"}
filter_mapping = {0: "off", 1: "medium", 2: "high"}

suggestion_xpath = '//div[contains(@class, "ouy7Mc")]//a'

# Regex to extract image URLs from Google's JS
RE_DATA_IMAGE = re.compile(r'"((?:dimg|pimg|tsuid)_[^"]*)":"((?:https?:)?//[^"]*)')

import logging

logger = logging.getLogger(__name__)


def request(query: str, params: "OnlineParams") -> None:
    """Build the Google search URL and redirect the request to the Camoufox service."""

    start = (params["pageno"] - 1) * 10

    # Build a standard Google search URL
    google_url = (
        "https://www.google.com/search?"
        + urlencode(
            {
                "q": query,
                "hl": "en",
                "start": start,
            }
        )
    )

    if params.get("time_range") and params["time_range"] in time_range_dict:
        google_url += "&" + urlencode({"tbs": "qdr:" + time_range_dict[params["time_range"]]})
    if params.get("safesearch"):
        google_url += "&" + urlencode({"safe": filter_mapping.get(params["safesearch"], "off")})

    # Instead of fetching Google directly, POST to our Camoufox service
    params["url"] = f"{camoufox_url}/scrape"
    params["method"] = "POST"
    params["json"] = {
        "url": google_url,
        "wait_after_load": 2,
        "timeout": 30000,
        "wait_for_selector": "a h3",
        "wait_until": "domcontentloaded",
    }
    # Remove any Google-specific headers/cookies that would confuse the proxy
    params["headers"] = {"Content-Type": "application/json"}
    params["cookies"] = {}


def response(resp: "SXNG_Response"):
    """Parse the response from Camoufox service containing rendered Google HTML."""

    import json

    results = EngineResults()

    try:
        data = json.loads(resp.text)
    except (json.JSONDecodeError, ValueError):
        logger.error("Failed to parse Camoufox response as JSON")
        return results

    page_html = data.get("html", "")
    if not page_html:
        logger.warning("Empty HTML from Camoufox")
        return results

    # Check for Google sorry/CAPTCHA page
    if "sorry" in data.get("url", "").lower() or "captcha" in page_html.lower():
        from searx.exceptions import SearxEngineCaptchaException
        raise SearxEngineCaptchaException()

    # Parse image map from JS
    data_image_map = {}
    for img_id, image_url in RE_DATA_IMAGE.findall(page_html):
        data_image_map[img_id] = image_url.encode('utf-8').decode("unicode-escape")

    # Parse the HTML DOM
    dom = html.fromstring(page_html)

    # Parse results using Google's result structure
    for result in eval_xpath_list(dom, './/div[contains(@class, "MjjYud")]'):
        try:
            title_tag = eval_xpath_getindex(result, './/div[contains(@role, "link")]', 0, default=None)
            if title_tag is None:
                continue

            title = extract_text(title_tag)

            # Get URL
            url_tag = eval_xpath_getindex(result, './/a[@jsname]/@href', 0, default=None)
            if url_tag is None:
                url_tag = eval_xpath_getindex(result, './/a/@href', 0, default=None)
            if url_tag is None or not url_tag.startswith("http"):
                continue

            url = url_tag

            # Get snippet/content
            content_tag = eval_xpath_getindex(
                result,
                './/div[contains(@class, "VwiC3b")]',
                0,
                default=None,
            )
            content = extract_text(content_tag) if content_tag is not None else ""

            # Get thumbnail if available
            thumbnail = None
            img_tag = eval_xpath_getindex(result, './/img/@id', 0, default=None)
            if img_tag and img_tag in data_image_map:
                thumbnail = data_image_map[img_tag]

            results.append(
                {
                    "url": url,
                    "title": title,
                    "content": content,
                    "thumbnail": thumbnail,
                }
            )

        except Exception as e:
            logger.debug("Error parsing result: %s", e)
            continue

    # Parse suggestions
    for suggestion in eval_xpath_list(dom, suggestion_xpath):
        results.append({"suggestion": extract_text(suggestion)})

    logger.info("google_browser: parsed %d results", len(results))
    return results
