import time
import random
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from fake_useragent import UserAgent


class GoogleFileSearch:
    def __init__(self, keyword, filetype="pdf", proxies=None):
        self.keyword = keyword
        self.filetype = filetype
        self.proxies = proxies or []
        self.proxy_index = 0
        self.all_urls = []
        self.driver = self._init_driver()

    def _init_driver(self):
        options = uc.ChromeOptions()
        ua = UserAgent()
        options.add_argument(f"--user-agent={ua.random}")
        if self.proxies:
            current_proxy = self.proxies[self.proxy_index % len(self.proxies)]
            print(f"🔌 Using proxy: {current_proxy}")
            options.add_argument(f'--proxy-server={current_proxy}')
            self.proxy_index += 1
        return uc.Chrome(options=options)

    def _search_google(self):
        self.driver.get("https://www.google.com")
        try:
            accept_btn = self.driver.find_element(By.ID, "L2AGLb")
            accept_btn.click()
        except:
            pass
        time.sleep(2)

        search_box = self.driver.find_element(By.ID, "gLFyf")
        search_box.send_keys(f"{self.keyword} filetype:{self.filetype}")
        search_box.submit()

        time.sleep(2)
        self.driver.get(self.driver.current_url + "&num=100")

    def _get_pdf_links(self):
        time.sleep(random.uniform(2, 3))
        links = self.driver.find_elements(By.XPATH, '//a[contains(@href, ".pdf")]')
        pdf_urls = [link.get_attribute("href") for link in links if link.get_attribute("href")]
        self.all_urls.extend(pdf_urls)

    def _next_page(self):
        try:
            next_element = self.driver.find_element(By.ID, "pnnext")
            href = next_element.get_attribute("href")
            if href:
                self.driver.get(href)
                return True
        except NoSuchElementException:
            return False
        return False

    def run(self):
        self._search_google()
        while True:
            self._get_pdf_links()
            if not self._next_page():
                break
        self.driver.quit()
        return list(set(self.all_urls))  # Remove duplicates


if __name__ == "__main__":
    proxies = [
        # "socks5://127.0.0.1:9050",
        # "http://123.45.67.89:8080",
        # "http://98.76.54.32:3128",
        # add more proxies here
    ]
    crawler = GoogleFileSearch("fishing forecast", proxies=proxies)
    results = crawler.run()
    print("\nFound PDF URLs:")
    for url in results:
        print(url)
