from __future__ import annotations

import getpass
import os
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError, CommandParser

GROUP_NAME = "Réviseurs"


class Command(BaseCommand):
    help = (
        "Ensure the reviewer group exists (with the review permission) and "
        "create or update a reviewer account in it."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username")
        parser.add_argument("--email", default="")
        parser.add_argument(
            "--password",
            help=(
                "Set the password non-interactively. Falls back to "
                "DJANGO_REVIEWER_PASSWORD, then an interactive prompt."
            ),
        )

    def _ensure_group(self) -> Group:
        group, _ = Group.objects.get_or_create(name=GROUP_NAME)
        permission = Permission.objects.get(
            codename="review_publication",
            content_type__app_label="catalog",
        )
        group.permissions.add(permission)
        return group

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        group = self._ensure_group()

        username = options["username"]
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={"email": options["email"], "is_staff": False},
        )
        if created:
            password = (
                options.get("password")
                or os.getenv("DJANGO_REVIEWER_PASSWORD")
                or getpass.getpass("Password: ")
            )
            if not password:
                raise CommandError("A password is required for a new reviewer.")
            user.set_password(password)
            user.is_active = True
            user.save()
        elif options["email"] and user.email != options["email"]:
            user.email = options["email"]
            user.save(update_fields=["email"])

        user.groups.add(group)
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} reviewer '{username}' in group '{GROUP_NAME}'."
            )
        )
