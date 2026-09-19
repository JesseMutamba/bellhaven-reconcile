from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urldefrag
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser
from .network import tls_context

USER_AGENT = "BellhavenReconcile/0.1"


class ScrapeError(Exception):
    pass


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        current, target = urlsplit(req.full_url), urlsplit(newurl)
        if (current.scheme, current.netloc) != (target.scheme, target.netloc):
            raise ScrapeError("Cross-origin redirect blocked; configure the canonical website URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class ScrapeResult:
    facilities: list
    pages: list
    absence_allowed: bool


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.blocks, self.cards = [], [], []
        self.script = None
        self.heading, self.label, self.value = [], None, None
        self.in_heading = False
        self.details = {}
        self.badges, self.badge = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h1":
            self.in_heading = True
        if tag == "dt":
            self.label = []
        if tag == "dd":
            self.value = []
            self.badges = []
        if tag == "br" and self.value is not None:
            self.value.append("\n")
        if tag == "span" and self.value is not None and "badge" in attrs.get("class", "").split():
            self.badge = []
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.script = []
        if attrs.get("data-facility-id"):
            self.cards.append({"identifier": attrs["data-facility-id"], "name": attrs.get("data-name"),
                               "address": {"streetAddress": attrs.get("data-address"),
                                           "addressLocality": attrs.get("data-city"),
                                           "addressRegion": attrs.get("data-state"),
                                           "postalCode": attrs.get("data-zip")},
                               "availableService": attrs.get("data-care", "").split("|")})

    def handle_data(self, data):
        if self.in_heading:
            self.heading.append(data)
        if self.label is not None:
            self.label.append(data)
        if self.value is not None:
            self.value.append(data)
        if self.badge is not None:
            self.badge.append(data)
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_heading = False
        if tag == "dt" and self.label is not None:
            self.current_label = "".join(self.label).strip().lower()
            self.label = None
        if tag == "span" and self.badge is not None:
            self.badges.append("".join(self.badge).strip())
            self.badge = None
        if tag == "dd" and self.value is not None:
            self.details[getattr(self, "current_label", "")] = ("".join(self.value).strip(), self.badges)
            self.value = None
        if tag == "script" and self.script is not None:
            self.blocks.append("".join(self.script))
            self.script = None


def walk(value):
    if isinstance(value, dict):
        if value.get("name") and isinstance(value.get("address"), dict):
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def parse_page(content, url):
    parser = PageParser()
    parser.feed(content)
    entities = list(parser.cards)
    if "address" in parser.details:
        raw_address = parser.details["address"][0]
        parts = re.match(r"^(.+)\n\s*(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", raw_address)
        if not parts or not parser.heading:
            raise ScrapeError(f"Unrecognized Bellhaven address layout on {url}")
        street, city, state, zip_code = parts.groups()
        entities.append({"name": "".join(parser.heading).strip(),
                         "telephone": parser.details.get("phone", (None, None))[0],
                         "identifier": urlsplit(url).path.rstrip("/").split("/")[-1],
                         "address": {"streetAddress": street, "addressLocality": city,
                                     "addressRegion": state, "postalCode": zip_code},
                         "availableService": parser.details.get("care offerings", (None, None))[1]})
    for block in parser.blocks:
        try:
            entities.extend(walk(json.loads(block)))
        except ValueError as exc:
            raise ScrapeError(f"Invalid location JSON-LD on {url}") from exc
    facilities = []
    for entity in entities:
        address = entity["address"]
        fields = {"name": entity["name"], "address": address.get("streetAddress"),
                  "city": address.get("addressLocality"), "state": address.get("addressRegion"),
                  "zip": address.get("postalCode")}
        if not all(isinstance(v, str) and v.strip() for v in fields.values()):
            raise ScrapeError(f"Incomplete structured facility address on {url}")
        services = entity.get("availableService")
        if services is not None:
            if not isinstance(services, list):
                services = [services]
            services = sorted({str(s.get("name", "") if isinstance(s, dict) else s).strip()
                               for s in services if s})
            services = [s for s in services if s]
        identifier = entity.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        if identifier is not None and not isinstance(identifier, (str, int)):
            raise ScrapeError(f"Unsupported facility identifier on {url}")
        fields.update(external_id=str(identifier) if identifier is not None else None,
                      care_offerings=services, source_url=url)
        if isinstance(entity.get("telephone"), str):
            fields["phone"] = entity["telephone"]
        facilities.append(fields)
    return facilities, parser.links


def collect(pages, allow_absence=False):
    from .matching import address_key, normal
    unique = {}
    for page in pages:
        page["sha256"] = hashlib.sha256(page["content"].encode()).hexdigest()
        facilities, _ = parse_page(page["content"], page["url"])
        for facility in facilities:
            key = facility["external_id"] or ":".join([address_key(facility), normal(facility["name"])])
            facility["key"] = key
            facility["source_sha256"] = page["sha256"]
            if key in unique:
                old = unique[key]
                if address_key(old) != address_key(facility) or normal(old["name"]) != normal(facility["name"]):
                    raise ScrapeError("Conflicting website records use the same facility identifier")
                if old["care_offerings"] is not None and facility["care_offerings"] is not None and old["care_offerings"] != facility["care_offerings"]:
                    raise ScrapeError("Website records disagree on care offerings")
                if facility["care_offerings"] is None:
                    continue
            unique[key] = facility
    if not unique:
        raise ScrapeError("No structured facilities found; refusing to infer missing accounts")
    return ScrapeResult(list(unique.values()), pages, allow_absence)


def scrape_demo(root):
    content = (Path(root) / "fixtures/locations.html").read_text()
    return collect([{"url": "https://bellhaven.example/locations", "content": content}], True)


def fetch(url):
    opener = build_opener(SameOriginRedirect(), HTTPSHandler(context=tls_context()))
    with opener.open(Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as response:
        if (urlsplit(response.geturl()).scheme, urlsplit(response.geturl()).netloc) != (urlsplit(url).scheme, urlsplit(url).netloc):
            raise ScrapeError("Cross-origin redirect encountered; configure the canonical website URL")
        content = response.read(5_000_001)
        if len(content) > 5_000_000:
            raise ScrapeError("Website page exceeded the size limit")
        return content.decode("utf-8")


def scrape_website(config):
    start = config.website_url
    origin = urlsplit(start)
    pattern = re.compile(config.path_pattern)
    robots_url = f"{origin.scheme}://{origin.netloc}/robots.txt"
    robots = RobotFileParser()
    try:
        robots.parse(fetch(robots_url).splitlines())
    except HTTPError as exc:
        if exc.code != 404:
            raise ScrapeError("Could not verify robots.txt; inspect website access before crawling") from exc
        robots.parse([])
    except Exception as exc:
        raise ScrapeError("Could not verify robots.txt; inspect website access before crawling") from exc
    queue, visited, pages = [start], set(), []
    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        if len(visited) >= config.max_pages:
            raise ScrapeError("Crawl limit reached; increase SCRAPE_MAX_PAGES or narrow the path pattern")
        if not robots.can_fetch(USER_AGENT, url):
            raise ScrapeError(f"Crawling is disallowed by robots.txt: {url}")
        visited.add(url)
        try:
            content = fetch(url)
            _, links = parse_page(content, url)
        except Exception as exc:
            raise ScrapeError(f"Unable to read a complete facility page: {url}") from exc
        pages.append({"url": url, "content": content})
        for href in links:
            link = urldefrag(urljoin(url, href))[0]
            target = urlsplit(link)
            if (target.scheme, target.netloc) == (origin.scheme, origin.netloc) and pattern.search(target.path + ("?" if target.query else "")):
                if link not in visited and link not in queue:
                    queue.append(link)
    result = collect(pages, config.allow_absence)
    if len(result.facilities) < config.expected_min:
        raise ScrapeError("Too few locations found; refusing an incomplete source snapshot")
    return result
