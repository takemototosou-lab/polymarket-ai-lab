"""CLI for the network-free external-analysis Phase 1 dry-run."""

import os
import sys
from pathlib import Path
from typing import Mapping

import external_analysis


DATA_DIR = Path(__file__).resolve().parent / "data"


def _write_stdout(output: str) -> None:
    payload = output.encode("utf-8")
    binary = getattr(sys.stdout, "buffer", None)
    if binary is None:
        sys.stdout.write(output)
        return
    written = binary.write(payload)
    if written != len(payload):
        raise OSError("dry-run出力を全バイト書き込めません")
    binary.flush()


def main(
    *,
    data_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    selected_data_dir = DATA_DIR if data_dir is None else data_dir
    selected_env = os.environ if env is None else env
    try:
        output = external_analysis.run_phase1(selected_data_dir, selected_env)
    except external_analysis.ConfigurationError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except external_analysis.ContractError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    except external_analysis.PhaseNotAvailableError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 3

    _write_stdout(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
