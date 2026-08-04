from django.core.management.base import BaseCommand

from catalog.services.summary_cache import cache_retention_days, purge_expired_summary_cache


class Command(BaseCommand):
    help = "Report or purge generated summary cache entries beyond the retention period."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete expired entries. Without this flag the command is a dry run.",
        )

    def handle(self, *args, **options) -> None:
        apply = options["apply"]
        count = purge_expired_summary_cache(apply=apply)
        mode = "deleted" if apply else "would delete"
        self.stdout.write(
            self.style.SUCCESS(
                f"Retention {cache_retention_days()} days: {mode} {count} cache entries."
            )
        )
