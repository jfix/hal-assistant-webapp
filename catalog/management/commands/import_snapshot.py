from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from catalog.services.imports import apply_snapshot_import, plan_snapshot_import


class Command(BaseCommand):
    help = "Plan or apply an immutable reviewed-workbook snapshot import."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("snapshot", type=Path)
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-report-sha256")

    def handle(self, *args: Any, **options: Any) -> None:
        path: Path = options["snapshot"]
        if not path.is_file():
            raise CommandError(f"Snapshot does not exist: {path}")

        try:
            if options["dry_run"]:
                plan, _ = plan_snapshot_import(path)
                output = plan.as_dict()
            else:
                expected = options.get("expected_report_sha256")
                if not expected:
                    raise CommandError("--apply requires --expected-report-sha256")
                output = apply_snapshot_import(
                    path,
                    expected_report_sha256=expected,
                ).as_dict()
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(json.dumps(output, ensure_ascii=False, indent=2))
