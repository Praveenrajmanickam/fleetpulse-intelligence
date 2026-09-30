"""Gold table storage (local Parquet): full deterministic rebuild from Silver, staged then
published by directory rename, with a build manifest. Same semantics as ParquetSilverStore;
a Delta implementation would replace only this module."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pyspark.sql import DataFrame

from fleetpulse.gold.schema import TABLES


class ParquetGoldStore:
    def __init__(self, root: Path, compression: str = "zstd"):
        self.root = Path(root)
        self.compression = compression

    def recover(self) -> dict:
        restored = 0
        trash = self.root / "_trash"
        if trash.exists():
            for build in trash.iterdir():
                for t in build.iterdir():
                    if not (self.root / t.name).exists():
                        t.rename(self.root / t.name)
                        restored += 1
            shutil.rmtree(trash)
        shutil.rmtree(self.root / "_staging", ignore_errors=True)
        return {"restored": restored}

    def staged(self, build_id: str, name: str) -> Path:
        return self.root / "_staging" / build_id / name

    def write(self, df: DataFrame, build_id: str, table: str) -> None:
        df.select(*TABLES[table].fieldNames()).coalesce(1).write.option("compression", self.compression).parquet(
            str(self.staged(build_id, table)))

    def write_jsonl(self, rows: list[dict], build_id: str, name: str) -> None:
        p = self.staged(build_id, name)
        p.mkdir(parents=True, exist_ok=True)
        (p / "evidence.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))

    def read(self, spark, table: str) -> DataFrame:
        return spark.read.schema(TABLES[table]).parquet(str(self.root / table))

    def publish(self, build_id: str, names: list[str], manifest: dict) -> None:
        trash = self.root / "_trash" / build_id
        trash.mkdir(parents=True, exist_ok=True)
        for n in names:
            cur, new = self.root / n, self.staged(build_id, n)
            if cur.exists():
                cur.rename(trash / n)
            new.rename(cur)
        (self.root / "_builds").mkdir(exist_ok=True)
        (self.root / "_builds" / f"{build_id}.json").write_text(json.dumps(manifest, indent=2, default=str))
        shutil.rmtree(self.root / "_trash", ignore_errors=True)
        shutil.rmtree(self.root / "_staging", ignore_errors=True)
