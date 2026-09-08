from django.core.management.base import BaseCommand

from apps.ai.retention import purge_expired


class Command(BaseCommand):
    help = "Delete AI audits older than twelve months."

    def handle(self, *args: object, **options: object) -> None:
        purge_expired()
