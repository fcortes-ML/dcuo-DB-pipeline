"""
DCUO feats - extraccion desde la API Census (capa raw / landing).

Que hace:
  1. Lee de config.toml el mundo y la lista de personajes a extraer.
  2. Resuelve el character_id de cada personaje por nombre y mundo.
  3. Baja, por personaje: ficha (`character`), feats completados
     (`characters_completed_feat`) y feats activos (`characters_active_feat`).
  4. Guarda cada respuesta tal cual llego en data/raw/<coleccion>/dt=<fecha>/.
     Esa estructura es la que despues se sube igual a GCS.
  5. Valida cada respuesta y escribe un manifiesto de la corrida en data/runs/.

Uso:
  python extract.py

Requiere:
  - config.toml (copiar config.example.toml y completarlo).
  - Variable de entorno DCUO_SERVICE_ID con un service ID propio de Census.

Un personaje que falla no corta la corrida: queda marcado con status "error"
en el manifiesto y el script termina con codigo de salida 1.
"""

import json
import os
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import requests

# --- Configuracion -----------------------------------------------------------

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.toml"
RAW_DIR = ROOT / "data" / "raw"
RUNS_DIR = ROOT / "data" / "runs"

# Service ID propio de census.daybreakgames.com, leido de la variable de entorno
# DCUO_SERVICE_ID. Si no esta definida se usa "example", el publico de
# demostracion, que tiene un limite de pedidos muy bajo.
SERVICE_ID = os.environ.get("DCUO_SERVICE_ID", "example")
SERVICE_ID_LABEL = "example" if SERVICE_ID == "example" else "propio"
NAMESPACE = "dcuo:v1"
BASE_URL = f"https://census.daybreakgames.com/s:{SERVICE_ID}/json/get/{NAMESPACE}"

PAGE_SIZE = 5000       # filas por pedido. Census devuelve 1 fila si no se indica.
MIN_INTERVAL_S = 1.0   # pausa de cortesia entre pedidos (service ID propio)
MAX_RETRIES = 3
TIMEOUT_S = 30

# Campos de la ficha que se copian al manifiesto para revisar la corrida.
CAMPOS_RESUMEN = (
    "character_id", "level", "combat_rating", "skill_points", "max_feats",
    "alignment_id", "power_type_id", "movement_mode_id", "active",
)


def load_config() -> tuple[str, list[str]]:
    """Lee config.toml y devuelve (world_id, lista de personajes)."""
    if not CONFIG_PATH.exists():
        raise SystemExit(
            "No se encontro config.toml. Copia config.example.toml como "
            "config.toml y completa world_id y characters."
        )
    with CONFIG_PATH.open("rb") as f:
        config = tomllib.load(f)

    world_id = str(config.get("world_id", "")).strip()
    if not world_id.isdigit():
        raise SystemExit("config.toml: world_id tiene que ser un numero, por ejemplo 2.")

    characters = config.get("characters", [])
    if not isinstance(characters, list) or not characters:
        raise SystemExit("config.toml: characters tiene que ser una lista con al menos un nombre.")
    if not all(isinstance(c, str) and c.strip() for c in characters):
        raise SystemExit("config.toml: cada personaje tiene que ser un nombre entre comillas.")

    return world_id, [c.strip() for c in characters]


# --- Acceso a la API -----------------------------------------------------------

_last_request = 0.0

# Errores que la API devuelve dentro del JSON (con HTTP 200) y que son
# pasajeros: conviene reintentar en lugar de dar el personaje por fallido.
RETRYABLE_API_ERRORS = ("service_unavailable",)


class RetryableAPIError(Exception):
    """Error transitorio informado por la API dentro de la respuesta."""


def census_get(collection: str, params: dict) -> dict:
    """GET a una coleccion, respetando el intervalo minimo y con reintentos."""
    global _last_request
    url = f"{BASE_URL}/{collection}"
    for attempt in range(1, MAX_RETRIES + 1):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT_S)
            response.raise_for_status()
            data = response.json()
            if str(data.get("error", "")).lower() in RETRYABLE_API_ERRORS:
                raise RetryableAPIError(f"la API devolvio error: {data['error']}")
            return data
        except (requests.RequestException, ValueError, RetryableAPIError) as exc:
            if attempt == MAX_RETRIES:
                raise
            backoff = 5 * 2 ** (attempt - 1)
            print(f"   reintento {attempt}/{MAX_RETRIES - 1} en {backoff}s ({exc})")
            time.sleep(backoff)
    raise RuntimeError("inalcanzable")


def check_response(data: dict, list_key: str) -> None:
    """Controles comunes a toda respuesta de Census."""
    if "error" in data:
        raise RuntimeError(f"la API devolvio error: {data['error']}")
    if list_key not in data:
        raise RuntimeError(f"falta la clave '{list_key}' en la respuesta")
    if int(data["returned"]) != len(data[list_key]):
        raise RuntimeError(
            f"'returned' dice {data['returned']} pero llegaron {len(data[list_key])} filas"
        )


def fetch_all(collection: str, params: dict) -> tuple[list[dict], list[dict]]:
    """Trae todas las filas de una coleccion, paginando con c:start si hace falta.

    Devuelve (filas, respuestas crudas). Si una pagina llega llena se pide la
    siguiente; si c:start no funcionara, la segunda pagina repetiria la primera
    y se corta con error en lugar de duplicar filas en silencio.
    """
    list_key = f"{collection}_list"
    rows: list[dict] = []
    raws: list[dict] = []
    start = 0
    while True:
        page_params = {**params, "c:limit": PAGE_SIZE}
        if start:
            page_params["c:start"] = start
        data = census_get(collection, page_params)
        raws.append(data)
        check_response(data, list_key)
        page = data[list_key]
        if start and page and page[0] == rows[0]:
            raise RuntimeError(f"{collection}: c:start no pagina (la pagina repite la primera)")
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows, raws
        start += PAGE_SIZE


def check_feats(rows: list[dict], character_id: str) -> None:
    """Pertenencia al personaje, feat_id numerico y sin duplicados."""
    ids = []
    for row in rows:
        if row.get("character_id") != character_id:
            raise RuntimeError(f"fila de otro personaje: {row}")
        feat_id = str(row.get("feat_id", ""))
        if not feat_id.isdigit():
            raise RuntimeError(f"feat_id vacio o no numerico: {row}")
        ids.append(feat_id)
    duplicados = len(ids) - len(set(ids))
    if duplicados:
        raise RuntimeError(f"{duplicados} feat_id duplicados")


# --- Persistencia ------------------------------------------------------------------


def save_raw(collection: str, run_id: str, run_date: str, key: str,
             data: dict, page: int = 0) -> Path:
    """Guarda la respuesta sin tocar: data/raw/<coleccion>/dt=<fecha>/<run>__<key>__p<n>.json"""
    folder = RAW_DIR / collection / f"dt={run_date}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_id}__{key}__p{page}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# --- Corrida --------------------------------------------------------------------


def extraer_personaje(name: str, world_id: str, run_id: str, run_date: str) -> dict:
    """Extrae un personaje y devuelve su entrada para el manifiesto."""
    entry: dict = {"name": name}

    # 1. Ficha. c:limit > 1 para detectar nombres repetidos en el mismo mundo.
    data = census_get("character", {"name": name, "world_id": world_id, "c:limit": 10})
    check_response(data, "character_list")
    chars = data["character_list"]
    if len(chars) != 1:
        raise RuntimeError(
            f"se esperaba 1 personaje y llegaron {len(chars)} "
            "(revisar el nombre exacto, mayusculas incluidas, o el mundo)"
        )
    char = chars[0]
    character_id = char["character_id"]
    save_raw("character", run_id, run_date, character_id, data)
    entry.update({campo: char.get(campo) for campo in CAMPOS_RESUMEN})

    # 2 y 3. Feats completados y activos.
    for collection, campo in (
        ("characters_completed_feat", "n_completed"),
        ("characters_active_feat", "n_active"),
    ):
        rows, raws = fetch_all(collection, {"character_id": character_id})
        for page, raw in enumerate(raws):
            save_raw(collection, run_id, run_date, character_id, raw, page)
        check_feats(rows, character_id)
        entry[campo] = len(rows)
        entry[f"{campo}_pages"] = len(raws)

    return entry


def main() -> int:
    world_id, personajes = load_config()

    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    run_date = started.strftime("%Y-%m-%d")
    print(f"Corrida {run_id} | {len(personajes)} personajes | service ID {SERVICE_ID_LABEL}")

    manifest = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "service_id": SERVICE_ID_LABEL,
        "namespace": NAMESPACE,
        "world_id": world_id,
        "page_size": PAGE_SIZE,
        "personajes": [],
    }

    for name in personajes:
        print(f"\n{name}")
        try:
            entry = extraer_personaje(name, world_id, run_id, run_date)
            entry["status"] = "ok"
            print(
                f"   id {entry['character_id']} | nivel {entry['level']} | "
                f"SP {entry['skill_points']} | max_feats {entry['max_feats']} | "
                f"completados {entry['n_completed']} | activos {entry['n_active']}"
            )
        except Exception as exc:  # un personaje con error no corta la corrida
            entry = {"name": name, "status": "error", "error": str(exc)}
            print(f"   ERROR: {exc}")
        manifest["personajes"].append(entry)

    finished = datetime.now(timezone.utc)
    errores = sum(1 for e in manifest["personajes"] if e["status"] == "error")
    manifest["finished_at"] = finished.isoformat()
    manifest["duracion_s"] = round((finished - started).total_seconds(), 1)
    manifest["n_ok"] = len(personajes) - errores
    manifest["n_error"] = errores

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = RUNS_DIR / f"{run_id}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nListo en {manifest['duracion_s']}s: {manifest['n_ok']} ok, {errores} con error.")
    print(f"Manifiesto: {manifest_path}")
    return 1 if errores else 0


if __name__ == "__main__":
    raise SystemExit(main())
