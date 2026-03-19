# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google News engine using a browser backend (Camoufox) to bypass
JavaScript requirements and anti-bot detection.

This engine proxies Google News requests through a Camoufox HTTP service
that renders the page in a real anti-detect browser.

Configuration in settings.yml::

    - name: google news (browser)
      engine: google_news_browser
      shortcut: gnb
      camoufox_url: http://camoufox-svc.searxng:8080
      timeout: 30.0
"""

import base64
import json
import logging
import typing as t
from urllib.parse import urlencode

from lxml import html

from searx.utils import (
    eval_xpath,
    eval_xpath_getindex,
    eval_xpath_list,
    extract_text,
)

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response

logger = logging.getLogger(__name__)

about = {
    "website": "https://news.google.com",
    "wikidata_id": "Q12020",
    "official_api_documentation": "https://developers.google.com/custom-search",
    "use_official_api": False,
    "require_api_key": False,
    "results": "HTML",
}

categories = ["news"]
paging = False
time_range_support = False
safesearch = False

# Camoufox service URL — configurable via settings.yml
camoufox_url = "http://camoufox-svc.searxng:8080"


def request(query, params):
    """Build Google News URL and redirect to Camoufox service."""

    google_news_url = (
        "https://news.google.com/search?"
        + urlencode(
            {
                "q": query,
                "hl": "en",
                "gl": "US",
                "ceid": "US:en",
            }
        )
    )

    # POST to Camoufox service
    params["url"] = f"{camoufox_url}/scrape"
    params["method"] = "POST"
    params["content"] = json.dumps({
        "url": google_news_url,
        "wait_after_load": 3,
        "timeout": 20000,
    }).encode()
    params["headers"]["Content-Type"] = "application/json"
    params["cookies"] = {}
    return params


def response(resp):
    """Parse Google News results from rendered HTML."""

    results = []

    try:
        data = json.loads(resp.text)
    except (json.JSONDecodeError, ValueError):
        logger.error("Failed to parse Camoufox response as JSON")
        return results

    page_html = data.get("html", "")
    if not page_html:
        logger.warning("Empty HTML from Camoufox")
        return results

    if "sorry" in data.get("url", "").lower():
        from searx.exceptions import SearxEngineCaptchaException
        raise SearxEngineCaptchaException()

    dom = html.fromstring(page_html)

    # Google News uses <article> elements for results
    for result in eval_xpath_list(dom, '//article'):
        try:
            # Extract the link
            link = eval_xpath_getindex(result, './/a/@href', 0, default=None)
            if link is None:
                continue

            # Google News uses relative links like ./articles/...
            if link.startswith('./'):
                link = 'https://news.google.com' + link[1:]

            # Try to extract the actual URL from Google's redirect
            if '/articles/' in link:
                # Google News encodes the real URL in the path
                try:
                    path_part = link.split('/articles/')[-1].split('?')[0]
                    decoded = base64.urlsafe_b64decode(path_part + '====')
                    actual_url = decoded[decoded.index(b'http'):].split(b'\xd2')[0].decode()
                    link = actual_url
                except Exception:
                    pass  # Keep the Google News link if decoding fails

            # Extract title
            title = extract_text(eval_xpath(result, './/a[1]'))
            if not title:
                title = extract_text(eval_xpath(result, './/h3'))
            if not title:
                title = extract_text(eval_xpath(result, './/h4'))
            if not title:
                continue

            # Extract source and time
            source = extract_text(eval_xpath(result, './/div[@data-n-tid]'))
            pub_time = extract_text(eval_xpath(result, './/time'))

            content = ' / '.join([x for x in [source, pub_time] if x])

            # Extract thumbnail
            thumbnail = eval_xpath_getindex(result, './/img/@src', 0, default=None)

            results.append({
                'url': link,
                'title': title,
                'content': content,
                'thumbnail': thumbnail,
            })

        except Exception as e:
            logger.debug("Error parsing news result: %s", e)
            continue

    logger.info("google_news_browser: parsed %d results", len(results))
    return results
