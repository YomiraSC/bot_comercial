# scoring_ml.py
# ============================================================
# Modelo de ML (LightGBM) para priorización y scoring de leads.
# Reemplaza el scoring manual de scoring_prebot.py.
#
# VARIABLES DEL MODELO:
#   Linea, Origen, SubOrigen, Sede   (categóricas)
#   dias_desde_captura               (antigüedad del lead)
#   delay_asignacion_h               (horas entre captura y asignación)
#   estado_prospecto_score           (ordinal: avance en el embudo)
#   cita_info_score                  (ordinal: resultado de cita informativa)
#   tiene_cita_cierre                (binaria)
#   motivo_descarte_definitivo       (binaria: descarte permanente)
#   motivo_descarte_temporal         (binaria: descarte temporal)
#   es_reasignado                    (binaria)
#   has_prev_sale                    (binaria: venta anterior en fondos)
#
# USO:
#   python scoring_ml.py                          # entrena + scorea (default)
#   python scoring_ml.py --mode score             # solo scorea con modelo guardado
#   python scoring_ml.py --test_months nov,dic    # cambia el mes de test
#   python scoring_ml.py --th_green 650           # ajusta umbrales de bucket
#
# INSTALACIÓN DE DEPENDENCIAS:
#   pip install lightgbm scikit-learn joblib
# ============================================================

from __future__ import annotations

import argparse
import warnings
from pathlib import Path
import re
import unicodedata
from datetime import datetime
from typing import Optional

import joblib
import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    HAS_ML = True
except ImportError:
    HAS_ML = False

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


# ============================================================
# CONSTANTES Y CONFIGURACIÓN DEL MODELO
# ============================================================

# Score ordinal por Estado Prospecto.
# Valores encontrados en los datos reales de Leads/.
ESTADO_PROSPECTO_SCORE: dict[str, int] = {
    "anulado":               0,
    "rechazado":             0,
    "firma expirada":        0,
    "firma cancelada":       0,
    "devuelto":              0,
    "contactado":           20,
    "con proforma":         35,
    "enviado a supervisor": 45,
    "enviado a adv":        50,
    "proforma aprobada":    55,
    "aprobado":             60,
    "pago parcial":         70,
    "pago completo":        80,
    "firmado":              85,
    # "inscrito" → filtrado como CERRADO antes del scoring
}
DEFAULT_ESTADO_PROSPECTO = 0  # sin estado asignado = lead recién llegado

# Motivo descarte definitivo: alta señal negativa.
MOTIVO_DEFINITIVO = frozenset({
    "fuera de zona",
    "ya compro",
    "numero no existe",
    "no interesado",
    "numero otra persona",
})

# Motivo descarte temporal: señal negativa moderada (puede cambiar).
MOTIVO_TEMPORAL = frozenset({
    "no contesta",
    "numero apagado",
})

# Score ordinal por Estado Cita Informativa.
CITA_INFO_SCORE: dict[str, int] = {
    "realizada":    2,
    "programada":   1,
    "no realizada": 0,
}
CITA_INFO_DEFAULT = -1  # sin registro de cita

# Tokens que indican lead cerrado (no se scorea).
CLOSED_TOKENS = ["inscri", "cancel", "descart", "resuelto", "cerrad", "won"]

# Columnas categóricas que entran al modelo (se encodean como int).
CAT_FEATURES = ["Linea_key", "Origen_key", "SubOrigen_key", "Sede_key"]

# Todas las features que entran al modelo (orden fijo).
FEATURE_COLS = [
    # categóricas
    "Linea_key",
    "Origen_key",
    "SubOrigen_key",
    "Sede_key",
    # numéricas / ordinales
    "dias_desde_captura",
    "delay_asignacion_h",
    "estado_prospecto_score",
    "cita_info_score",
    "tiene_cita_cierre",
    # binarias
    "motivo_descarte_definitivo",
    "motivo_descarte_temporal",
    "es_reasignado",
    "has_prev_sale",
]


# ============================================================
# UTILS
# ============================================================

def safe_str(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return str(x).strip()


def strip_accents(s: str) -> str:
    s = safe_str(s)
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def norm_text(x) -> str:
    s = strip_accents(safe_str(x)).lower().strip()
    return re.sub(r"\s+", " ", s)


def canon_linea_key(x) -> str:
    s = norm_text(x)
    if not s:
        return ""
    if "vehic" in s:
        return "vehiculos"
    if "inmueb" in s:
        return "inmuebles"
    return s


def norm_phone(x) -> str:
    s = safe_str(x)
    digits = re.sub(r"\D+", "", s)
    if not digits:
        return ""
    if digits.startswith("51") and len(digits) > 9:
        digits = digits[-9:]
    if digits.startswith("0") and len(digits) > 9:
        digits = digits[-9:]
    if len(digits) > 9:
        digits = digits[-9:]
    return digits


def norm_doc(x) -> str:
    return re.sub(r"\D+", "", safe_str(x))


def to_bool_reasignado(x) -> int:
    return 1 if norm_text(x) in ("si", "sí", "true", "1", "yes") else 0


def combine_date_time(date_col, time_col) -> pd.Series:
    """Combina columna de fecha + columna de hora en un datetime."""
    d = pd.to_datetime(date_col, errors="coerce")
    if time_col is None:
        return d
    t = time_col
    if pd.api.types.is_timedelta64_dtype(t):
        return d + t
    if pd.api.types.is_numeric_dtype(t):
        tn = pd.to_numeric(t, errors="coerce")
        mask = tn.between(0, 1)
        td = pd.to_timedelta(tn.where(mask, np.nan), unit="D")
        return d + td
    t_str = t.astype("string")
    t_dt = pd.to_datetime(t_str, errors="coerce")
    if t_dt.notna().any():
        td = (
            pd.to_timedelta(t_dt.dt.hour,   unit="h")
            + pd.to_timedelta(t_dt.dt.minute, unit="m")
            + pd.to_timedelta(t_dt.dt.second, unit="s")
        )
        return (d + td).where(d.notna(), pd.NaT)
    return d


# ============================================================
# CATEGORY ENCODER
# ============================================================

class CatEncoder:
    """
    Encoder str → int para columnas categóricas.
    - fit()       : aprende el mapeo desde datos de entrenamiento.
    - transform() : aplica el mapeo. Valores desconocidos → -1
                    (LightGBM los trata como NaN/missing, comportamiento correcto).
    El encoder se guarda junto al modelo en el .pkl para garantizar
    consistencia entre training y scoring.
    """

    def __init__(self) -> None:
        self.mappings: dict[str, dict[str, int]] = {}

    def fit(self, df: pd.DataFrame, cols: list[str]) -> "CatEncoder":
        for col in cols:
            unique_vals = sorted(df[col].fillna("").astype(str).unique())
            self.mappings[col] = {v: i for i, v in enumerate(unique_vals)}
        return self

    def transform(self, df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        df = df.copy()
        for col in cols:
            m = self.mappings.get(col, {})
            df[col] = df[col].fillna("").astype(str).map(
                lambda x, _m=m: _m.get(x, -1)
            )
        return df

    def fit_transform(self, df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        self.fit(df, cols)
        return self.transform(df, cols)


# ============================================================
# LOADERS
# ============================================================

def read_excels_in_folder(folder: Path, prefix: str) -> list[Path]:
    if not folder.exists():
        return []
    files = sorted([p for p in folder.glob("*.xlsx") if not p.name.startswith("~$")])
    if files:
        print(f"[OK]   {prefix}: {len(files)} archivos en {folder}")
    else:
        print(f"[WARN] No hay xlsx en {folder}")
    return files


def load_leads(files: list[Path]) -> pd.DataFrame:
    """
    Carga los archivos de Leads y genera columnas normalizadas.
    Extiende la versión de prebot con: Fecha Descarte, Motivo Descarte,
    Estado Cita Informativa, Estado Cita Cierre.
    """
    usecols = [
        "Telefono", "Email",
        "Fecha Asignacion", "Hora Asignacion",
        "Fecha Redes Sociales", "Hora Redes Sociales",
        "Fecha Descarte", "Motivo Descarte",
        "Anuncio", "Campaña", "Linea", "Origen", "SubOrigen", "Sede",
        "Es Reasignado", "Estado", "Estado Prospecto",
        "Estado Cita Informativa", "Estado Cita Cierre",
    ]
    dfs = []
    for f in files:
        try:
            df = pd.read_excel(f, usecols=lambda c: c in usecols)
            df["__file__"] = f.name
            dfs.append(df)
        except Exception as e:
            print(f"[WARN] No pude leer {f.name}: {e}")
    if not dfs:
        return pd.DataFrame()

    out = pd.concat(dfs, ignore_index=True)

    # ── Identificadores ──────────────────────────────────
    out["phone_norm"] = out.get("Telefono", pd.Series(dtype=str)).apply(norm_phone)
    out["reasignado"] = out.get("Es Reasignado", pd.Series(dtype=str)).apply(to_bool_reasignado)

    # ── Categorías con clave normalizada ─────────────────
    for c in ["Anuncio", "Campaña", "Linea", "Origen", "SubOrigen", "Sede"]:
        col = out.get(c, pd.Series(dtype=str))
        out[c] = col.astype("string").fillna("").map(safe_str)
        out[c + "_key"] = out[c].map(canon_linea_key if c == "Linea" else norm_text)

    # ── Fechas y horas ───────────────────────────────────
    out["dt_asignacion"] = combine_date_time(
        out.get("Fecha Asignacion"), out.get("Hora Asignacion")
    )
    out["dt_rs"] = combine_date_time(
        out.get("Fecha Redes Sociales"), out.get("Hora Redes Sociales")
    )
    out["delay_h"] = (out["dt_asignacion"] - out["dt_rs"]).dt.total_seconds() / 3600.0
    out["delay_h"] = out["delay_h"].where(out["delay_h"].between(-24, 24 * 30), np.nan)

    # ── Estados ──────────────────────────────────────────
    out["estado_norm"] = (
        out.get("Estado", pd.Series(dtype=str))
        .astype("string").str.lower().str.strip().fillna("")
    )
    out["estado_prospecto_norm"] = (
        out.get("Estado Prospecto", pd.Series(dtype=str))
        .astype("string").str.lower().str.strip().fillna("")
    )
    out["motivo_descarte_norm"] = (
        out.get("Motivo Descarte", pd.Series(dtype=str))
        .astype("string").fillna("").map(norm_text)
    )
    out["estado_cita_info_norm"] = (
        out.get("Estado Cita Informativa", pd.Series(dtype=str))
        .astype("string").fillna("").map(norm_text)
    )
    out["estado_cita_cierre_norm"] = (
        out.get("Estado Cita Cierre", pd.Series(dtype=str))
        .astype("string").fillna("").map(norm_text)
    )

    return out


def load_prospectos(files: list[Path]) -> pd.DataFrame:
    usecols = [
        "Telefono", "Documento", "Fojas", "Certificado Total",
        "Estado", "Fecha Inscripción", "Fecha Registro",
    ]
    dfs = []
    for f in files:
        try:
            df = pd.read_excel(f, usecols=lambda c: c in usecols)
            df["__file__"] = f.name
            dfs.append(df)
        except Exception as e:
            print(f"[WARN] No pude leer {f.name}: {e}")
    if not dfs:
        return pd.DataFrame()

    out = pd.concat(dfs, ignore_index=True)
    out["phone_norm"]  = out.get("Telefono",  pd.Series(dtype=str)).apply(norm_phone)
    out["doc_norm"]    = out.get("Documento", pd.Series(dtype=str)).apply(norm_doc)
    out["estado_norm"] = (
        out.get("Estado", pd.Series(dtype=str))
        .astype("string").str.lower().str.strip().fillna("")
    )
    out["fecha_insc"] = pd.to_datetime(out.get("Fecha Inscripción"), errors="coerce")
    out["foja_norm"]  = out.get("Fojas",            pd.Series(dtype=str)).astype("string").str.strip().fillna("")
    out["cert_norm"]  = out.get("Certificado Total", pd.Series(dtype=str)).astype("string").str.strip().fillna("")

    # Busca "Fecha Registro" aunque el nombre varíe levemente
    fecha_reg_col = next(
        (c for c in out.columns if "fecha registro" in norm_text(c)), None
    )
    out["fecha_registro"] = (
        pd.to_datetime(out[fecha_reg_col], errors="coerce")
        if fecha_reg_col else pd.NaT
    )
    return out


def load_ventas(files: list[Path]) -> pd.DataFrame:
    usecols = ["Foja", "Certificado", "Producto", "Origen_Venta", "Ciudad"]
    dfs = []
    for f in files:
        try:
            df = pd.read_excel(f, usecols=lambda c: c in usecols)
            df["__file__"] = f.name
            dfs.append(df)
        except Exception as e:
            print(f"[WARN] No pude leer {f.name}: {e}")
    if not dfs:
        return pd.DataFrame()
    out = pd.concat(dfs, ignore_index=True)
    out["foja_norm"] = out.get("Foja",        pd.Series(dtype=str)).astype("string").str.strip().fillna("")
    out["cert_norm"] = out.get("Certificado", pd.Series(dtype=str)).astype("string").str.strip().fillna("")
    return out


def load_fondos(fondos_path: Path) -> pd.DataFrame:
    if not fondos_path.exists():
        return pd.DataFrame()
    usecols = [
        "Linea", "Cta_Inscripcion_con_IGV", "N_Doc",
        "Estado_Asociado", "Estado_Adjudicado", "Fec_Inscripcion",
    ]
    try:
        df = pd.read_excel(fondos_path, usecols=lambda c: c in usecols)
    except Exception:
        df = pd.read_excel(fondos_path)

    df["Linea"]     = df.get("Linea", pd.Series(dtype=str)).astype("string").fillna("").map(safe_str)
    df["Linea_key"] = df["Linea"].map(canon_linea_key)
    if "Cta_Inscripcion_con_IGV" in df.columns:
        df["Cta_Inscripcion_con_IGV"] = pd.to_numeric(df["Cta_Inscripcion_con_IGV"], errors="coerce")
    df["doc_norm"]               = df.get("N_Doc",             pd.Series(dtype=str)).apply(norm_doc)
    df["estado_asociado_norm"]   = df.get("Estado_Asociado",   pd.Series(dtype=str)).astype("string").fillna("").map(norm_text)
    df["estado_adjudicado_norm"] = df.get("Estado_Adjudicado", pd.Series(dtype=str)).astype("string").fillna("").map(norm_text)
    # Buscar específicamente Fec_Inscripcion: debe empezar con "fec" y contener "inscrip".
    # Esto evita matchear Cta_Inscripcion_con_IGV (columna de montos en soles)
    # que también contiene "inscrip" y cuyos valores numéricos se parseaban
    # incorrectamente como timestamps epoch (01/01/1970).
    fec_col = next(
        (c for c in df.columns
         if norm_text(c).startswith("fec") and "inscrip" in norm_text(c)),
        None,
    )
    if fec_col:
        raw_fec = pd.to_datetime(df[fec_col], errors="coerce")
        # Guardia extra: celdas vacías guardadas como 0 → epoch 1970 → NaT.
        df["fec_insc_fondos"] = raw_fec.where(raw_fec > pd.Timestamp("2000-01-01"), pd.NaT)
    else:
        df["fec_insc_fondos"] = pd.NaT
    return df


# ============================================================
# LABELING (igual que prebot — no se modifica la lógica)
# ============================================================

def build_won_maps(pros: pd.DataFrame, ven: pd.DataFrame) -> dict[str, int]:
    """
    Construye un mapa {phone_norm → is_won}.
    Un lead se considera WON si en Prospectos tiene estado inscrito,
    fecha de inscripción, o aparece en el cruce Foja+Certificado de Ventas.
    """
    if pros.empty:
        return {}
    ven_pairs = (
        set(zip(ven["foja_norm"].astype(str), ven["cert_norm"].astype(str)))
        if not ven.empty else set()
    )

    def is_won_row(r) -> bool:
        est = safe_str(r.get("estado_norm", ""))
        if "inscri" in est:
            return True
        if pd.notna(r.get("fecha_insc", pd.NaT)):
            return True
        foja = safe_str(r.get("foja_norm", ""))
        cert = safe_str(r.get("cert_norm", ""))
        return bool(foja and cert and (foja, cert) in ven_pairs)

    pros = pros.copy()
    pros["is_won"] = pros.apply(is_won_row, axis=1).astype(int)
    m = pros.groupby("phone_norm")["is_won"].max().to_dict()
    return {k: int(v) for k, v in m.items() if k}


def build_prev_sale_info(
    pros: pd.DataFrame, fondos: pd.DataFrame
) -> tuple[dict, dict]:
    """
    Retorna:
      doc_to_fec_fondos  → {doc_norm: fecha_inscripcion_fondos}
      phone_to_doc       → {phone_norm: doc_norm}
    Solo incluye registros ACTIVOS y No adjudicados en Fondos.
    """
    if pros.empty or fondos.empty:
        return {}, {}

    f = fondos[fondos["doc_norm"].astype(str).str.len() > 0].copy()
    f = f[f["fec_insc_fondos"].notna()]
    # Cualquier inscripción con fecha válida cuenta como venta anterior,
    # sin importar si está adjudicado o no. El filtro previo
    # (ACTIVO + No adjudicado) dejaba solo 311 DNIs → 2 leads en el dataset.
    cond_activo  = f["estado_asociado_norm"].str.contains(r"\bactivo\b", regex=True, na=False)
    #cond_no_adj  = f["estado_adjudicado_norm"].str.contains("no adjudicado", na=False)
    f_elig       = f.loc[cond_activo]
    doc_to_fec   = f_elig.groupby("doc_norm")["fec_insc_fondos"].min().to_dict()

    p = pros[pros["phone_norm"].astype(str).str.len() > 0].copy()
    p = p[p["doc_norm"].astype(str).str.len() > 0]
    phone_to_doc = (
        p.groupby("phone_norm")["doc_norm"]
        .agg(lambda s: next((x for x in s.astype(str) if x), ""))
        .to_dict()
    )
    return doc_to_fec, phone_to_doc


def attach_prev_sale(
    leads: pd.DataFrame,
    prospectos: pd.DataFrame,
    fondos: pd.DataFrame,
) -> pd.DataFrame:
    """
    Agrega al DataFrame de leads:
      doc_norm, has_prev_sale, fec_insc_fondos, dt_lead.
    has_prev_sale = 1 si se cumplen las tres condiciones:
      1. El lead tiene Fecha Asignacion (dt_asignacion) conocida.
      2. Existe Fec_Inscripcion en Fondos para el DNI del lead (fec_insc_fondos notna).
      3. Fec_Inscripcion (Fondos) < Fecha Asignacion (Lead).
    """
    leads = leads.copy()
    leads["doc_norm"]        = ""
    leads["has_prev_sale"]   = 0
    leads["fec_insc_fondos"] = pd.NaT
    leads["fecha_reg_prosp"] = pd.NaT

    # fecha de referencia del lead (prioridad: Fecha Registro en Prospectos)
    if not prospectos.empty and "fecha_registro" in prospectos.columns:
        phone_to_reg = prospectos.groupby("phone_norm")["fecha_registro"].min().to_dict()
        leads["fecha_reg_prosp"] = leads["phone_norm"].map(
            lambda p: phone_to_reg.get(p, pd.NaT)
        )
    leads["dt_lead"] = (
        leads["fecha_reg_prosp"].fillna(leads["dt_rs"]).fillna(leads["dt_asignacion"])
    )

    if not prospectos.empty and not fondos.empty:
        doc_to_fec, phone_to_doc = build_prev_sale_info(prospectos, fondos)
        leads["doc_norm"]        = leads["phone_norm"].map(
            lambda p: safe_str(phone_to_doc.get(p, ""))
        )
        leads["fec_insc_fondos"] = leads["doc_norm"].map(
            lambda d: doc_to_fec.get(d, pd.NaT)
        )
        # Condición explícita: Fec_Inscripcion (Fondos) < Fecha Asignacion (Lead)
        leads["has_prev_sale"] = (
            leads["dt_asignacion"].notna()
            & leads["fec_insc_fondos"].notna()
            & (
                leads["fec_insc_fondos"].dt.floor("D")
                < leads["dt_asignacion"].dt.floor("D")
            )
        ).astype(int)

    return leads


# ============================================================
# FEATURE ENGINEERING
# ============================================================

def engineer_features(
    leads: pd.DataFrame,
    ref_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Agrega todas las columnas de features al DataFrame de leads.
    ref_date: fecha de referencia para calcular antigüedad.
              Si None, usa datetime.now() (scoring en tiempo real).
    """
    df = leads.copy()
    now = pd.Timestamp(ref_date or datetime.now())

    # ── Antigüedad del lead ──────────────────────────────
    # Días desde la Fecha Asignacion hasta el momento del scoring.
    # Cap a 730 días (2 años). -1 si la fecha no existe.
    raw_days = (now - df["dt_asignacion"]).dt.total_seconds() / 86400.0
    df["dias_desde_captura"] = raw_days.clip(0, 730).fillna(-1)

    # ── Delay de asignación (ya existe como delay_h) ─────
    df["delay_asignacion_h"] = pd.to_numeric(df["delay_h"], errors="coerce")

    # ── Estado Prospecto → score ordinal ─────────────────
    # Cuanto más avanzado en el embudo, mayor score.
    df["estado_prospecto_score"] = df["estado_prospecto_norm"].map(
        lambda x: ESTADO_PROSPECTO_SCORE.get(norm_text(x), DEFAULT_ESTADO_PROSPECTO)
    )

    # ── Motivo Descarte → flags binarias ─────────────────
    df["motivo_descarte_definitivo"] = df["motivo_descarte_norm"].map(
        lambda x: 1 if norm_text(x) in MOTIVO_DEFINITIVO else 0
    )
    df["motivo_descarte_temporal"] = df["motivo_descarte_norm"].map(
        lambda x: 1 if norm_text(x) in MOTIVO_TEMPORAL else 0
    )

    # ── Cita Informativa → score ordinal ─────────────────
    # Realizada=2, Programada=1, No realizada=0, sin cita=-1
    df["cita_info_score"] = df["estado_cita_info_norm"].map(
        lambda x: CITA_INFO_SCORE.get(norm_text(x), CITA_INFO_DEFAULT)
    )

    # ── Cita Cierre → binaria ────────────────────────────
    df["tiene_cita_cierre"] = df["estado_cita_cierre_norm"].map(
        lambda x: 0 if not norm_text(x) else 1
    )

    # ── Reasignado → binaria ─────────────────────────────
    df["es_reasignado"] = df.get("reasignado", pd.Series(0, index=df.index))

    # ── Categóricas: asegurar string limpio ──────────────
    for c in CAT_FEATURES:
        df[c] = df[c].fillna("").astype(str)

    return df


# ============================================================
# CERRADOS
# ============================================================

def is_closed_row(r) -> bool:
    """Detecta leads ya cerrados (inscriptos, cancelados, descartados, etc.)."""
    e  = safe_str(r.get("estado_norm", ""))
    ep = safe_str(r.get("estado_prospecto_norm", ""))
    return (
        any(t in e  for t in CLOSED_TOKENS)
        or any(t in ep for t in CLOSED_TOKENS)
    )


# ============================================================
# ML — ENTRENAMIENTO
# ============================================================

def train_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
) -> "lgb.LGBMClassifier":
    """
    Entrena LightGBM con class_weight='balanced' para corregir
    el desbalanceo típico de leads (pocos convertidos vs muchos no).
    Los hiperparámetros son un punto de partida robusto;
    se pueden optimizar con --tune si el dataset crece.
    """
    model = lgb.LGBMClassifier(
        n_estimators=500,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=10,       # evita overfitting con pocos datos
        subsample=0.8,
        colsample_bytree=0.8,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    model.fit(X_train, y_train)
    return model


# ============================================================
# ML — EVALUACIÓN
# ============================================================

def print_lift_table(
    y_true: pd.Series,
    scores: pd.Series,
    th_green: float,
    th_yellow: float,
    title: str = "LIFT TABLE",
) -> None:
    """
    Imprime una tabla de lift por bucket (VERDE / AMARILLO / ROJO).
    Muestra tasa de conversión y lift vs base de cada grupo.
    """
    buckets = pd.Series(
        np.where(scores >= th_green,  "VERDE",
        np.where(scores >= th_yellow, "AMARILLO", "ROJO")),
        index=y_true.index,
    )
    base = float(y_true.mean())
    g = (
        pd.DataFrame({"Bucket": buckets, "label": y_true})
        .groupby("Bucket")["label"]
        .agg(count="count", wins="sum", conv_rate="mean")
    )
    g["lift_vs_base"] = (g["conv_rate"] / base).round(2)
    g["conv_rate"]    = g["conv_rate"].round(4)
    rank_map = {"VERDE": 1, "AMARILLO": 2, "ROJO": 3}
    g = g.sort_values("Bucket", key=lambda s: s.map(rank_map).fillna(99))
    print(f"\n  {title}")
    print(f"  Base conversión : {base:.4f}  ({int(y_true.sum())} wins / {len(y_true)} leads)")
    print(g.to_string())


def evaluate_model(
    model: "lgb.LGBMClassifier",
    X_test: pd.DataFrame,
    y_test: pd.Series,
    th_green: float,
    th_yellow: float,
) -> None:
    """
    Imprime AUC-ROC, lift table y ranking de importancia de variables.
    """
    proba = model.predict_proba(X_test)[:, 1]
    auc   = roc_auc_score(y_test, proba)

    print("\n=========== EVALUACIÓN ML — TEST SET ===========")
    print(f"  Leads en test   : {len(y_test)}")
    print(f"  Convertidos     : {int(y_test.sum())} ({y_test.mean():.2%})")
    print(f"  AUC-ROC         : {auc:.4f}  (1.0=perfecto | 0.5=aleatorio)")

    scores_test = pd.Series(proba * 1000, index=X_test.index)
    print_lift_table(y_test, scores_test, th_green, th_yellow, "LIFT TABLE (test set)")

    # ── Importancia de variables ─────────────────────────
    feat_imp = pd.Series(
        model.feature_importances_, index=X_test.columns
    ).sort_values(ascending=False)
    max_imp = feat_imp.max() if feat_imp.max() > 0 else 1
    print("\n  Importancia de variables (gain — mayor = más útil para el modelo):")
    for feat, imp in feat_imp.items():
        bar = "█" * int(imp / max_imp * 25)
        print(f"    {feat:<35} {bar}  ({imp:.0f})")
    print("=================================================\n")


# ============================================================
# ML — SCORING Y BUCKETS
# ============================================================

def assign_buckets(
    scores: pd.Series,
    th_green: float,
    th_yellow: float,
) -> pd.Series:
    """
    Asigna bucket por umbral de score (0-1000).
    Fallback automático si salen 0 VERDES con los umbrales dados.
    """
    s = pd.to_numeric(scores, errors="coerce").fillna(0.0)
    buckets = pd.Series(
        np.where(s >= th_green,  "VERDE",
        np.where(s >= th_yellow, "AMARILLO", "ROJO")),
        index=scores.index,
    )
    if (buckets == "VERDE").sum() == 0 and len(buckets) > 0:
        auto_green  = float(s.quantile(0.85))
        auto_yellow = float(s.quantile(0.50))
        print(
            f"[WARN] 0 VERDES con th_green={th_green:.0f}. "
            f"Ajuste automático → th_green={auto_green:.1f}, th_yellow={auto_yellow:.1f}"
        )
        buckets = pd.Series(
            np.where(s >= auto_green,  "VERDE",
            np.where(s >= auto_yellow, "AMARILLO", "ROJO")),
            index=scores.index,
        )
    return buckets


# ============================================================
# MAIN
# ============================================================

def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="Scoring ML de leads con LightGBM.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["train_and_score", "score"],
        default="train_and_score",
        help=(
            "train_and_score : entrena con histórico y scorea todos los leads (default).\n"
            "score           : carga modelo guardado y scorea leads nuevos.\n"
        ),
    )
    parser.add_argument(
        "--model_file", default="modelo_lgbm.pkl",
        help="Archivo .pkl donde se guarda/carga el modelo (default: modelo_lgbm.pkl).",
    )
    parser.add_argument(
        "--leads_file", default="",
        help="Nombre exacto de un xlsx de Leads para filtrar (opcional).",
    )
    parser.add_argument(
        "--test_months", default="dic",
        help=(
            "Subcadenas del nombre de archivo para el test set.\n"
            "Ej: 'dic'  o  'dic,nov'  (separadas por coma)."
        ),
    )
    parser.add_argument(
        "--th_green",  type=float, default=700.0,
        help="Umbral Score para VERDE   (0-1000, default=700).",
    )
    parser.add_argument(
        "--th_yellow", type=float, default=450.0,
        help="Umbral Score para AMARILLO (0-1000, default=450).",
    )
    parser.add_argument(
        "--out", default="output_ml",
        help="Carpeta de salida (default: output_ml/).",
    )
    parser.add_argument(
        "--no_eval", action="store_true",
        help="Omite la evaluación (lift table y AUC).",
    )
    args = parser.parse_args()

    # ── Validar dependencias ─────────────────────────────
    if not HAS_ML:
        raise SystemExit(
            "\n[ERROR] Faltan librerías. Instala con:\n"
            "  pip install lightgbm scikit-learn joblib\n"
        )

    # ── Rutas ────────────────────────────────────────────
    base_dir    = Path(__file__).resolve().parent
    leads_dir   = base_dir / "Leads"
    pros_dir    = base_dir / "Prospectos"
    ven_dir     = base_dir / "Ventas"
    fondos_path = base_dir / "bd_fondos.xlsx"
    out_dir     = base_dir / args.out
    model_path  = base_dir / args.model_file
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Cargar datos ──────────────────────────────────────
    all_lead_files = read_excels_in_folder(leads_dir, "Leads")
    if not all_lead_files:
        raise SystemExit("[ERROR] No hay archivos en Leads/. Revisa la carpeta.")

    if args.leads_file:
        all_lead_files = [p for p in all_lead_files if p.name == args.leads_file]
        if not all_lead_files:
            raise SystemExit(f"[ERROR] No encontré '{args.leads_file}' en {leads_dir}.")

    pros_files = read_excels_in_folder(pros_dir, "Prospectos")
    ven_files  = read_excels_in_folder(ven_dir,  "Ventas")

    prospectos = load_prospectos(pros_files) if pros_files else pd.DataFrame()
    ventas     = load_ventas(ven_files)      if ven_files  else pd.DataFrame()
    fondos     = load_fondos(fondos_path)    if fondos_path.exists() else pd.DataFrame()

    print(
        f"[OK]   Prospectos: {len(prospectos)} "
        f"| Ventas: {len(ventas)} "
        f"| Fondos: {len(fondos)}"
    )

    # ── Mapa de conversiones (phone → is_won) ─────────────
    won_map: dict[str, int] = {}
    if not prospectos.empty:
        won_map = build_won_maps(prospectos, ventas)
        print(f"[OK]   Conversiones detectadas en Prospectos: {sum(won_map.values())}")

    # ── Separar archivos train / test ─────────────────────
    test_keys = [k.strip().lower() for k in args.test_months.split(",")]

    def is_test_file(p: Path) -> bool:
        return any(k in p.stem.lower() for k in test_keys)

    train_files = [p for p in all_lead_files if not is_test_file(p)]
    test_files  = [p for p in all_lead_files if is_test_file(p)]

    # ── Encoder y modelo ─────────────────────────────────
    cat_encoder: CatEncoder = CatEncoder()
    model: Optional["lgb.LGBMClassifier"] = None

    # ==========================================================
    # MODO: train_and_score
    # ==========================================================
    if args.mode == "train_and_score":
        print(f"\n[TRAIN] Archivos: {[p.name for p in train_files]}")
        print(f"[TEST]  Archivos: {[p.name for p in test_files]}")

        if not train_files:
            raise SystemExit(
                "[ERROR] No hay archivos para training. "
                "Revisa --test_months (actualmente: '{}').".format(args.test_months)
            )

        # ── Cargar y preparar training ─────────────────────
        leads_tr = load_leads(train_files)
        leads_tr = attach_prev_sale(leads_tr, prospectos, fondos)
        leads_tr = engineer_features(leads_tr)
        leads_tr["label"] = leads_tr["phone_norm"].map(
            lambda p: won_map.get(p, np.nan)
        )

        train_df = leads_tr[leads_tr["label"].notna()].copy()
        train_df["label"] = train_df["label"].astype(int)

        n_total = len(leads_tr)
        n_lab   = len(train_df)
        n_pos   = int(train_df["label"].sum())
        n_neg   = n_lab - n_pos

        print(
            f"\n[TRAIN] {n_total} leads cargados | "
            f"{n_lab} etiquetados | "
            f"{n_pos} convertidos ({n_pos / max(n_lab, 1):.2%}) | "
            f"{n_neg} no convertidos"
        )

        if n_lab < 50:
            raise SystemExit(
                f"[ERROR] Solo {n_lab} leads etiquetados en training. "
                "Se necesitan al menos 50. Agrega más archivos de leads o prospectos."
            )
        if n_pos == 0:
            raise SystemExit(
                "[ERROR] 0 conversiones en el training set. "
                "El modelo no puede aprender. "
                "Revisa el cruce con Prospectos/Ventas."
            )

        # ── Encode + entrenamiento ─────────────────────────
        X_train = cat_encoder.fit_transform(train_df[FEATURE_COLS], CAT_FEATURES)
        X_train = X_train[FEATURE_COLS]
        y_train = train_df["label"]

        print("[INFO] Entrenando LightGBM...")
        model = train_model(X_train, y_train)

        # Guarda modelo + encoder juntos para garantizar consistencia
        joblib.dump({"model": model, "cat_encoder": cat_encoder}, model_path)
        print(f"[OK]   Modelo guardado en: {model_path}")

        # ── Evaluación en test set ─────────────────────────
        if not args.no_eval and test_files:
            leads_te = load_leads(test_files)
            leads_te = attach_prev_sale(leads_te, prospectos, fondos)
            leads_te = engineer_features(leads_te)
            leads_te["label"] = leads_te["phone_norm"].map(
                lambda p: won_map.get(p, np.nan)
            )

            test_df = leads_te[leads_te["label"].notna()].copy()
            test_df["label"] = test_df["label"].astype(int)

            if len(test_df) > 0 and test_df["label"].sum() > 0:
                X_test = cat_encoder.transform(test_df[FEATURE_COLS], CAT_FEATURES)
                X_test = X_test[FEATURE_COLS]
                evaluate_model(model, X_test, test_df["label"], args.th_green, args.th_yellow)
            else:
                print(
                    f"[WARN] Test set tiene {len(test_df)} leads "
                    f"con {int(test_df['label'].sum()) if len(test_df) else 0} conversiones. "
                    "No hay suficientes para evaluar."
                )
        elif not test_files:
            print(
                "[WARN] No se identificaron archivos de test. "
                f"Revisa --test_months (actualmente: '{args.test_months}')."
            )

    # ==========================================================
    # MODO: score (carga modelo ya entrenado)
    # ==========================================================
    else:
        if not model_path.exists():
            raise SystemExit(
                f"[ERROR] No encontré el modelo en '{model_path}'.\n"
                "Primero ejecuta con --mode train_and_score."
            )
        artifact    = joblib.load(model_path)
        model       = artifact["model"]
        cat_encoder = artifact["cat_encoder"]
        print(f"[OK]   Modelo cargado desde: {model_path}")

    # ==========================================================
    # SCORING — se aplica a TODOS los leads
    # ==========================================================
    print("\n[INFO] Scoring de leads...")

    all_leads = load_leads(all_lead_files)
    if all_leads.empty:
        raise SystemExit("[ERROR] No se cargaron leads para scoring.")

    all_leads = attach_prev_sale(all_leads, prospectos, fondos)
    all_leads = engineer_features(all_leads)
    all_leads["label"] = all_leads["phone_norm"].map(
        lambda p: won_map.get(p, np.nan)
    )

    X_score = cat_encoder.transform(all_leads[FEATURE_COLS], CAT_FEATURES)[FEATURE_COLS]
    all_leads["p_convert_ml"] = model.predict_proba(X_score)[:, 1]
    all_leads["Score_ML"]     = (all_leads["p_convert_ml"] * 1000).clip(0, 1000).round(2)

    # ── Detectar cerrados ─────────────────────────────────
    all_leads["is_closed"] = all_leads.apply(is_closed_row, axis=1)

    # ── Asignar buckets (solo accionables) ───────────────
    all_leads["Bucket"] = "CERRADO"
    mask_open = ~all_leads["is_closed"]
    if mask_open.any():
        all_leads.loc[mask_open, "Bucket"] = assign_buckets(
            all_leads.loc[mask_open, "Score_ML"],
            args.th_green,
            args.th_yellow,
        )

    rank_map = {"VERDE": 1, "AMARILLO": 2, "ROJO": 3, "CERRADO": 4}
    all_leads["BucketRank"] = all_leads["Bucket"].map(rank_map).fillna(99).astype(int)

    # ── Exportar a Excel ─────────────────────────────────
    export_cols = [c for c in [
        # identificación y auditoría
        "__file__",
        "Telefono", "phone_norm", "doc_norm",
        "dt_rs", "dt_asignacion", "dt_lead", "fec_insc_fondos",
        # segmentación original
        "Campaña", "Linea", "Origen", "SubOrigen", "Sede",
        # estados y motivos (para auditoría)
        "Estado", "Estado Prospecto", "Motivo Descarte", "Es Reasignado",
        # features auditables (para que el equipo entienda el score)
        "dias_desde_captura",
        "delay_asignacion_h",
        "estado_prospecto_score",
        "cita_info_score",
        "tiene_cita_cierre",
        "motivo_descarte_definitivo",
        "motivo_descarte_temporal",
        "has_prev_sale",
        # output del modelo
        "p_convert_ml",
        "Score_ML",
        "Bucket",
        "BucketRank",
        # label para validación posterior
        "label",
    ] if c in all_leads.columns]

    out_xlsx = out_dir / "ranking_ml.xlsx"
    (
        all_leads
        .sort_values(["BucketRank", "Score_ML"], ascending=[True, False])
        [export_cols]
        .to_excel(out_xlsx, index=False)
    )
    print(f"[OK]   Exportado: {out_xlsx}")

    # ── Resumen de buckets ────────────────────────────────
    print("\n[RESUMEN BUCKETS]")
    print(all_leads["Bucket"].value_counts(dropna=False).to_string())

    # ── Lift sobre los leads scoreados con label conocida ─
    if not args.no_eval:
        eval_mask = ~all_leads["is_closed"] & all_leads["label"].notna()
        eval_df   = all_leads[eval_mask].copy()
        eval_df["label"] = eval_df["label"].astype(int)
        if len(eval_df) > 0 and eval_df["label"].sum() > 0:
            print_lift_table(
                eval_df["label"],
                eval_df["Score_ML"],
                args.th_green,
                args.th_yellow,
                "LIFT TABLE (todos los leads accionables con label conocida)",
            )
        else:
            print(
                "[INFO] No hay leads accionables con label conocida para el lift final. "
                "(Normal si scoreas leads nuevos sin historial en Prospectos.)"
            )


if __name__ == "__main__":
    main()
