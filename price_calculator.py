from curl_cffi import requests
from bs4 import BeautifulSoup
from tabulate import tabulate, SEPARATING_LINE

class FetchError(Exception):
    pass

class Product:
    def __init__(self, name, code):
        self.name = name
        self.code = code.upper()
        self.original_price = None

    def calculate_final_price(self, shipment_per_product, intended_markup):
        partial_price = self.original_price + shipment_per_product
        self.markup = partial_price * intended_markup
        self.final_price = partial_price + self.markup

def get_prices(products):
    session = requests.Session()

    resp = session.get("https://www.hlj.com/search/", params={"Word": "nendoroid"}, impersonate="chrome")
    if resp.status_code != 200:
        raise FetchError(f"server returned an unexpected response (status code is {resp.status_code})")

    csrf_token = BeautifulSoup(resp.text, "html.parser").find("input", id="csrf_token")["value"]

    api_headers = {"Host": "www.hlj.com",
                   "Referer": resp.url,
                   "X-Requested-With": "XMLHttpRequest"}

    change_currency_data = {"currency": "JPY", "csrfmiddlewaretoken": csrf_token}
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

    return resp.json()

def format_price(price):
    return f"S/{price * 0.029:,.2f} – S/{price * 0.03:,.2f}"

def calculate_prices(products, shipping_cost, intended_markup):
    total_original_value = 0
    total_final_value = 0
    total_markup = 0
    prices = get_prices(products)
    table = []
    for product in products:
        product.original_price = float(prices[product.code]["sellPriceNoFormat"])
        product.calculate_final_price(shipping_cost / len(products), intended_markup)

        total_original_value += product.original_price
        total_final_value += product.final_price
        total_markup += product.markup

        table.append([product.name,
                      format_price(product.original_price),
                      format_price(product.final_price),
                      format_price(product.markup)])

    table.append(SEPARATING_LINE)
    table.append(["Total",
                  format_price(total_original_value),
                  format_price(total_final_value),
                  format_price(total_markup)])
    table.append(["Shipping", format_price(shipping_cost)])
    table.append(["Total + Shipping", format_price(total_original_value + shipping_cost)])

    print(tabulate(table, headers=["Product", "Original Price", "Sell Price", "Markup"]))


#calculate_prices([Product("HGAC Wing Gundam", "ban983663"), Product("HGUC Revive RX-178 Gundam Mk-II", "bann01311"), Product("HGFC G Gundam", "banh582652-up"), Product("HGUC Zeta Gundam", "banh556110-up"), Product("HGUC RX-93 Nu Gundam", "banh579539-up"), Product("HGUC Hi-Nu Gundam", "banh595706-up"), Product("RG Wing Gundam Zero", "banh688743"), Product("30MM Spinatia Assassin Type", "bans61923"), Product("Entry Grade Strike Gundam", "bans62168")], 6440, 0.4)
calculate_prices([Product("HGAC Wing Gundam", "ban983663"), Product("HGUC Revive RX-178 Gundam Mk-II", "bann01311"), Product("HGFC Aile Strike Gundam", "banh587794-up"), Product("HGUC Victory Gundam", "banh630384-up"), Product("HG Gundam Aerial Rebuild", "bans65096"), Product("HG Zaku II Type C / Type C-5", "bann16745"), Product("MSG: The Origin HG Zaku I (Char Aznable)", "bann16379")], 5530, 0.4)
#calculate_prices([Product("30MM Portanova (Green)", "banh577955-up"), Product("30MM Exa Vehicle (Horse Mecha Ver.) [Dark Gray]", "banh662996"), Product("30MM Baskyrotto (Gray)", "banh663108"), Product("30MM Baskyrotto (Brown)", "banh663795"), Product("30MM Gardonova (Green)", "banh666857"), Product("30MM Option Parts Set 16", "banh666864"), Product("30MM Exa Vehicle (Horse Mecha Ver.) [White]", "banh674227"), Product("30MM Option Parts Set 18", "banh683359"), Product("30MM Customize Weapons (Plasma Weapons)", "banh685919"), Product("30MM Egritte 02", "banh688705"), Product("30MM Levinix (Type-B)", "banh691842"), Product("30MM Tecprot 02", "banh720221"), Product("30MM Cielnova (Green)", "bans60252"), Product("30MM Exa Vehicle (Dog Mecha Ver.)", "bans61995"), Product("30MM Exa Vehicle (Tiltroto Ver.)", "bans65444")], 7350, 0.4)
#calculate_prices([Product("30MM Portanova (Green)", "banh577955-up"), Product("30MM Exa Vehicle (Horse Mecha Ver.) [Dark Gray]", "banh662996"), Product("30MM Gardonova (Green)", "banh666857"), Product("30MM Customize Weapons (Plasma Weapons)", "banh685919"), Product("30MM Tecprot 01", "banh720054"), Product("30MM Tecprot 02", "banh720221")], 4620, 0.4)
