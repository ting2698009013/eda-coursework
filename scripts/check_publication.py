"""Check public inputs and compact results, not unpublished netlists or raw logs."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lutsearch.statistics import paired_comparison


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    count = 0
    for manifest_name in ("manifest.json", "manifest_holdout.json"):
        manifest = json.loads((ROOT / manifest_name).read_text(encoding="utf-8"))
        for item in manifest["circuits"]:
            payload = (ROOT / item["file"]).read_bytes()
            require(hashlib.sha256(payload).hexdigest() == item["sha256"],
                    f"Benchmark checksum mismatch: {item['file']}")
            require(len(payload) == item["bytes"], f"Benchmark length: {item['file']}")
            count += 1
    license_digest = hashlib.sha256((ROOT / "benchmarks/EPFL_LICENSE").read_bytes()).hexdigest()
    require(license_digest == "83e4dd21429a91fb7cea67a476032a9641425e5355df2e0f589a738b6ec9fd2c",
            "EPFL license must retain its original contents")

    report_dir = ROOT / "reports/extension"
    expected_counts = {"e1_stability": 1000, "e2_budget": 600, "e3_length": 90,
                       "e3_score": 6, "e4_development": 250, "e5_holdout": 630}
    stages = {}
    for name, expected in expected_counts.items():
        with (report_dir / f"{name}.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        require(len(rows) == expected, f"Unexpected row count: {name}")
        require(len({row["trial"] for row in rows}) == expected, f"Duplicate trial: {name}")
        for row in rows:
            require(row["valid_result"] == "True", f"Invalid summary: {row['trial']}")
            require(row["cec_aig"] == row["cec_blif"] == "equivalent",
                    f"Unexpected saved CEC status: {row['trial']}")
            require(int(row["levels"]) <= int(row["baseline_levels"]),
                    f"Depth constraint violated: {row['trial']}")
            expected_gain = 100 * (int(row["baseline_luts"]) - int(row["luts"])) / int(row["baseline_luts"])
            require(math.isclose(float(row["gain_percent"]), expected_gain, abs_tol=1e-9),
                    f"Saved gain inconsistent: {row['trial']}")
        stages[name] = rows

    findings = json.loads((report_dir / "findings.json").read_text(encoding="utf-8"))
    for key, name, candidate in (("E1", "e1_stability", "lut_beam"),
                                 ("E5", "e5_holdout", "sequence_local")):
        rows = stages[name]
        records = [dict(circuit=row["circuit"], block_id=row["block_id"], method=row["method"],
                        valid_result=True, winner=dict(metrics=dict(luts=int(row["luts"]))))
                   for row in rows]
        actual = paired_comparison(records, candidate=candidate)
        saved = findings[key]
        require(actual["status"] == "ok", f"Incomplete primary pairs: {key}")
        require(actual["blocks_per_circuit"] == saved["blocks_per_circuit"],
                f"Primary sample mismatch: {key}")
        require(math.isclose(actual["delta_percent"], saved["delta_percent"], abs_tol=1e-9),
                f"Primary estimate mismatch: {key}")
        for computed, recorded in zip(actual["ci95_percent"], saved["ci95_percent"]):
            require(math.isclose(computed, recorded, abs_tol=1e-9),
                    f"Bootstrap interval mismatch: {key}")

    print(f"Verified {count} benchmark checksums, original EPFL license, "
          f"{sum(expected_counts.values())} summary rows and E1/E5 primary statistics.")
    print("Scope: public summaries only; original netlists and CEC logs are not revalidated here.")


if __name__ == "__main__":
    main()
