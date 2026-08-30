import time
import threading
import uuid
import urllib3
import requests
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class DynamicConfig:
    def __init__(self, github_url, interval=30):
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
MAX_WORKERS = 16  # Local workers on this machine
CHECKER_ID = str(uuid.uuid4())

def send_heartbeat():
    """Background thread to register this checker node and its worker count to the orchestrator."""
    while True:
        try:
            requests.post(f"{CONFIG.url}/heartbeat", json={
                "checker_id": CHECKER_ID,
                "workers": MAX_WORKERS
            }, timeout=3)
        except requests.exceptions.RequestException:
            pass
        time.sleep(15) # Heartbeat every 15 seconds

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

def process_single_wallet(wallet):
    mnemonic = wallet["mnemonic"]
    address = wallet["address"]
    
    balance = check_balance_via_rpc(address)
    if balance > 0:
        try:
            requests.post(f"{CONFIG.url}/report_hit", json={
                "mnemonic": mnemonic,
                "address": address,
                "balance": balance
            }, timeout=3)
        except requests.exceptions.RequestException:
            pass

if __name__ == "__main__":
    print(f"Starting Checker Node (ID: {CHECKER_ID[:8]}...) with {MAX_WORKERS} workers...", flush=True)
    
    # Start background heartbeat daemon
    hb_thread = threading.Thread(target=send_heartbeat, daemon=True)
    hb_thread.start()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            try:
                # Fetch a batch matching worker capacity instead of hammering endpoint per wallet
                res = requests.get(f"{CONFIG.url}/get_wallets?count={MAX_WORKERS}", timeout=3)
                if res.status_code == 200:
                    wallets = res.json().get("wallets", [])
                    if not wallets:
                        time.sleep(0.5)
                        continue
                    
                    # Distribute batch across local thread pool workers cleanly
                    futures = [executor.submit(process_single_wallet, w) for w in wallets]
                    for future in futures:
                        future.result()
                else:
                    time.sleep(0.5)
            except requests.exceptions.RequestException:
                time.sleep(1)
