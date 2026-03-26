import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from urllib.parse import unquote
from bs4 import BeautifulSoup
from curl_cffi import requests

class FetchError(Exception):
    pass

@dataclass
class HLJProduct:
    code: str
    name: str
    url: str
    image: str

    on_sale: bool = None
    release_date: str = None
    local_original_price: float = None
    local_sell_price: float = None
    stock_status: str = None
    remaining_stock: int = None
    original_price: int = None
    sell_price: int = None
    order_limit: int = None

class HLJSearch:
    def __init__(self, search_params, session=None):
        self.search_params = search_params
        self.current_page = 1
        self.number_of_pages = 1
        self.currency = "PEN"
        self.session = session

    def do(self):
        if not self.session:
            self.session = requests.Session()
        session = self.session

        search_params = self.search_params.build(self.current_page)
        resp = session.get("https://www.hlj.com/search/", params=search_params, impersonate="chrome")
        if resp.status_code != 200:
            raise FetchError(f"server returned an unexpected response (status code is {resp.status_code})")

        csrf_token = self._get_csrf_token(resp.text)
        products = self._parse_page(resp.text, search_params)
        if not csrf_token or not products:
            return []

        api_headers = {"Host": "www.hlj.com",
                       "Referer": resp.url,
                       "X-Requested-With": "XMLHttpRequest"}

        current_currency = self._get_currency_data()["currencyCode"]
        if current_currency != self.currency:
            change_currency_data = {"currency": self.currency,
                                    "csrfmiddlewaretoken": csrf_token}
            resp = session.post("https://www.hlj.com/common/currency_change/", data=change_currency_data, headers=api_headers,
                                impersonate="chrome")
            if resp.status_code != 200:
                raise FetchError(f"server returned an unexpected response (status code is {resp.status_code})")
            session.cookies.set("hljCurrencyData", resp.json()["hljCurrencyData"], domain="www.hlj.com", path="/")

        prices_params = {"item_codes": ",".join([prod.code for prod in products]),
                         "csrfmiddlewaretoken": csrf_token}
        resp = session.get("https://www.hlj.com/search/livePrice/", params=prices_params, headers=api_headers,
                           impersonate="chrome")
        if resp.status_code != 200:
            raise FetchError(f"server returned an unexpected response (status code is {resp.status_code})")

        self._update_prices(products, resp.json())

        return products

    def next_page(self):
        if self.number_of_pages > self.current_page:
            self.current_page += 1
            return True
        return False

    def _get_csrf_token(self, html):
        return BeautifulSoup(html, "html.parser").find("input", id="csrf_token")["value"]

    def _parse_page(self, html, search_params):
        page = BeautifulSoup(html, "html.parser")

        # The site returns its whole catalogue when a search doesn't have results. Check if this is the case by
        # comparing the checkbox items that correspond to each filter parameter exist.
        check_param = None
        for param in search_params:
            if param[0] not in ("Sort", "Page", "Word"):
                check_param = param
                break
        filter_section = page.find("ul", id=check_param[0])
        if not filter_section or not filter_section.find("input", value=check_param[1]):
            return []

        # This list of page buttons has the following formats:
        # [<] [1] [2] [...] [5] [>]
        # [<] [1] [2] [>]
        # In short, the second to last button is always the last page.
        page_buttons = page.select(".result > span > ul")[0].find_all("li")
        if page_buttons:
            self.number_of_pages = int(page_buttons[-2].text)

        products = []
        for section in page.find_all("div", class_="search-widget-block"):
            code = section.find("a", class_="wishlist-link")["href"].split("/")[-1]
            name = section.find("img")["alt"]
            url = f"https://www.hlj.com{section.find('a')['href']}"
            image = f"https:{section.find('img')['src']}"
            products.append(HLJProduct(code, name, url, image))
        return products

    def _update_prices(self, products, product_info):
        for product in products:
            info = product_info[product.code]
            product.stock_status = info["stockStatusCode"]
            product.original_price = int(info["JPYprice"])
            product.sell_price = info["JPYspecial_price"]
            product.local_original_price = float(info["priceNoFormat"])
            product.local_sell_price = float(info["sellPriceNoFormat"])
            product.on_sale = product.original_price - product.sell_price > 0
            product.release_date = info["release_date"]
            product.order_limit = info["orderQtyLimit"]
            if info["remainingStockStatus"]:
                product.remaining_stock = int(re.match(r"Only ([0-9]+) left in stock", info["remainingStockStatus"]).group(1))

            # HACK: "orderstop" exists and correctly marks some closed pre-orders, but HLJ inconsistently also uses "futurerelease"
            # for items that can no longer be ordered.
            # The only reliable ways to distinguish are parsing the page HTML (slower) or checking if orderQtyLimit == 99999, which
            # empirically indicates closed pre-orders.
            if product.stock_status == "futurerelease" and product.order_limit == 99999:
                product.stock_status = "orderstop"

    def _get_currency_data(self):
        return json.loads(unquote(self.session.cookies["hljCurrencyData"]))

@dataclass
class SearchParams:
    params: list
    query: str = None

    def build(self, page):
        params = []
        if self.query:
            params.append(("Word", self.query))
        params.extend(self.params)
        params.extend([("Sort", "releaseDate desc"), ("Page", page)])
        return params

    def copy(self):
        return SearchParams(self.params.copy(), self.query)

class ProductTracker:
    def __init__(self, categories):
        self.categories = categories

        self.stock = [("StockLevel", "In\xa0Stock"), ("StockLevel", "All Future Release")]
        self.session = requests.Session()
        self.first_run = False

    def process(self):
        saved_data = self._read_saved()
        new_data = {}

        changes = {}
        for category in self.categories:
            products = self._fetch_category(category)
            self._process_changes(products, saved_data, new_data, changes)
        self._save_data(sorted(new_data.values(), key=lambda x: x["code"]))
        return changes.values()

    def _read_saved(self):
        read = {}
        try:
            with open("saved.csv", newline="") as f:
                for row in csv.DictReader(f):
                    read[row["code"]] = {"stock": row["stock"], "price": int(row["price"])}
        except FileNotFoundError:
            self.first_run = True
        return read

    def _save_data(self, data):
        with open("saved.csv.tmp", "w", newline="") as f:
            writer = csv.DictWriter(f, ["code", "stock", "price"])
            writer.writeheader()
            writer.writerows(data)
        os.replace("saved.csv.tmp", "saved.csv")

    def _fetch_category(self, category):
        search_params = category.copy()
        search_params.params.extend(self.stock)

        search = HLJSearch(search_params, self.session)
        # TODO: search.do() can except, add exception handling and logging
        products = search.do()
        while search.next_page():
            time.sleep(3)
            products.extend(search.do())
        return products

    def _process_changes(self, products, saved_products, save_products, changes):
        for product in products:
            if product.stock_status in ("instock", "futurerelease"):
                save_products[product.code] = {
                    "code": product.code,
                    "stock": product.stock_status,
                    "price": product.sell_price,
                    }

            if self.first_run:
                continue

            last_saved = saved_products.get(product.code)
            if product.stock_status == "instock" and (not last_saved or last_saved["stock"] != "instock"):
                changes[f"{product.code}_restock"] = ("restock", product)
            elif product.stock_status == "futurerelease" and not last_saved:
                changes[f"{product.code}_newrelease"] = ("newrelease", product)
            if product.on_sale and (not last_saved or last_saved["price"] > product.sell_price):
                changes[f"{product.code}_onsale"] = ("onsale", product)

class ntfyProductNotifier:
    def __init__(self, topic):
        self.topic = topic

    def notify(self, change):
        title, url, image, text = self._format_change(change)
        requests.post(f"https://ntfy.sh/{self.topic}", data=text, headers={
            "Title": title,
            "Click": url,
            "Attach": image,
            })

    def _format_change(self, change):
        change_type, product = change
        text = f"{product.name} — S/{product.local_sell_price:.2f}"
        match change_type:
            case "restock":
                title = "Back in Stock"
            case "newrelease":
                title = "Pre-Order Open"
            case "onsale":
                title = "On Sale"
                savings = product.local_original_price - product.local_sell_price
                discount_percent = (savings / product.local_original_price) * 100
                text += f" ({discount_percent:.0f}% off)"

        return title, product.url, product.image, text

LINEUP = {
    "EG": SearchParams(params=[
        ("GenreCode2", "Gundam"),
        ("MacroType2", "Other Gundam Kits")
        ], query="entry grade"),
    "HG": SearchParams(params=[
        ("GenreCode2", "Gundam"),
        ("MacroType2", "High Grade Kits")
        ]),
    "RG": SearchParams(params=[
        ("GenreCode2", "Gundam"),
        ("MacroType2", "Real Grade Kits")
        ]),
    "FM": SearchParams(params=[
        ("GenreCode2","Gundam"),
        ("MacroType2", "High Grade Kits"),
        ("MacroType2", "Other Gundam Kits"),
        ], query="full mechanics"),
    "MG": SearchParams(params=[
        ("GenreCode2", "Gundam"),
        ("MacroType2", "Master Grade Kits")
        ]),
    "PG": SearchParams(params=[
        ("GenreCode2", "Gundam"),
        ("MacroType2", "Perfect Grade Kits")
        ]),
    "30MM": SearchParams(params=[
        ("SeriesID2", "30MM / 30Minutes Missions"),
        ("MacroType2", "Injection Kits")
        ]),
    "30MM Extras": SearchParams(params=[
        ("SeriesID2", "30MM / 30Minutes Missions"),
        ("MacroType2", "Decals"),
        ("MacroType2", "Option Kits"),
        ("MacroType2", "Paint Markers")
        ]),
    "30MS": SearchParams(params=[
        ("SeriesID2", "30MS / 30 Minutes Sisters"),
        ("MacroType2", "Injection Kits")
        ]),
    "30MS Extras": SearchParams(params=[
        ("SeriesID2", "30MS / 30 Minutes Sisters"),
        ("MacroType2", "Decals"),
        ("MacroType2", "Option Kits")
        ]),
    "30MF": SearchParams(params=[
        ("SeriesID2", "30MF / 30 Minutes Fantasy"),
        ("MacroType2", "Injection Kits")
        ]),
    "30MF Extras": SearchParams(params=[
        ("SeriesID2", "30MF / 30 Minutes Fantasy"),
        ("MacroType2", "Option Kits")
        ]),
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--ntfy-topic", help="post notifications to this topic")
    args = parser.parse_args()

    ntfy_topic = args.ntfy_topic if args.ntfy_topic else os.environ.get("PRICE_TRACKER_NTFY_TOPIC")
    if not ntfy_topic:
        print(f"{os.path.basename(sys.argv[0])}: error: you need to provide the ntfy topic as a command line argument or as an environment variable")
        exit(1)

    tracker = ProductTracker(LINEUP.values())
    changes = tracker.process()

    notifier = ntfyProductNotifier(ntfy_topic)
    for change in changes:
        notifier.notify(change)
