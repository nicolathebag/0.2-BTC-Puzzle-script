import itertools
import random
import math
import sys
import time
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

# Load the seed word list from a text file
with open("seedwords.txt", "r") as file:
    # keep the same parsing as original (comma-separated)
    seed_words = [word.strip() for word in file.read().split(",") if word.strip()]

# Target Bitcoin address to check against (Legacy address format)
target_address = "1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ"

# Track start time and total combinations tested
start_time = time.time()
total_combinations_tested = 0
last_progress_time = time.time()


def generate_address_from_seed(seed_phrase):
    try:
        # Generate the seed from the mnemonic phrase
        seed_bytes = Bip39SeedGenerator(seed_phrase).Generate()

        # Generate the BIP44 wallet (Legacy address format)
        bip44_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.BITCOIN)
        bip44_acc = bip44_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0)

        # Return the legacy address
        return bip44_acc.PublicKey().ToAddress()

    except Exception:
        # Suppress detailed errors and continue
        return None


# Shuffle the word list for randomness (optional)
random.shuffle(seed_words)

n = len(seed_words)
if n < 12:
    print(f"Need at least 12 seed words; found {n}.")
    sys.exit(1)

# Calculate total number of permutations: P(n, 12) = n! / (n-12)!
try:
    total = math.perm(n, 12)
except AttributeError:
    total = math.factorial(n) // math.factorial(n - 12)


def _format_time(seconds):
    if seconds == float('inf'):
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"

print(f"Total words: {n}")
print(f"Total possible ordered 12-word permutations: {total:,} (~{total:.3e})")

# Try to use tqdm if available for a nicer progress bar
use_tqdm = False
try:
    from tqdm import tqdm
    use_tqdm = True
except Exception:
    use_tqdm = False

# Create the permutations generator
perms = itertools.permutations(seed_words, 12)

# If tqdm available, wrap the iterator with it (gives ETA, rate, etc.)
if use_tqdm:
    iterator = tqdm(perms, total=total, unit="comb")
else:
    iterator = perms

print("Starting the seed phrase checks...")

try:
    if use_tqdm:
        for combo in iterator:
            total_combinations_tested += 1
            seed_phrase = " ".join(combo)
            address = generate_address_from_seed(seed_phrase)
            if address is None:
                continue
            if address == target_address:
                print(f"\nMatch found! Seed Phrase: {seed_phrase} => Address: {address}")
                break
    else:
        bar_width = 40
        for tested, combo in enumerate(iterator, start=1):
            seed_phrase = " ".join(combo)
            address = generate_address_from_seed(seed_phrase)
            if address is None:
                # still count this iteration for progress
                total_combinations_tested = tested
            else:
                total_combinations_tested = tested
            # Check match
            if address == target_address:
                print(f"\nMatch found! Seed Phrase: {seed_phrase} => Address: {address}")
                break

            # Update console progress every 0.5s
            if time.time() - last_progress_time >= 0.5:
                elapsed = time.time() - start_time
                rate = total_combinations_tested / elapsed if elapsed > 0 else 0
                remaining = max(total - total_combinations_tested, 0)
                eta = remaining / rate if rate > 0 else float('inf')
                percent = (total_combinations_tested / total) * 100
                filled = int(bar_width * total_combinations_tested // total)
                bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
                print(f"\r{bar} {percent:6.4f}% {total_combinations_tested:,}/{total:,} {rate:,.2f} comb/s ETA {_format_time(eta)}", end="", flush=True)
                last_progress_time = time.time()

except KeyboardInterrupt:
    print("\nInterrupted by user.")

finally:
    elapsed_total = time.time() - start_time
    rate = total_combinations_tested / elapsed_total if elapsed_total > 0 else 0
    print()
    print(f"Finished. Tested {total_combinations_tested:,} combinations in {elapsed_total:.2f}s ({rate:,.2f} comb/s).")
