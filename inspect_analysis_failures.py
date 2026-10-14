#!/usr/bin/env python3
"""
inspect_analysis_failures.py — Per ogni problema 'analysis' fallito (o non
valutabile), legge la trace originale nel file JSON di sessione e cerca
l'ULTIMA chiamata submit(...) fatta dall'agente, per stabilire con
CERTEZZA (non per inferenza statistica) se il fallimento e':

  - FORMATO: nessuna submit() valida trovata nella trace (l'agente non ha
    mai sottomesso un dizionario, o ha esaurito gli step prima di riuscirci)
  - CONTENUTO: una submit({...}) ben formata e' stata trovata, ma con
    valori sbagliati (system_level/fault_type non corretti)

Uso:
    python3 inspect_analysis_failures.py \
        --failure-details analysis_output/failure_details.csv \
        --results-dir aiopslab/data/results \
        --output analysis_output/analysis_failure_causes.csv
"""

import argparse
import csv
import json
import re
from pathlib import Path


SUBMIT_RE = re.compile(r"submit\s*\((.*?)\)\s*$", re.DOTALL)
DICT_ARG_RE = re.compile(r"submit\s*\(\s*\{", re.DOTALL)


def find_last_submit(trace):
    """Cerca, scorrendo la trace dall'ultimo messaggio al primo, l'ultima
    chiamata submit(...) presente nel testo prodotto dall'agente
    (role == 'assistant'). Ritorna (submit_text, is_dict_call) oppure
    (None, False) se non ne trova nessuna."""
    for item in reversed(trace or []):
        if item.get("role") != "assistant":
            continue
        content = item.get("content", "") or ""
        if "submit(" not in content:
            continue
        m = SUBMIT_RE.search(content.strip())
        submit_text = m.group(0) if m else content[content.find("submit("):]
        is_dict_call = bool(DICT_ARG_RE.search(submit_text))
        return submit_text.strip(), is_dict_call
    return None, False


def classify(submit_text, is_dict_call):
    if submit_text is None:
        return "FORMATO: nessuna submit() trovata nella trace (mai sottomesso)"
    if not is_dict_call:
        return "FORMATO: submit() trovata ma senza un dizionario {...} come argomento"
    return "CONTENUTO: submit({...}) ben formata trovata, valori probabilmente sbagliati"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--failure-details", type=str, default="analysis_output/failure_details.csv")
    parser.add_argument("--results-dir", type=str, default="aiopslab/data/results")
    parser.add_argument("--archive-dir", type=str, default="aiopslab/data/results_archive",
                         help="Cartella di archivio (se hai gia' usato cleanup_duplicate_results.py), "
                              "controllata come fallback se il file non e' in --results-dir")
    parser.add_argument("--output", type=str, default="analysis_output/analysis_failure_causes.csv")
    parser.add_argument("--task-type", type=str, default="analysis",
                         help="Task type da ispezionare (default: analysis)")
    args = parser.parse_args()

    failure_csv = Path(args.failure_details)
    if not failure_csv.exists():
        print(f"[ERRORE] {failure_csv} non trovato. Lancia prima analyze_results.py.")
        return

    results_dir = Path(args.results_dir)
    archive_dir = Path(args.archive_dir)

    rows_out = []
    with open(failure_csv) as f:
        reader = csv.DictReader(f)
        target_rows = [r for r in reader if r["task_type"] == args.task_type]

    if not target_rows:
        print(f"Nessun fallimento trovato per task_type='{args.task_type}' in {failure_csv}.")
        return

    print(f"Problemi '{args.task_type}' falliti/non valutabili trovati: {len(target_rows)}\n")

    for row in target_rows:
        fname = row.get("file")
        if not fname:
            continue
        json_path = results_dir / fname
        if not json_path.exists():
            json_path = archive_dir / fname
        if not json_path.exists():
            print(f"[WARN] file non trovato per {row['problem_id']}: {fname}")
            continue

        with open(json_path) as jf:
            data = json.load(jf)

        submit_text, is_dict_call = find_last_submit(data.get("trace"))
        cause = classify(submit_text, is_dict_call)

        print(f"--- {row['problem_id']} ---")
        print(f"  Esito: {row['esito']}   Dettaglio: {row.get('outcome_detail', '')}")
        print(f"  Causa: {cause}")
        if submit_text:
            preview = submit_text[:200].replace("\n", " ")
            print(f"  Ultima submit() trovata: {preview}")
        print()

        rows_out.append(
            {
                "problem_id": row["problem_id"],
                "esito": row["esito"],
                "outcome_detail": row.get("outcome_detail", ""),
                "causa": cause,
                "ultima_submit": submit_text or "",
                "file": fname,
            }
        )

    if rows_out:
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            writer.writeheader()
            writer.writerows(rows_out)
        print(f"Risultato completo salvato in: {args.output}")

        n_formato = sum(1 for r in rows_out if r["causa"].startswith("FORMATO"))
        n_contenuto = sum(1 for r in rows_out if r["causa"].startswith("CONTENUTO"))
        print(f"\n=== Riepilogo ===")
        print(f"Problemi di FORMATO (mai sottomesso / submit malformata): {n_formato}")
        print(f"Problemi di CONTENUTO (submit valida, risposta sbagliata): {n_contenuto}")


if __name__ == "__main__":
    main()