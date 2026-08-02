#!/usr/bin/env python3
"""
cleanup_duplicate_results.py — Ripulisce aiopslab/data/results/*.json quando
lo stesso problem_id e' stato eseguito piu' volte (es. perche' gpt.py e'
stato rilanciato da capo diverse volte dopo un crash).

Per ogni problem_id con piu' di un file, tiene UN SOLO run e sposta gli
altri in una cartella di archivio (non li cancella mai in modo definitivo,
a meno che tu non passi --delete esplicitamente).

Criterio di scelta di quale run tenere (default: --keep latest):
  - latest   : tiene il run piu' recente (start_time piu' alto)
  - success  : tiene un run con success=True se esiste tra i duplicati,
               altrimenti ricade su 'latest'

Uso (SEMPRE prima in modalita' anteprima, senza --apply, per vedere cosa
farebbe senza toccare nulla):
    python3 cleanup_duplicate_results.py --results-dir aiopslab/data/results
    python3 cleanup_duplicate_results.py --results-dir aiopslab/data/results --apply
    python3 cleanup_duplicate_results.py --results-dir aiopslab/data/results --apply --keep success
    python3 cleanup_duplicate_results.py --results-dir aiopslab/data/results --apply --delete
"""

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path


def extract_success(results: dict):
    if not results:
        return None
    if "success" in results:
        return bool(results["success"])
    if "Detection Accuracy" in results:
        return results["Detection Accuracy"] == "Correct"
    if "Localization Accuracy" in results:
        try:
            return float(results["Localization Accuracy"]) >= 1.0
        except (TypeError, ValueError):
            return None
    return None


def load_all(results_dir: Path):
    records = []
    for f in sorted(results_dir.glob("*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] impossibile leggere {f.name}: {e}")
            continue
        records.append(
            {
                "path": f,
                "problem_id": data.get("problem_id") or "unknown",
                "start_time": data.get("start_time") or 0,
                "success": extract_success(data.get("results") or {}),
            }
        )
    return records


def choose_keeper(group, keep_strategy):
    if keep_strategy == "success":
        successes = [r for r in group if r["success"] is True]
        if successes:
            return max(successes, key=lambda r: r["start_time"])
    return max(group, key=lambda r: r["start_time"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=str, default="aiopslab/data/results")
    parser.add_argument("--archive-dir", type=str, default="aiopslab/data/results_archive")
    parser.add_argument("--keep", choices=["latest", "success"], default="latest")
    parser.add_argument("--apply", action="store_true", help="Applica davvero le modifiche (default: solo anteprima)")
    parser.add_argument("--delete", action="store_true", help="Cancella i duplicati invece di archiviarli (richiede --apply)")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    archive_dir = Path(args.archive_dir)

    if not results_dir.exists():
        print(f"[ERRORE] {results_dir} non trovato.")
        return

    records = load_all(results_dir)
    print(f"File JSON totali trovati: {len(records)}")

    by_problem = defaultdict(list)
    for r in records:
        by_problem[r["problem_id"]].append(r)

    duplicated = {pid: group for pid, group in by_problem.items() if len(group) > 1}
    print(f"Problem_id distinti: {len(by_problem)}")
    print(f"Problem_id con duplicati: {len(duplicated)}")

    total_to_remove = sum(len(g) - 1 for g in duplicated.values())
    print(f"File duplicati da rimuovere: {total_to_remove}")

    if not duplicated:
        print("\nNessun duplicato trovato, cartella gia' pulita.")
        return

    plan = []
    for pid, group in sorted(duplicated.items()):
        keeper = choose_keeper(group, args.keep)
        losers = [r for r in group if r["path"] != keeper["path"]]
        plan.append((pid, keeper, losers))

    print("\n=== Anteprima (nessuna modifica ancora applicata) ===" if not args.apply else "\n=== Applico le modifiche ===")
    for pid, keeper, losers in plan:
        outcome = keeper["success"]
        print(f"\n{pid}  ({len(losers) + 1} run trovati)")
        print(f"  TENUTO : {keeper['path'].name}  (success={outcome})")
        for loser in losers:
            azione = "cancellato" if args.delete else f"spostato in {archive_dir}/"
            print(f"  {'RIMOSSO' if args.apply else 'da rimuovere'} ({azione}): {loser['path'].name}  (success={loser['success']})")

    if not args.apply:
        print(f"\n[ANTEPRIMA] Nessun file e' stato toccato. Rilancia con --apply per eseguire davvero la pulizia.")
        print(f"Di default i duplicati vengono SPOSTATI in {archive_dir} (non cancellati). Aggiungi anche --delete per cancellarli definitivamente.")
        return

    if args.delete:
        archive_dir = None
    else:
        archive_dir.mkdir(parents=True, exist_ok=True)

    removed = 0
    for pid, keeper, losers in plan:
        for loser in losers:
            if args.delete:
                loser["path"].unlink()
            else:
                shutil.move(str(loser["path"]), str(archive_dir / loser["path"].name))
            removed += 1

    print(f"\nFatto. File rimossi: {removed}")
    if not args.delete:
        print(f"Archiviati (non persi) in: {archive_dir}")


if __name__ == "__main__":
    main()