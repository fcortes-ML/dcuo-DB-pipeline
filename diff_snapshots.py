"""
DCUO feats - diferencias entre corridas (tecnica H3).

Compara, para cada personaje, dos corridas de extract.py y muestra:
  - los feats completados nuevos (y si alguno desaparecio, que no deberia pasar),
  - los feats activos que entraron y salieron, y cuantos de los que salieron
    pasaron a completados,
  - los cambios en la ficha (nivel, combat rating, skill points, max_feats).
El detalle queda en data/diffs/<antes>__<despues>.csv.

Uso:
  python diff_snapshots.py                  # por personaje: ultima corrida vs la anterior
  python diff_snapshots.py --antes RUN_ID   # RUN_ID vs la ultima corrida
  python diff_snapshots.py --antes RUN_ID --despues RUN_ID
  python diff_snapshots.py --listar         # lista las corridas disponibles
  python diff_snapshots.py --serie max_feats  # un campo de la ficha, corrida por corrida
"""

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
RUNS_DIR = ROOT / "data" / "runs"
DIFFS_DIR = ROOT / "data" / "diffs"

CAMPOS_FICHA = ("level", "combat_rating", "skill_points", "max_feats")
CAMPOS_SERIE = CAMPOS_FICHA + ("n_completed", "n_active")
COMPLETED = "characters_completed_feat"
ACTIVE = "characters_active_feat"


# --- Lectura de corridas -------------------------------------------------------


def load_runs() -> list[dict]:
    """Todos los manifiestos de data/runs, ordenados del mas viejo al mas nuevo."""
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in RUNS_DIR.glob("*.json")]
    return sorted(runs, key=lambda m: m["run_id"])


def ok_entries(manifest: dict) -> dict[str, dict]:
    """Personajes que salieron bien en una corrida, indexados por character_id."""
    return {
        e["character_id"]: e
        for e in manifest["personajes"]
        if e.get("status") == "ok" and e.get("character_id")
    }


def load_feat_ids(collection: str, run_id: str, character_id: str) -> set[int] | None:
    """feat_id de una coleccion para un personaje en una corrida (None si no hay archivo)."""
    files = sorted(RAW_DIR.glob(f"{collection}/dt=*/{run_id}__{character_id}__p*.json"))
    if not files:
        return None
    ids: set[int] = set()
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids.update(int(row["feat_id"]) for row in data.get(f"{collection}_list", []))
    return ids


def pick_pairs(runs: list[dict], antes: str | None, despues: str | None) -> list[tuple]:
    """Arma los pares (character_id, nombre, corrida_antes, corrida_despues) a comparar."""
    by_id = {m["run_id"]: m for m in runs}
    for run_id in (antes, despues):
        if run_id and run_id not in by_id:
            raise SystemExit(f"No existe la corrida {run_id}. Usa --listar para ver las disponibles.")

    pairs = []
    if antes:
        m_despues = by_id[despues] if despues else runs[-1]
        m_antes = by_id[antes]
        ok_a, ok_d = ok_entries(m_antes), ok_entries(m_despues)
        for cid in ok_d:
            if cid in ok_a:
                pairs.append((cid, ok_d[cid]["name"], m_antes, m_despues))
        return pairs

    # Sin argumentos: para cada personaje, sus dos ultimas corridas exitosas.
    historia: dict[str, list[dict]] = {}
    nombres: dict[str, str] = {}
    for m in runs:
        for cid, entry in ok_entries(m).items():
            historia.setdefault(cid, []).append(m)
            nombres[cid] = entry["name"]
    for cid, corridas in historia.items():
        if len(corridas) >= 2:
            pairs.append((cid, nombres[cid], corridas[-2], corridas[-1]))
    return pairs


# --- Comparacion -------------------------------------------------------------------


def comparar(cid: str, nombre: str, m_antes: dict, m_despues: dict, filas: list[dict]) -> None:
    ra, rd = m_antes["run_id"], m_despues["run_id"]
    ea, ed = ok_entries(m_antes)[cid], ok_entries(m_despues)[cid]
    print(f"\n{nombre}  ({ra} -> {rd})")

    cambios = [
        f"{campo} {ea.get(campo)} -> {ed.get(campo)}"
        for campo in CAMPOS_FICHA
        if ea.get(campo) != ed.get(campo)
    ]
    print("   ficha: " + (" | ".join(cambios) if cambios else "sin cambios"))

    comp_a, comp_d = load_feat_ids(COMPLETED, ra, cid), load_feat_ids(COMPLETED, rd, cid)
    act_a, act_d = load_feat_ids(ACTIVE, ra, cid), load_feat_ids(ACTIVE, rd, cid)
    if None in (comp_a, comp_d):
        print("   completados: falta el archivo crudo de alguna de las dos corridas")
        return

    nuevos = sorted(comp_d - comp_a)
    perdidos = sorted(comp_a - comp_d)
    print(f"   completados: {len(comp_a)} -> {len(comp_d)} "
          f"(+{len(nuevos)} nuevos, {len(perdidos)} desaparecidos)")
    if nuevos:
        print("     nuevos: " + ", ".join(map(str, nuevos)))
    if perdidos:
        print("     ATENCION, desaparecidos: " + ", ".join(map(str, perdidos)))

    base = {"antes": ra, "despues": rd, "character_id": cid, "personaje": nombre}
    for fid in nuevos:
        filas.append({**base, "coleccion": "completado", "feat_id": fid, "cambio": "nuevo",
                      "estaba_activo": bool(act_a and fid in act_a)})
    for fid in perdidos:
        filas.append({**base, "coleccion": "completado", "feat_id": fid, "cambio": "desaparecido",
                      "estaba_activo": ""})

    if None in (act_a, act_d):
        print("   activos: falta el archivo crudo de alguna de las dos corridas")
        return
    salieron, entraron = act_a - act_d, act_d - act_a
    a_completado = salieron & set(nuevos)
    print(f"   activos: {len(act_a)} -> {len(act_d)} (salieron {len(salieron)}, "
          f"entraron {len(entraron)}; de los que salieron, {len(a_completado)} pasaron a completados)")
    for fid in sorted(salieron):
        filas.append({**base, "coleccion": "activo", "feat_id": fid, "cambio": "salio",
                      "estaba_activo": True})
    for fid in sorted(entraron):
        filas.append({**base, "coleccion": "activo", "feat_id": fid, "cambio": "entro",
                      "estaba_activo": False})


def serie(runs: list[dict], campo: str) -> None:
    """Tabla con un campo de la ficha: una fila por corrida, una columna por personaje.

    '-' = el personaje no salio bien en esa corrida (o el campo no existia todavia).
    '*' = el valor cambio respecto de la ultima corrida en que el personaje salio bien.
    """
    # character_id -> nombre. Se recorre de la corrida mas nueva a la mas vieja para
    # que las columnas sigan el orden de config.toml y muestren el nombre actual.
    nombres: dict[str, str] = {}
    for m in reversed(runs):
        for cid, entry in ok_entries(m).items():
            nombres.setdefault(cid, entry["name"])
    anchos = {cid: max(len(n), 9) for cid, n in nombres.items()}

    print(f"{campo}\n")
    print("run_id".ljust(18) + "  ".join(n.rjust(anchos[cid]) for cid, n in nombres.items()))
    previo: dict[str, str] = {}
    for m in runs:
        ok = ok_entries(m)
        celdas = []
        for cid in nombres:
            valor = ok.get(cid, {}).get(campo)
            if valor is None:
                texto = "-"
            else:
                texto = str(valor) + ("*" if cid in previo and previo[cid] != valor else "")
                previo[cid] = valor
            celdas.append(texto.rjust(anchos[cid]))
        print(m["run_id"].ljust(18) + "  ".join(celdas))
    print("\n- = sin dato en esa corrida | * = cambio respecto del dato anterior")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diferencias entre corridas de extract.py")
    parser.add_argument("--antes", help="run_id de la corrida base")
    parser.add_argument("--despues", help="run_id de la corrida a comparar (por defecto, la ultima)")
    parser.add_argument("--listar", action="store_true", help="lista las corridas disponibles")
    parser.add_argument("--serie", choices=CAMPOS_SERIE,
                        help="muestra un campo de la ficha en todas las corridas")
    args = parser.parse_args()

    runs = load_runs()
    if not runs:
        raise SystemExit("No hay corridas en data/runs. Corre primero extract.py.")

    if args.listar:
        for m in runs:
            n_ok = len(ok_entries(m))
            print(f"{m['run_id']}  {n_ok}/{len(m['personajes'])} personajes ok")
        return 0
    if args.serie:
        serie(runs, args.serie)
        return 0
    if args.despues and not args.antes:
        raise SystemExit("--despues requiere --antes.")

    pairs = pick_pairs(runs, args.antes, args.despues)
    if not pairs:
        raise SystemExit("No hay dos corridas exitosas para comparar.")

    filas: list[dict] = []
    for cid, nombre, m_antes, m_despues in pairs:
        comparar(cid, nombre, m_antes, m_despues, filas)

    DIFFS_DIR.mkdir(parents=True, exist_ok=True)
    primera = min(p[2]["run_id"] for p in pairs)
    ultima = max(p[3]["run_id"] for p in pairs)
    out = DIFFS_DIR / f"{primera}__{ultima}.csv"
    columnas = ["antes", "despues", "character_id", "personaje", "coleccion",
                "feat_id", "cambio", "estaba_activo"]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(filas)
    print(f"\nDetalle: {out} ({len(filas)} filas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
