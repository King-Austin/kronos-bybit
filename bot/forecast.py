import os
import sys
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import INTERVAL_MS, VENDOR, Settings

if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import torch  # noqa: E402
from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402


@dataclass
class Forecast:
    paths: np.ndarray
    mean: pd.DataFrame
    last_close: float
    last_timestamp: pd.Timestamp
    horizon: int
    elapsed_s: float

    @property
    def terminal(self) -> np.ndarray:
        return self.paths[:, -1]

    @property
    def expected_return(self) -> float:
        return float(self.terminal.mean() / self.last_close - 1)

    @property
    def prob_up(self) -> float:
        return float((self.terminal > self.last_close).mean())

    @property
    def q05(self) -> float:
        return float(np.quantile(self.terminal, 0.05) / self.last_close - 1)

    @property
    def q95(self) -> float:
        return float(np.quantile(self.terminal, 0.95) / self.last_close - 1)


_MODEL_CACHE: dict[tuple, tuple] = {}


def _device(name: str) -> str:
    if name == "auto":
        if torch.cuda.is_available():
            return "cuda:0"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    return name


def load_predictor(model_name: str, tokenizer_name: str, device: str = "cpu") -> KronosPredictor:
    key = (model_name, tokenizer_name, device)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key][0]
    dev = _device(device)
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_name)
    model = Kronos.from_pretrained(model_name)
    model.eval()
    predictor = KronosPredictor(model, tokenizer, device=dev, max_context=512)
    _MODEL_CACHE[key] = (predictor,)
    return predictor


def forecast(settings: Settings, df: pd.DataFrame, horizon: int | None = None,
             sample_paths: int | None = None, temperature: float = 1.0,
             top_p: float = 0.9) -> Forecast:
    horizon = horizon or settings.horizon
    sample_paths = max(1, sample_paths or settings.sample_paths)
    lookback = min(settings.lookback, 512, len(df))
    if lookback < 64:
        raise ValueError(f"need at least 64 closed candles, got {len(df)}")

    hist = df.tail(lookback).reset_index(drop=True)
    width = INTERVAL_MS[settings.interval]
    last_ts = hist["timestamp"].iloc[-1]
    y_timestamp = pd.Series([last_ts + pd.Timedelta(milliseconds=width * (i + 1)) for i in range(horizon)])

    predictor = load_predictor(settings.model, settings.tokenizer, settings.device)
    x_df = hist[["open", "high", "low", "close", "volume", "amount"]].copy()
    x_df = x_df.astype(float)
    x_timestamp = hist["timestamp"]

    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
    started = time.perf_counter()
    closes = []
    means = []
    with torch.no_grad():
        for i in range(sample_paths):
            torch.manual_seed(1000 + i)
            pred = predictor.predict(
                df=x_df,
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=horizon,
                T=temperature,
                top_p=top_p,
                sample_count=1,
                verbose=False,
            )
            closes.append(pred["close"].to_numpy(dtype=float))
            means.append(pred)
    elapsed = time.perf_counter() - started

    paths = np.vstack(closes)
    mean = pd.concat(means).groupby(level=0).mean()
    mean = mean.reindex(y_timestamp)
    return Forecast(
        paths=paths,
        mean=mean,
        last_close=float(hist["close"].iloc[-1]),
        last_timestamp=last_ts,
        horizon=horizon,
        elapsed_s=elapsed,
    )
