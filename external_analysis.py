"""Phase 1 external-analysis foundation without external communication."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Protocol

import analyze_market


class ConfigurationError(Exception):
    """Raised when a Phase 1 configuration value is invalid."""


class ContractError(Exception):
    """Raised when analysis input or result data violates the contract."""


class PhaseNotAvailableError(Exception):
    """Raised when a later-phase operation is requested."""


@dataclass(frozen=True)
class ExternalAnalysisConfig:
    provider: str
    dry_run: bool
    max_markets_per_run: int
    reasoning_effort: str


@dataclass(frozen=True)
class AnalysisRequest:
    market_id: str
    question: str
    description: str
    resolution_source: str
    yes_price: Decimal
    no_price: Decimal
    volume: Decimal
    liquidity: Decimal
    deadline: str
    category: str
    days_until_deadline: Decimal
    market_url: str
    analysis_reference_time: str
    selection_reason: str
    reasoning_effort: str


@dataclass(frozen=True)
class ProviderDryRunResult:
    market_id: str
    accepted: bool


class AnalysisProvider(Protocol):
    def analyze(self, request: AnalysisRequest) -> ProviderDryRunResult:
        """Accept a request without producing a formal analysis result."""


class FakeAnalysisProvider:
    def analyze(self, request: AnalysisRequest) -> ProviderDryRunResult:
        return ProviderDryRunResult(market_id=request.market_id, accepted=True)


def load_config(env: Mapping[str, str]) -> ExternalAnalysisConfig:
    provider = env.get("POLYMARKET_AI_PROVIDER", "fake")
    raw_dry_run = env.get("POLYMARKET_AI_DRY_RUN", "true")
    raw_limit = env.get("POLYMARKET_MAX_MARKETS_PER_RUN", "1")
    reasoning_effort = env.get("POLYMARKET_AI_REASONING_EFFORT", "low")

    if provider == "openai":
        raise PhaseNotAvailableError("OpenAI providerはPhase 1では利用できません")
    if provider != "fake":
        raise ConfigurationError("provider設定が不正です")

    if raw_dry_run not in ("true", "false"):
        raise ConfigurationError("dry-run設定が不正です")
    if raw_dry_run == "false":
        raise PhaseNotAvailableError("非dry-run実行はPhase 1では利用できません")

    if not raw_limit.isascii() or not raw_limit.isdecimal():
        raise ConfigurationError("市場数上限が不正です")
    if len(raw_limit) > 1 and raw_limit.startswith("0"):
        raise ConfigurationError("市場数上限が不正です")
    limit = int(raw_limit)
    if not 1 <= limit <= 10:
        raise ConfigurationError("市場数上限は1以上10以下で指定してください")

    if reasoning_effort not in ("low", "medium", "high"):
        raise ConfigurationError("reasoning effort設定が不正です")

    return ExternalAnalysisConfig(
        provider=provider,
        dry_run=True,
        max_markets_per_run=limit,
        reasoning_effort=reasoning_effort,
    )


def load_phase1_snapshot(
    data_dir: Path,
) -> tuple[Path, Path, list[dict[str, object]]]:
    try:
        input_path = analyze_market.find_latest_analysis_input(data_dir)
        result_path = analyze_market.output_path_for(input_path)
        if not result_path.is_file():
            raise ValueError("対応する分析結果JSONがありません")
        input_records = analyze_market.load_analysis_inputs(input_path)
        result_records = analyze_market.load_existing_pending_results(
            result_path
        )
        if any(
            record["schema_version"] != analyze_market.SCHEMA_VERSION
            for record in result_records
        ):
            raise ValueError("Phase 1はschema_version 2.0だけを受理します")
        analyze_market.validate_existing_pending_results(
            result_records,
            input_records,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContractError(str(exc)) from None
    return input_path, result_path, input_records


def select_markets(
    records: list[dict[str, object]],
    limit: int,
) -> list[dict[str, object]]:
    return records[:limit]


def build_requests(
    records: list[dict[str, object]],
    reasoning_effort: str,
) -> list[AnalysisRequest]:
    return [
        AnalysisRequest(
            market_id=record["市場ID"],
            question=record["市場"],
            description=record["市場説明"],
            resolution_source=record["解決情報源"],
            yes_price=record["YES価格"],
            no_price=record["NO価格"],
            volume=record["出来高"],
            liquidity=record["流動性"],
            deadline=record["締切日"],
            category=record["カテゴリ"],
            days_until_deadline=record["締切までの日数"],
            market_url=record["URL"],
            analysis_reference_time=record["分析基準日時"],
            selection_reason=record["選定理由"],
            reasoning_effort=reasoning_effort,
        )
        for record in records
    ]


def format_dry_run(
    input_path: Path,
    result_path: Path,
    config: ExternalAnalysisConfig,
    *,
    pending_count: int,
    requests: list[AnalysisRequest],
) -> str:
    lines = [
        "external analysis dry-run",
        f"input: {input_path.name}",
        f"result: {result_path.name}",
        f"provider: {config.provider}",
        f"reasoning_effort: {config.reasoning_effort}",
        f"max_markets: {config.max_markets_per_run}",
        f"pending_markets: {pending_count}",
        f"selected_markets: {len(requests)}",
        "market_ids:",
    ]
    lines.extend(f"- {request.market_id}" for request in requests)
    return "\n".join(lines) + "\n"


def run_phase1(data_dir: Path, env: Mapping[str, str]) -> str:
    config = load_config(env)
    input_path, result_path, records = load_phase1_snapshot(data_dir)
    selected = select_markets(records, config.max_markets_per_run)
    requests = build_requests(selected, config.reasoning_effort)
    return format_dry_run(
        input_path,
        result_path,
        config,
        pending_count=len(records),
        requests=requests,
    )
