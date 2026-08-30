import hashlib
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from mnemonic import Mnemonic
import ecdsa
import requests

class DynamicConfig:
    def __init__(self, github_url, interval=15):
        self.github_url = github_url
        self.interval = interval
        self._url = self._initial_fetch()
        self._lock = threading.Lock()
        
        # Start background thread to watch for changes
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

# Initialize dynamic configuration
CONFIG = DynamicConfig("https://raw.githubusercontent.com/iceyfromdcrd/prlchecker/refs/heads/main/orchestrator.txt")

BECH32M_CONST = 0x2bc830a3
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

def convertbits(data, frombits, tobits, pad=True):
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret

def bech32_polymod(values):
    GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk

def bech32_hrp_expand(s):
    return [ord(x) >> 5 for x in s] + [0] + [ord(x) & 31 for x in s]

def bech32m_create_checksum(hrp, data):
    values = bech32_hrp_expand(hrp) + data + [0, 0, 0, 0, 0, 0]
    polymod = bech32_polymod(values) ^ BECH32M_CONST
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]

def bech32m_encode(hrp, data):
    combined = data + bech32m_create_checksum(hrp, data)
    return hrp + '1' + ''.join([CHARSET[d] for d in combined])

def generate_pearl_wallet():
    mnemo = Mnemonic("english")
    mnemonic_phrase = mnemo.generate(strength=128)
    seed = mnemo.to_seed(mnemonic_phrase)
    
    priv_key_bytes = hashlib.sha256(seed).digest()
    sk = ecdsa.SigningKey.from_string(priv_key_bytes, curve=ecdsa.SECP256k1)
    vk = sk.get_verifying_key()
    
    pub_key_bytes = vk.to_string()
    x_coord = pub_key_bytes[:32]
    
    data_5bit = convertbits(x_coord, 8, 5)
    bech32_data = [1] + data_5bit 
    pearl_address = bech32m_encode("prl", bech32_data)
    
    return {"mnemonic": mnemonic_phrase, "address": pearl_address}

def worker_batch():
    batch = [generate_pearl_wallet() for _ in range(50)]
    try:
        response = requests.post(f"{CONFIG.url}/submit", json={"wallets": batch}, timeout=3)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

if __name__ == "__main__":
    MAX_WORKERS = 32
    print(f"Starting Generator Node with {MAX_WORKERS} workers...", flush=True)
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            futures = [executor.submit(worker_batch) for _ in range(MAX_WORKERS)]
            time.sleep(0.1)