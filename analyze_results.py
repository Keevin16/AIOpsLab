#!/usr/bin/env python3
"""
analyze_results.py — Analisi locale dei risultati AIOpsLab, senza W&B.

Legge tutti i file JSON prodotti da `session.to_json()` (di default in
`aiopslab/data/results/*.json`), li confronta con l'elenco COMPLETO dei
problemi definiti nel ProblemRegistry di AIOpsLab, e produce:

  - quali problemi sono stati eseguiti e RISOLTI CORRETTAMENTE
  - quali sono stati eseguiti ma RISOLTI CON ESITO NEGATIVO (fallimento)
  - quali sono stati eseguiti ma NON VALUTABILI (es. crash prima della eval)
  - quali problemi NON SONO STATI ESEGUITI AFFATTO, con la loro tipologia
  - percentuali di successo per TASK TYPE, per FAMIGLIA DI FAULT e per
    APPLICAZIONE/AMBIENTE (Hotel Reservation, Social Network, Astronomy
    Shop, TiDB, Flower...), calcolate sul totale dei problemi ATTESI
  - un elenco di dettaglio di tutti i problemi falliti/non valutabili, con
    la tipologia, la famiglia di fault, l'app coinvolta e il motivo
    specifico del fallimento quando disponibile (es. 'Incorrect' vs
    'Invalid Format' per detection, punteggio/giudizio del judge se
    qualitative_eval e' abilitato)

La mappatura famiglia-di-fault -> applicazione viene ricavata SOLO dal
codice sorgente di AIOpsLab (lettura statica dei file .py sotto
aiopslab/orchestrator/problems/), senza importare o istanziare nulla:
questo evita qualunque effetto collaterale sul cluster (alcune classi App,
es. HotelReservation, creano namespace/configmap gia' al costruttore).

Uso:
    python3 analyze_results.py
    python3 analyze_results.py --results-dir aiopslab/data/results --output-dir analysis_output
"""

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # non richiede un display grafico
import matplotlib.pyplot as plt


TASK_TYPES = ["detection", "localization", "analysis", "mitigation"]


# --------------------------------------------------------------------------
# Utility: tipologia e famiglia di fault di un problem_id
# --------------------------------------------------------------------------

def infer_task_type(problem_id: str) -> str:
    """Deduce il tipo di task da un problem_id."""
    if problem_id.startswith("noop_detection"):
        return "detection"
    m = re.search(r"-(detection|localization|analysis|mitigation)(-\d+)?$", problem_id)
    if m:
        return m.group(1)
    return "unknown"


def infer_fault_family(problem_id: str) -> str:
    """Deduce la famiglia di fault (problem_id senza il suffisso
    '-<task_type>-<n>'). Es. 'k8s_target_port-misconfig-analysis-2' ->
    'k8s_target_port-misconfig'."""
    m = re.match(r"^(.*)-(detection|localization|analysis|mitigation)(-\d+)?$", problem_id)
    return m.group(1) if m else problem_id


# --------------------------------------------------------------------------
# ProblemRegistry: elenco completo + mappa famiglia -> applicazione
# (entrambi ricavati SOLO da lettura statica del codice sorgente, mai da
# import/istanziazione, per non avere effetti collaterali sul cluster)
# --------------------------------------------------------------------------

def load_full_registry_ids(repo_root: Path):
    """Legge aiopslab/orchestrator/problems/registry.py e ne estrae, con
    ast.parse (non regex), le chiavi TOP-LEVEL reali del dict
    PROBLEM_REGISTRY insieme al nome della classe Python associata a
    ciascuna. Ritorna (dict problem_id -> class_name) oppure None se il
    file non si trova."""
    registry_path = repo_root / "aiopslab" / "orchestrator" / "problems" / "registry.py"
    if not registry_path.exists():
        print(f"[WARN] {registry_path} non trovato, salto il confronto con l'elenco completo.")
        return None

    try:
        tree = ast.parse(registry_path.read_text())
    except SyntaxError as e:
        print(f"[WARN] Impossibile leggere {registry_path}: {e}")
        return None

    pairs = {}

    class FindDict(ast.NodeVisitor):
        def visit_Assign(self, node):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "PROBLEM_REGISTRY":
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(v, ast.Lambda) and isinstance(v.body, ast.Call) and isinstance(v.body.func, ast.Name):
                            cls_name = v.body.func.id
                        elif isinstance(v, ast.Name):
                            cls_name = v.id
                        else:
                            cls_name = None
                        pairs[k.value] = cls_name
            self.generic_visit(node)

    FindDict().visit(tree)
    return pairs


def build_family_to_app_map(repo_root: Path, problem_to_class: dict):
    """Per ogni classe Python usata nel registry, trova (via lettura
    statica, con una semplice regex su 'class X(' e 'self.app = Y(') quale
    applicazione (HotelReservation, SocialNetwork, AstronomyShop, TiDBCluster,
    Flower...) usa. Ritorna un dict fault_family -> nome applicazione.
    I 3 problemi 'noop_detection_*' sono gestiti come caso speciale perche'
    l'app e' scelta a runtime in base a un parametro, non fissa nel file."""
    problems_dir = repo_root / "aiopslab" / "orchestrator" / "problems"
    if not problems_dir.exists():
        return {}

    class_to_file = {}
    file_to_apps = {}
    for path in problems_dir.rglob("*.py"):
        if path.name == "registry.py":
            continue
        try:
            content = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for cls in re.findall(r"^class (\w+)\(", content, re.MULTILINE):
            class_to_file[cls] = path
        apps = set(re.findall(r"self\.app\s*=\s*(\w+)\(", content))
        file_to_apps[path] = apps

    family_to_app = {}
    for pid, cls_name in problem_to_class.items():
        if pid.startswith("noop_detection"):
            if "hotel_reservation" in pid:
                family_to_app[pid] = "HotelReservation"
            elif "social_network" in pid:
                family_to_app[pid] = "SocialNetwork"
            elif "astronomy_shop" in pid:
                family_to_app[pid] = "AstronomyShop"
            continue

        path = class_to_file.get(cls_name)
        if not path:
            continue
        apps = file_to_apps.get(path, set())
        family = infer_fault_family(pid)
        if len(apps) == 1:
            family_to_app[family] = list(apps)[0]
        # se un file usa piu' app diverse per classi diverse (raro, solo
        # no_op.py, gia' gestito sopra) non sovrascriviamo con un valore
        # ambiguo: la famiglia resta assente dalla mappa

    return family_to_app


# --------------------------------------------------------------------------
# Normalizzazione dell'esito di una sessione
# --------------------------------------------------------------------------

def extract_success_and_detail(results: dict):
    """Normalizza il campo di successo/accuratezza. Ritorna una tupla
    (success, detail):
      - success: True / False / None
      - detail: stringa con il motivo/valore grezzo, utile per capire IL
        TIPO di fallimento (es. 'Incorrect' vs 'Invalid Format' per
        detection non sono lo stesso tipo di errore: il secondo indica che
        l'agente non ha nemmeno rispettato il formato di submit atteso)
    """
    if not results:
        return None, None

    if "success" in results:
        return bool(results["success"]), f"success={results['success']}"

    if "Detection Accuracy" in results:
        acc = results["Detection Accuracy"]
        if acc == "Correct":
            return True, acc
        if acc in ("Incorrect", "Invalid Format"):
            return False, acc
        return None, acc

    if "Localization Accuracy" in results:
        try:
            val = float(results["Localization Accuracy"])
            return val >= 1.0, f"Localization Accuracy={val}"
        except (TypeError, ValueError):
            return None, str(results["Localization Accuracy"])

    return None, None


def extract_duration_seconds(record: dict, results: dict):
    start = record.get("start_time")
    end = record.get("end_time")
    if start is not None and end is not None:
        try:
            return float(end) - float(start)
        except (TypeError, ValueError):
            pass
    for key in ("TTD", "TTL", "TTA", "TTM"):
        if results and key in results:
            try:
                return float(results[key])
            except (TypeError, ValueError):
                continue
    return None


# --------------------------------------------------------------------------
# Caricamento sessioni
# --------------------------------------------------------------------------

def load_sessions(results_dir: Path, family_to_app: dict) -> pd.DataFrame:
    files = sorted(results_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(
            f"Nessun file .json trovato in {results_dir}. "
            f"Controlla il percorso (default atteso: aiopslab/data/results)."
        )

    rows = []
    for f in files:
        try:
            with open(f, "r") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] impossibile leggere {f.name}: {e}")
            continue

        results = data.get("results") or {}
        problem_id = data.get("problem_id") or "unknown"
        success, detail = extract_success_and_detail(results)
        fault_family = infer_fault_family(problem_id)

        row = {
            "file": f.name,
            "session_id": data.get("session_id"),
            "agent": data.get("agent"),
            "problem_id": problem_id,
            "task_type": infer_task_type(problem_id),
            "fault_family": fault_family,
            "app": family_to_app.get(fault_family) or family_to_app.get(problem_id, "unknown"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "duration_sec": extract_duration_seconds(data, results),
            "steps": results.get("steps"),
            "in_tokens": results.get("in_tokens"),
            "out_tokens": results.get("out_tokens"),
            "reasoning_score": results.get("reasoning_score"),
            "reasoning_judgement": results.get("reasoning_judgement"),
            "success": success,
            "outcome_detail": detail,
            "raw_results": json.dumps(results, ensure_ascii=False),
        }
        rows.append(row)

    return pd.DataFrame(rows)


def latest_run_per_problem(df: pd.DataFrame) -> pd.DataFrame:
    df_sorted = df.sort_values("start_time")
    return df_sorted.groupby("problem_id", as_index=False).last()


# --------------------------------------------------------------------------
# Classificazione e breakdown generico per una colonna qualsiasi
# (task_type, fault_family, o app)
# --------------------------------------------------------------------------

def classify_against_registry(df_latest: pd.DataFrame, all_problem_ids):
    executed_ids = set(df_latest["problem_id"])

    solved_correct = df_latest[df_latest["success"] == True]
    solved_incorrect = df_latest[df_latest["success"] == False]
    executed_not_evaluated = df_latest[df_latest["success"].isna()]

    if all_problem_ids is not None:
        not_executed_ids = sorted(set(all_problem_ids) - executed_ids)
    else:
        not_executed_ids = []

    not_executed = pd.DataFrame(
        {
            "problem_id": not_executed_ids,
            "task_type": [infer_task_type(p) for p in not_executed_ids],
            "fault_family": [infer_fault_family(p) for p in not_executed_ids],
        }
    )

    return solved_correct, solved_incorrect, executed_not_evaluated, not_executed


def build_breakdown_table(df_latest: pd.DataFrame, all_problem_ids, group_col: str, id_to_group=None):
    """Tabella di breakdown generica, raggruppata per group_col
    ('task_type', 'fault_family' o 'app'). Il totale ATTESO per ciascun
    gruppo viene calcolato sull'intero registry (se disponibile), usando
    id_to_group per mappare ogni problem_id atteso al proprio gruppo
    (necessario per fault_family/app, dove il gruppo non si deduce dal solo
    problem_id eseguito ma serve la stessa logica usata per il dataset)."""
    if all_problem_ids is not None:
        if id_to_group is not None:
            expected_groups = [id_to_group(p) for p in all_problem_ids]
        else:
            expected_groups = [p for p in all_problem_ids]
        expected_by_group = pd.Series(expected_groups).value_counts()
    else:
        expected_by_group = df_latest[group_col].value_counts()

    all_groups = sorted(set(list(expected_by_group.index) + list(df_latest[group_col].unique())))

    rows = []
    for g in all_groups:
        expected = int(expected_by_group.get(g, 0))
        subset = df_latest[df_latest[group_col] == g]

        n_correct = int((subset["success"] == True).sum())
        n_incorrect = int((subset["success"] == False).sum())
        n_not_evaluated = int(subset["success"].isna().sum())
        n_executed = len(subset)
        n_not_executed = max(expected - n_executed, 0)

        rows.append(
            {
                group_col: g,
                "problemi_attesi": expected,
                "eseguiti": n_executed,
                "non_eseguiti": n_not_executed,
                "risolti_correttamente": n_correct,
                "risolti_esito_negativo": n_incorrect,
                "eseguiti_non_valutabili": n_not_evaluated,
                "%_risolti_correttamente_su_attesi": round(100 * n_correct / expected, 1) if expected else None,
                "%_esito_negativo_su_attesi": round(100 * n_incorrect / expected, 1) if expected else None,
                "%_non_eseguiti_su_attesi": round(100 * n_not_executed / expected, 1) if expected else None,
            }
        )

    result = pd.DataFrame(rows)
    # ordina dal peggiore (meno risolti correttamente in %) al migliore, utile
    # per capire subito DOVE il modello fatica di piu'
    return result.sort_values("%_risolti_correttamente_su_attesi", na_position="first")


def build_failure_details(df_latest: pd.DataFrame) -> pd.DataFrame:
    """Elenco di dettaglio di tutti i problemi con esito negativo o non
    valutabile, con motivo del fallimento quando disponibile: utile per
    leggere rapidamente DOVE e PERCHE' il modello ha fallito, invece di
    aprire ogni singolo file JSON a mano."""
    failed = df_latest[(df_latest["success"] == False) | (df_latest["success"].isna())].copy()
    failed["esito"] = failed["success"].map({False: "esito_negativo", None: "non_valutabile"})
    failed.loc[failed["success"].isna(), "esito"] = "non_valutabile"
    cols = [
        "problem_id", "task_type", "fault_family", "app", "esito",
        "outcome_detail", "reasoning_score", "reasoning_judgement",
        "steps", "duration_sec", "file",
    ]
    return failed[cols].sort_values(["task_type", "fault_family"])


# --------------------------------------------------------------------------
# Grafici
# --------------------------------------------------------------------------

def plot_outcome_breakdown(pct_table: pd.DataFrame, group_col: str, output_dir: Path, filename: str, title: str):
    data = pct_table.dropna(subset=["problemi_attesi"])
    data = data[data["problemi_attesi"] > 0]
    if data.empty:
        print(f"[WARN] nessun dato disponibile per il grafico {filename}")
        return

    fig_height = max(4, 0.35 * len(data) + 1.5)
    fig, ax = plt.subplots(figsize=(9, fig_height))
    y = range(len(data))

    correct_pct = data["%_risolti_correttamente_su_attesi"].fillna(0)
    incorrect_pct = data["%_esito_negativo_su_attesi"].fillna(0)
    not_eval_pct = (100 * data["eseguiti_non_valutabili"] / data["problemi_attesi"]).fillna(0)
    not_exec_pct = data["%_non_eseguiti_su_attesi"].fillna(0)

    ax.barh(y, correct_pct, label="Risolti correttamente", color="#2ca02c")
    ax.barh(y, incorrect_pct, left=correct_pct, label="Esito negativo", color="#d62728")
    ax.barh(y, not_eval_pct, left=correct_pct + incorrect_pct, label="Eseguiti, non valutabili", color="#ff7f0e")
    ax.barh(
        y,
        not_exec_pct,
        left=correct_pct + incorrect_pct + not_eval_pct,
        label="Non eseguiti",
        color="#7f7f7f",
    )

    ax.set_yticks(list(y))
    ax.set_yticklabels(data[group_col])
    ax.set_xlabel("% sul totale dei problemi attesi")
    ax.set_xlim(0, 100)
    ax.set_title(title)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08 - 0.01 * len(data)), ncol=2)
    fig.tight_layout()

    out_path = output_dir / filename
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Grafico salvato: {out_path}")


def plot_avg_duration(df_latest: pd.DataFrame, output_dir: Path):
    summary = df_latest.groupby("task_type")["duration_sec"].mean().dropna()
    if summary.empty:
        print("[WARN] nessun dato di durata disponibile per il grafico avg_duration")
        return
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(summary.index, summary.values)
    ax.set_ylabel("Durata media (secondi)")
    ax.set_xlabel("Task type")
    ax.set_title("Durata media della sessione per tipo di task (solo eseguiti)")
    fig.tight_layout()
    out_path = output_dir / "avg_duration_by_task_type.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Grafico salvato: {out_path}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=str, default="aiopslab/data/results")
    parser.add_argument("--output-dir", type=str, default="analysis_output")
    parser.add_argument(
        "--repo-root",
        type=str,
        default=".",
        help="Cartella root del repo AIOpsLab (default: cartella corrente)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    sys.path.insert(0, str(repo_root))

    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Registry completo + mappa famiglia->app, letti SOLO da sorgente ---
    problem_to_class = load_full_registry_ids(repo_root)
    all_problem_ids = list(problem_to_class.keys()) if problem_to_class is not None else None
    family_to_app = build_family_to_app_map(repo_root, problem_to_class) if problem_to_class else {}

    if all_problem_ids is not None:
        print(f"Problemi totali attesi dal ProblemRegistry: {len(all_problem_ids)}")
        print(f"Famiglie di fault mappate a un'applicazione: {len(family_to_app)}")
    else:
        print("[WARN] Registry non disponibile: le analisi 'attesi/non eseguiti' saranno limitate all'osservato.")

    # --- Caricamento sessioni ---
    df = load_sessions(results_dir, family_to_app)
    print(f"\nFile di sessione caricati: {len(df)}")

    df_latest = latest_run_per_problem(df)
    print(f"Problem_id distinti eseguiti (ultimo run per ciascuno): {len(df_latest)}")

    # --- CSV di dettaglio (tutti i run) ---
    sessions_csv = output_dir / "sessions_detail.csv"
    df.drop(columns=["raw_results"]).to_csv(sessions_csv, index=False)
    print(f"\nDettaglio sessioni (tutti i run) salvato: {sessions_csv}")

    # --- Classificazione sull'ultimo run di ciascun problema ---
    solved_correct, solved_incorrect, executed_not_evaluated, not_executed = classify_against_registry(
        df_latest, all_problem_ids
    )
    solved_correct[["problem_id", "task_type", "fault_family", "app", "reasoning_score", "duration_sec", "steps"]].to_csv(
        output_dir / "solved_correctly.csv", index=False
    )
    solved_incorrect[["problem_id", "task_type", "fault_family", "app", "outcome_detail", "reasoning_score", "duration_sec", "steps"]].to_csv(
        output_dir / "solved_incorrectly.csv", index=False
    )
    executed_not_evaluated[["problem_id", "task_type", "fault_family", "app", "duration_sec", "steps", "file"]].to_csv(
        output_dir / "executed_not_evaluated.csv", index=False
    )
    if all_problem_ids is not None:
        not_executed.to_csv(output_dir / "not_executed.csv", index=False)

    print(f"\nRisolti correttamente:      {len(solved_correct)}")
    print(f"Risolti con esito negativo: {len(solved_incorrect)}")
    print(f"Eseguiti ma non valutabili: {len(executed_not_evaluated)}")
    if all_problem_ids is not None:
        print(f"Non eseguiti affatto:       {len(not_executed)}")

    # --- Dettaglio di TUTTI i fallimenti (esito negativo + non valutabili) ---
    failure_details = build_failure_details(df_latest)
    failure_csv = output_dir / "failure_details.csv"
    failure_details.to_csv(failure_csv, index=False)
    print(f"\nDettaglio fallimenti salvato: {failure_csv}")
    if not failure_details.empty:
        print("\n=== Riepilogo fallimenti per famiglia di fault ===")
        print(failure_details.groupby(["fault_family", "esito"]).size().to_string())

    # --- Breakdown per TASK TYPE ---
    pct_task = build_breakdown_table(df_latest, all_problem_ids, "task_type", infer_task_type)
    pct_task.to_csv(output_dir / "percentages_by_task_type.csv", index=False)
    print("\n=== Percentuali per task type ===")
    print(pct_task.to_string(index=False))
    plot_outcome_breakdown(pct_task, "task_type", output_dir, "outcome_breakdown_by_task_type.png",
                            "Esito per tipo di task (sul totale dei problemi attesi)")

    # --- Breakdown per FAMIGLIA DI FAULT ---
    pct_family = build_breakdown_table(df_latest, all_problem_ids, "fault_family", infer_fault_family)
    pct_family.to_csv(output_dir / "percentages_by_fault_family.csv", index=False)
    print("\n=== Percentuali per famiglia di fault (ordinate dalla piu' problematica) ===")
    print(pct_family.to_string(index=False))
    plot_outcome_breakdown(pct_family, "fault_family", output_dir, "outcome_breakdown_by_fault_family.png",
                            "Esito per famiglia di fault (sul totale dei problemi attesi)")

    # --- Breakdown per APPLICAZIONE/AMBIENTE ---
    if all_problem_ids is not None and family_to_app:
        def id_to_app(pid):
            fam = infer_fault_family(pid)
            return family_to_app.get(fam) or family_to_app.get(pid, "unknown")

        pct_app = build_breakdown_table(df_latest, all_problem_ids, "app", id_to_app)
        pct_app.to_csv(output_dir / "percentages_by_app.csv", index=False)
        print("\n=== Percentuali per applicazione/ambiente ===")
        print(pct_app.to_string(index=False))
        plot_outcome_breakdown(pct_app, "app", output_dir, "outcome_breakdown_by_app.png",
                                "Esito per applicazione/ambiente (sul totale dei problemi attesi)")
    else:
        print("\n[WARN] Salto il breakdown per app: registry o mappa famiglia->app non disponibili.")

    # --- Grafico durata media ---
    plot_avg_duration(df_latest, output_dir)


if __name__ == "__main__":
    main()