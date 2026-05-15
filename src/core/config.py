from __future__ import annotations

from pathlib import Path

from pydantic import AnyUrl, Field, SecretStr
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    PolyForge runtime configuration.

    - Loads from environment variables prefixed with POLYFORGE_
    - Also reads from a local .env file (development convenience)
    - Keeps secrets out of logs via SecretStr
    """

    model_config = SettingsConfigDict(
        env_prefix="POLYFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = Field(default="dev", description="Environment name: dev|staging|prod")

    dry_run: bool = Field(
        default=True,
        description="Safety flag. When true, order placement is blocked even if trading is enabled.",
    )
    trading_enabled: bool = Field(
        default=False,
        description="Master enable for trading functionality. Keep false until production-ready.",
    )
    execute_enabled: bool = Field(
        default=False,
        description="Allows the executor to run when invoked via CLI. Still gated by trading_enabled and confirmation.",
    )
    live_confirm_phrase: str = Field(
        default="I_UNDERSTAND",
        description="Required phrase for interactive confirmation on first live run.",
    )
    live_confirm_env: str | None = Field(
        default=None,
        description="Non-interactive override. If set to the live_confirm_phrase, allows first live run without prompt.",
    )
    max_order_size_usd: float = Field(default=500.0, ge=0.0)
    max_order_slippage_bps: int = Field(default=50, ge=0, le=10_000)
    min_order_size_usd: float = Field(default=5.0, ge=0.0)
    default_post_only: bool = Field(default=True)
    default_time_in_force: str = Field(default="GTC")
    fees_bps: int = Field(default=0, ge=0, le=10_000, description="Optional fee estimate used for previews.")

    chain_id: int = Field(default=137, description="Polygon mainnet chain id (137).")
    rpc_url: AnyUrl | None = Field(
        default=None,
        description="Optional RPC URL for chain reads. Not required for Phase 1 scanner.",
    )

    gamma_base_url: AnyUrl = Field(default="https://gamma-api.polymarket.com")
    data_api_base_url: AnyUrl | None = Field(
        default=None,
        description="Optional Polymarket Data API base URL (if available in your environment).",
    )

    clob_host: AnyUrl = Field(default="https://clob.polymarket.com")
    clob_ws_url: AnyUrl | None = Field(
        default=None,
        description="Optional CLOB WebSocket URL. If not set, code will infer from clob_host when possible.",
    )

    wallet_mode: str = Field(
        default="hot",
        description="Wallet mode: hot|proxy|cold. hot=local signer; proxy=deposit/funder/EIP-1271 flows; cold=read-only (no signing).",
    )
    key_encryption: bool = Field(
        default=False,
        description="If true, secrets manager is expected to provide decrypted keys at runtime; PolyForge will not decrypt itself.",
    )
    key_encryption_password: SecretStr | None = Field(default=None)
    max_withdraw_usd: float = Field(default=0.0, ge=0.0)
    min_wallet_balance_usd: float = Field(default=0.0, ge=0.0)
    api_rate_limit_per_s: float = Field(default=5.0, ge=0.0, le=1000.0)
    audit_log_path: Path = Field(default=Path("./data/audit/audit.jsonl"))

    preset: str = Field(
        default="conservative",
        description="Strategy preset: conservative|balanced|aggressive. Applied only when apply_preset is true.",
    )
    apply_preset: bool = Field(
        default=False,
        description="If true, overwrite selected risk/execution knobs with the chosen preset values.",
    )

    wallet_private_key: SecretStr | None = Field(default=None, description="Signer private key. Never commit.")

    clob_api_key: str | None = Field(default=None, description="CLOB API key (L2).")
    clob_api_secret: SecretStr | None = Field(default=None, description="CLOB API secret (L2).")
    clob_api_passphrase: SecretStr | None = Field(default=None, description="CLOB API passphrase (L2).")

    clob_signature_type: str | None = Field(
        default=None,
        description="Optional signature mode. Used for deposit wallets / POLY_1271 when supported by client.",
    )
    clob_funder_address: str | None = Field(
        default=None,
        description="Optional funder/deposit wallet address (EIP-1271 style flows), if supported by client.",
    )

    db_url: str = Field(default="duckdb:///./data/polyforge.duckdb")
    initial_cash_balance: float = Field(
        default=10_000.0, ge=0.0, description="Starting cash balance for new portfolios (demo/sim)."
    )

    backtest_initial_capital: float = Field(default=10_000.0, ge=0.0)
    backtest_slippage_bps: int = Field(default=10, ge=0, le=10_000)
    backtest_impact_coeff: float = Field(
        default=0.15,
        ge=0.0,
        description="Impact coefficient used in the volume-based slippage model.",
    )
    backtest_fee_bps: int = Field(default=0, ge=0, le=10_000)
    backtest_gas_usd: float = Field(default=0.0, ge=0.0)
    backtest_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    backtest_walk_forward_train_days: int = Field(default=120, ge=1, le=3650)
    backtest_walk_forward_test_days: int = Field(default=30, ge=1, le=3650)
    backtest_purged_kfold_splits: int = Field(default=5, ge=2, le=50)
    backtest_purge_days: int = Field(default=2, ge=0, le=365)
    backtest_monte_carlo_paths: int = Field(default=2000, ge=0, le=500_000)
    backtest_monte_carlo_block_size: int = Field(default=24, ge=1, le=10_000)
    reports_dir: Path = Field(default=Path("./reports"))

    request_timeout_s: float = Field(default=15.0, ge=1.0, le=120.0)
    max_retries: int = Field(default=3, ge=0, le=10)

    risk_per_trade_pct: float = Field(
        default=0.03,
        ge=0.0,
        le=0.10,
        description="Base risk budget per trade (fraction of equity). Default aligns with 2-5% target band.",
    )
    risk_min_per_trade_pct: float = Field(default=0.02, ge=0.0, le=0.10)
    risk_max_per_trade_pct: float = Field(default=0.05, ge=0.0, le=0.10)
    max_market_exposure_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    max_correlated_exposure_pct: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Max exposure across a correlated group (Phase 2 uses category as correlation proxy).",
    )
    max_daily_drawdown_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    max_total_drawdown_pct: float = Field(default=0.20, ge=0.0, le=1.0)
    circuit_breaker_cooldown_s: int = Field(default=3600, ge=0, le=86_400)

    scanner_market_limit: int = Field(default=200, ge=1, le=5_000)
    scanner_mispricing_min_sum: float = Field(default=0.98, ge=0.0, le=2.0)
    scanner_mispricing_max_sum: float = Field(default=1.02, ge=0.0, le=2.0)
    scanner_whale_trade_usd_threshold: float = Field(default=2_500.0, ge=0.0)
    scanner_resolution_window_hours: int = Field(
        default=72,
        ge=0,
        le=24 * 365,
        description="Treat markets within this window as higher risk / higher attention.",
    )

    llm_provider: str = Field(
        default="mock",
        description="LLM provider: mock|openai|anthropic|groq. Phase 3 supports mock and openai out of the box.",
    )
    llm_model: str = Field(default="gpt-4o-mini", description="Provider model identifier.")
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=1200, ge=64, le=32_000)

    openai_api_key: SecretStr | None = Field(default=None)
    anthropic_api_key: SecretStr | None = Field(default=None)
    groq_api_key: SecretStr | None = Field(default=None)

    agent_checkpointer: str = Field(default="memory", description="Checkpoint backend: memory|duckdb")
    agent_thread_id: str = Field(default="polyforge", description="LangGraph thread/session identifier.")

    cycle_interval_minutes: int = Field(default=5, ge=1, le=24 * 60)
    scanner_interval_minutes: int = Field(default=5, ge=1, le=24 * 60)
    agent_interval_minutes: int = Field(default=5, ge=1, le=24 * 60)

    enabled_strategies: list[str] = Field(default_factory=lambda: ["scanner", "agents"])
    alert_on_high_confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    alert_rate_limit_per_minute: int = Field(default=10, ge=0, le=300)
    discord_webhook_url: AnyUrl | None = Field(default=None)

    dashboard_port: int = Field(default=8501, ge=1, le=65535)
    dashboard_auto_refresh_seconds: int = Field(default=45, ge=5, le=3600)

    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=False, description="Serialize logs as JSON (recommended for production).")
    log_dir: Path = Field(default=Path("./data/logs"))

    telegram_bot_token: SecretStr | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)

    @field_validator("env")
    @classmethod
    def _validate_env(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"dev", "staging", "prod"}:
            raise ValueError("POLYFORGE_ENV must be one of: dev, staging, prod")
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("enabled_strategies", mode="before")
    @classmethod
    def _parse_enabled_strategies(cls, v: object) -> object:
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return parts
        return v

    @field_validator("wallet_mode")
    @classmethod
    def _validate_wallet_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"hot", "proxy", "cold"}:
            raise ValueError("POLYFORGE_WALLET_MODE must be one of: hot, proxy, cold")
        return v

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"conservative", "balanced", "aggressive"}:
            raise ValueError("POLYFORGE_PRESET must be one of: conservative, balanced, aggressive")
        return v

    @field_validator(
        "wallet_private_key",
        "clob_api_secret",
        "clob_api_passphrase",
        "telegram_bot_token",
        "openai_api_key",
        "anthropic_api_key",
        "groq_api_key",
        "key_encryption_password",
    )
    @classmethod
    def _reject_placeholder_secrets(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return None
        raw = v.get_secret_value().strip()
        lowered = raw.lower()
        if lowered in {"", "changeme", "change_me", "your_key_here", "your_private_key"}:
            raise ValueError("Secret value looks like a placeholder; set a real value in .env")
        return v

    @model_validator(mode="after")
    def _safety_checks(self) -> "Settings":
        if self.apply_preset:
            vals = _preset_values(self.preset)
            for k, v in vals.items():
                setattr(self, k, v)

        if self.llm_provider.strip().lower() == "openai" and not self.openai_api_key:
            raise ValueError("LLM provider is openai but POLYFORGE_OPENAI_API_KEY is missing")
        if self.llm_provider.strip().lower() == "anthropic" and not self.anthropic_api_key:
            raise ValueError("LLM provider is anthropic but POLYFORGE_ANTHROPIC_API_KEY is missing")
        if self.llm_provider.strip().lower() == "groq" and not self.groq_api_key:
            raise ValueError("LLM provider is groq but POLYFORGE_GROQ_API_KEY is missing")

        if self.trading_enabled and self.dry_run:
            return self

        if self.trading_enabled and self.wallet_mode == "cold":
            raise ValueError("Trading enabled but wallet_mode is cold (no signing). Disable trading or enable dry-run.")

        if self.trading_enabled and self.wallet_mode == "hot" and not self.wallet_private_key:
            raise ValueError("Trading enabled but wallet_mode=hot and POLYFORGE_WALLET_PRIVATE_KEY is missing")

        if self.trading_enabled and self.wallet_mode == "proxy":
            if not self.clob_funder_address:
                raise ValueError("Trading enabled but wallet_mode=proxy and POLYFORGE_CLOB_FUNDER_ADDRESS is missing")

        if self.trading_enabled:
            missing = []
            if not self.clob_api_key:
                missing.append("POLYFORGE_CLOB_API_KEY")
            if not self.clob_api_secret:
                missing.append("POLYFORGE_CLOB_API_SECRET")
            if not self.clob_api_passphrase:
                missing.append("POLYFORGE_CLOB_API_PASSPHRASE")
            if missing:
                raise ValueError(f"Trading enabled but missing CLOB API creds: {', '.join(missing)}")

        return self


def get_settings() -> Settings:
    return Settings()


def _preset_values(name: str) -> dict[str, object]:
    name = name.strip().lower()
    if name == "balanced":
        return {
            "risk_per_trade_pct": 0.02,
            "max_order_size_usd": 250.0,
            "max_market_exposure_pct": 0.08,
            "max_correlated_exposure_pct": 0.25,
            "max_order_slippage_bps": 60,
            "api_rate_limit_per_s": 5.0,
        }
    if name == "aggressive":
        return {
            "risk_per_trade_pct": 0.04,
            "max_order_size_usd": 750.0,
            "max_market_exposure_pct": 0.15,
            "max_correlated_exposure_pct": 0.40,
            "max_order_slippage_bps": 80,
            "api_rate_limit_per_s": 8.0,
        }
    return {
        "risk_per_trade_pct": 0.01,
        "max_order_size_usd": 100.0,
        "max_market_exposure_pct": 0.05,
        "max_correlated_exposure_pct": 0.20,
        "max_order_slippage_bps": 40,
        "api_rate_limit_per_s": 3.0,
    }
