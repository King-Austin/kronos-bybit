import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "Kronos"

load_dotenv(ROOT / ".env")

_hf_home = os.getenv("HF_HOME")
if _hf_home:
    _hf_path = Path(_hf_home)
    if not _hf_path.is_absolute():
        _hf_path = ROOT / _hf_path
    _hf_path.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(_hf_path)


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


BASE_URLS = {
    "demo": "https://api-demo.bybit.com",
    "testnet": "https://api-testnet.bybit.com",
    "live": "https://api.bybit.com",
}

DEFAULT_DATA_URL = {
    "demo": "https://api.bytick.com",
    "testnet": "https://api-testnet.bybit.com",
    "live": "https://api.bytick.com",
}

INTERVAL_MS = {
    "1": 60_000,
    "3": 180_000,
    "5": 300_000,
    "15": 900_000,
    "30": 1_800_000,
    "60": 3_600_000,
    "120": 7_200_000,
    "240": 14_400_000,
    "360": 21_600_000,
    "720": 43_200_000,
    "D": 86_400_000,
    "W": 604_800_000,
    "M": 2_592_000_000,
}

INTERVAL_FREQ = {
    "1": "1min",
    "3": "3min",
    "5": "5min",
    "15": "15min",
    "30": "30min",
    "60": "1h",
    "120": "2h",
    "240": "4h",
    "360": "6h",
    "720": "12h",
    "D": "1D",
    "W": "1W",
}


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_secret: str
    env: str
    data_url: str
    trade_url: str
    symbol: str
    category: str
    interval: str
    lookback: int
    horizon: int
    sample_paths: int
    model: str
    tokenizer: str
    device: str
    dry_run: bool
    risk_pct: float
    max_leverage: float
    prob_threshold: float
    fee_bps: float
    slippage_bps: float
    atr_k: float
    rr: float
    state_file: Path
    log_file: Path

    @property
    def round_trip_cost_bps(self) -> float:
        return 2 * (self.fee_bps + self.slippage_bps)


def load_settings() -> Settings:
    env = os.getenv("BYBIT_ENV", "demo").strip().lower()
    if env not in BASE_URLS:
        raise ValueError(f"BYBIT_ENV must be one of {sorted(BASE_URLS)}")
    return Settings(
        api_key=os.getenv("BYBIT_API_KEY", "").strip(),
        api_secret=os.getenv("BYBIT_API_SECRET", "").strip(),
        env=env,
        data_url=os.getenv("BYBIT_DATA_URL", DEFAULT_DATA_URL[env]).rstrip("/"),
        trade_url=os.getenv("BYBIT_TRADE_URL", BASE_URLS[env]).rstrip("/"),
        symbol=os.getenv("SYMBOL", "BTCUSDT").strip().upper(),
        category=os.getenv("CATEGORY", "linear").strip().lower(),
        interval=os.getenv("INTERVAL", "15").strip(),
        lookback=_int("LOOKBACK", 512),
        horizon=_int("HORIZON", 8),
        sample_paths=_int("SAMPLE_PATHS", 8),
        model=os.getenv("MODEL", "NeoQuasar/Kronos-small").strip(),
        tokenizer=os.getenv("TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base").strip(),
        device=os.getenv("DEVICE", "cpu").strip(),
        dry_run=_bool("DRY_RUN", True),
        risk_pct=_float("RISK_PCT", 0.01),
        max_leverage=_float("MAX_LEVERAGE", 1.0),
        prob_threshold=_float("PROB_THRESHOLD", 0.60),
        fee_bps=_float("FEE_BPS", 5.5),
        slippage_bps=_float("SLIPPAGE_BPS", 2.0),
        atr_k=_float("ATR_K", 1.5),
        rr=_float("RR", 1.5),
        state_file=ROOT / os.getenv("STATE_FILE", "data/state.json"),
        log_file=ROOT / os.getenv("LOG_FILE", "data/trades.jsonl"),
    )
