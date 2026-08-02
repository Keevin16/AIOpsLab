#!/usr/bin/env python3
"""
select_missing_problems.py — Estrae, dal not_executed.csv prodotto da
analyze_results.py, l'elenco dei problem_id NON ancora eseguiti, filtrati
per una o piu' tipologie di task, e li scrive in un file di testo pronto
da passare a `clients/gpt.py --problems-file`.

Uso:
    python3 select_missing_problems.py
    python3 select_missing_problems.py --task-types analysis
    python3 select_missing_problems.py --task-types analysis,detection,localization
    python3 select_missing_problems.py --not-executed-csv analysis_output/not_executed.csv --output missing_rca.txt
"""

import argparse
import csv
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--not-executed-csv", type=str, default="analysis_output/not_executed.csv")
    parser.add_argument(
        "--executed-not-evaluated-csv",
        type=str,
        default="analysis_output/executed_not_evaluated.csv",
        help="CSV dei run interrotti/non valutabili (crash prima della eval), "
             "usato solo se --include-crashed e' attivo.",
    )
    parser.add_argument(
        "--include-crashed",
        action="store_true",
        help="Include anche i problemi eseguiti ma non valutabili (crash a meta' run), "
             "non solo quelli mai eseguiti.",
    )
    parser.add_argument(
        "--task-types",
        type=str,
        default="analysis",
        help="Tipologie da includere, separate da virgola. Es: 'analysis' oppure "
             "'analysis,detection,localization'. Default: analysis",
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default="",
        help="Famiglie di fault (o problem_id esatti) da escludere, separati da virgola. "
             "Es: 'astronomy_shop_loadgenerator_flood_homepage' esclude sia la variante "
             "detection che localization di quella famiglia.",
    )
    parser.add_argument("--output", type=str, default="missing_problems.txt")
    args = parser.parse_args()

    csv_path = Path(args.not_executed_csv)
    if not csv_path.exists():
        print(f"[ERRORE] {csv_path} non trovato.")
        print("         Genera prima questo file lanciando analyze_results.py")
        print("         (richiede che l'import di ProblemRegistry funzioni: verifica con")
        print('         python3 -c "from aiopslab.orchestrator.problems.registry import ProblemRegistry; print(len(ProblemRegistry().PROBLEM_REGISTRY))"')
        print("         -> NON deve dare errore. Se da' errore, il not_executed.csv attuale")
        print("         non e' affidabile: rigeneralo dopo aver risolto l'import.)")
        sys.exit(1)

    wanted_types = {t.strip() for t in args.task_types.split(",") if t.strip()}

    selected = []
    sources = {"not_executed": 0, "executed_not_evaluated": 0}

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["task_type"] in wanted_types:
                selected.append(row["problem_id"])
                sources["not_executed"] += 1

    if args.include_crashed:
        crashed_path = Path(args.executed_not_evaluated_csv)
        if crashed_path.exists():
            with open(crashed_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row["task_type"] in wanted_types and row["problem_id"] not in selected:
                        selected.append(row["problem_id"])
                        sources["executed_not_evaluated"] += 1
        else:
            print(f"[WARN] --include-crashed richiesto ma {crashed_path} non trovato, ignorato.")

    exclude_terms = {t.strip() for t in args.exclude.split(",") if t.strip()}
    excluded_count = 0
    if exclude_terms:
        before = len(selected)
        selected = [
            pid for pid in selected
            if pid not in exclude_terms
            and not any(pid.startswith(term + "-") for term in exclude_terms)
        ]
        excluded_count = before - len(selected)

    with open(args.output, "w") as f:
        for pid in selected:
            f.write(pid + "\n")

    print(f"Tipologie richieste: {sorted(wanted_types)}")
    print(f"Mai eseguiti: {sources['not_executed']}")
    if args.include_crashed:
        print(f"Eseguiti ma interrotti/non valutabili (aggiunti): {sources['executed_not_evaluated']}")
    if exclude_terms:
        print(f"Esclusi manualmente ({sorted(exclude_terms)}): {excluded_count}")
    print(f"Totale problemi da rilanciare: {len(selected)}")
    for pid in selected:
        print(f"  - {pid}")
    print(f"\nElenco salvato in: {args.output}")
    print(f"Per eseguirli: python3 clients/gpt.py --problems-file {args.output}")


if __name__ == "__main__":
    main()