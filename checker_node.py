import time
import threading
import urllib3
import requests
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class DynamicConfig:
    def __init__(self, github_url, interval=15):
        self.github_url = github_url
        self.interval = interval
        self._url = self._initial_fetch()
        self._lock = threading.Lock()
        
        t = threading.Thread(target=self._watcher, daemon=True)
        t.start()

    def _initial_fetch(self):
        print("[*] Fetching initial Orchestrator URL from GitHub...", flush=True)
        while True:
            try:
                response = requests.get(self.github_url, timeout=5)
                if response.status_code == 200:
                    url = response.text.strip()
                    if url:
                        print(f"[+] Loaded Orchestrator URL: {url}", flush=True)
                        return url
            except requests.exceptions.RequestException:
                pass
            print("[-] Failed to fetch URL. Retrying in 5 seconds...", flush=True)
            time.sleep(5)

    def _watcher(self):
        while True:
            time.sleep(self.interval)
            try:
                response = requests.get(self.github_url, timeout=5)
                if response.status_code == 200:
                    new_url = response.text.strip()
                    if new_url:
                        with self._lock:
                            if new_url != self._url:
                                print(f"\n[+] Orchestrator URL updated dynamically to: {new_url}", flush=True)
                                self._url = new_url
            except requests.exceptions.RequestException:
                pass

    @property
    def url(self):
        with self._lock:
            return self._url

CONFIG = DynamicConfig("https://raw.githubusercontent.com/iceyfromdcrd/prlchecker/refs/heads/main/orchestrator.txt")
RPC_URL = "https://localhost:44207"
RPC_USER = "rpcuser"
RPC_PASS = "rpcpass"

def check_balance_via_rpc(address):
    payload = {
        "jsonrpc": "1.0",
        "id": "python-checker",
        "method": "getreceivedbyaddress",
        "params": [address, 0]
    }
    try:
        response = requests.post(
            RPC_URL, json=payload, auth=(RPC_USER, RPC_PASS), verify=False, timeout=3
        )
        if response.status_code == 200:
            result = response.json()
            if "error" in result and result["error"] is not None:
                return 0.0
            return float(result.get("result", 0))
    except requests.exceptions.RequestException:
        pass
    return 0.0

def process_wallet():
    try:
        res = requests.get(f"{CONFIG.url}/get_wallet", timeout=2)
        if res.status_code == 200:
            data = res.json()
            wallet = data.get("wallet")
            if not wallet:
                return
            
            mnemonic = wallet["mnemonic"]
            address = wallet["address"]
            
            balance = check_balance_via_rpc(address)
            if balance > 0:
                requests.post(f"{CONFIG.url}/report_hit", json={
                    "mnemonic": mnemonic,
                    "address": address,
                    "balance": balance
                })
        else:
            time.sleep(0.5)
    except requests.exceptions.RequestException:
        time.sleep(1)

if __name__ == "__main__":
    MAX_WORKERS = 16
    print(f"Starting Checker Node with {MAX_WORKERS} workers...", flush=True)
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            futures = [executor.submit(process_wallet) for _ in range(MAX_WORKERS)]
            for f in futures:
                f.result()