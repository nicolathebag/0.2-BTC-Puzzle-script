#!/usr/bin/env python3
import argparse
import itertools
import json
import math
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime
from functools import partial
from typing import Iterable, Tuple, Set

# bip_utils imports
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

# Try to import the BIP39 validator (different bip_utils versions expose slightly different APIs)
try:
    from bip_utils import Bip39MnemonicValidator
    def is_valid_mnemonic(mnemonic: str) -> bool:
        try:
            validator = Bip39MnemonicValidator(mnemonic)
            try:
                return bool(validator.Validate())
            except Exception:
                try:
                    return bool(Bip39MnemonicValidator.IsValid(mnemonic))
                except Exception:
                    return False
        except TypeError:
            try:
                return bool(Bip39MnemonicValidator.IsValid(mnemonic))
            except Exception:
                return False
except Exception:
    # If bip_utils doesn't expose a validator, fall back to allowing all and warn the user
    def is_valid_mnemonic(mnemonic: str) -> bool:
        # We can't validate without Bip39MnemonicValidator; allow and warn
        return True

# Generate address from seed phrase (BIP39 -> BIP44 legacy address used in original script)
def generate_address_from_seed(seed_phrase: str) -> str:
    try:
        seed_bytes = Bip39SeedGenerator(seed_phrase).Generate()
        bip44_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.BITCOIN)
        bip44_acc = bip44_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0)
        return bip44_acc.PublicKey().ToAddress()
    except Exception:
        return None

# Worker used by multiprocessing pool. Receives a tuple (index, combo) where combo is a tuple of words
def worker_process(target_address: str, item: Tuple[int, Tuple[str, ...]]) -> Tuple[int, str, str]:
    index, combo = item
    seed_phrase = " ".join(combo)

    # Validate BIP39 mnemonic (checksum) before generating seed
    if not is_valid_mnemonic(seed_phrase):
        return (index, seed_phrase, None)

    address = generate_address_from_seed(seed_phrase)
    return (index, seed_phrase, address)

def _format_time(seconds: float) -> str:
    if seconds == float('inf'):
        return "unknown"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m:02d}m{s:02d}s"
    return f"{s}s"

def save_checkpoint(checkpoint_file: str, state: dict):
    tmp = checkpoint_file + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, checkpoint_file)
    except Exception as e:
        print(f"Warning: failed to write checkpoint: {e}")

def load_checkpoint(checkpoint_file: str) -> dict:
    if not checkpoint_file or not os.path.exists(checkpoint_file):
        return {}
    try:
        with open(checkpoint_file, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def iter_with_start(generator: Iterable, start_index: int):
    from itertools import islice
    return islice(generator, start_index, None)

def load_bip39_wordlist_from_file(path: str) -> Set[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    words = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            w = line.strip()
            if w:
                words.add(w)
    return words

def try_load_bip39_from_bip_utils() -> Set[str]:
    # Best-effort attempt to load a built-in BIP39 English wordlist from bip_utils.
    try:
        # different bip_utils versions expose different modules; try common ones
        from bip_utils import Bip39WordsList
        try:
            # Bip39WordsList() may expose a WordsList() or get_wordlist method
            wl = Bip39WordsList()
            return set(wl.GetWords()) if hasattr(wl, "GetWords") else set(wl.WordsList())
        except Exception:
            # fallback: try to import english list directly
            try:
                from bip_utils import Bip39WordsNum  # not ideal; just to detect availability
                # If we get here but can't extract words, fail to be safe
            except Exception:
                pass
    except Exception:
        pass
    # Can't auto-load
    return set()

def main():
    parser = argparse.ArgumentParser(description="Check seed combinations/permutations with progress, validation, checkpointing and multiprocessing")

    parser.add_argument("--words-file", default="seedwords.txt", help="CSV or comma-separated file containing candidate seed words (default: seedwords.txt)")
    parser.add_argument("--target-address", default="1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ", help="Target Bitcoin address to find")
    parser.add_argument("--mode", choices=["permutations", "combinations"], default="permutations", help="Use ordered permutations or unordered combinations of k words (default: permutations)")
    parser.add_argument("--k", type=int, default=12, help="Number of words in the mnemonic phrase to try (default: 12)")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes to use (default: 1; set >1 to enable multiprocessing)")
    parser.add_argument("--checkpoint", default="checkpoint.json", help="Checkpoint file path")
    parser.add_argument("--checkpoint-interval", type=int, default=10000, help="Save checkpoint every N tested combos (default: 10000)")
    parser.add_argument("--checkpoint-interval-seconds", type=int, default=300, help="Save checkpoint at least every M seconds (default: 300s)")
    parser.add_argument("--matches-csv", default="matches.csv", help="CSV file to append matches to")
    parser.add_argument("--log-file", default="run.log", help="Log file to write run stats")
    parser.add_argument("--use-tqdm", action="store_true", help="Use tqdm for progress bar if available")
    parser.add_argument("--start-index", type=int, default=None, help="Start index (overrides checkpoint) to resume from)")
    parser.add_argument("--bip39-wordlist-file", default=None, help="Path to BIP39 wordlist file (one word per line). If not provided, script will try to auto-load from bip_utils; if that fails, the filter is skipped.")

    args = parser.parse_args()

    if not os.path.exists(args.words_file):
        print(f"Words file not found: {args.words_file}")
        sys.exit(1)

    with open(args.words_file, "r", encoding="utf-8") as f:
        data = f.read().strip()
    if "," in data:
        seed_words = [w.strip() for w in data.split(",") if w.strip()]
    else:
        seed_words = [w.strip() for w in data.split() if w.strip()]

    n = len(seed_words)
    k = args.k
    if n < k:
        print(f"Need at least {k} seed words; found {n}.")
        sys.exit(1)

    # Load BIP39 wordlist (if requested/available) and filter seed_words against it
    bip39_words = set()
    if args.bip39_wordlist_file:
        try:
            bip39_words = load_bip39_wordlist_from_file(args.bip39_wordlist_file)
            print(f"Loaded BIP39 wordlist from {args.bip39_wordlist_file} ({len(bip39_words)} words).")
        except Exception as e:
            print(f"Failed to load BIP39 wordlist from file: {e}. Continuing without filtering.")
            bip39_words = set()
    else:
        # Try to auto-load from bip_utils
        auto = try_load_bip39_from_bip_utils()
        if auto:
            bip39_words = auto
            print(f"Auto-loaded BIP39 wordlist from bip_utils ({len(bip39_words)} words).")
        else:
            print("No BIP39 wordlist provided and auto-load failed. If you want to filter words to the official BIP39 list, pass --bip39-wordlist-file <path>.")
            bip39_words = set()

    if bip39_words:
        original_n = len(seed_words)
        seed_words = [w for w in seed_words if w in bip39_words]
        filtered_n = len(seed_words)
        print(f"Filtered provided words against BIP39 wordlist: {original_n} -> {filtered_n}.")
        if filtered_n < k:
            print(f"After filtering you have only {filtered_n} candidate words which is less than k={k}. Exiting.")
            sys.exit(1)

    try:
        if args.mode == "permutations":
            total = math.perm(len(seed_words), k)
        else:
            total = math.comb(len(seed_words), k)
    except AttributeError:
        if args.mode == "permutations":
            total = math.factorial(len(seed_words)) // math.factorial(len(seed_words) - k)
        else:
            total = math.factorial(len(seed_words)) // (math.factorial(k) * math.factorial(len(seed_words) - k))

    print(f"Total candidate words after filtering: {len(seed_words)}")
    print(f"Mode: {args.mode}, choosing k={k} words")
    print(f"Total possible {args.mode} of {k} words: {total:,} (~{total:.3e})")

    if args.mode == "permutations":
        generator = itertools.permutations(seed_words, k)
    else:
        generator = itertools.combinations(seed_words, k)

    checkpoint = load_checkpoint(args.checkpoint)
    start_index = 0
    if args.start_index is not None:
        start_index = args.start_index
    elif checkpoint.get("last_index") is not None:
        start_index = int(checkpoint.get("last_index", 0))

    if start_index:
        print(f"Resuming from index {start_index}")
        generator = iter_with_start(generator, start_index)

    use_tqdm = args.use_tqdm
    if use_tqdm:
        try:
            from tqdm import tqdm
        except Exception:
            use_tqdm = False

    matches_file = args.matches_csv
    if not os.path.exists(matches_file):
        with open(matches_file, "w", encoding="utf-8") as f:
            f.write("index,seed_phrase,address,datetime\n")

    log_file = args.log_file
    lf = open(log_file, "a", encoding="utf-8")
    lf.write(f"\n--- Run started at {datetime.utcnow().isoformat()}Z ---\n")
    lf.flush()

    workers = max(1, args.workers)
    start_time = time.time()
    total_tested = start_index
    last_checkpoint_saved = time.time()
    last_console_update = time.time()

    def indexed_iter(gen, start_idx: int = 0):
        i = start_idx
        for item in gen:
            yield (i, item)
            i += 1

    indexed_generator = indexed_iter(generator, start_index)

    try:
        if workers > 1:
            pool = mp.Pool(processes=workers)
            func = partial(worker_process, args.target_address)
            if use_tqdm:
                from tqdm import tqdm
                pbar = tqdm(total=total - start_index, unit="comb")
            else:
                pbar = None

            for idx, seed_phrase, address in pool.imap_unordered(func, indexed_generator, chunksize=256):
                total_tested = max(total_tested, idx + 1)

                if address is None:
                    pass
                else:
                    if address == args.target_address:
                        print(f"\nMatch found! Index {idx} Seed Phrase: {seed_phrase} => Address: {address}")
                        with open(matches_file, "a", encoding="utf-8") as mf:
                            mf.write(f"{idx},\"{seed_phrase}\",{address},{datetime.utcnow().isoformat()}Z\n")
                        state = {"last_index": idx + 1, "tested": total_tested, "timestamp": time.time()}
                        save_checkpoint(args.checkpoint, state)
                        pool.terminate()
                        break

                # Console progress update (1s)
                if not pbar and (time.time() - last_console_update >= 1.0):
                    elapsed = time.time() - start_time
                    rate = total_tested / elapsed if elapsed > 0 else 0
                    remaining = max(total - total_tested, 0)
                    eta = remaining / rate if rate > 0 else float('inf')
                    percent = (total_tested / total) * 100
                    print(f"\rTested {total_tested:,}/{total:,} ({percent:.6f}%) {rate:,.2f} comb/s ETA {_format_time(eta)}", end="", flush=True)
                    last_console_update = time.time()

                if pbar:
                    pbar.update(1)

                # Checkpointing: every N combos OR every M seconds
                if (total_tested % args.checkpoint_interval == 0) or (time.time() - last_checkpoint_saved >= args.checkpoint_interval_seconds):
                    state = {"last_index": total_tested, "tested": total_tested, "timestamp": time.time()}
                    save_checkpoint(args.checkpoint, state)
                    last_checkpoint_saved = time.time()

            try:
                pool.close()
                pool.join()
            except Exception:
                pass

        else:
            if use_tqdm:
                try:
                    from tqdm import tqdm
                    pbar = tqdm(total=total - start_index, unit="comb")
                except Exception:
                    pbar = None
            else:
                pbar = None

            for idx, combo in indexed_generator:
                total_tested = idx + 1
                seed_phrase = " ".join(combo)

                if not is_valid_mnemonic(seed_phrase):
                    address = None
                else:
                    address = generate_address_from_seed(seed_phrase)

                if address is not None and address == args.target_address:
                    print(f"\nMatch found! Index {idx} Seed Phrase: {seed_phrase} => Address: {address}")
                    with open(matches_file, "a", encoding="utf-8") as mf:
                        mf.write(f"{idx},\"{seed_phrase}\",{address},{datetime.utcnow().isoformat()}Z\n")
                    state = {"last_index": idx + 1, "tested": total_tested, "timestamp": time.time()}
                    save_checkpoint(args.checkpoint, state)
                    break

                if pbar:
                    pbar.update(1)
                else:
                    if time.time() - last_console_update >= 1.0:
                        elapsed = time.time() - start_time
                        rate = total_tested / elapsed if elapsed > 0 else 0
                        remaining = max(total - total_tested, 0)
                        eta = remaining / rate if rate > 0 else float('inf')
                        percent = (total_tested / total) * 100
                        print(f"\rTested {total_tested:,}/{total:,} ({percent:.6f}%) {rate:,.2f} comb/s ETA {_format_time(eta)}", end="", flush=True)
                        last_console_update = time.time()

                # Checkpointing: every N combos OR every M seconds
                if (total_tested % args.checkpoint_interval == 0) or (time.time() - last_checkpoint_saved >= args.checkpoint_interval_seconds):
                    state = {"last_index": total_tested, "tested": total_tested, "timestamp": time.time()}
                    save_checkpoint(args.checkpoint, state)
                    last_checkpoint_saved = time.time()

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        elapsed = time.time() - start_time
        rate = total_tested / elapsed if elapsed > 0 else 0
        lf.write(f"Run finished at {datetime.utcnow().isoformat()}Z tested={total_tested} elapsed_s={elapsed:.2f} rate={rate:.2f}\n")
        lf.flush()
        lf.close()
        print()
        print(f"Finished. Tested {total_tested:,} combinations in {elapsed:.2f}s ({rate:,.2f} comb/s).")

if __name__ == "__main__":
    main()
