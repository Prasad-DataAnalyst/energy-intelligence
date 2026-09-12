"""
content/data_storyteller.py — Data-to-Video Pipeline Engine
=============================================================
Ingests CSV / JSON / Excel files, performs statistical analysis to find
the most counterintuitive result, generates matplotlib chart specs, and
produces a structured video narrative with scene breakdown.

Pipeline:
  DataLoader → DataAnalyzer → ChartSpecGenerator → DataNarrativeGenerator
  all orchestrated by DataStorytellerEngine
"""
import csv
import json
import logging
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("brain.data_storyteller")

HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_VIDEO_STYLES = {
    "by_the_numbers": {"hook": "The numbers tell a story most people miss", "tone": "analytical"},
    "data_revealed":  {"hook": "What the data actually shows will surprise you", "tone": "investigative"},
    "shocking_stats": {"hook": "These statistics contradict everything you've been told", "tone": "confrontational"},
    "year_in_review": {"hook": "Looking at the full year of data reveals a shocking trend", "tone": "reflective"},
    "the_rankings":   {"hook": "We ranked everything so you don't have to", "tone": "authoritative"},
}

CHART_TYPES = {
    "bar":       {"use_when": "comparing categories", "lib": "matplotlib"},
    "line":      {"use_when": "showing trends over time", "lib": "matplotlib"},
    "scatter":   {"use_when": "showing correlations", "lib": "matplotlib"},
    "pie":       {"use_when": "showing proportions (max 6 slices)", "lib": "matplotlib"},
    "heatmap":   {"use_when": "showing matrix data", "lib": "matplotlib"},
    "histogram": {"use_when": "showing distributions", "lib": "matplotlib"},
}

TITLE_PATTERNS_DATA = [
    "What {dataset} Data Reveals About {topic}",
    "The {topic} Data Nobody Is Showing You",
    "I Analyzed {n_rows} {topic} Records — Here's What I Found",
    "{topic}: The Numbers Tell A Different Story",
    "The {year} {topic} Data Is Shocking",
    "What {n_years} Years of {topic} Data Reveals",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> Optional[float]:
    """Convert val to float; return None on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _is_numeric_column(values: List[Any]) -> bool:
    """Return True when ≥70 % of non-null values parse as float."""
    non_null = [v for v in values if v not in (None, "", "null", "NULL", "NA", "N/A")]
    if not non_null:
        return False
    parsed = sum(1 for v in non_null if _safe_float(v) is not None)
    return parsed / len(non_null) >= 0.7


def _is_temporal_column(name: str, values: List[Any]) -> bool:
    """Heuristic: column name contains date/time keywords or values parse as dates."""
    temporal_keywords = {"date", "time", "year", "month", "day", "timestamp", "dt", "created", "updated"}
    if any(kw in name.lower() for kw in temporal_keywords):
        return True
    # Sample up to 10 values and try dateutil / strptime
    samples = [v for v in values[:20] if v not in (None, "", "null")][:10]
    if not samples:
        return False
    parsed = 0
    for s in samples:
        try:
            from dateutil import parser as _du
            _du.parse(str(s))
            parsed += 1
        except Exception:
            # Try common formats manually
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y"):
                try:
                    datetime.strptime(str(s), fmt)
                    parsed += 1
                    break
                except ValueError:
                    continue
    return parsed / len(samples) >= 0.6


def _records_to_column_dict(data: List[Dict]) -> Dict[str, List[Any]]:
    """Convert list-of-dicts to dict-of-lists keyed by column name."""
    if not data:
        return {}
    col_dict: Dict[str, List[Any]] = {}
    for col in data[0].keys():
        col_dict[col] = [row.get(col) for row in data]
    return col_dict


# ---------------------------------------------------------------------------
# Class 1: DataLoader
# ---------------------------------------------------------------------------

class DataLoader:
    """Loads CSV / JSON / Excel files; tries pandas first, falls back to stdlib."""

    def load(self, file_path: str) -> Dict:
        """
        Detect file type and dispatch to the appropriate loader.

        Returns:
            {data, columns, row_count, file_type, summary_stats}
            or {} on unrecoverable failure.
        """
        path = Path(file_path)
        if not path.exists():
            log.error("DataLoader.load: file not found: %s", file_path)
            return {}
        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                result = self._load_csv(file_path)
            elif suffix in (".json", ".jsonl"):
                result = self._load_json(file_path)
            elif suffix in (".xlsx", ".xls", ".ods"):
                result = self._load_excel(file_path)
            else:
                # Attempt CSV as default
                log.warning("DataLoader.load: unknown extension %s, trying CSV", suffix)
                result = self._load_csv(file_path)
        except Exception as exc:
            log.exception("DataLoader.load: unexpected error for %s: %s", file_path, exc)
            return {}

        if not result:
            return {}
        result["file_type"] = suffix.lstrip(".")
        result["source_path"] = file_path
        return result

    # ------------------------------------------------------------------
    def _load_csv(self, path: str) -> Dict:
        """Load CSV using pandas; fall back to csv.DictReader."""
        try:
            import pandas as pd
            df = pd.read_csv(path, on_bad_lines="skip")
            data = df.to_dict(orient="records")
            summary = self._summarize_pd(df)
            return {
                "data": data,
                "columns": list(df.columns),
                "row_count": len(df),
                "summary_stats": summary,
            }
        except ImportError:
            pass
        except Exception as exc:
            log.warning("DataLoader._load_csv pandas failed (%s), trying stdlib", exc)

        # stdlib fallback
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                reader = csv.DictReader(fh)
                data = list(reader)
            if not data:
                return {"data": [], "columns": [], "row_count": 0, "summary_stats": {}}
            columns = list(data[0].keys())
            summary = self._summarize_stdlib(data, columns)
            return {"data": data, "columns": columns, "row_count": len(data), "summary_stats": summary}
        except Exception as exc:
            log.error("DataLoader._load_csv stdlib failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    def _load_json(self, path: str) -> Dict:
        """Load JSON; normalize nested structures with pandas if available."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = json.load(fh)
        except Exception as exc:
            log.error("DataLoader._load_json read failed: %s", exc)
            return {}

        # Normalize to list-of-dicts
        try:
            import pandas as pd
            if isinstance(raw, list):
                df = pd.json_normalize(raw)
            elif isinstance(raw, dict):
                # Try common wrappers
                for key in ("data", "results", "items", "records", "rows"):
                    if key in raw and isinstance(raw[key], list):
                        df = pd.json_normalize(raw[key])
                        break
                else:
                    df = pd.json_normalize([raw])
            else:
                log.warning("DataLoader._load_json: unexpected root type %s", type(raw))
                return {}
            data = df.to_dict(orient="records")
            summary = self._summarize_pd(df)
            return {"data": data, "columns": list(df.columns), "row_count": len(df), "summary_stats": summary}
        except ImportError:
            pass
        except Exception as exc:
            log.warning("DataLoader._load_json pandas normalize failed: %s", exc)

        # stdlib fallback — flatten shallowly
        try:
            if isinstance(raw, list):
                data = raw
            elif isinstance(raw, dict):
                for key in ("data", "results", "items", "records", "rows"):
                    if key in raw and isinstance(raw[key], list):
                        data = raw[key]
                        break
                else:
                    data = [raw]
            else:
                return {}
            if not data or not isinstance(data[0], dict):
                return {}
            columns = list(data[0].keys())
            summary = self._summarize_stdlib(data, columns)
            return {"data": data, "columns": columns, "row_count": len(data), "summary_stats": summary}
        except Exception as exc:
            log.error("DataLoader._load_json stdlib fallback failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    def _load_excel(self, path: str) -> Dict:
        """Load Excel with pandas or openpyxl; combine all sheets."""
        all_data: List[Dict] = []
        columns: List[str] = []

        try:
            import pandas as pd
            xl = pd.ExcelFile(path)
            frames = []
            for sheet in xl.sheet_names:
                try:
                    df_sheet = pd.read_excel(xl, sheet_name=sheet)
                    df_sheet["_sheet"] = sheet
                    frames.append(df_sheet)
                except Exception as exc:
                    log.warning("DataLoader._load_excel: sheet %s failed: %s", sheet, exc)
            if not frames:
                return {}
            df = pd.concat(frames, ignore_index=True)
            all_data = df.to_dict(orient="records")
            summary = self._summarize_pd(df)
            return {"data": all_data, "columns": list(df.columns), "row_count": len(df), "summary_stats": summary}
        except ImportError:
            pass
        except Exception as exc:
            log.warning("DataLoader._load_excel pandas failed: %s", exc)

        # openpyxl fallback
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            for ws in wb.worksheets:
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue
                header = [str(c) if c is not None else f"col_{i}" for i, c in enumerate(rows[0])]
                for row in rows[1:]:
                    record = {header[i]: (row[i] if i < len(row) else None) for i in range(len(header))}
                    record["_sheet"] = ws.title
                    all_data.append(record)
                    if not columns:
                        columns = header + ["_sheet"]
            wb.close()
            if not all_data:
                return {}
            summary = self._summarize_stdlib(all_data, columns)
            return {"data": all_data, "columns": columns, "row_count": len(all_data), "summary_stats": summary}
        except Exception as exc:
            log.error("DataLoader._load_excel openpyxl failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    def _summarize_pd(self, df) -> Dict:
        """Compute summary stats from a pandas DataFrame."""
        try:
            import pandas as pd
            numeric_cols = list(df.select_dtypes(include="number").columns)
            categorical_cols = list(df.select_dtypes(exclude="number").columns)
            missing_pct = float(df.isnull().mean().mean() * 100)
            date_range: Dict = {}
            for col in df.columns:
                try:
                    parsed = pd.to_datetime(df[col], infer_datetime_format=True, errors="coerce")
                    if parsed.notna().sum() > len(df) * 0.5:
                        date_range = {
                            "column": col,
                            "min": str(parsed.min()),
                            "max": str(parsed.max()),
                        }
                        break
                except Exception:
                    continue
            return {
                "row_count": len(df),
                "col_count": len(df.columns),
                "numeric_cols": numeric_cols,
                "categorical_cols": categorical_cols,
                "missing_pct": round(missing_pct, 2),
                "date_range": date_range,
            }
        except Exception as exc:
            log.warning("DataLoader._summarize_pd failed: %s", exc)
            return {}

    def _summarize_stdlib(self, data: List[Dict], columns: List[str]) -> Dict:
        """Compute summary stats using only stdlib."""
        if not data:
            return {}
        col_dict = _records_to_column_dict(data)
        numeric_cols = [c for c in columns if _is_numeric_column(col_dict.get(c, []))]
        categorical_cols = [c for c in columns if c not in numeric_cols]
        # Missing pct
        total_cells = len(data) * len(columns)
        missing = sum(1 for row in data for v in row.values() if v in (None, "", "null", "NULL"))
        missing_pct = round((missing / total_cells * 100) if total_cells else 0.0, 2)
        # Date range
        date_range: Dict = {}
        for col in columns:
            if _is_temporal_column(col, col_dict.get(col, [])):
                vals = [str(v) for v in col_dict[col] if v not in (None, "")]
                if vals:
                    vals_sorted = sorted(vals)
                    date_range = {"column": col, "min": vals_sorted[0], "max": vals_sorted[-1]}
                break
        return {
            "row_count": len(data),
            "col_count": len(columns),
            "numeric_cols": numeric_cols,
            "categorical_cols": categorical_cols,
            "missing_pct": missing_pct,
            "date_range": date_range,
        }

    # convenience wrappers kept as aliases for backward compatibility
    def _summarize(self, df) -> Dict:
        try:
            return self._summarize_pd(df)
        except Exception:
            return {}

    def get_numeric_columns(self, data: Dict) -> List[str]:
        """Return column names whose values are predominantly numeric."""
        records = data.get("data", [])
        columns = data.get("columns", [])
        if not records or not columns:
            return []
        col_dict = _records_to_column_dict(records)
        return [c for c in columns if _is_numeric_column(col_dict.get(c, []))]

    def get_temporal_columns(self, data: Dict) -> List[str]:
        """Return column names that appear to contain date/time values."""
        records = data.get("data", [])
        columns = data.get("columns", [])
        if not records or not columns:
            return []
        col_dict = _records_to_column_dict(records)
        return [c for c in columns if _is_temporal_column(c, col_dict.get(c, []))]


# ---------------------------------------------------------------------------
# Class 2: DataAnalyzer
# ---------------------------------------------------------------------------

class DataAnalyzer:
    """Statistical analysis to find counterintuitive or surprising data insights."""

    def __init__(self):
        self._loader = DataLoader()

    # ------------------------------------------------------------------
    def find_surprising_finding(self, data: Dict) -> Dict:
        """
        Identify the single most counterintuitive result in the dataset.

        Priority:
          1. Strongest outlier in highest-variance numeric column
          2. Largest time-series change
          3. Strongest correlation between two numeric columns
        """
        numeric_cols = self._loader.get_numeric_columns(data)
        records = data.get("data", [])
        if not records or not numeric_cols:
            return {
                "finding_type": "no_data",
                "description": "No numeric columns found for analysis",
                "magnitude": 0.0,
                "column": "",
                "value": None,
            }

        col_dict = _records_to_column_dict(records)

        # Compute variance per numeric column
        col_variances: List[tuple] = []
        for col in numeric_cols:
            vals = [_safe_float(v) for v in col_dict[col] if _safe_float(v) is not None]
            if len(vals) < 3:
                continue
            try:
                var = statistics.variance(vals)
                col_variances.append((col, var, vals))
            except Exception:
                continue

        if not col_variances:
            return {
                "finding_type": "insufficient_data",
                "description": "Not enough numeric data for statistical analysis",
                "magnitude": 0.0,
                "column": "",
                "value": None,
            }

        # Highest-variance column
        col_variances.sort(key=lambda x: x[1], reverse=True)
        top_col, top_var, top_vals = col_variances[0]

        # Outliers in that column
        outliers = self.find_outliers(top_vals, top_col)

        # Time-series change (if temporal column present)
        temporal_cols = self._loader.get_temporal_columns(data)
        ts_finding: Dict = {}
        if temporal_cols:
            ts_finding = self._largest_time_series_change(data, temporal_cols[0], numeric_cols)

        # Correlation
        correlations = self.compute_correlations(data)

        # Pick the most impactful finding
        if outliers:
            best_outlier = max(outliers, key=lambda x: abs(x["z_score"]))
            mean_val = statistics.mean(top_vals)
            return {
                "finding_type": "outlier",
                "description": (
                    f"An extreme outlier was found in '{top_col}': value {best_outlier['value']:.4g} "
                    f"is {abs(best_outlier['z_score']):.1f} standard deviations from the mean ({mean_val:.4g})"
                ),
                "magnitude": abs(best_outlier["z_score"]),
                "column": top_col,
                "value": best_outlier["value"],
            }

        if ts_finding and ts_finding.get("magnitude", 0) > 10:
            return ts_finding

        if correlations:
            best_corr = correlations[0]
            return {
                "finding_type": "correlation",
                "description": (
                    f"Strong correlation (r={best_corr['correlation']:.2f}) between "
                    f"'{best_corr['col_a']}' and '{best_corr['col_b']}': {best_corr['interpretation']}"
                ),
                "magnitude": abs(best_corr["correlation"]) * 100,
                "column": best_corr["col_a"],
                "value": best_corr["correlation"],
            }

        # Fallback: highest variance stat
        mean_v = statistics.mean(top_vals)
        stdev_v = statistics.stdev(top_vals) if len(top_vals) > 1 else 0
        return {
            "finding_type": "high_variance",
            "description": (
                f"'{top_col}' shows unusually high variance (σ={stdev_v:.4g}, μ={mean_v:.4g}), "
                "indicating extreme inconsistency in the data"
            ),
            "magnitude": top_var,
            "column": top_col,
            "value": mean_v,
        }

    def _largest_time_series_change(self, data: Dict, time_col: str, numeric_cols: List[str]) -> Dict:
        """Find the numeric column with the largest % change from first to last time point."""
        records = data.get("data", [])
        if not records or not numeric_cols:
            return {}
        try:
            sorted_records = sorted(records, key=lambda r: str(r.get(time_col, "")))
            best: Dict = {}
            best_pct = 0.0
            for col in numeric_cols:
                vals = [_safe_float(r.get(col)) for r in sorted_records]
                vals = [v for v in vals if v is not None]
                if len(vals) < 2:
                    continue
                first_val, last_val = vals[0], vals[-1]
                if first_val == 0:
                    continue
                pct_change = abs((last_val - first_val) / first_val * 100)
                if pct_change > best_pct:
                    best_pct = pct_change
                    direction = "increased" if last_val > first_val else "decreased"
                    best = {
                        "finding_type": "time_series_change",
                        "description": (
                            f"'{col}' {direction} by {pct_change:.1f}% over the time period "
                            f"(from {first_val:.4g} to {last_val:.4g})"
                        ),
                        "magnitude": pct_change,
                        "column": col,
                        "value": last_val,
                    }
            return best
        except Exception as exc:
            log.warning("DataAnalyzer._largest_time_series_change failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    def extract_top_stats(self, data: Dict, n: int = 5) -> List[Dict]:
        """Extract the n most notable statistics from the dataset."""
        loader = DataLoader()
        numeric_cols = loader.get_numeric_columns(data)
        records = data.get("data", [])
        if not records or not numeric_cols:
            return []

        col_dict = _records_to_column_dict(records)
        stats: List[Dict] = []

        for col in numeric_cols:
            vals = [_safe_float(v) for v in col_dict[col] if _safe_float(v) is not None]
            if len(vals) < 2:
                continue
            try:
                mean_v = statistics.mean(vals)
                stdev_v = statistics.stdev(vals)
                min_v = min(vals)
                max_v = max(vals)
                median_v = statistics.median(vals)
                stats.append({"stat_type": "mean",   "value": round(mean_v, 4),   "column": col, "description": f"Average {col}: {mean_v:.4g}"})
                stats.append({"stat_type": "max",    "value": round(max_v, 4),    "column": col, "description": f"Maximum {col}: {max_v:.4g}"})
                stats.append({"stat_type": "min",    "value": round(min_v, 4),    "column": col, "description": f"Minimum {col}: {min_v:.4g}"})
                stats.append({"stat_type": "median", "value": round(median_v, 4), "column": col, "description": f"Median {col}: {median_v:.4g}"})
                if mean_v != 0:
                    cv = stdev_v / abs(mean_v)
                    stats.append({"stat_type": "cv", "value": round(cv, 4), "column": col, "description": f"Coefficient of variation for {col}: {cv:.2f}"})
            except Exception as exc:
                log.debug("extract_top_stats: column %s failed: %s", col, exc)
                continue

        # Sort by absolute value descending to surface the most dramatic numbers
        stats.sort(key=lambda s: abs(s["value"]) if s["value"] is not None else 0, reverse=True)
        return stats[:n]

    # ------------------------------------------------------------------
    def detect_trend(self, values: List[float]) -> Dict:
        """
        Linear regression on the value series.
        Returns {direction: up/down/flat, magnitude: float, r_squared: float}.
        """
        n = len(values)
        if n < 2:
            return {"direction": "flat", "magnitude": 0.0, "r_squared": 0.0}
        try:
            import numpy as np
            x = np.arange(n, dtype=float)
            y = np.array(values, dtype=float)
            coeffs = np.polyfit(x, y, 1)
            slope = float(coeffs[0])
            y_pred = np.polyval(coeffs, x)
            ss_res = float(np.sum((y - y_pred) ** 2))
            ss_tot = float(np.sum((y - np.mean(y)) ** 2))
            r_squared = 1 - ss_res / ss_tot if ss_tot != 0 else 0.0
        except ImportError:
            # Pure Python fallback — Ordinary Least Squares
            x_vals = list(range(n))
            x_mean = statistics.mean(x_vals)
            y_mean = statistics.mean(values)
            num = sum((x_vals[i] - x_mean) * (values[i] - y_mean) for i in range(n))
            den = sum((x_vals[i] - x_mean) ** 2 for i in range(n))
            slope = num / den if den != 0 else 0.0
            intercept = y_mean - slope * x_mean
            y_pred = [slope * x_vals[i] + intercept for i in range(n)]
            ss_res = sum((values[i] - y_pred[i]) ** 2 for i in range(n))
            ss_tot = sum((values[i] - y_mean) ** 2 for i in range(n))
            r_squared = 1 - ss_res / ss_tot if ss_tot != 0 else 0.0
        except Exception as exc:
            log.warning("DataAnalyzer.detect_trend failed: %s", exc)
            return {"direction": "flat", "magnitude": 0.0, "r_squared": 0.0}

        if abs(slope) < 1e-10:
            direction = "flat"
        elif slope > 0:
            direction = "up"
        else:
            direction = "down"
        return {
            "direction": direction,
            "magnitude": round(abs(slope), 6),
            "r_squared": round(max(0.0, min(1.0, r_squared)), 4),
        }

    # ------------------------------------------------------------------
    def find_outliers(self, values: List[float], column: str) -> List[Dict]:
        """Return data points > 2 standard deviations from the mean."""
        if len(values) < 3:
            return []
        try:
            mean_v = statistics.mean(values)
            stdev_v = statistics.stdev(values)
            if stdev_v == 0:
                return []
            outliers = []
            for idx, v in enumerate(values):
                z = (v - mean_v) / stdev_v
                if abs(z) > 2.0:
                    outliers.append({"index": idx, "value": round(v, 6), "z_score": round(z, 3)})
            return outliers
        except Exception as exc:
            log.warning("DataAnalyzer.find_outliers failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    def compute_correlations(self, data: Dict) -> List[Dict]:
        """Return top 3 correlations between numeric columns, sorted by |r|."""
        loader = DataLoader()
        numeric_cols = loader.get_numeric_columns(data)
        records = data.get("data", [])
        if len(numeric_cols) < 2 or not records:
            return []

        col_dict = _records_to_column_dict(records)
        pairs: List[Dict] = []

        def _pearson(x_vals: List[float], y_vals: List[float]) -> Optional[float]:
            n = len(x_vals)
            if n < 3:
                return None
            try:
                from scipy.stats import pearsonr
                r, _ = pearsonr(x_vals, y_vals)
                return float(r)
            except ImportError:
                pass
            try:
                import numpy as np
                return float(np.corrcoef(x_vals, y_vals)[0, 1])
            except ImportError:
                pass
            # Pure Python
            mx = statistics.mean(x_vals)
            my = statistics.mean(y_vals)
            num = sum((x_vals[i] - mx) * (y_vals[i] - my) for i in range(n))
            dx = math.sqrt(sum((v - mx) ** 2 for v in x_vals))
            dy = math.sqrt(sum((v - my) ** 2 for v in y_vals))
            return num / (dx * dy) if dx * dy != 0 else None

        for i in range(len(numeric_cols)):
            for j in range(i + 1, len(numeric_cols)):
                col_a = numeric_cols[i]
                col_b = numeric_cols[j]
                # Align non-null pairs
                paired = [
                    (_safe_float(col_dict[col_a][k]), _safe_float(col_dict[col_b][k]))
                    for k in range(len(records))
                    if _safe_float(col_dict[col_a][k]) is not None
                    and _safe_float(col_dict[col_b][k]) is not None
                ]
                if len(paired) < 3:
                    continue
                x_vals = [p[0] for p in paired]
                y_vals = [p[1] for p in paired]
                r = _pearson(x_vals, y_vals)
                if r is None or math.isnan(r):
                    continue
                if abs(r) >= 0.3:
                    strength = "strong" if abs(r) >= 0.7 else "moderate"
                    direction = "positive" if r > 0 else "negative"
                    pairs.append({
                        "col_a": col_a,
                        "col_b": col_b,
                        "correlation": round(r, 4),
                        "interpretation": f"{strength} {direction} correlation between {col_a} and {col_b}",
                    })

        pairs.sort(key=lambda p: abs(p["correlation"]), reverse=True)
        return pairs[:3]


# ---------------------------------------------------------------------------
# Class 3: ChartSpecGenerator
# ---------------------------------------------------------------------------

class ChartSpecGenerator:
    """Produces chart specification dicts and optionally renders them with matplotlib."""

    _COLOR_SCHEMES = {
        "neon":    {"bg": "#1a1a2e", "accent": "#e94560", "text": "#ffffff", "secondary": "#16213e"},
        "cool":    {"bg": "#0f3460", "accent": "#e94560", "text": "#ffffff", "secondary": "#533483"},
        "minimal": {"bg": "#ffffff", "accent": "#e94560", "text": "#000000", "secondary": "#eeeeee"},
    }

    def __init__(self):
        self._loader = DataLoader()

    # ------------------------------------------------------------------
    def generate_chart_specs(self, data: Dict, finding: Dict) -> List[Dict]:
        """
        Return 3–5 chart spec dicts suited to the shape of the data.

        Rules:
          - If temporal column exists → include line chart
          - If categories < 10 → bar chart
          - If ≥ 2 numeric cols → scatter plot
          - Always include highlight_value from the surprising finding
        """
        numeric_cols = self._loader.get_numeric_columns(data)
        temporal_cols = self._loader.get_temporal_columns(data)
        records = data.get("data", [])
        columns = data.get("columns", [])
        col_dict = _records_to_column_dict(records) if records else {}
        highlight_col = finding.get("column", "")
        highlight_val = finding.get("value")
        specs: List[Dict] = []

        def _spec(chart_id: str, chart_type: str, title: str,
                  x_col: str, y_col: str, caption: str,
                  x_label: str = "", y_label: str = "",
                  color_scheme: str = "neon") -> Dict:
            return {
                "chart_id": chart_id,
                "chart_type": chart_type,
                "title": title,
                "x_col": x_col,
                "y_col": y_col,
                "x_label": x_label or x_col,
                "y_label": y_label or y_col,
                "color_scheme": color_scheme,
                "highlight": {"column": highlight_col, "value": highlight_val},
                "caption": caption,
            }

        # 1. Temporal line chart
        if temporal_cols and numeric_cols:
            time_col = temporal_cols[0]
            val_col = highlight_col if highlight_col in numeric_cols else numeric_cols[0]
            specs.append(_spec(
                "chart_001", "line",
                f"{val_col} Over Time",
                time_col, val_col,
                f"Trend of {val_col} across the time period",
                x_label=time_col, y_label=val_col,
            ))

        # 2. Bar chart (categorical x, numeric y)
        categorical_cols = [c for c in columns if c not in numeric_cols and c not in temporal_cols]
        if categorical_cols and numeric_cols:
            cat_col = categorical_cols[0]
            n_cats = len(set(str(v) for v in col_dict.get(cat_col, []) if v is not None))
            if n_cats < 10:
                val_col = highlight_col if highlight_col in numeric_cols else numeric_cols[0]
                specs.append(_spec(
                    "chart_002", "bar",
                    f"{val_col} by {cat_col}",
                    cat_col, val_col,
                    f"Comparison of {val_col} across {cat_col} categories",
                    x_label=cat_col, y_label=val_col,
                ))

        # 3. Scatter plot (2 numeric cols)
        if len(numeric_cols) >= 2:
            col_a = numeric_cols[0]
            col_b = numeric_cols[1] if numeric_cols[1] != col_a else (numeric_cols[2] if len(numeric_cols) > 2 else numeric_cols[0])
            if col_a != col_b:
                specs.append(_spec(
                    "chart_003", "scatter",
                    f"Correlation: {col_a} vs {col_b}",
                    col_a, col_b,
                    f"Relationship between {col_a} and {col_b}",
                    x_label=col_a, y_label=col_b,
                    color_scheme="cool",
                ))

        # 4. Histogram of the surprising column
        if highlight_col and highlight_col in numeric_cols:
            specs.append(_spec(
                "chart_004", "histogram",
                f"Distribution of {highlight_col}",
                highlight_col, "frequency",
                f"How {highlight_col} values are distributed — note the outlier",
                x_label=highlight_col, y_label="Frequency",
            ))

        # 5. Pie chart for categorical proportions if applicable
        if categorical_cols and not any(s["chart_type"] == "pie" for s in specs):
            cat_col = categorical_cols[0]
            unique_cats = set(str(v) for v in col_dict.get(cat_col, []) if v is not None)
            if 2 <= len(unique_cats) <= 6:
                specs.append(_spec(
                    "chart_005", "pie",
                    f"Proportions of {cat_col}",
                    cat_col, "count",
                    f"Share of each {cat_col} category",
                    color_scheme="minimal",
                ))

        # Ensure at least 3 specs
        while len(specs) < 3 and numeric_cols:
            col = numeric_cols[len(specs) % len(numeric_cols)]
            specs.append(_spec(
                f"chart_00{len(specs)+1}", "histogram",
                f"Distribution of {col}",
                col, "frequency",
                f"Distribution of values in {col}",
                x_label=col, y_label="Frequency",
            ))

        return specs[:5]

    # ------------------------------------------------------------------
    def render_chart(self, spec: Dict, data: Dict, output_path: str) -> Optional[str]:
        """
        Render the chart described by spec and save as PNG.
        Returns output_path on success, None if matplotlib is unavailable.
        """
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
        except ImportError:
            log.warning("render_chart: matplotlib not available; skipping render")
            return None

        records = data.get("data", [])
        if not records:
            log.warning("render_chart: no records in data dict")
            return None

        scheme = self._COLOR_SCHEMES.get(spec.get("color_scheme", "neon"), self._COLOR_SCHEMES["neon"])
        bg = scheme["bg"]
        accent = scheme["accent"]
        text_color = scheme["text"]

        fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(scheme["secondary"])
        for spine in ax.spines.values():
            spine.set_edgecolor(accent)
        ax.tick_params(colors=text_color)
        ax.xaxis.label.set_color(text_color)
        ax.yaxis.label.set_color(text_color)
        ax.title.set_color(text_color)

        chart_type = spec.get("chart_type", "bar")
        x_col = spec.get("x_col", "")
        y_col = spec.get("y_col", "")
        highlight = spec.get("highlight", {})

        try:
            col_dict = _records_to_column_dict(records)

            if chart_type == "line":
                x_vals = col_dict.get(x_col, [])
                y_vals = [_safe_float(v) for v in col_dict.get(y_col, [])]
                pairs = [(str(x_vals[i]), y_vals[i]) for i in range(len(x_vals)) if y_vals[i] is not None]
                if pairs:
                    xs, ys = zip(*pairs)
                    ax.plot(xs, ys, color=accent, linewidth=2.5, marker="o", markersize=4)
                    ax.set_xticks(range(0, len(xs), max(1, len(xs) // 10)))
                    ax.set_xticklabels([xs[i] for i in range(0, len(xs), max(1, len(xs) // 10))], rotation=45, ha="right")

            elif chart_type == "bar":
                x_vals = [str(v) for v in col_dict.get(x_col, []) if v is not None]
                y_vals = [_safe_float(v) for v in col_dict.get(y_col, [])]
                pairs = [(x_vals[i], y_vals[i]) for i in range(min(len(x_vals), len(y_vals))) if y_vals[i] is not None]
                # Aggregate by category
                from collections import defaultdict
                agg: Dict = defaultdict(list)
                for xv, yv in pairs:
                    agg[xv].append(yv)
                cats = list(agg.keys())[:20]
                means = [statistics.mean(agg[c]) for c in cats]
                colors = [accent if str(c) == str(highlight.get("value")) else scheme["secondary"] for c in cats]
                bars = ax.bar(cats, means, color=colors, edgecolor=accent, linewidth=0.8)
                ax.set_xticks(range(len(cats)))
                ax.set_xticklabels(cats, rotation=45, ha="right", color=text_color)

            elif chart_type == "scatter":
                x_vals = [_safe_float(v) for v in col_dict.get(x_col, [])]
                y_vals = [_safe_float(v) for v in col_dict.get(y_col, [])]
                pairs = [(x_vals[i], y_vals[i]) for i in range(min(len(x_vals), len(y_vals)))
                         if x_vals[i] is not None and y_vals[i] is not None]
                if pairs:
                    xs, ys = zip(*pairs)
                    ax.scatter(xs, ys, color=accent, alpha=0.6, s=30)
                    # Highlight outlier
                    hval = _safe_float(highlight.get("value"))
                    if hval is not None:
                        ax.axvline(hval, color="yellow", linestyle="--", alpha=0.7, label=f"Highlight: {hval}")

            elif chart_type == "histogram":
                x_vals = [_safe_float(v) for v in col_dict.get(x_col, []) if _safe_float(v) is not None]
                if x_vals:
                    ax.hist(x_vals, bins=30, color=accent, edgecolor=bg, alpha=0.85)
                    hval = _safe_float(highlight.get("value"))
                    if hval is not None:
                        ax.axvline(hval, color="yellow", linestyle="--", linewidth=2,
                                   label=f"Highlight: {hval:.4g}")
                        ax.legend(facecolor=bg, edgecolor=accent, labelcolor=text_color)

            elif chart_type == "pie":
                from collections import Counter
                cats_raw = [str(v) for v in col_dict.get(x_col, []) if v is not None]
                counter = Counter(cats_raw)
                top_items = counter.most_common(6)
                if top_items:
                    labels, sizes = zip(*top_items)
                    pie_colors = [accent, "#533483", "#0f3460", "#e94560", "#16213e", "#1a1a2e"]
                    ax.pie(sizes, labels=labels, colors=pie_colors[:len(labels)],
                           autopct="%1.1f%%", textprops={"color": text_color})

            ax.set_title(spec.get("title", ""), fontsize=18, fontweight="bold", color=text_color, pad=15)
            ax.set_xlabel(spec.get("x_label", x_col), fontsize=13, color=text_color)
            ax.set_ylabel(spec.get("y_label", y_col), fontsize=13, color=text_color)

            # Caption annotation
            caption = spec.get("caption", "")
            if caption:
                fig.text(0.5, 0.01, caption, ha="center", fontsize=10,
                         color=text_color, alpha=0.8, style="italic")

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            fig.tight_layout()
            fig.savefig(output_path, facecolor=bg, dpi=100, bbox_inches="tight")
            plt.close(fig)
            log.info("render_chart: saved %s", output_path)
            return output_path

        except Exception as exc:
            log.error("render_chart: rendering failed: %s", exc)
            plt.close(fig)
            return None


# ---------------------------------------------------------------------------
# Class 4: DataNarrativeGenerator
# ---------------------------------------------------------------------------

class DataNarrativeGenerator:
    """Converts data analysis results into a structured YouTube video narrative."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    def generate_narrative(self, data: Dict, finding: Dict, style: str = "data_revealed") -> Dict:
        """
        Build a complete 5-part video narrative from the data and finding.
        Returns {hook, setup, conflict, revelation, cta, style, chart_count}.
        """
        if style not in DATA_VIDEO_STYLES:
            style = "data_revealed"
            log.warning("generate_narrative: unknown style, defaulting to data_revealed")

        style_cfg = DATA_VIDEO_STYLES[style]
        base_hook = style_cfg["hook"]
        row_count = data.get("row_count", data.get("summary_stats", {}).get("row_count", 0))
        source_path = data.get("source_path", "the dataset")
        dataset_name = Path(source_path).stem if source_path != "the dataset" else "this dataset"
        columns = data.get("columns", [])
        finding_desc = finding.get("description", "an unexpected pattern in the data")
        finding_col = finding.get("column", "the data")
        finding_val = finding.get("value")
        finding_magnitude = finding.get("magnitude", 0)

        hook = (
            f"{base_hook}. "
            f"When we examined {dataset_name}, we found that {finding_col} "
            + (f"hit {finding_val:.4g} — " if finding_val is not None and isinstance(finding_val, (int, float)) else "")
            + "a number almost nobody expected."
        )

        setup = (
            f"We analyzed {row_count:,} data points from {dataset_name}, "
            f"covering {len(columns)} variables. "
            f"The dataset spans everything from {columns[0] if columns else 'the primary metric'} "
            f"to {columns[-1] if len(columns) > 1 else 'supporting indicators'}."
        )

        tone = style_cfg["tone"]
        if tone == "confrontational":
            conflict_opener = "Conventional wisdom says one thing — this data says another."
        elif tone == "investigative":
            conflict_opener = "At first glance, the numbers seem normal. Then you look closer."
        elif tone == "reflective":
            conflict_opener = "Looking back at the full picture, the assumptions don't hold."
        else:
            conflict_opener = "Most people assume the trend goes one way."

        conflict = (
            f"{conflict_opener} "
            f"The standard narrative around {finding_col} misses a critical detail: "
            f"the variance in this column alone is {finding_magnitude:.2f}x what you'd expect "
            f"from a well-behaved system."
        )

        if finding.get("finding_type") == "outlier":
            revelation = (
                f"Here's the actual finding: {finding_desc}. "
                f"That single data point skews the entire picture — "
                f"and it's exactly what most analysts overlook when they summarize with averages."
            )
        elif finding.get("finding_type") == "correlation":
            revelation = (
                f"The data shows: {finding_desc}. "
                f"This correlation is strong enough that ignoring it leads to systematically wrong conclusions."
            )
        elif finding.get("finding_type") == "time_series_change":
            revelation = (
                f"Over the observed period: {finding_desc}. "
                f"That trajectory is impossible to explain without revisiting the underlying assumptions."
            )
        else:
            revelation = (
                f"After thorough analysis: {finding_desc}. "
                f"The data doesn't lie — and now you can see it clearly."
            )

        cta = (
            f"Share this with anyone who still thinks {finding_col} behaves predictably. "
            f"The data proves otherwise. Drop a comment below with what surprised you most."
        )

        return {
            "hook": hook,
            "setup": setup,
            "conflict": conflict,
            "revelation": revelation,
            "cta": cta,
            "style": style,
            "chart_count": 0,  # updated by the orchestrator after chart generation
            "dataset_name": dataset_name,
            "row_count": row_count,
        }

    # ------------------------------------------------------------------
    def build_scenes(self, narrative: Dict, chart_specs: List[Dict]) -> List[Dict]:
        """
        Convert narrative sections into a 5-scene timeline.
        Each scene: {title, narration, duration_s, chart_spec, text_overlay}.
        """
        def _get_spec(chart_type: str) -> Optional[Dict]:
            for spec in chart_specs:
                if spec.get("chart_type") == chart_type:
                    return spec
            return chart_specs[0] if chart_specs else None

        scenes = [
            {
                "scene_number": 1,
                "title": "Hook",
                "narration": narrative.get("hook", ""),
                "duration_s": 15,
                "chart_spec": _get_spec("histogram"),
                "text_overlay": narrative.get("hook", "").split(".")[0] + ".",
                "time_range": "0–15s",
            },
            {
                "scene_number": 2,
                "title": "Setup",
                "narration": narrative.get("setup", ""),
                "duration_s": 30,
                "chart_spec": _get_spec("bar"),
                "text_overlay": f"Dataset: {narrative.get('row_count', 0):,} data points",
                "time_range": "15–45s",
            },
            {
                "scene_number": 3,
                "title": "Conflict",
                "narration": narrative.get("conflict", ""),
                "duration_s": 30,
                "chart_spec": _get_spec("line"),
                "text_overlay": "What the data actually shows...",
                "time_range": "45–75s",
            },
            {
                "scene_number": 4,
                "title": "Revelation",
                "narration": narrative.get("revelation", ""),
                "duration_s": 30,
                "chart_spec": _get_spec("scatter"),
                "text_overlay": "The surprising finding",
                "time_range": "75–105s",
            },
            {
                "scene_number": 5,
                "title": "CTA",
                "narration": narrative.get("cta", ""),
                "duration_s": 15,
                "chart_spec": _get_spec("pie"),
                "text_overlay": "Share this now",
                "time_range": "105–120s",
            },
        ]
        return scenes

    # ------------------------------------------------------------------
    def suggest_titles(self, data: Dict, finding: Dict) -> List[str]:
        """Generate title suggestions using TITLE_PATTERNS_DATA."""
        source_path = data.get("source_path", "")
        dataset_name = Path(source_path).stem if source_path else "the data"
        row_count = data.get("row_count", data.get("summary_stats", {}).get("row_count", 0))
        topic = finding.get("column", "this topic").replace("_", " ").title()
        current_year = datetime.now(timezone.utc).year

        # Estimate n_years from date_range
        date_range = data.get("summary_stats", {}).get("date_range", {})
        n_years = 1
        if date_range:
            try:
                min_str = str(date_range.get("min", ""))
                max_str = str(date_range.get("max", ""))
                min_year = int(min_str[:4]) if min_str else current_year
                max_year = int(max_str[:4]) if max_str else current_year
                n_years = max(1, max_year - min_year)
            except (ValueError, IndexError):
                n_years = 1

        titles = []
        for pattern in TITLE_PATTERNS_DATA:
            try:
                title = pattern.format(
                    dataset=dataset_name.replace("_", " ").title(),
                    topic=topic,
                    n_rows=f"{row_count:,}" if row_count else "Thousands of",
                    year=current_year,
                    n_years=n_years,
                )
                titles.append(title)
            except KeyError:
                titles.append(pattern)
        return titles


# ---------------------------------------------------------------------------
# Class 5: DataStorytellerEngine (orchestrator)
# ---------------------------------------------------------------------------

class DataStorytellerEngine:
    """Full data-to-video pipeline orchestrator."""

    def __init__(self, memory=None):
        if memory is not None:
            self.memory = memory
        else:
            try:
                from core.memory import get_memory
                self.memory = get_memory()
            except Exception:
                self.memory = None
        self._loader = DataLoader()
        self._analyzer = DataAnalyzer()
        self._chart_gen = ChartSpecGenerator()
        self._narrative_gen = DataNarrativeGenerator()

    # ------------------------------------------------------------------
    def process_data_file(self, file_path: str, style: str = "data_revealed") -> Dict:
        """
        Full pipeline: load → analyze → chart specs → render → narrative → scenes → titles.

        Returns:
            {title_suggestions, scenes, charts_rendered, finding, style, timestamp}
        """
        log.info("DataStorytellerEngine.process_data_file: starting %s", file_path)

        # 1. Load file
        data = self._loader.load(file_path)
        if not data:
            log.error("process_data_file: failed to load %s", file_path)
            return {}

        # 2. Analyze for surprising finding
        finding = self._analyzer.find_surprising_finding(data)
        log.info("process_data_file: finding_type=%s column=%s", finding.get("finding_type"), finding.get("column"))

        # 3. Generate chart specs
        chart_specs = self._chart_gen.generate_chart_specs(data, finding)
        log.info("process_data_file: generated %d chart specs", len(chart_specs))

        # 4. Render charts to /tmp/charts/
        output_dir = Path("/tmp/charts") / Path(file_path).stem
        charts_rendered: List[str] = []
        for spec in chart_specs:
            chart_path = str(output_dir / f"{spec['chart_id']}.png")
            rendered = self._chart_gen.render_chart(spec, data, chart_path)
            if rendered:
                charts_rendered.append(rendered)
                spec["rendered_path"] = rendered

        # 5. Generate narrative
        narrative = self._narrative_gen.generate_narrative(data, finding, style)
        narrative["chart_count"] = len(charts_rendered)

        # 6. Build scenes
        scenes = self._narrative_gen.build_scenes(narrative, chart_specs)

        # 7. Suggest titles
        title_suggestions = self._narrative_gen.suggest_titles(data, finding)

        # 8. Save to memory
        result = {
            "title_suggestions": title_suggestions,
            "scenes": scenes,
            "chart_specs": chart_specs,
            "charts_rendered": charts_rendered,
            "finding": finding,
            "narrative": narrative,
            "style": style,
            "source_file": file_path,
            "row_count": data.get("row_count", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self.memory is not None:
            try:
                self.memory.save_data_project(result)
                log.info("process_data_file: saved to memory")
            except Exception as exc:
                log.warning("process_data_file: memory.save_data_project failed: %s", exc)

        log.info(
            "process_data_file: complete — %d scenes, %d charts rendered, %d title suggestions",
            len(scenes), len(charts_rendered), len(title_suggestions),
        )
        return result

    # ------------------------------------------------------------------
    def process_from_memory(self, n: int = 5) -> List[Dict]:
        """
        Retrieve unprocessed data files from memory and run the pipeline on each.
        Returns list of result dicts.
        """
        if self.memory is None:
            log.warning("process_from_memory: no memory instance available")
            return []
        results = []
        try:
            pending = self.memory.get_data_projects(status="pending", n=n)
        except Exception as exc:
            log.error("process_from_memory: memory.get_data_projects failed: %s", exc)
            return []

        for project in pending:
            file_path = project.get("source_file") or project.get("file_path", "")
            if not file_path:
                log.warning("process_from_memory: project missing file_path, skipping")
                continue
            style = project.get("style", "data_revealed")
            try:
                result = self.process_data_file(file_path, style=style)
                if result:
                    results.append(result)
            except Exception as exc:
                log.error("process_from_memory: failed for %s: %s", file_path, exc)
                continue
        return results

    # ------------------------------------------------------------------
    def print_report(self, result: Dict) -> None:
        """Pretty-print the pipeline result to stdout."""
        if not result:
            print("[DataStorytellerEngine] No result to display.")
            return

        sep = "=" * 72
        print(sep)
        print("  DATA STORYTELLER REPORT")
        print(sep)
        print(f"  Source  : {result.get('source_file', 'N/A')}")
        print(f"  Rows    : {result.get('row_count', 0):,}")
        print(f"  Style   : {result.get('style', 'N/A')}")
        print(f"  Charts  : {len(result.get('charts_rendered', []))} rendered")
        print()

        finding = result.get("finding", {})
        print(f"  SURPRISING FINDING ({finding.get('finding_type', 'unknown').upper()})")
        print(f"  {finding.get('description', 'N/A')}")
        print(f"  Magnitude: {finding.get('magnitude', 0):.4g}  |  Column: {finding.get('column', 'N/A')}")
        print()

        print("  TITLE SUGGESTIONS:")
        for i, title in enumerate(result.get("title_suggestions", []), 1):
            print(f"  {i:>2}. {title}")
        print()

        print("  SCENES:")
        for scene in result.get("scenes", []):
            print(f"  [{scene.get('time_range', '')}] Scene {scene.get('scene_number')}: {scene.get('title')}")
            narration = scene.get("narration", "")
            print(f"       {narration[:100]}{'...' if len(narration) > 100 else ''}")
        print(sep)
